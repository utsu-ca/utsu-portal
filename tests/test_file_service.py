import pytest

from utsu_core.contracts import FileKind
from utsu_core.services.files import DocumentService


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
