"""Plug-in anchoring: queue a digest, publish it through the configured provider off the request path, and heal
pending or unconfirmed anchors whenever their status is read (no cron needed in split deployments)."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..anchor import AnchorProvider
from ..store import Doc, Store

log = logging.getLogger("utsu_core.anchors")
_PURPOSE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
RETRY_AFTER = 60
SYSTEM = "system"
ACTIVE = ("pending", "submitted")
PUBLIC = ("id", "purpose", "digest", "provider", "status", "ref", "tx", "chain_id", "block", "created_at", "at")


def _clean_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in list((meta or {}).items())[:12]:
        if isinstance(key, str) and isinstance(value, (str, int, bool)) and not isinstance(value, float):
            out[key[:40]] = value[:200] if isinstance(value, str) else value
    return out


class AnchorRuntime:
    def __init__(self, store: Store, provider: AnchorProvider, audit: Any, now: Callable[[], int],
                 background: bool | None = None) -> None:
        self._store = store
        self._provider = provider
        self._audit = audit
        self._now = now
        remote = provider.remote if background is None else background
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="anchor") if remote else None

    @property
    def enabled(self) -> bool:
        return bool(self._provider.enabled)

    def for_owner(self, owner: str) -> "AnchorView":
        return AnchorView(self, owner)

    def submit(self, owner: str, purpose: str, digest: str, meta: Mapping[str, Any] | None = None) -> str:
        if not _PURPOSE.fullmatch(purpose or ""):
            raise ValueError("anchor purpose must be a short lowercase identifier")
        if not _DIGEST.fullmatch(digest or ""):
            raise ValueError("anchor digest must be 32 bytes of lowercase hex")
        anchor_id = f"{owner}.{purpose}.{digest[:32]}"
        doc = {"id": anchor_id, "owner": owner, "purpose": purpose, "digest": digest, "meta": _clean_meta(meta),
               "provider": self._provider.name, "status": "pending" if self.enabled else "disabled",
               "created_at": self._now(), "next_at": 0, "attempts": 0}
        if self._store.create("anchors", anchor_id, doc):
            self._audit.record(SYSTEM, "anchor.queued", anchor_id, None, None,
                               {"owner": owner, "digest": digest, "provider": doc["provider"]})
        self._dispatch(anchor_id)
        return anchor_id

    def status(self, owner: str, anchor_id: str) -> Doc | None:
        doc = self._store.get("anchors", anchor_id)
        if doc is None or doc.get("owner") != owner:
            return None
        if self.enabled and doc.get("status") in ACTIVE and self._now() >= int(doc.get("next_at", 0)):
            self._dispatch(anchor_id)
            doc = self._store.get("anchors", anchor_id) or doc
        return {key: doc[key] for key in PUBLIC if key in doc}

    def sweep(self) -> int:
        """Run every due anchor synchronously (scheduled maintenance)."""
        due = [doc["id"] for doc in self._store.list("anchors") if doc.get("status") in ACTIVE]
        return sum(1 for anchor_id in due if self._work(anchor_id))

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _dispatch(self, anchor_id: str) -> None:
        if not self.enabled:
            return
        if self._executor is None:
            self._work(anchor_id)
        else:
            try:
                self._executor.submit(self._work, anchor_id)
            except RuntimeError:  # shut down: maintenance or a later status read retries
                log.warning("anchor executor closed; %s left for retry", anchor_id)

    def _work(self, anchor_id: str) -> bool:
        now = self._now()
        claimed: dict[str, Doc] = {}

        def claim(doc: Doc | None) -> Doc | None:
            if doc is None or doc.get("status") not in ACTIVE or now < int(doc.get("next_at", 0)):
                return None
            claimed["doc"] = doc
            return {**doc, "next_at": now + RETRY_AFTER, "attempts": int(doc.get("attempts", 0)) + 1}

        self._store.update("anchors", anchor_id, claim)
        doc = claimed.get("doc")
        if doc is None:
            return False  # another worker or replica holds it, or it is not due
        try:
            if doc["status"] == "pending":
                result = self._provider.submit(doc["digest"], {"purpose": doc["purpose"], **doc.get("meta", {})})
            else:
                result = self._provider.confirm(doc)
        except Exception as exc:
            log.warning("anchor %s attempt failed: %s", anchor_id, type(exc).__name__)
            self._store.update("anchors", anchor_id, lambda d: {**d, "error": type(exc).__name__, "at": now} if d else None)
            return True
        if not result:
            return True
        update = {key: value for key, value in result.items() if key in PUBLIC or key in ("error", "block_hash", "from")}
        updated = self._store.update("anchors", anchor_id, lambda d: {**d, **update, "at": now} if d else None)
        if updated and updated.get("status") != doc["status"]:
            self._audit.record(SYSTEM, f"anchor.{updated['status']}", anchor_id, None, None,
                               {"owner": doc["owner"], "digest": doc["digest"], "ref": str(updated.get("ref", ""))[:200]})
        return True


class AnchorView:
    """The `AnchorService` handed to one plug-in: its anchors only."""

    def __init__(self, runtime: AnchorRuntime, owner: str) -> None:
        self._runtime = runtime
        self._owner = owner

    @property
    def enabled(self) -> bool:
        return self._runtime.enabled

    def submit(self, purpose: str, digest: str, meta: Mapping[str, Any] | None = None) -> str:
        return self._runtime.submit(self._owner, purpose, digest, meta)

    def status(self, anchor_id: str) -> Doc | None:
        return self._runtime.status(self._owner, anchor_id)
