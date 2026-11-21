import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from utsu_core.contracts import FileKind
from utsu_core.services.files import DocumentService
from utsu_core.services.receipt_storage import ReceiptStorage, YEAR
from utsu_core.services.upload_quota import GIB, QuotaStore
from utsu_core.settings import Settings
from utsu_core.store import MemoryStore


class RecordingAudit:
    def __init__(self) -> None:
        self.records = []

    def record(self, actor, action, target, before=None, after=None, extra=None) -> None:
        self.records.append((actor, action, target, extra))


def test_file_service_scopes_owners_and_audits_successful_reads_and_deletes():
    audit = RecordingAudit()
    deleted = []
    service = DocumentService(audit)
    service.register_kind(
        "agm",
        "scan",
        FileKind(
            authorize=lambda action, actor, _resource, _reference: actor == "reviewer" and action in {"read", "delete"},
            read=lambda _reference, _resource: b"encrypted-adapter-content",
            delete=lambda reference, _resource: deleted.append(reference),
            audit_action="agm.scan_accessed",
            audit_target=lambda resource, _reference: f"user:{resource}",
        ),
    )
    service.freeze()
    owner_view = service.for_owner("agm")

    with pytest.raises(PermissionError):
        service.for_owner("other").read("scan", "scan-id", "reviewer", "user-id")
    with pytest.raises(PermissionError):
        owner_view.read("scan", "scan-id", "applicant", "user-id")
    assert audit.records == []

    assert owner_view.read("scan", "scan-id", "reviewer", "user-id") == b"encrypted-adapter-content"
    owner_view.delete("scan", "scan-id", "reviewer", "user-id")
    assert deleted == ["scan-id"]
    assert audit.records == [
        ("reviewer", "agm.scan_accessed", "user:user-id", {"kind": "scan", "reference": "scan-id"}),
        ("reviewer", "file.deleted", "user:user-id", {"kind": "scan", "reference": "scan-id"}),
    ]

    with pytest.raises(RuntimeError, match="registrations are frozen"):
        service.register_kind("agm", "late", FileKind(authorize=lambda *_: True, read=lambda *_: b""))


def data_url(raw=b"%PDF-1", mime="application/pdf"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def quota(settings=None, inner=None, usage=None):
    inner = inner or MemoryStore()
    return QuotaStore(inner, usage or inner, settings or Settings(secret="s" * 32))


def test_personal_quota_exact_boundary_aggregates_types_and_releases_on_delete():
    store = quota()
    store.put("media", "scan", {"id": "scan", "uuid": "user", "face": data_url(b"x" * 7_500_000)})
    store.put("financial_profiles", "user", {"uuid": "user", "void_cheque": {"data_url": data_url(b"x" * 7_500_000)}})
    assert store.usage("user:user")["used_bytes"] == 15_000_000
    with pytest.raises(HTTPException) as exc:
        store.put("agm_media", "agm", {"id": "agm", "uuid": "user", "face": data_url(b"x")})
    assert exc.value.status_code == 413
    assert store.get("agm_media", "agm") is None
    store.delete("media", "scan")
    store.put("agm_media", "agm", {"id": "agm", "uuid": "user", "face": data_url(b"x")})
    assert store.usage("user:user")["used_bytes"] == 7_500_001
    store.put("financial_profiles", "user", {"uuid": "user", "status": "destroyed"})
    assert store.usage("user:user")["used_bytes"] == 1


def test_shared_quota_reservations_prevent_parallel_overallocation():
    usage = MemoryStore()
    settings = Settings(secret="s" * 32, user_storage_bytes=10)
    writers = [quota(settings, MemoryStore(), usage) for _ in range(2)]

    def upload(index):
        try:
            writers[index].put("media", str(index), {"id": str(index), "uuid": "user", "face": data_url(b"x" * 6)})
            return 201
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(upload, range(2))) == [201, 413]
    assert writers[0].usage("user:user")["used_bytes"] == 6


def test_legacy_usage_failed_write_and_explicit_reservation_recovery():
    inner = MemoryStore()
    inner.put("verifications", "old", {"id": "old", "uuid": "user"})
    inner.put("media", "old", {"face": data_url(b"old")})
    store = quota(Settings(secret="s" * 32, user_storage_bytes=5), inner)
    assert store.usage("user:user")["used_bytes"] == 3
    assert not store.replace("media", "old", {"uuid": "user", "face": data_url(b"four")}, 99)
    assert store.usage("user:user")["used_bytes"] == 3
    inner.update("upload_quotas", "user:user", lambda doc: {
        **doc, "items": {**doc["items"], "agm_media:orphan": {"size": 2, "token": "interrupted", "previous": 0}}})
    assert store.usage("user:user")["reserved_files"] == 1
    repaired = store.reconcile("user:user", "portal")
    assert repaired["used_bytes"] == 3 and repaired["reserved_files"] == 0


def test_club_quota_counts_distinct_registered_years_not_elapsed_time_and_caps():
    store = quota()
    store.put("clubs", "club", {"id": "club", "approval_year": 2025, "registered_years": [2020, 2025, 2025]})
    assert store.limit("club:club") == 2 * GIB
    store.put("clubs", "club", {"id": "club", "registered_years": list(range(2010, 2026))})
    assert store.limit("club:club") == 10 * GIB


def receipt_storage(tmp_path, *, enabled=True):
    settings = Settings(secret="s" * 32, receipt_storage_path=str(tmp_path), receipt_cold_remote="cold:receipts",
                        receipt_archive_enabled=enabled)
    store = quota(settings)
    store.put("clubs", "club", {"id": "club", "registered_years": [2025, 2026]})
    clock = [int(datetime(2026, 10, 1, tzinfo=ZoneInfo("America/Toronto")).timestamp())]
    audit = RecordingAudit()
    storage = ReceiptStorage(store, settings, lambda: clock[0], audit)
    return store, storage, clock, audit


def receipt_record(at, raw=b"%PDF-1", reference="receipt"):
    return {"id": reference, "club_id": "club", "at": at, "data_url": data_url(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "transaction_id": None}


def transport(monkeypatch, storage):
    remote = {}

    def rclone(*args):
        if args[0] == "copyto":
            remote[args[2]] = Path(args[1]).read_bytes()
            return b""
        return remote[args[1]]

    monkeypatch.setattr(storage, "_rclone", rclone)
    return remote


def test_receipt_migration_preserves_content_ids_usage_and_encrypts_hot_files(tmp_path):
    store, storage, clock, _audit = receipt_storage(tmp_path)
    store.put("receipts", "receipt", receipt_record(clock[0] - 10))
    usage = store.usage("club:club")["used_bytes"]
    assert storage.migrate() == ["receipt"]
    assert storage.migrate() == []
    receipt = store.get("receipts", "receipt")
    assert "data_url" not in receipt and receipt["storage"]["tier"] == "hot"
    encrypted = (tmp_path / receipt["storage"]["key"]).read_bytes()
    assert b"%PDF-1" not in encrypted and data_url().encode() not in encrypted
    assert storage.read("receipt", "club") == data_url().encode()
    assert store.usage("club:club")["used_bytes"] == usage
    assert store.get("receipts", "receipt")["last_accessed_at"] == clock[0]


def test_receipt_report_only_then_previous_year_archive_and_cold_read(tmp_path, monkeypatch):
    store, storage, clock, audit = receipt_storage(tmp_path)
    remote = transport(monkeypatch, storage)
    storage.create(receipt_record(clock[0] - 100 * 86400))
    receipt = store.get("receipts", "receipt")
    path = tmp_path / receipt["storage"]["key"]
    assert storage.archive()[0]["reason"] == "previous_academic_year"
    assert path.exists() and not remote
    assert storage.archive(apply=True)[0]["state"] == "archived"
    assert not path.exists() and store.get("receipts", "receipt")["storage"]["tier"] == "cold"
    assert store.usage("club:club")["used_bytes"] == 6
    clock[0] += 60
    assert storage.read("receipt", "club") == data_url().encode()
    assert store.get("receipts", "receipt")["last_accessed_at"] == clock[0]
    assert any(record[1] == "receipt.archived" for record in audit.records)


def test_receipt_archive_inactivity_boundary_and_recent_access(tmp_path):
    store, storage, clock, _audit = receipt_storage(tmp_path)
    storage.create(receipt_record(clock[0]))
    assert storage.archive() == []
    clock[0] += YEAR - 1
    # Isolate the inactivity rule from the separate academic-year rule.
    from utsu_core.programs.clubs.module import academic_year
    store.update("receipts", "receipt", lambda doc: {**doc, "academic_year": academic_year(clock[0] + 1)})
    assert storage.archive() == []
    clock[0] += 1
    assert storage.archive()[0]["reason"] == "inactive"
    storage.read("receipt", "club")
    assert storage.archive() == []


@pytest.mark.parametrize("failure", ["offline", "corrupt", "changed"])
def test_archive_failure_or_concurrent_read_retains_hot_copy(tmp_path, monkeypatch, failure):
    store, storage, clock, _audit = receipt_storage(tmp_path)
    remote = transport(monkeypatch, storage)
    storage.create(receipt_record(clock[0] - 100 * 86400))
    receipt = store.get("receipts", "receipt")
    path = tmp_path / receipt["storage"]["key"]
    original = storage._rclone

    def rclone(*args):
        if failure == "offline":
            raise OSError("offline")
        result = original(*args)
        if args[0] == "copyto":
            if failure == "corrupt":
                remote[args[2]] = b"corrupt"
            else:
                storage.read("receipt", "club")
        return result

    monkeypatch.setattr(storage, "_rclone", rclone)
    if failure == "changed":
        assert storage.archive(apply=True)[0]["state"] == "changed_during_copy"
    else:
        assert storage.archive(apply=True)[0]["state"] == "error"
    assert path.exists() and store.get("receipts", "receipt")["storage"]["tier"] == "hot"


def test_archive_requires_explicit_enablement_and_rejects_invalid_keys(tmp_path):
    _store, storage, _clock, _audit = receipt_storage(tmp_path, enabled=False)
    with pytest.raises(RuntimeError, match="archiving requires"):
        storage.archive(apply=True)
    with pytest.raises(ValueError, match="invalid receipt blob key"):
        storage._path("../outside")


def test_receipt_reads_work_during_interrupted_quota_reservation(tmp_path):
    store, storage, clock, _audit = receipt_storage(tmp_path)
    storage.create(receipt_record(clock[0]))
    store.usage_store.update("upload_quotas", "club:club", lambda doc: {
        **doc, "items": {**doc["items"], "receipts:receipt": {"size": 6, "token": "interrupted", "previous": 6}}})
    with ThreadPoolExecutor(2) as executor:
        assert list(executor.map(lambda _: storage.read("receipt", "club"), range(2))) == [data_url().encode()] * 2
    assert store.usage("club:club")["reserved_files"] == 1


def test_one_missing_receipt_does_not_block_other_archives(tmp_path, monkeypatch):
    store, storage, clock, _audit = receipt_storage(tmp_path)
    transport(monkeypatch, storage)
    storage.create(receipt_record(clock[0] - 100 * 86400, reference="missing"))
    storage.create(receipt_record(clock[0] - 100 * 86400, reference="good"))
    missing = store.get("receipts", "missing")
    (tmp_path / missing["storage"]["key"]).unlink()
    results = storage.archive(apply=True)
    assert [result["state"] for result in results] == ["error", "archived"]


def test_maintenance_reports_archive_failure_and_reconciles_quota(tmp_path, monkeypatch):
    from utsu_core import receipt_maintenance

    store, storage, clock, _audit = receipt_storage(tmp_path)
    transport(monkeypatch, storage)
    storage.create(receipt_record(clock[0] - 100 * 86400))
    receipt = store.get("receipts", "receipt")
    (tmp_path / receipt["storage"]["key"]).unlink()
    ctx = SimpleNamespace(upload_quotas=store, services={"receipt_storage": storage}, settings=storage.settings)
    monkeypatch.setattr(receipt_maintenance, "create_app", lambda: SimpleNamespace(state=SimpleNamespace(ctx=ctx)))
    assert receipt_maintenance.main(["--apply"]) == 1
    assert receipt_maintenance.main(["--reconcile-owner", "club:club"]) == 0

def test_missing_file_is_not_audited():
    audit = RecordingAudit()
    service = DocumentService(audit)
    service.register_kind(
        "clubs",
        "receipt",
        FileKind(authorize=lambda *_: True, read=lambda *_: None),
    )

    with pytest.raises(FileNotFoundError):
        service.for_owner("clubs").read("receipt", "missing", "member", "club-id")
    assert audit.records == []
