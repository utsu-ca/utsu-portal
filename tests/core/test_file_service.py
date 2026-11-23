import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from core.contracts import FileKind
from core.crypto import Crypter, EncryptingStore, PREFIX
from core.pagination import PageOrder
from core.services import upload_quota
from core.services.files import DocumentService
from core.services.financial_access import FinancialAccessStore
from core.services.receipt_storage import ReceiptStorage, YEAR
from core.services.upload_quota import (
    GIB,
    QUOTA_POLICY_ENTRY_POINT_GROUP,
    QuotaStore,
    UploadQuotaPolicy,
)
from core.settings import Settings
from core.store import MemoryStore, SQLiteStore


class RecordingAudit:
    def __init__(self) -> None:
        self.records = []

    def record(self, actor, action, target, before=None, after=None, extra=None) -> None:
        self.records.append((actor, action, target, extra))


def test_core_store_wrappers_preserve_indexed_pages_and_financial_audits(monkeypatch):
    inner = SQLiteStore()
    encrypted = EncryptingStore(inner, Crypter(None, "s" * 32))
    audit = RecordingAudit()
    financial = FinancialAccessStore(encrypted, SimpleNamespace(audit=audit))
    store = QuotaStore(financial, inner, Settings(secret="s" * 32))
    for uid in ("a", "b"):
        encrypted.put("financial_profiles", uid, {"uuid": uid, "account_number": uid})
    assert inner.get("financial_profiles", "a")["account_number"].startswith(PREFIX)

    def no_scan(*_args, **_kwargs):
        pytest.fail("page unexpectedly scanned the collection")

    monkeypatch.setattr(inner, "list", no_scan)
    page = store.page("financial_profiles", order_by=(PageOrder("uuid", str),), after=None, limit=1)
    assert [(item["uuid"], item["account_number"]) for item in page] == [("a", "a")]
    assert ("system:financial", "financial.access", "financial_profiles:*", {"operation": "list"}) in audit.records

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit, "record", unavailable)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        store.page("financial_profiles", order_by=(PageOrder("uuid", str),), after=None, limit=1)


def test_financial_access_policy_audits_only_matching_submissions():
    audit = RecordingAudit()
    store = FinancialAccessStore(MemoryStore(), SimpleNamespace(audit=audit))
    order = (PageOrder("id", str),)
    store.get("submissions", "senate_application:user")
    store.list("submissions", form_id="senate_application")
    store.page("submissions", order_by=order, after=None, limit=1, equals={"form_id": "senate_application"})
    assert audit.records == []

    store.get("submissions", "student_aid_bursary:user")
    store.list("submissions", form_id="student_aid_bursary")
    store.page("submissions", order_by=order, after=None, limit=1, equals={"form_id": "student_aid_bursary"})
    store.list("submissions")
    assert [record[3]["operation"] for record in audit.records] == ["get", "list", "list", "list"]


def test_encrypted_sort_fields_are_not_persisted_in_page_indexes():
    inner = SQLiteStore()
    store = EncryptingStore(inner, Crypter(None, "s" * 32))
    for uid, number in (("a", "9876"), ("b", "1234")):
        store.put("financial_profiles", uid, {"uuid": uid, "account_number": number})

    page = store.page("financial_profiles", order_by=(PageOrder("account_number", str),), after=None, limit=2)
    assert [item["uuid"] for item in page] == ["b", "a"]
    assert inner._c.execute("SELECT COUNT(*) FROM portal_page_specs").fetchone()[0] == 0


def test_encrypted_nested_filters_are_not_persisted_in_page_indexes():
    inner = SQLiteStore()
    store = EncryptingStore(inner, Crypter(None, "s" * 32))
    for uid, term in (("a", "fall"), ("b", "winter")):
        store.put("submissions", uid, {"id": uid, "data": {"term": term}})

    page = store.page("submissions", order_by=(PageOrder("id", str),), after=None, limit=2,
                      equals={"data.term": "winter"})
    assert [item["id"] for item in page] == ["b"]
    assert inner._c.execute("SELECT COUNT(*) FROM portal_page_specs").fetchone()[0] == 0


def test_encrypted_top_level_filters_are_compared_after_decryption():
    inner = SQLiteStore()
    store = EncryptingStore(inner, Crypter(None, "s" * 32))
    for uid, number in (("a", "1234"), ("b", "9876"), ("c", "1234")):
        store.put("financial_profiles", uid, {"uuid": uid, "account_number": number})

    order = (PageOrder("uuid", str),)
    first = store.page("financial_profiles", order_by=order, after=None, limit=1,
                       equals={"account_number": "1234"})
    second = store.page("financial_profiles", order_by=order, after=("a",), limit=1,
                        equals={"account_number": "1234"})
    assert [first[0]["uuid"], second[0]["uuid"]] == ["a", "c"]
    assert [item["uuid"] for item in store.list("financial_profiles", uuid="c", account_number="1234")] == ["c"]
    assert inner._c.execute("SELECT COUNT(*) FROM portal_page_specs").fetchone()[0] == 0


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


def program_upload_policy() -> UploadQuotaPolicy:
    def discover_items(store, owner):
        uid = owner.removeprefix("user:")
        return {
            f"program:{doc['id']}"
            for doc in store.list("program_uploads", uuid=uid)
            if doc.get("id")
        }

    def list_owners(store):
        return {
            f"user:{doc['uuid']}" for doc in store.list("program_uploads") if doc.get("uuid")
        }

    def item_size(store, item):
        doc = store.get("program_uploads", item.removeprefix("program:"))
        return doc["size"] if doc else 0

    return UploadQuotaPolicy(
        name="program-uploads",
        collections=frozenset({"program_uploads"}),
        item_prefixes=("program:",),
        owner_for=lambda _store, _collection, _key, doc: f"user:{doc['uuid']}",
        item_for=lambda _collection, key: f"program:{key}",
        size_for=lambda _collection, doc: doc["size"],
        discover_items=discover_items,
        list_owners=list_owners,
        item_size=item_size,
        write_size=lambda _store, _collection, _key, doc, _item: doc["size"] if doc else 0,
        limit_for=lambda _store, config, owner: (
            config.user_storage_bytes if owner.startswith("user:") else None
        ),
        reconcile_prefixes=lambda scope: {
            "auth": (), "portal": ("program:",), "all": ("program:",)
        }[scope],
    )


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


def test_legacy_quota_policies_account_for_retained_data_without_program_policies():
    inner, usage = MemoryStore(), MemoryStore()
    settings = Settings(secret="s" * 32, user_storage_bytes=9, club_storage_year_bytes=6)
    inner.put("clubs", "club", {"id": "club", "registered_years": [2025]})
    inner.put("agm_checks", "scan", {"id": "scan", "uuid": "user"})
    inner.put("agm_media", "scan", {"id": "scan", "face": data_url(b"agm")})
    inner.put("financial_profiles", "user", {
        "uuid": "user", "void_cheque": {"data_url": data_url(b"cheque")}
    })
    inner.put("receipts", "receipt", {
        "id": "receipt", "club_id": "club", "data_url": data_url(b"r")
    })

    store = QuotaStore(inner, usage, settings, policies=())
    store.backfill()

    assert store.usage("user:user")["used_bytes"] == 9
    assert store.usage("club:club")["used_bytes"] == 1
    assert usage.get("upload_quotas", "user:user")["items"] == {
        "agm_media:scan": {"size": 3},
        "banking:user": {"size": 6},
    }


def test_package_quota_policy_shares_reservations_with_always_on_policies():
    inner, usage = MemoryStore(), MemoryStore()
    settings = Settings(secret="s" * 32, user_storage_bytes=5)
    inner.put("agm_media", "legacy", {
        "id": "legacy", "uuid": "user", "face": data_url(b"old")
    })
    store = QuotaStore(inner, usage, settings, policies=(program_upload_policy(),))

    store.put("program_uploads", "new", {"id": "new", "uuid": "user", "size": 2})
    with pytest.raises(HTTPException) as exc:
        store.put("program_uploads", "too-large", {"id": "too-large", "uuid": "user", "size": 1})

    assert exc.value.status_code == 413
    assert store.usage("user:user")["used_bytes"] == 5


def test_external_quota_provider_is_loaded_and_catalogued(monkeypatch: pytest.MonkeyPatch):
    policy = program_upload_policy()
    provider = SimpleNamespace(name="sample", load=lambda: policy)
    monkeypatch.setattr(
        upload_quota.metadata,
        "entry_points",
        lambda *, group: (provider,) if group == QUOTA_POLICY_ENTRY_POINT_GROUP else (),
    )

    usage = MemoryStore()
    store = QuotaStore(MemoryStore(), usage, Settings(secret="s" * 32))
    assert any(item.name == "program-uploads" for item in store.policies)
    catalogue = usage.get("quota_policy_catalogue", "current")
    assert catalogue is not None
    assert any(entry["name"] == "program-uploads" for entry in catalogue["policies"])


def test_external_quota_provider_failure_fails_closed(monkeypatch: pytest.MonkeyPatch):
    provider = SimpleNamespace(name="broken", load=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(
        upload_quota.metadata,
        "entry_points",
        lambda *, group: (provider,) if group == QUOTA_POLICY_ENTRY_POINT_GROUP else (),
    )

    with pytest.raises(RuntimeError, match="failed to load"):
        QuotaStore(MemoryStore(), MemoryStore(), Settings(secret="s" * 32))


def test_retained_policy_missing_provider_fails_closed():
    inner, usage = MemoryStore(), MemoryStore()
    settings = Settings(secret="s" * 32)
    QuotaStore(inner, usage, settings, policies=(program_upload_policy(),))
    # Provider uninstalled: a new process without it must fail closed.
    with pytest.raises(RuntimeError, match="retained upload quota policies are required"):
        QuotaStore(inner, usage, settings)


def test_stale_process_is_fenced_after_catalogue_change():
    inner, usage = MemoryStore(), MemoryStore()
    settings = Settings(secret="s" * 32)
    stale = QuotaStore(inner, usage, settings)
    QuotaStore(inner, usage, settings, policies=(program_upload_policy(),))
    with pytest.raises(RuntimeError, match="stale and must restart"):
        stale.put("media", "scan", {"id": "scan", "uuid": "user", "face": data_url(b"x")})


def test_policy_shape_change_without_migration_fails_closed():
    inner, usage = MemoryStore(), MemoryStore()
    settings = Settings(secret="s" * 32)
    QuotaStore(inner, usage, settings, policies=(program_upload_policy(),))
    changed = replace(program_upload_policy(), item_prefixes=("other:",))
    with pytest.raises(RuntimeError, match="changed shape without a catalogue migration"):
        QuotaStore(inner, usage, settings, policies=(changed,))


def test_replacing_a_legacy_quota_policy_must_keep_its_collection_group_and_item_keys():
    inner, usage = MemoryStore(), MemoryStore()
    settings = Settings(secret="s" * 32)
    base = QuotaStore(inner, usage, settings)
    banking = next(policy for policy in base.policies if policy.name == "legacy-banking")
    partial = replace(banking, name="sap-banking-only", collections=frozenset({"sap_banking"}))

    with pytest.raises(ValueError, match="must cover its collections"):
        QuotaStore(inner, usage, settings, policies=(partial,))


def test_program_policy_handoff_preserves_legacy_quota_items_and_reconciliation():
    inner, usage = MemoryStore(), MemoryStore()
    settings = Settings(secret="s" * 32, club_storage_year_bytes=10)
    inner.put("clubs", "club", {"id": "club", "registered_years": [2025]})
    inner.put("receipts", "receipt", {
        "id": "receipt", "club_id": "club", "data_url": data_url(b"legacy")
    })
    compatibility = QuotaStore(inner, usage, settings)
    assert compatibility.usage("club:club")["used_bytes"] == 6
    legacy_receipts = next(
        policy for policy in compatibility.policies if policy.name == "legacy-club-receipts"
    )
    clubs_policy = replace(legacy_receipts, name="clubs-receipts")
    migrated = QuotaStore(inner, usage, settings, policies=(clubs_policy,))

    assert migrated.usage("club:club")["used_bytes"] == 6
    assert migrated.reconcile("club:club", "portal")["used_bytes"] == 6
    assert usage.get("upload_quotas", "club:club")["items"] == {
        "receipts:receipt": {"size": 6}
    }


def test_club_quota_counts_distinct_registered_years_not_elapsed_time_and_caps():
    store = quota()
    store.put("clubs", "club", {"id": "club", "approval_year": 2025, "registered_years": [2020, 2025, 2025]})
    assert store.limit("club:club") == 2 * GIB
    store.put("clubs", "club", {"id": "club", "registered_years": list(range(2010, 2026))})
    assert store.limit("club:club") == 10 * GIB


def receipt_storage(tmp_path, *, enabled=True, academic_year=None):
    if academic_year is None:
        from core.programs.clubs.module import academic_year

    settings = Settings(secret="s" * 32, receipt_storage_path=str(tmp_path), receipt_cold_remote="cold:receipts",
                        receipt_archive_enabled=enabled)
    store = quota(settings)
    store.put("clubs", "club", {"id": "club", "registered_years": [2025, 2026]})
    clock = [int(datetime(2026, 10, 1, tzinfo=ZoneInfo("America/Toronto")).timestamp())]
    audit = RecordingAudit()
    storage = ReceiptStorage(store, settings, lambda: clock[0], audit, academic_year=academic_year)
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


def test_receipt_storage_uses_supplied_year_policy(tmp_path):
    store, storage, clock, _audit = receipt_storage(tmp_path, academic_year=lambda timestamp: timestamp // 100)
    clock[0] = clock[0] // 100 * 100
    storage.create(receipt_record(clock[0]))
    assert store.get("receipts", "receipt")["academic_year"] == clock[0] // 100
    assert storage.archive() == []
    clock[0] += 100
    assert storage.archive()[0]["reason"] == "previous_academic_year"


def test_receipt_archive_inactivity_boundary_and_recent_access(tmp_path):
    store, storage, clock, _audit = receipt_storage(tmp_path)
    storage.create(receipt_record(clock[0]))
    assert storage.archive() == []
    clock[0] += YEAR - 1
    # Isolate the inactivity rule from the separate academic-year rule.
    from core.programs.clubs.module import academic_year
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
    from core import receipt_maintenance

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


def test_replicas_starting_together_do_not_fence_each_other():
    import threading

    usage = MemoryStore()
    original_get = usage.get
    barrier = threading.Barrier(2)

    def get_after_both_read(col, id):
        record = original_get(col, id)
        if col == upload_quota.CATALOGUE_COLLECTION and record is None:
            try:
                barrier.wait(2)
            except threading.BrokenBarrierError:
                pass
        return record

    usage.get = get_after_both_read
    stores: dict[str, QuotaStore] = {}

    def boot(name):
        stores[name] = QuotaStore(MemoryStore(), usage, Settings(secret="s" * 32))

    threads = [threading.Thread(target=boot, args=(name,)) for name in "ab"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    catalogue = original_get(upload_quota.CATALOGUE_COLLECTION, upload_quota.CATALOGUE_ID)
    assert {store._epoch for store in stores.values()} == {catalogue["epoch"]}
    for index, store in enumerate(stores.values()):
        store.put("media", f"scan{index}", {"id": f"scan{index}", "uuid": "user", "face": data_url(b"x")})


def test_restart_with_unchanged_policies_keeps_the_epoch():
    inner, usage = MemoryStore(), MemoryStore()
    settings = Settings(secret="s" * 32)
    first = QuotaStore(inner, usage, settings, policies=(program_upload_policy(),))
    second = QuotaStore(inner, usage, settings, policies=(program_upload_policy(),))
    assert first._epoch == second._epoch
    first.put("program_uploads", "x", {"id": "x", "uuid": "user", "size": 1})


PROCESS_SCRIPT = """
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from test_file_service import data_url, program_upload_policy
from core.services.upload_quota import QuotaStore
from core.settings import Settings
from core.store import MemoryStore, SQLiteStore

mode = sys.argv[4]
usage = SQLiteStore(sys.argv[3])
policies = (program_upload_policy(),) if mode.startswith("extra") else ()
try:
    store = QuotaStore(MemoryStore(), usage, Settings(secret="s" * 32), policies=policies)
except RuntimeError as exc:
    print("startup-failed:", exc, flush=True)
    sys.exit(0)
print("ready", flush=True)
if mode.endswith("wait"):
    sys.stdin.readline()
try:
    store.put("media", "scan", {"id": "scan", "uuid": "user", "face": data_url(b"x")})
    print("write-ok", flush=True)
except RuntimeError as exc:
    print("write-refused:", exc, flush=True)
"""


def test_quota_catalogue_fences_and_retains_across_real_processes(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    quota_db = str(tmp_path / "quota.db")

    def command(mode):
        return [sys.executable, "-c", PROCESS_SCRIPT, str(root / "src"),
                str(Path(__file__).resolve().parent), quota_db, mode]

    def run(mode):
        result = subprocess.run(command(mode), capture_output=True, text=True, timeout=60, check=True)
        return result.stdout.strip().splitlines()

    stale = subprocess.Popen(command("plain-wait"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert stale.stdout.readline().strip() == "ready"
        # A newer process declares an extra policy while the older process is still serving.
        assert run("extra") == ["ready", "write-ok"]
        # The older process holds the previous epoch and must refuse further writes.
        stale_output, stale_error = stale.communicate("go\n", timeout=60)
        assert "write-refused: quota policy catalogue changed" in stale_output, stale_error
    finally:
        stale.kill()

    # The provider is no longer installed but its retained records still need it: fail closed.
    assert run("plain")[0].startswith("startup-failed: retained upload quota policies are required")
    # Restoring the provider recovers; restarts with unchanged policies keep working.
    assert run("extra") == ["ready", "write-ok"]
