"""Decoded-byte quotas shared by every upload path, including legacy records.

Core owns the durable retained-policy catalogue and fencing epoch. The
catalogue lives in the quota store (SQLite/Redis-backed via ``Store``) so it
survives provider removal and is shared across processes. Every writer
checks the epoch before reserving quota; a stale process fails closed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Callable

from fastapi import HTTPException

from ..settings import Settings
from ..pagination import PageOrder
from ..store import Doc, MemoryStore, Store

GIB = 1024 ** 3
QUOTA_POLICY_ENTRY_POINT_GROUP = "utsu.portal.upload_quota_policies"

CATALOGUE_COLLECTION = "quota_policy_catalogue"
CATALOGUE_ID = "current"
CATALOGUE_SCHEMA_VERSION = 1
CATALOGUE_MIN_VERSION = 1
CATALOGUE_MAX_VERSION = 1


class RetainedPolicyError(RuntimeError):
    """A retained quota policy cannot be enforced safely."""


@dataclass(frozen=True)
class RetainedPolicy:
    """Durable declaration of a quota policy's identity and shape."""

    name: str
    collections: frozenset[str]
    item_prefixes: tuple[str, ...]
    version: int = 1
    status: str = "active"  # active | retained

    def fingerprint(self) -> str:
        """Shape fingerprint: collections, item prefixes, and version.

        The name is an identifier, not part of the shape, so a sanctioned
        replacement policy (same collections and prefixes, new name) keeps
        the same fingerprint.
        """
        payload = json.dumps(
            {
                "collections": sorted(self.collections),
                "item_prefixes": sorted(self.item_prefixes),
                "version": self.version,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _policy_fingerprint(policy: UploadQuotaPolicy) -> str:
    return RetainedPolicy(
        name=policy.name,
        collections=policy.collections,
        item_prefixes=policy.item_prefixes,
    ).fingerprint()


def _catalogue_doc(epoch: int, policies: Iterable[RetainedPolicy]) -> Doc:
    return {
        "id": CATALOGUE_ID,
        "schema_version": CATALOGUE_SCHEMA_VERSION,
        "epoch": epoch,
        "policies": [
            {
                "name": policy.name,
                "collections": sorted(policy.collections),
                "item_prefixes": sorted(policy.item_prefixes),
                "version": policy.version,
                "status": policy.status,
                "fingerprint": policy.fingerprint(),
            }
            for policy in sorted(policies, key=lambda item: item.name)
        ],
    }


def _parse_catalogue(doc: Doc | None) -> tuple[int, dict[str, RetainedPolicy]]:
    if doc is None:
        return 0, {}
    schema_version = doc.get("schema_version")
    if not isinstance(schema_version, int) or not CATALOGUE_MIN_VERSION <= schema_version <= CATALOGUE_MAX_VERSION:
        raise RetainedPolicyError(
            f"unsupported quota policy catalogue schema version: {schema_version!r}"
        )
    epoch = doc.get("epoch")
    if not isinstance(epoch, int) or epoch < 0:
        raise RetainedPolicyError("quota policy catalogue has an invalid epoch")
    policies: dict[str, RetainedPolicy] = {}
    for entry in doc.get("policies", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise RetainedPolicyError("quota policy catalogue contains an invalid entry")
        policy = RetainedPolicy(
            name=entry["name"],
            collections=frozenset(entry.get("collections", [])),
            item_prefixes=tuple(entry.get("item_prefixes", [])),
            version=int(entry.get("version", 1)),
            status=str(entry.get("status", "active")),
        )
        if entry.get("fingerprint") != policy.fingerprint():
            raise RetainedPolicyError(
                f"quota policy catalogue entry for {policy.name} has a mismatched fingerprint"
            )
        if policy.name in policies:
            raise RetainedPolicyError(f"duplicate quota policy catalogue entry: {policy.name}")
        policies[policy.name] = policy
    return epoch, policies


def _require_atomic_store(store: Store, label: str) -> None:
    """Fail closed unless the store provides atomic compare-and-swap.

    The generic ``Store.create`` is get-then-put and is not atomic. Only
    backends that override ``create``/``replace`` with atomic versions may
    hold the catalogue. ``MemoryStore`` and ``SQLiteStore`` qualify;
    custom backends must override both methods.
    """
    if type(store).create is Store.create or type(store).replace is Store.replace:
        raise RetainedPolicyError(
            f"{label} store does not provide atomic compare-and-swap; "
            "quota policy catalogue requires an atomic backend (SQLite, SQL, Firestore, or a custom Store overriding create/replace)"
        )


def data_url_size(value: str) -> int:
    return len(base64.b64decode(value.split(",", 1)[1], validate=True))


@dataclass(frozen=True)
class UploadQuotaPolicy:
    """Collection-specific rules consumed by the shared reservation mechanism."""

    name: str
    collections: frozenset[str]
    item_prefixes: tuple[str, ...]
    owner_for: Callable[[Store, str, str, Doc], str]
    item_for: Callable[[str, str], str]
    size_for: Callable[[str, Doc], int]
    discover_items: Callable[[Store, str], set[str]]
    list_owners: Callable[[Store], set[str]]
    item_size: Callable[[Store, str], int]
    write_size: Callable[[Store, str, str, Doc | None, str], int]
    limit_for: Callable[[Store, Settings, str], int | None]
    reconcile_prefixes: Callable[[str], tuple[str, ...]]


def _media_size(_collection: str, doc: Doc) -> int:
    return sum(data_url_size(doc[field]) for field in ("face", "id_card") if doc.get(field))


def _banking_size(_collection: str, doc: Doc) -> int:
    return sum(data_url_size(cheque["data_url"]) for cheque in (
        doc.get("void_cheque"), (doc.get("pending") or {}).get("void_cheque")
    ) if cheque)


def _receipt_size(_collection: str, doc: Doc) -> int:
    return doc["size"] if doc.get("storage") else data_url_size(doc["data_url"])


def _user_limit(_store: Store, settings: Settings, owner: str) -> int | None:
    return settings.user_storage_bytes if owner.startswith("user:") else None


def _media_policy(collection: str, parent_collection: str) -> UploadQuotaPolicy:
    prefix = f"{collection}:"

    def owner_for(store: Store, _collection: str, key: str, doc: Doc) -> str:
        uid = doc.get("uuid")
        if not uid:
            parent = store.get(parent_collection, key)
            uid = parent and parent.get("uuid")
        if not uid:
            raise RuntimeError(f"upload owner missing for {collection}/{key}")
        return f"user:{uid}"

    def discover_items(store: Store, owner: str) -> set[str]:
        if not owner.startswith("user:"):
            return set()
        uid = owner.removeprefix("user:")
        items = {
            f"{prefix}{doc['id']}" for doc in store.list(collection, uuid=uid) if doc.get("id")
        }
        for parent in store.list(parent_collection, uuid=uid):
            key = parent["id"]
            if store.get(collection, key):
                items.add(f"{prefix}{key}")
        return items

    def list_owners(store: Store) -> set[str]:
        owners = {
            f"user:{doc['uuid']}"
            for source in (store.list(collection), store.list(parent_collection))
            for doc in source
            if doc.get("uuid")
        }
        return owners

    def item_size(store: Store, item: str) -> int:
        key = item.removeprefix(prefix)
        doc = store.get(collection, key)
        return _media_size(collection, doc) if doc else 0

    def write_size(_store: Store, _collection: str, _key: str, doc: Doc | None, _item: str) -> int:
        return _media_size(collection, doc) if doc else 0

    def reconcile_prefixes(scope: str) -> tuple[str, ...]:
        return {
            "auth": (prefix,) if collection == "media" else (),
            "portal": (prefix,) if collection == "agm_media" else (),
            "all": (prefix,),
        }[scope]

    return UploadQuotaPolicy(
        name=f"legacy-{collection}",
        collections=frozenset({collection}),
        item_prefixes=(prefix,),
        owner_for=owner_for,
        item_for=lambda _collection, key: f"{prefix}{key}",
        size_for=_media_size,
        discover_items=discover_items,
        list_owners=list_owners,
        item_size=item_size,
        write_size=write_size,
        limit_for=_user_limit,
        reconcile_prefixes=reconcile_prefixes,
    )


def _banking_policy() -> UploadQuotaPolicy:
    collections = ("financial_profiles", "sap_banking")
    prefix = "banking:"

    def owner_for(_store: Store, _collection: str, key: str, doc: Doc) -> str:
        uid = doc.get("uuid") or key
        if not uid:
            raise RuntimeError(f"upload owner missing for financial_profiles/{key}")
        return f"user:{uid}"

    def discover_items(store: Store, owner: str) -> set[str]:
        if not owner.startswith("user:"):
            return set()
        uid = owner.removeprefix("user:")
        return {f"{prefix}{uid}"} if any(store.get(collection, uid) for collection in collections) else set()

    def list_owners(store: Store) -> set[str]:
        return {
            f"user:{doc['uuid']}"
            for collection in collections
            for doc in store.list(collection)
            if doc.get("uuid")
        }

    def item_size(store: Store, item: str) -> int:
        key = item.removeprefix(prefix)
        return max(
            (_banking_size(collection, doc) for collection in collections
             if (doc := store.get(collection, key)) is not None),
            default=0,
        )

    def write_size(store: Store, collection: str, key: str, doc: Doc | None, _item: str) -> int:
        if doc is not None:
            return _banking_size(collection, doc)
        other_collection = collections[1] if collection == collections[0] else collections[0]
        other = store.get(other_collection, key)
        return _banking_size(other_collection, other) if other else 0

    def reconcile_prefixes(scope: str) -> tuple[str, ...]:
        return {"auth": (), "portal": (prefix,), "all": (prefix,)}[scope]

    return UploadQuotaPolicy(
        name="legacy-banking",
        collections=frozenset(collections),
        item_prefixes=(prefix,),
        owner_for=owner_for,
        item_for=lambda _collection, key: f"{prefix}{key}",
        size_for=_banking_size,
        discover_items=discover_items,
        list_owners=list_owners,
        item_size=item_size,
        write_size=write_size,
        limit_for=_user_limit,
        reconcile_prefixes=reconcile_prefixes,
    )


def _receipt_policy() -> UploadQuotaPolicy:
    prefix = "receipts:"

    def owner_for(_store: Store, _collection: str, _key: str, doc: Doc) -> str:
        return f"club:{doc['club_id']}"

    def discover_items(store: Store, owner: str) -> set[str]:
        if not owner.startswith("club:"):
            return set()
        club_id = owner.removeprefix("club:")
        return {f"{prefix}{doc['id']}" for doc in store.list("receipts", club_id=club_id)}

    def list_owners(store: Store) -> set[str]:
        return {f"club:{doc['club_id']}" for doc in store.list("receipts")}

    def item_size(store: Store, item: str) -> int:
        doc = store.get("receipts", item.removeprefix(prefix))
        return _receipt_size("receipts", doc) if doc else 0

    def write_size(_store: Store, _collection: str, _key: str, doc: Doc | None, _item: str) -> int:
        return _receipt_size("receipts", doc) if doc else 0

    def limit_for(store: Store, settings: Settings, owner: str) -> int | None:
        if not owner.startswith("club:"):
            return None
        club = store.get("clubs", owner.removeprefix("club:"))
        if not club:
            raise RuntimeError(f"quota owner does not exist: {owner}")
        years = set(club.get("registered_years", []))
        if club.get("approval_year") is not None:
            years.add(club["approval_year"])
        return min(max(1, len(years)) * settings.club_storage_year_bytes, 10 * GIB)

    def reconcile_prefixes(scope: str) -> tuple[str, ...]:
        return {"auth": (), "portal": (prefix,), "all": (prefix,)}[scope]

    return UploadQuotaPolicy(
        name="legacy-club-receipts",
        collections=frozenset({"receipts"}),
        item_prefixes=(prefix,),
        owner_for=owner_for,
        item_for=lambda _collection, key: f"{prefix}{key}",
        size_for=_receipt_size,
        discover_items=discover_items,
        list_owners=list_owners,
        item_size=item_size,
        write_size=write_size,
        limit_for=limit_for,
        reconcile_prefixes=reconcile_prefixes,
    )


def _legacy_policies() -> tuple[UploadQuotaPolicy, ...]:
    """Always-on compatibility policies for data retained from optional or uninstalled programs."""
    return (
        _media_policy("media", "verifications"),
        _media_policy("agm_media", "agm_checks"),
        _banking_policy(),
        _receipt_policy(),
    )


def _installed_policies() -> tuple[UploadQuotaPolicy, ...]:
    """Load external quota policies via entry points.

    Providers are loaded here; the catalogue check in ``QuotaStore`` decides
    whether they may run. A provider that cannot be loaded fails closed.
    """
    loaded: list[UploadQuotaPolicy] = []
    for provider in metadata.entry_points(group=QUOTA_POLICY_ENTRY_POINT_GROUP):
        try:
            policy = provider.load()
        except Exception as exc:
            raise RetainedPolicyError(
                f"external upload quota provider {provider.name} failed to load: {exc}"
            ) from exc
        if not isinstance(policy, UploadQuotaPolicy):
            raise RetainedPolicyError(
                f"external upload quota provider {provider.name} did not return an UploadQuotaPolicy"
            )
        loaded.append(policy)
    return tuple(loaded)


class QuotaStore(Store):
    """Reserve writes and retain compatibility policies regardless of enabled program packages."""

    def __init__(
        self,
        inner: Store,
        usage_store: Store,
        settings: Settings,
        *,
        policies: Iterable[UploadQuotaPolicy] = (),
        _discover_installed: bool = True,
    ):
        self.inner, self.usage_store, self.settings = inner, usage_store, settings
        installed = _installed_policies() if _discover_installed else ()
        self._additional_policies = (*installed, *tuple(policies))
        compatibility = _legacy_policies()
        claimed: set[str] = set()
        for policy in self._additional_policies:
            overlap = claimed.intersection(policy.collections)
            if overlap:
                raise ValueError(f"multiple upload quota policies handle {', '.join(sorted(overlap))}")
            claimed.update(policy.collections)
        for policy in compatibility:
            overlap = policy.collections.intersection(claimed)
            if overlap:
                replacements = [
                    candidate for candidate in self._additional_policies
                    if candidate.collections.intersection(policy.collections)
                ]
                if (
                    len(replacements) != 1
                    or replacements[0].collections != policy.collections
                    or set(replacements[0].item_prefixes) != set(policy.item_prefixes)
                ):
                    raise ValueError(
                        f"replacement policy for {policy.name} must cover its collections "
                        "and preserve its quota item prefixes"
                    )
        self.policies = tuple(
            policy for policy in compatibility if not policy.collections.intersection(claimed)
        ) + self._additional_policies
        self._policy_by_collection: dict[str, UploadQuotaPolicy] = {}
        self._policy_by_item_prefix: dict[str, UploadQuotaPolicy] = {}
        for policy in self.policies:
            for collection in policy.collections:
                if collection in self._policy_by_collection:
                    raise ValueError(f"multiple upload quota policies handle {collection}")
                self._policy_by_collection[collection] = policy
            for prefix in policy.item_prefixes:
                if any(prefix.startswith(other) or other.startswith(prefix) for other in self._policy_by_item_prefix):
                    raise ValueError(f"overlapping upload quota item prefix: {prefix}")
                self._policy_by_item_prefix[prefix] = policy
        self._initialized: set[str] = set()
        self._lock = threading.RLock()
        self._epoch: int | None = None
        self._sync_catalogue()

    # -- retained-policy catalogue -------------------------------------

    def _declared_policies(self) -> tuple[RetainedPolicy, ...]:
        return tuple(
            RetainedPolicy(
                name=policy.name,
                collections=policy.collections,
                item_prefixes=policy.item_prefixes,
            )
            for policy in self.policies
        )

    def _sync_catalogue(self) -> None:
        """Reconcile the durable catalogue with the policies this process runs, in one atomic update.

        - New policies are declared and the epoch is bumped; an unchanged catalogue keeps its epoch, so replicas
          starting together do not fence each other.
        - A catalogued policy with no installed policy of the same shape fails closed: its records still need
          executable rules that are no longer installed. A same-shape policy under a new name is a rename.
        - A fingerprint mismatch for a known name fails closed.
        """
        _require_atomic_store(self.usage_store, "quota")
        declared = {policy.name: policy for policy in self._declared_policies()}
        declared_fingerprints = {policy.fingerprint() for policy in declared.values()}

        def merge(current):
            epoch, catalogued = _parse_catalogue(current)
            missing = sorted(
                name for name, policy in catalogued.items()
                if name not in declared and policy.fingerprint() not in declared_fingerprints
            )
            if missing:
                raise RetainedPolicyError(
                    "retained upload quota policies are required but not installed: " + ", ".join(missing)
                )
            for name, policy in declared.items():
                existing = catalogued.get(name)
                if existing is not None and existing.fingerprint() != policy.fingerprint():
                    raise RetainedPolicyError(
                        f"upload quota policy {name} changed shape without a catalogue migration"
                    )
            if current is not None and not set(declared) - set(catalogued):
                return None
            return _catalogue_doc(epoch + 1, {**catalogued, **declared}.values())

        record = self.usage_store.update(CATALOGUE_COLLECTION, CATALOGUE_ID, merge)
        if record is None:
            raise RetainedPolicyError("quota policy catalogue could not be written")
        self._epoch = int(record["epoch"])

    def _check_epoch(self) -> None:
        """Fence stale processes: refuse writes after a catalogue change."""
        epoch, _ = _parse_catalogue(
            self.usage_store.get(CATALOGUE_COLLECTION, CATALOGUE_ID)
        )
        if self._epoch is not None and epoch != self._epoch:
            raise RetainedPolicyError(
                "quota policy catalogue changed; this process is stale and must restart"
            )

    def _policy_for_item(self, item: str) -> UploadQuotaPolicy:
        policy = next(
            (policy for prefix, policy in self._policy_by_item_prefix.items() if item.startswith(prefix)),
            None,
        )
        if policy is None:
            raise RuntimeError(f"no upload quota policy for item {item}")
        return policy

    def _initialize(self, owner: str) -> None:
        with self._lock:
            if owner in self._initialized:
                return
            items = sorted({
                item
                for policy in self.policies
                for item in policy.discover_items(self.inner, owner)
            })

            def seed(current):
                current = current or {"id": owner, "items": {}}
                for item in items:
                    if item in current["items"]:
                        continue
                    size = self._policy_for_item(item).item_size(self.inner, item)
                    if size:
                        current["items"][item] = {"size": size}
                return current

            self.usage_store.update("upload_quotas", owner, seed)
            self._initialized.add(owner)

    def limit(self, owner: str) -> int:
        limits = {
            policy_limit
            for policy in self.policies
            if (policy_limit := policy.limit_for(self.inner, self.settings, owner)) is not None
        }
        if not limits:
            raise RuntimeError(f"quota owner does not exist: {owner}")
        if len(limits) > 1:
            raise RuntimeError(f"conflicting upload quota limits for {owner}")
        return next(iter(limits))

    def usage(self, owner: str) -> dict[str, int]:
        self._initialize(owner)
        record = self.usage_store.get("upload_quotas", owner)
        if record is None:
            raise RuntimeError(f"quota record missing: {owner}")
        used = sum(item["size"] for item in record["items"].values())
        limit = self.limit(owner)
        return {"used_bytes": used, "limit_bytes": limit, "remaining_bytes": max(0, limit - used),
                "reserved_files": sum(bool(item.get("token")) for item in record["items"].values())}

    def backfill(self) -> None:
        owners = {
            owner for policy in self.policies for owner in policy.list_owners(self.inner)
        }
        for owner in sorted(owners):
            self._initialize(owner)

    def reconcile(self, owner: str, scope: str) -> dict[str, int]:
        """Writers must be stopped; rebuild only the collections owned by this deployment."""
        prefixes = tuple(
            prefix for policy in self.policies for prefix in policy.reconcile_prefixes(scope)
        )
        snapshot = QuotaStore(
            self.inner,
            MemoryStore(),
            self.settings,
            policies=self._additional_policies,
            _discover_installed=False,
        )
        snapshot._initialize(owner)
        record = snapshot.usage_store.get("upload_quotas", owner)
        if record is None:
            raise RuntimeError(f"quota record missing after rebuilding: {owner}")
        rebuilt = record["items"]

        def repair(current):
            current = current or {"id": owner, "items": {}}
            current["items"] = {key: value for key, value in current["items"].items()
                                if not key.startswith(prefixes)}
            current["items"].update({key: value for key, value in rebuilt.items() if key.startswith(prefixes)})
            return current

        self.usage_store.update("upload_quotas", owner, repair)
        self._initialized.add(owner)
        return self.usage(owner)

    def _write(
        self, col: str, key: str, doc: Doc | None, operation: Callable[[], Any]
    ) -> Any:
        policy = self._policy_by_collection.get(col)
        if policy is None:
            return operation()
        self._check_epoch()
        old = self.inner.get(col, key)
        if doc is not None:
            current_doc = doc
        elif old is not None:
            current_doc = old
        else:
            return operation()
        owner = policy.owner_for(self.inner, col, key, current_doc)
        if old is not None and policy.owner_for(self.inner, col, key, old) != owner:
            raise RuntimeError("upload ownership cannot be changed")
        self._initialize(owner)
        item, token = policy.item_for(col, key), secrets.token_hex(16)
        size = policy.write_size(self.inner, col, key, doc, item)
        limit = self.limit(owner)

        def reserve(current):
            entry = current["items"].get(item, {"size": 0})
            if entry.get("token"):
                raise HTTPException(409, "upload write is in progress; check storage reservations before retrying")
            previous = entry["size"]
            total = sum(value["size"] for value in current["items"].values())
            if size > previous and total + size - previous > limit:
                raise HTTPException(413, "total storage allocation exceeded")
            current["items"][item] = {"size": max(size, previous), "previous": previous, "token": token}
            return current

        self.usage_store.update("upload_quotas", owner, reserve)
        try:
            return operation()
        finally:
            final_size = policy.item_size(self.inner, item)

            def finish(current):
                entry = current["items"][item]
                if entry.get("token") != token:
                    raise RuntimeError("upload quota reservation changed")
                if final_size:
                    current["items"][item] = {"size": final_size}
                else:
                    current["items"].pop(item)
                return current

            self.usage_store.update("upload_quotas", owner, finish)

    def get(self, col: str, id: str) -> Doc | None:
        return self.inner.get(col, id)

    def list(self, col: str, **equals: Any) -> list[Doc]:
        return self.inner.list(col, **equals)

    def page(self, col: str, *, order_by: tuple[PageOrder, ...], after: tuple[Any, ...] | None,
             limit: int, equals: Mapping[str, Any] | None = None) -> list[Doc]:
        return self.inner.page(col, order_by=order_by, after=after, limit=limit, equals=equals)

    def put(self, col: str, id: str, doc: Doc) -> None:
        return self._write(col, id, doc, lambda: self.inner.put(col, id, doc))

    def create(self, col: str, id: str, doc: Doc) -> bool:
        return self._write(col, id, doc, lambda: self.inner.create(col, id, doc))

    def replace(self, col: str, id: str, doc: Doc, expected_rev: int) -> bool:
        policy = self._policy_by_collection.get(col)
        if policy is not None:
            old = self.inner.get(col, id)
            if old is not None and policy.size_for(col, old) == policy.size_for(col, doc):
                if policy.owner_for(self.inner, col, id, old) != policy.owner_for(self.inner, col, id, doc):
                    raise RuntimeError("upload ownership cannot be changed")
                return self.inner.replace(col, id, doc, expected_rev)
        return self._write(col, id, doc, lambda: self.inner.replace(col, id, doc, expected_rev))

    def delete(self, col: str, id: str) -> None:
        return self._write(col, id, None, lambda: self.inner.delete(col, id))
