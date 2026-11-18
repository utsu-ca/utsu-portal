"""Authorized adapters for legacy encrypted document records."""
from __future__ import annotations

from dataclasses import dataclass

from ..contracts import AuditService, FileKind


@dataclass(frozen=True)
class _RegisteredKind:
    owner: str
    adapter: FileKind


class DocumentService:
    def __init__(self, audit: AuditService) -> None:
        self._audit = audit
        self._kinds: dict[str, _RegisteredKind] = {}
        self._frozen = False

    def register_kind(self, owner: str, identifier: str, adapter: FileKind) -> None:
        if self._frozen:
            raise RuntimeError("file-kind registrations are frozen")
        if not owner or not identifier:
            raise ValueError("file-kind owner and identifier are required")
        if identifier in self._kinds:
            raise ValueError(f"duplicate file-kind registration: {identifier}")
        self._kinds[identifier] = _RegisteredKind(owner, adapter)

    def freeze(self) -> None:
        self._frozen = True

    def for_owner(self, owner: str) -> FileServiceView:
        return FileServiceView(self, owner)

    def read_for_owner(self, owner: str, kind: str, reference: str, actor: str, resource: str) -> bytes:
        return self._read(owner, kind, reference, actor, resource)

    def delete_for_owner(self, owner: str, kind: str, reference: str, actor: str, resource: str) -> None:
        self._delete(owner, kind, reference, actor, resource)

    def read(self, kind: str, reference: str, actor: str, resource: str) -> bytes:
        return self._read(None, kind, reference, actor, resource)

    def _read(self, owner: str | None, kind: str, reference: str, actor: str, resource: str) -> bytes:
        registered = self._registered(owner, kind)
        adapter = registered.adapter
        if not adapter.authorize("read", actor, resource, reference):
            raise PermissionError("file read is not authorized")
        content = adapter.read(reference, resource)
        if content is None:
            raise FileNotFoundError(reference)
        if type(content) is not bytes:
            raise TypeError("file adapter must return bytes")
        target = self._target(kind, reference, resource, adapter)
        extra = adapter.audit_extra(resource, reference, content) if adapter.audit_extra else {"kind": kind, "reference": reference}
        self._audit.record(actor, adapter.audit_action, target, None, None, extra)
        return content

    def delete(self, kind: str, reference: str, actor: str, resource: str) -> None:
        self._delete(None, kind, reference, actor, resource)

    def _delete(self, owner: str | None, kind: str, reference: str, actor: str, resource: str) -> None:
        registered = self._registered(owner, kind)
        adapter = registered.adapter
        if adapter.delete is None or not adapter.authorize("delete", actor, resource, reference):
            raise PermissionError("file deletion is not authorized")
        adapter.delete(reference, resource)
        target = self._target(kind, reference, resource, adapter)
        extra = adapter.audit_extra(resource, reference, None) if adapter.audit_extra else {"kind": kind, "reference": reference}
        self._audit.record(actor, "file.deleted", target, None, None, extra)

    def _registered(self, owner: str | None, kind: str) -> _RegisteredKind:
        registered = self._kinds.get(kind)
        if registered is None or (owner is not None and registered.owner != owner):
            raise PermissionError(f"file kind {kind} is not available")
        return registered

    @staticmethod
    def _target(kind: str, reference: str, resource: str, adapter: FileKind) -> str:
        return adapter.audit_target(resource, reference) if adapter.audit_target else f"file:{kind}:{reference}"


class FileServiceView:
    def __init__(self, service: DocumentService, owner: str) -> None:
        self._service = service
        self._owner = owner

    def read(self, kind: str, reference: str, actor: str, resource: str) -> bytes:
        return self._service.read_for_owner(self._owner, kind, reference, actor, resource)

    def delete(self, kind: str, reference: str, actor: str, resource: str) -> None:
        self._service.delete_for_owner(self._owner, kind, reference, actor, resource)
