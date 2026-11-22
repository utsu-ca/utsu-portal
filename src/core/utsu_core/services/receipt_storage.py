"""Private encrypted receipt blobs; rclone is only a transport to cold storage."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from ..crypto import Crypter
from ..contracts import AuditService
from ..settings import Settings

log = logging.getLogger(__name__)
YEAR = 365 * 24 * 3600
TORONTO = ZoneInfo("America/Toronto")


def academic_year(timestamp: int) -> int:
    date = datetime.fromtimestamp(timestamp, TORONTO)
    return date.year if date.month >= 9 else date.year - 1


class ReceiptIntegrityError(RuntimeError):
    pass


class ReceiptStore(Protocol):
    def get(self, collection: str, key: str, /) -> dict[str, Any] | None: ...
    def list(self, collection: str, /, **equals: Any) -> list[dict[str, Any]]: ...
    def create(self, collection: str, key: str, value: dict[str, Any], /) -> bool: ...
    def replace(self, collection: str, key: str, value: dict[str, Any], expected_rev: int, /) -> bool: ...
    def update(self, collection: str, key: str,
               fn: Callable[[dict[str, Any] | None], dict[str, Any] | None], /) -> dict[str, Any] | None: ...


class ReceiptStorage:
    def __init__(self, store: ReceiptStore, settings: Settings, now: Callable[[], int], audit: AuditService,
                 *, academic_year: Callable[[int], int]):
        self.store, self.settings, self.now, self.audit = store, settings, now, audit
        self.academic_year = academic_year
        self.crypter = Crypter(settings.data_key, settings.secret)
        self.root = Path(settings.receipt_storage_path) if settings.receipt_storage_path else None
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
        if settings.receipt_cold_remote and (
            settings.receipt_cold_remote.startswith("-") or ":" not in settings.receipt_cold_remote
        ):
            raise ValueError("PORTAL_RECEIPT_COLD_REMOTE must be an rclone remote:path")

    def _path(self, key: str) -> Path:
        if self.root is None:
            raise RuntimeError("PORTAL_RECEIPT_STORAGE_PATH is required for external receipts")
        if not re.fullmatch(r"[a-f0-9]{32}\.enc", key):
            raise ValueError("invalid receipt blob key")
        path = self.root / key
        if path.is_symlink():
            raise ValueError("receipt blobs cannot be symbolic links")
        return path

    def _remote(self, key: str) -> str:
        self._path(key)
        if not self.settings.receipt_cold_remote:
            raise RuntimeError("PORTAL_RECEIPT_COLD_REMOTE is required for cold receipt access")
        return f"{self.settings.receipt_cold_remote.rstrip('/')}/{key}"

    @staticmethod
    def _rclone(*args: str) -> bytes:
        try:
            return subprocess.run(["rclone", *args], check=True, capture_output=True, timeout=300).stdout
        except (OSError, subprocess.SubprocessError):
            log.exception("receipt cold-storage operation failed")
            raise

    def _externalize(self, receipt: dict) -> dict:
        content = receipt["data_url"]
        raw = base64.b64decode(content.split(",", 1)[1], validate=True)
        if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
            raise ReceiptIntegrityError("legacy receipt content checksum mismatch")
        encrypted = self.crypter.enc(content).encode("ascii")
        key = secrets.token_hex(16) + ".enc"
        destination = self._path(key)
        fd, temporary = tempfile.mkstemp(dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {**{k: v for k, v in receipt.items() if k != "data_url"},
                "size": len(raw),
                "media_type": content.split(";", 1)[0].removeprefix("data:"),
                "academic_year": self.academic_year(receipt["at"]),
                "last_accessed_at": receipt.get("last_accessed_at", self.now()),
                "storage": {"key": key, "tier": "hot", "sha256": hashlib.sha256(encrypted).hexdigest()}}

    def _discard_unreferenced(self, receipt: dict) -> None:
        current = self.store.get("receipts", receipt["id"])
        if not current or current.get("storage", {}).get("key") != receipt["storage"]["key"]:
            self._path(receipt["storage"]["key"]).unlink(missing_ok=True)

    def create(self, receipt: dict) -> None:
        if self.root is None:
            if not self.store.create("receipts", receipt["id"], receipt):
                raise RuntimeError("receipt identifier collision")
            return
        external = self._externalize(receipt)
        try:
            if not self.store.create("receipts", receipt["id"], external):
                raise RuntimeError("receipt identifier collision")
        finally:
            self._discard_unreferenced(external)

    def migrate(self) -> list[str]:
        if self.root is None:
            raise RuntimeError("receipt migration requires PORTAL_RECEIPT_STORAGE_PATH")
        migrated = []
        for receipt in self.store.list("receipts"):
            if receipt.get("storage"):
                continue
            external = self._externalize(receipt)
            try:
                if self.store.replace("receipts", receipt["id"], external, receipt.get("_rev", 0)):
                    migrated.append(receipt["id"])
                    self.audit.record("system:storage", "receipt.migrated", f"club:{receipt['club_id']}",
                                      None, None, {"receipt": receipt["id"]})
            finally:
                self._discard_unreferenced(external)
        return migrated

    def read(self, reference: str, club_id: str) -> bytes | None:
        receipt = self.store.get("receipts", reference)
        if not receipt or receipt["club_id"] != club_id:
            return None
        storage = receipt.get("storage")
        if not storage:
            content = receipt["data_url"]
        else:
            if storage["tier"] == "hot":
                try:
                    encrypted = self._path(storage["key"]).read_bytes()
                except FileNotFoundError:
                    current = self.store.get("receipts", reference)
                    if not current or current.get("storage", {}).get("tier") != "cold":
                        raise
                    encrypted = self._rclone("cat", self._remote(storage["key"]))
            elif storage["tier"] == "cold":
                encrypted = self._rclone("cat", self._remote(storage["key"]))
            else:
                raise ValueError("unknown receipt storage tier")
            if hashlib.sha256(encrypted).hexdigest() != storage["sha256"]:
                raise ReceiptIntegrityError("receipt ciphertext checksum mismatch")
            content = self.crypter.dec(encrypted.decode("ascii"))
            raw = base64.b64decode(content.split(",", 1)[1], validate=True)
            if len(raw) != receipt["size"] or hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
                raise ReceiptIntegrityError("receipt content checksum mismatch")

        def accessed(current):
            if not current or current["club_id"] != club_id:
                raise FileNotFoundError(reference)
            return {**current, "last_accessed_at": max(self.now(), current.get("last_accessed_at", 0))}

        self.store.update("receipts", reference, accessed)
        return content.encode("ascii")

    def archive(self, *, apply: bool = False) -> list[dict]:
        if apply and (not self.settings.receipt_archive_enabled or not self.settings.receipt_cold_remote):
            raise RuntimeError("archiving requires PORTAL_RECEIPT_ARCHIVE_ENABLED=1 and PORTAL_RECEIPT_COLD_REMOTE")
        now = self.now()
        results = []
        for receipt in self.store.list("receipts"):
            storage = receipt.get("storage")
            if storage and storage["tier"] == "cold":
                # Recover an interrupted deletion only after re-verifying the cold copy.
                if apply and self._path(storage["key"]).exists():
                    try:
                        self._verify_cold(storage)
                        self.audit.record("system:storage", "receipt.archive_cleanup", f"club:{receipt['club_id']}",
                                          None, None, {"receipt": receipt["id"]})
                        self._path(storage["key"]).unlink(missing_ok=True)
                    except (OSError, subprocess.SubprocessError, ReceiptIntegrityError):
                        log.exception("cold receipt cleanup failed for %s", receipt["id"])
                        results.append({"id": receipt["id"], "club_id": receipt["club_id"], "state": "error"})
                continue
            old_year = receipt.get("academic_year", self.academic_year(receipt["at"])) < self.academic_year(now)
            inactive = now - receipt.get("last_accessed_at", receipt["at"]) >= YEAR
            if not old_year and not inactive:
                continue
            result = {"id": receipt["id"], "club_id": receipt["club_id"],
                      "reason": "previous_academic_year" if old_year else "inactive", "state": "candidate"}
            if apply:
                if not storage:
                    result["state"] = "requires_migration"
                else:
                    try:
                        source = self._path(storage["key"])
                        if hashlib.sha256(source.read_bytes()).hexdigest() != storage["sha256"]:
                            raise ReceiptIntegrityError("hot receipt checksum mismatch; refusing to archive")
                        self._rclone("copyto", str(source), self._remote(storage["key"]))
                        self._verify_cold(storage)
                        self.audit.record("system:storage", "receipt.archive_verified", f"club:{receipt['club_id']}",
                                          None, None, {"receipt": receipt["id"], "reason": result["reason"]})
                        cold = {**receipt, "storage": {**storage, "tier": "cold"}, "archived_at": now}
                        if self.store.replace("receipts", receipt["id"], cold, receipt.get("_rev", 0)):
                            self.audit.record("system:storage", "receipt.archived", f"club:{receipt['club_id']}",
                                              None, None, {"receipt": receipt["id"], "reason": result["reason"]})
                            source.unlink(missing_ok=True)
                            result["state"] = "archived"
                        else:
                            result["state"] = "changed_during_copy"
                    except (OSError, subprocess.SubprocessError, ReceiptIntegrityError):
                        log.exception("receipt archive failed for %s", receipt["id"])
                        result["state"] = "error"
            results.append(result)
        return results

    def _verify_cold(self, storage: dict) -> None:
        copied = self._rclone("cat", self._remote(storage["key"]))
        if hashlib.sha256(copied).hexdigest() != storage["sha256"]:
            raise ReceiptIntegrityError("cold receipt checksum mismatch; retaining hot copy")
