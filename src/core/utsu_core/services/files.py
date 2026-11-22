"""Authorized adapters for legacy encrypted document records."""
from __future__ import annotations

from dataclasses import dataclass

from ..contracts import AuditService, FileAuthorization, FileKind


@dataclass(frozen=True)
class _RegisteredKind:
    owner: str
    adapter: FileKind


class DocumentService:
    def __init__(self, audit: AuditService) -> None:
        self._audit = audit
        self._kinds: dict[str, _RegisteredKind] = {}
        self._authorizers: dict[str, dict[str, FileAuthorization]] = {}
        self._frozen = False

    def register_kind(self, owner: str, identifier: str, adapter: FileKind) -> None:
        if self._frozen:
            raise RuntimeError("file-kind registrations are frozen")
        if not owner or not identifier:
            raise ValueError("file-kind owner and identifier are required")
        if identifier in self._kinds:
            raise ValueError(f"duplicate file-kind registration: {identifier}")
        if type(adapter.authorizer_actions) is not frozenset or type(adapter.dependent_actions) is not frozenset:
            raise TypeError("file-kind delegation actions must be frozen sets")
        if not (adapter.authorizer_actions | adapter.dependent_actions) <= {"read", "create", "delete"}:
            raise ValueError("file-kind delegation contains an unknown action")
        self._kinds[identifier] = _RegisteredKind(owner, adapter)

    def register_authorizer(self, owner: str, identifier: str, dependencies: tuple[str, ...],
                            authorization: FileAuthorization) -> None:
        if self._frozen:
            raise RuntimeError("file-kind registrations are frozen")
        if not owner:
            raise ValueError("file authorizer owner is required")
        if type(authorization.actions) is not frozenset:
            raise TypeError("file authorizer actions must be a frozen set")
        kind = self._kinds.get(identifier)
        if (kind is None or kind.owner not in dependencies or not authorization.actions
                or not authorization.actions <= kind.adapter.authorizer_actions):
            raise PermissionError(f"file kind {identifier} does not accept this authorizer")
        authorizers = self._authorizers.setdefault(identifier, {})
        if owner in authorizers:
            raise ValueError(f"duplicate file authorizer for {owner}: {identifier}")
        authorizers[owner] = authorization

    def freeze(self) -> None:
        self._frozen = True

    def for_owner(self, owner: str, dependencies: tuple[str, ...] = ()) -> FileServiceView:
        return FileServiceView(self, owner, dependencies)

    def read_for_owner(self, owner: str, kind: str, reference: str, actor: str, resource: str,
                       dependencies: tuple[str, ...] = ()) -> bytes:
        return self._read(owner, kind, reference, actor, resource, dependencies)

    def delete_for_owner(self, owner: str, kind: str, reference: str, actor: str, resource: str,
                         dependencies: tuple[str, ...] = ()) -> None:
        self._delete(owner, kind, reference, actor, resource, dependencies)

    def create_for_owner(self, owner: str, kind: str, reference: str, actor: str, resource: str,
                         content: bytes, dependencies: tuple[str, ...] = ()) -> None:
        self._create(owner, kind, reference, actor, resource, content, dependencies)

    def read(self, kind: str, reference: str, actor: str, resource: str) -> bytes:
        return self._read(None, kind, reference, actor, resource)

    def create(self, kind: str, reference: str, actor: str, resource: str, content: bytes) -> None:
        self._create(None, kind, reference, actor, resource, content)

    def _create(self, owner: str | None, kind: str, reference: str, actor: str, resource: str,
                content: bytes, dependencies: tuple[str, ...] = ()) -> None:
        registered = self._registered(owner, kind, "create", dependencies)
        adapter = registered.adapter
        if adapter.create is None or not self._authorized(kind, adapter, "create", actor, resource, reference):
            raise PermissionError("file creation is not authorized")
        if type(content) is not bytes:
            raise TypeError("file content must be bytes")
        target = self._target(kind, reference, resource, adapter)
        extra = adapter.audit_extra(resource, reference, content) if adapter.audit_extra else {
            "kind": kind, "reference": reference
        }
        self._audit.record(actor, adapter.create_audit_action, target, None, None, extra)
        adapter.create(reference, resource, actor, content)

    def _read(self, owner: str | None, kind: str, reference: str, actor: str, resource: str,
              dependencies: tuple[str, ...] = ()) -> bytes:
        registered = self._registered(owner, kind, "read", dependencies)
        adapter = registered.adapter
        if not self._authorized(kind, adapter, "read", actor, resource, reference):
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

    def _delete(self, owner: str | None, kind: str, reference: str, actor: str, resource: str,
                dependencies: tuple[str, ...] = ()) -> None:
        registered = self._registered(owner, kind, "delete", dependencies)
        adapter = registered.adapter
        if adapter.delete is None or not self._authorized(kind, adapter, "delete", actor, resource, reference):
            raise PermissionError("file deletion is not authorized")
        adapter.delete(reference, resource)
        target = self._target(kind, reference, resource, adapter)
        extra = adapter.audit_extra(resource, reference, None) if adapter.audit_extra else {"kind": kind, "reference": reference}
        self._audit.record(actor, "file.deleted", target, None, None, extra)

    def _registered(self, owner: str | None, kind: str, action: str,
                    dependencies: tuple[str, ...]) -> _RegisteredKind:
        registered = self._kinds.get(kind)
        if registered is None or (
            owner is not None and registered.owner != owner
            and not (action in registered.adapter.dependent_actions and registered.owner in dependencies)
        ):
            raise PermissionError(f"file kind {kind} is not available")
        return registered

    def _authorized(self, kind: str, adapter: FileKind, action: str, actor: str, resource: str,
                    reference: str) -> bool:
        return adapter.authorize(action, actor, resource, reference) or any(
            action in grant.actions and grant.authorize(action, actor, resource, reference)
            for grant in self._authorizers.get(kind, {}).values()
        )

    @staticmethod
    def _target(kind: str, reference: str, resource: str, adapter: FileKind) -> str:
        return adapter.audit_target(resource, reference) if adapter.audit_target else f"file:{kind}:{reference}"


class FileServiceView:
    def __init__(self, service: DocumentService, owner: str, dependencies: tuple[str, ...] = ()) -> None:
        self._service = service
        self._owner = owner
        self._dependencies = dependencies

    def read(self, kind: str, reference: str, actor: str, resource: str) -> bytes:
        return self._service.read_for_owner(self._owner, kind, reference, actor, resource, self._dependencies)

    def create(self, kind: str, reference: str, actor: str, resource: str, content: bytes) -> None:
        self._service.create_for_owner(self._owner, kind, reference, actor, resource, content, self._dependencies)

    def delete(self, kind: str, reference: str, actor: str, resource: str) -> None:
        self._service.delete_for_owner(self._owner, kind, reference, actor, resource, self._dependencies)
