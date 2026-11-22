"""Versioned public contracts for portal plug-ins."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from fastapi import APIRouter
from fastapi.params import Depends as AuthDependency

from .pagination import PageOrder

CORE_PLUGIN_API_MAJOR = 1
PLUGIN_MANIFEST_SCHEMA_VERSION = 1


def _new_registrations() -> dict[tuple[str, str], Any]:
    return {}


@dataclass(frozen=True)
class PluginManifest:
    id: str
    version: str
    core_api_major: int
    factory: str
    kind: str = "program"
    auth_modes: tuple[str, ...] = ("local", "remote")
    dependencies: tuple[str, ...] = ()
    order: int = 1000
    form_ids: tuple[str, ...] = ()
    interface: str = "plugin"
    roles: tuple[tuple[str, str], ...] = ()
    protection: tuple[tuple[str, tuple[str, ...]], ...] = ()
    json_protection: tuple[tuple[str, tuple[str, ...]], ...] = ()
    settings: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    unlinkable: tuple[str, ...] = ()
    default_enabled: bool = True
    permissions: tuple[tuple[str, str], ...] = ()
    role_permissions: tuple[tuple[str, tuple[str, ...]], ...] = ()


class AuthService(Protocol):
    def require_verified(self) -> AuthDependency: ...
    def require_rostered(self) -> AuthDependency: ...
    def require_permission(self, *permissions: str) -> AuthDependency: ...
    def has_permission(self, account: Mapping[str, Any], *permissions: str) -> bool: ...
    def has_any_permission(self, account: Mapping[str, Any], *permissions: str) -> bool: ...
    def require_role(self, *roles: str) -> AuthDependency: ...
    def has_role(self, account: Mapping[str, Any], *roles: str) -> bool: ...
    def is_rostered(self, account: Mapping[str, Any]) -> bool: ...
    def display_name(self, uuid: str) -> str | None: ...


class StoreService(Protocol):
    def get(self, collection: str, key: str) -> dict[str, Any] | None: ...
    def list(self, collection: str, **equals: Any) -> list[dict[str, Any]]: ...
    def page(self, collection: str, *, order_by: tuple[PageOrder, ...],
             after: tuple[Any, ...] | None, limit: int,
             equals: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def create(self, collection: str, key: str, value: dict[str, Any]) -> bool: ...
    def update(self, collection: str, key: str,
               fn: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None: ...
    def delete(self, collection: str, key: str) -> None: ...


class AuditService(Protocol):
    def record(self, actor: str | None, action: str, target: str, before: Any = None,
               after: Any = None, extra: dict[str, Any] | None = None) -> None: ...


class FileService(Protocol):
    def read(self, kind: str, reference: str, actor: str, resource: str) -> bytes: ...
    def delete(self, kind: str, reference: str, actor: str, resource: str) -> None: ...


@dataclass(frozen=True)
class FileKind:
    authorize: Callable[[str, str, str, str], bool]
    read: Callable[[str, str], bytes | None]
    delete: Callable[[str, str], None] | None = None
    audit_action: str = "file.read"
    audit_target: Callable[[str, str], str] | None = None
    audit_extra: Callable[[str, str, bytes | None], dict[str, Any]] | None = None


@dataclass(frozen=True)
class FormContribution:
    yaml: str


@dataclass(frozen=True)
class SubmissionPolicy:
    require_roster: bool = False
    save: Callable[[dict[str, Any] | None, dict[str, Any]], dict[str, Any]] | None = None
    validate_status_change: Callable[[dict[str, Any]], None] | None = None


@dataclass(frozen=True)
class NotificationType:
    form_id: str
    role: str
    email_template: str = "portal_status"


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    body: str


class FormsService(Protocol):
    def submission(self, form_id: str, uid: str) -> dict[str, Any] | None: ...
    def submissions(self, form_id: str, **filters: Any) -> list[dict[str, Any]]: ...
    def update_submission(self, form_id: str, uid: str,
                          update: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None: ...


class NotificationService(Protocol):
    def notify(self, kind: str, recipients: tuple[str, ...], params: Mapping[str, Any],
               dedupe_key: str) -> str: ...


class ThreadDenied(PermissionError):
    """The account may not perform this operation on the thread."""


class MessageRejected(ValueError):
    """The message is empty, too long or otherwise invalid."""


class MessageRateLimited(MessageRejected):
    """The account is posting too quickly."""


@dataclass(frozen=True)
class ThreadAccess:
    read: bool = True
    post: bool = False
    staff: bool = False  # internal notes and redaction


@dataclass(frozen=True)
class ThreadType:
    """A record-linked thread kind. `authorize(account, record_id)` returns None to deny."""

    authorize: Callable[[Mapping[str, Any], str], ThreadAccess | None]
    internal_notes: bool = False
    max_body: int = 4000


class MessagingService(Protocol):
    def post(self, thread_type: str, record_id: str, account: Mapping[str, Any], body: str,
             internal: bool = False, name_choice: str = "real") -> dict[str, Any]: ...
    def messages(self, thread_type: str, record_id: str, account: Mapping[str, Any], after: int = 0,
                 limit: int = 100) -> list[dict[str, Any]]: ...
    def redact(self, thread_type: str, record_id: str, seq: int, account: Mapping[str, Any],
               reason: str) -> dict[str, Any]: ...
    def mark_read(self, thread_type: str, record_id: str, account: Mapping[str, Any], seq: int) -> int: ...
    def thread_info(self, thread_type: str, record_id: str) -> dict[str, Any] | None: ...
    def set_thread_metadata(self, thread_type: str, record_id: str, metadata: Mapping[str, Any]) -> None: ...
    def page_threads(self, thread_type: str, *, order_by: tuple[PageOrder, ...],
                     after: tuple[Any, ...] | None, limit: int,
                     equals: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...


class ProvidedService(Protocol):
    """A plug-in service other plug-ins reach through `provides`/`dependencies`, never by import."""

    def for_consumer(self, consumer: str) -> Any: ...


class EmailService(Protocol):
    @property
    def is_configured(self) -> bool: ...
    def render_template(self, template_id: str, params: Mapping[str, Any]) -> EmailMessage: ...
    def send_template(self, to: str, template_id: str, params: Mapping[str, Any],
                      dedupe_key: str | None = None, actor: str | None = None) -> str: ...


class IntegrityService(Protocol):
    def issue(self, purpose: str, subject: str, ttl: int) -> str: ...
    def digest(self, purpose: str, value: bytes) -> str: ...


class AnchorService(Protocol):
    """Publishes a 32-byte digest to the configured external ledger (PORTAL_ANCHOR). Never blocks on the chain."""

    @property
    def enabled(self) -> bool: ...
    def submit(self, purpose: str, digest: str, meta: Mapping[str, Any] | None = None) -> str: ...
    def status(self, anchor_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class PluginServices:
    auth: AuthService
    store: StoreService
    audit: AuditService
    files: FileService
    forms: FormsService
    notifications: NotificationService
    messaging: MessagingService
    anchor: AnchorService
    email: EmailService
    integrity: IntegrityService
    events: Any
    now: Callable[[], int]
    settings: Mapping[str, Any]
    provided: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass
class RegistrationContext:
    """Mutable during plug-in configuration and immutable after freeze()."""

    owner: str
    _values: dict[tuple[str, str], Any] = field(default_factory=_new_registrations)
    _frozen: bool = False
    _resolver: Callable[[str], Any] | None = None

    def register(self, kind: str, identifier: str, value: Any) -> None:
        if self._frozen:
            raise RuntimeError("plug-in registrations are frozen")
        if not kind or not identifier:
            raise ValueError("registration kind and identifier are required")
        key = (kind, identifier)
        if key in self._values:
            raise ValueError(f"duplicate {kind} registration: {identifier}")
        self._values[key] = value

    def register_role(self, identifier: str, value: Any) -> None:
        self.register("role", identifier, value)

    def register_form(self, identifier: str, value: FormContribution) -> None:
        self.register("form", identifier, value)

    def register_submission_policy(self, identifier: str, value: SubmissionPolicy) -> None:
        self.register("submission_policy", identifier, value)

    def register_file_kind(self, identifier: str, value: FileKind) -> None:
        self.register("file", identifier, value)

    def register_notification_type(self, identifier: str, value: NotificationType) -> None:
        self.register("notification", identifier, value)

    def register_thread_type(self, identifier: str, value: ThreadType) -> None:
        self.register("thread", identifier, value)

    def provide(self, name: str, factory: Callable[[PluginServices], ProvidedService]) -> None:
        """Offer a service named in the manifest's `provides`; the host builds it with this plug-in's services."""
        self.register("provide", name, factory)

    def require(self, name: str) -> Any:
        """A dependency's provided service, scoped to this plug-in, for registering contributions."""
        if self._frozen:
            raise RuntimeError("plug-in registrations are frozen")
        if self._resolver is None:
            raise LookupError(f"provided service {name} is unavailable")
        return self._resolver(name)

    def freeze(self) -> Mapping[tuple[str, str], Any]:
        self._frozen = True
        frozen_values: dict[tuple[str, str], Any] = {}
        frozen_values.update(self._values)
        return MappingProxyType(frozen_values)


class Plugin(Protocol):
    manifest: PluginManifest

    def configure(self, registration: RegistrationContext) -> None: ...
    def router(self, services: PluginServices) -> APIRouter: ...


class PluginLifecycle(Protocol):
    def startup(self, services: PluginServices) -> Any: ...
    def shutdown(self, services: PluginServices) -> Any: ...
