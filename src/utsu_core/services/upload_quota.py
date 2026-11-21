"""Decoded-byte quotas shared by every upload path, including legacy records."""
from __future__ import annotations

import base64
import secrets
import threading
from typing import Callable, Any

from fastapi import HTTPException

from ..settings import Settings
from ..store import MemoryStore, Store

GIB = 1024 ** 3
COLLECTIONS = frozenset({"media", "agm_media", "receipts", "financial_profiles", "sap_banking"})


def data_url_size(value: str) -> int:
    return len(base64.b64decode(value.split(",", 1)[1], validate=True))


class QuotaStore(Store):
    """Reserve before writing; interrupted writes retain quota until explicitly reconciled."""

    def __init__(self, inner: Store, usage_store: Store, settings: Settings):
        self.inner, self.usage_store, self.settings = inner, usage_store, settings
        self._initialized: set[str] = set()
        self._lock = threading.RLock()

    def _owner(self, col: str, key: str, doc: dict) -> str:
        if col == "receipts":
            return f"club:{doc['club_id']}"
        uid = doc.get("uuid")
        if col in {"financial_profiles", "sap_banking"}:
            uid = uid or key
        if not uid:
            check = self.inner.get("verifications" if col == "media" else "agm_checks", key)
            uid = check and check.get("uuid")
        if not uid:
            raise RuntimeError(f"upload owner missing for {col}/{key}")
        return f"user:{uid}"

    @staticmethod
    def _item(col: str, key: str) -> str:
        return f"{'banking' if col in {'sap_banking', 'financial_profiles'} else col}:{key}"

    @staticmethod
    def _size(col: str, doc: dict) -> int:
        if col in {"media", "agm_media"}:
            return sum(data_url_size(doc[field]) for field in ("face", "id_card") if doc.get(field))
        if col == "receipts":
            return doc["size"] if doc.get("storage") else data_url_size(doc["data_url"])
        return sum(data_url_size(cheque["data_url"]) for cheque in (
            doc.get("void_cheque"), (doc.get("pending") or {}).get("void_cheque")
        ) if cheque)

    def _initialize(self, owner: str) -> None:
        with self._lock:
            if owner in self._initialized:
                return
            items: dict[str, dict] = {}
            if owner.startswith("club:"):
                for doc in self.inner.list("receipts", club_id=owner.removeprefix("club:")):
                    items[self._item("receipts", doc["id"])] = {"size": self._size("receipts", doc)}
            else:
                uid = owner.removeprefix("user:")
                for col in ("financial_profiles", "sap_banking"):
                    doc = self.inner.get(col, uid)
                    if doc:
                        item = self._item(col, uid)
                        items[item] = {"size": max(self._size(col, doc), items.get(item, {}).get("size", 0))}
                for col in ("media", "agm_media"):
                    for doc in self.inner.list(col, uuid=uid):
                        if doc.get("id"):
                            items[self._item(col, doc["id"])] = {"size": self._size(col, doc)}
            for col, parents in (("media", "verifications"), ("agm_media", "agm_checks")):
                for parent in self.inner.list(parents, uuid=owner.removeprefix("user:")):
                    key = parent["id"]
                    doc = self.inner.get(col, key)
                    if doc:
                        items[self._item(col, key)] = {"size": self._size(col, doc)}

            def seed(current):
                current = current or {"id": owner, "items": {}}
                for key in items:
                    if key in current["items"]:
                        continue
                    collection, reference = key.split(":", 1)
                    if collection == "banking":
                        documents = [self.inner.get(col, reference) for col in ("financial_profiles", "sap_banking")]
                        size = max((self._size("financial_profiles", doc) for doc in documents if doc), default=0)
                    else:
                        stored = self.inner.get(collection, reference)
                        size = self._size(collection, stored) if stored else 0
                    if size:
                        current["items"][key] = {"size": size}
                return current

            self.usage_store.update("upload_quotas", owner, seed)
            self._initialized.add(owner)

    def limit(self, owner: str) -> int:
        if owner.startswith("user:"):
            return self.settings.user_storage_bytes
        club = self.inner.get("clubs", owner.removeprefix("club:"))
        if not club:
            raise RuntimeError(f"quota owner does not exist: {owner}")
        years = set(club.get("registered_years", []))
        if club.get("approval_year") is not None:
            years.add(club["approval_year"])
        return min(max(1, len(years)) * self.settings.club_storage_year_bytes, 10 * GIB)

    def usage(self, owner: str) -> dict:
        self._initialize(owner)
        record = self.usage_store.get("upload_quotas", owner)
        if record is None:
            raise RuntimeError(f"quota record missing: {owner}")
        used = sum(item["size"] for item in record["items"].values())
        limit = self.limit(owner)
        return {"used_bytes": used, "limit_bytes": limit, "remaining_bytes": max(0, limit - used),
                "reserved_files": sum(bool(item.get("token")) for item in record["items"].values())}

    def backfill(self) -> None:
        owners = {f"club:{doc['club_id']}" for doc in self.inner.list("receipts")}
        for col in ("financial_profiles", "sap_banking", "verifications", "agm_checks", "media", "agm_media"):
            owners.update(f"user:{doc['uuid']}" for doc in self.inner.list(col) if doc.get("uuid"))
        for owner in sorted(owners):
            self._initialize(owner)

    def reconcile(self, owner: str, scope: str) -> dict:
        """Writers must be stopped; rebuild only the collections owned by this deployment."""
        prefixes = {"auth": ("media:",), "portal": ("agm_media:", "banking:", "receipts:"),
                    "all": ("media:", "agm_media:", "banking:", "receipts:")}[scope]
        snapshot = QuotaStore(self.inner, MemoryStore(), self.settings)
        snapshot._initialize(owner)
        rebuilt = snapshot.usage_store.get("upload_quotas", owner)["items"]

        def repair(current):
            current = current or {"id": owner, "items": {}}
            current["items"] = {key: value for key, value in current["items"].items()
                                if not key.startswith(prefixes)}
            current["items"].update({key: value for key, value in rebuilt.items() if key.startswith(prefixes)})
            return current

        self.usage_store.update("upload_quotas", owner, repair)
        self._initialized.add(owner)
        return self.usage(owner)

    def _write(self, col: str, key: str, doc: dict | None, operation: Callable[[], Any]) -> Any:
        if col not in COLLECTIONS:
            return operation()
        old = self.inner.get(col, key)
        if old is None and doc is None:
            return operation()
        owner = self._owner(col, key, doc if doc is not None else old)
        if old and self._owner(col, key, old) != owner:
            raise RuntimeError("upload ownership cannot be changed")
        self._initialize(owner)
        item, token = self._item(col, key), secrets.token_hex(16)
        size = self._size(col, doc) if doc is not None else 0
        if doc is None and col in {"sap_banking", "financial_profiles"}:
            other = self.inner.get("financial_profiles" if col == "sap_banking" else "sap_banking", key)
            size = self._size(col, other) if other else 0
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
            stored = self.inner.get(col, key)
            final_size = self._size(col, stored) if stored else 0
            if col in {"sap_banking", "financial_profiles"}:
                other = self.inner.get("financial_profiles" if col == "sap_banking" else "sap_banking", key)
                final_size = max(final_size, self._size(col, other) if other else 0)

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

    def get(self, col, id):
        return self.inner.get(col, id)

    def list(self, col, **equals):
        return self.inner.list(col, **equals)

    def put(self, col, id, doc):
        return self._write(col, id, doc, lambda: self.inner.put(col, id, doc))

    def create(self, col, id, doc):
        return self._write(col, id, doc, lambda: self.inner.create(col, id, doc))

    def replace(self, col, id, doc, expected_rev):
        if col in COLLECTIONS:
            old = self.inner.get(col, id)
            if old is not None and self._size(col, old) == self._size(col, doc):
                if self._owner(col, id, old) != self._owner(col, id, doc):
                    raise RuntimeError("upload ownership cannot be changed")
                return self.inner.replace(col, id, doc, expected_rev)
        return self._write(col, id, doc, lambda: self.inner.replace(col, id, doc, expected_rev))

    def delete(self, col, id):
        return self._write(col, id, None, lambda: self.inner.delete(col, id))
