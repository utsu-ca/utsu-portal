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
    config: tuple[str, ...] = ()
    audit_collections: tuple[str, ...] = ()
    audit_forms: tuple[str, ...] = ()
    quota_domains: tuple[str, ...] = ()
    frontend: bool = False
    derived_roles: tuple[tuple[str, str], ...] = ()  # roles this plug-in computes (never stored on an account)
    scoped_roles: tuple[tuple[str, str], ...] = ()  # derived roles held within one scope (e.g. one club), never globally


class AuthService(Protocol):
    """Authorization dependencies return account identity and role fields, not private account data."""

    def require_user(self) -> AuthDependency: ...
    def require_verified(self) -> AuthDependency: ...
    def require_rostered(self) -> AuthDependency: ...
    def require_permission(self, *permissions: str) -> AuthDependency: ...
    def has_permission(self, account: Mapping[str, Any], *permissions: str, scope: str | None = None) -> bool: ...
    def has_any_permission(self, account: Mapping[str, Any], *permissions: str, scope: str | None = None) -> bool: ...
    def require_role(self, *roles: str) -> AuthDependency: ...
    def has_role(self, account: Mapping[str, Any], *roles: str) -> bool: ...
    def is_rostered(self, account: Mapping[str, Any]) -> bool: ...
    def display_name(self, uuid: str) -> str | None: ...


@dataclass(frozen=True)
class PersonSummary:
    status: str | None
    first_name: str | None
    last_name: str | None


@dataclass(frozen=True)
class RosterSummary:
    rostered: bool
    division: str | None
    reason: str | None


@dataclass(frozen=True)
class ContactSummary:
    uuid: str
    status: str | None
    email_verified: bool
    email: str | None


class IdentityService(Protocol):
    """Minimal identity, roster and contact projections; callers must authorize their own use."""

    def account(self, uuid: str) -> Mapping[str, Any] | None: ...
    def person(self, uuid: str) -> PersonSummary | None: ...
    def roster(self, uuid: str) -> RosterSummary | None: ...
    def same_roster_person(self, first_uuid: str, second_uuid: str) -> bool: ...
    def preferred_name(self, uuid: str) -> str | None: ...
    def resolve_contact(self, username: str) -> ContactSummary | None: ...
    def find_contact(self, email: str = "", student_number: str = "") -> ContactSummary | None: ...


@dataclass(frozen=True)
class AgmReviewerPerson:
    first_name: str | None
    last_name: str | None
    student_number: str | None


class AgmReviewService(Protocol):
    def roster_match(self, actor_uuid: str, person: Mapping[str, str]) -> Mapping[str, Any] | None: ...
    def reviewer_person(self, actor_uuid: str, subject_uuid: str) -> AgmReviewerPerson | None: ...


class _UnavailableIdentity:
    def account(self, uuid: str) -> Mapping[str, Any] | None:
        raise RuntimeError("core identity service is unavailable")

    def person(self, uuid: str) -> PersonSummary | None:
        raise RuntimeError("core identity service is unavailable")

    def roster(self, uuid: str) -> RosterSummary | None:
        raise RuntimeError("core identity service is unavailable")

    def same_roster_person(self, first_uuid: str, second_uuid: str) -> bool:
        raise RuntimeError("core identity service is unavailable")

    def preferred_name(self, uuid: str) -> str | None:
        raise RuntimeError("core identity service is unavailable")

    def resolve_contact(self, username: str) -> ContactSummary | None:
        raise RuntimeError("core identity service is unavailable")

    def find_contact(self, email: str = "", student_number: str = "") -> ContactSummary | None:
        raise RuntimeError("core identity service is unavailable")


class StoreService(Protocol):
    def get(self, collection: str, key: str) -> dict[str, Any] | None: ...
    def list(self, collection: str, **equals: Any) -> list[dict[str, Any]]: ...
    def page(self, collection: str, *, order_by: tuple[PageOrder, ...],
             after: tuple[Any, ...] | None, limit: int,
             equals: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def put(self, collection: str, key: str, value: dict[str, Any]) -> None: ...
    def create(self, collection: str, key: str, value: dict[str, Any]) -> bool: ...
    def replace(self, collection: str, key: str, value: dict[str, Any], expected_rev: int) -> bool: ...
    def update(self, collection: str, key: str,
               fn: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None: ...
    def delete(self, collection: str, key: str) -> None: ...


class AuditService(Protocol):
    def record(self, actor: str | None, action: str, target: str, before: Any = None,
               after: Any = None, extra: dict[str, Any] | None = None) -> None: ...


class FileService(Protocol):
    def read(self, kind: str, reference: str, actor: str, resource: str) -> bytes: ...
    def create(self, kind: str, reference: str, actor: str, resource: str, content: bytes) -> None: ...
    def delete(self, kind: str, reference: str, actor: str, resource: str) -> None: ...


class ClubReceiptsService(Protocol):
    """Receipt creation and read access without exposing storage configuration or keys."""

    def create(self, receipt: dict[str, Any]) -> None: ...
    def read(self, reference: str, club_id: str) -> bytes | None: ...


class ClubInvitationLinkService(Protocol):
    def portal_link(self) -> str: ...


class QuotaService(Protocol):
    """Usage for declared owner domains; callers must authorize the requested resource."""

    def usage(self, domain: str, resource: str) -> dict[str, int]: ...


class _UnavailableQuota:
    def usage(self, domain: str, resource: str) -> dict[str, int]:
        raise RuntimeError("core quota service is unavailable")


class _UnavailableClubReceipts:
    def create(self, receipt: dict[str, Any]) -> None:
        raise RuntimeError("core club receipt service is unavailable")

    def read(self, reference: str, club_id: str) -> bytes | None:
        raise RuntimeError("core club receipt service is unavailable")


class _UnavailableClubInvitationLink:
    def portal_link(self) -> str:
        raise RuntimeError("core club invitation link service is unavailable")


class _UnavailableAgmSSO:
    def register_zoom_client(self, eligibility: Callable[[str], str | None]) -> bool:
        raise RuntimeError("core AGM SSO registration service is unavailable")


class _UnavailableAgmReview:
    def roster_match(self, actor_uuid: str, person: Mapping[str, str]) -> Mapping[str, Any] | None:
        raise RuntimeError("core AGM review service is unavailable")

    def reviewer_person(self, actor_uuid: str, subject_uuid: str) -> AgmReviewerPerson | None:
        raise RuntimeError("core AGM review service is unavailable")


class _UnavailableAgmImageLimits:
    @property
    def max_scan_image_bytes(self) -> int:
        raise RuntimeError("core AGM image limits service is unavailable")


@dataclass(frozen=True)
class FileKind:
    """A program-owned adapter; create receives reference, resource, actor and content bytes."""

    authorize: Callable[[str, str, str, str], bool]
    read: Callable[[str, str], bytes | None]
    delete: Callable[[str, str], None] | None = None
    audit_action: str = "file.read"
    audit_target: Callable[[str, str], str] | None = None
    audit_extra: Callable[[str, str, bytes | None], dict[str, Any]] | None = None
    create: Callable[[str, str, str, bytes], None] | None = None
    create_audit_action: str = "file.create"
    authorizer_actions: frozenset[str] = frozenset()
    dependent_actions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FileAuthorization:
    authorize: Callable[[str, str, str, str], bool]
    actions: frozenset[str] = frozenset({"read"})


@dataclass(frozen=True)
class FormContribution:
    yaml: str


@dataclass(frozen=True)
class SubmissionPolicy:
    """
    `permission` is what an account must hold to submit (a membership permission is granted through the derived
    `live_member` / `rostered_member` roles). `keep_membership` stores the membership held at submission on it, so the
    application stays eligible if the applicant is later off the roster.
    """
    permission: str | None = None
    keep_membership: bool = False
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
    def page_submissions(self, form_id: str, *, order_by: tuple[PageOrder, ...],
                         after: tuple[Any, ...] | None, limit: int,
                         filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def update_submission(self, form_id: str, uid: str,
                          update: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class BankingRoutePolicy:
    mine: str = "/profile/financial"
    staff: str = "/finance/profiles/{uid}"
    finance_path: str = "/finance/profiles"
    admin_permission: str = "utsu:profile:financial:manage"
    finance_permission: str = "utsu:profile:financial:manage"
    audit_owner: str = "profile"
    email_template: str = "financial_banking_reverify"
    finance_detail_routes: bool = False
    validate_submission: Callable[[str], None] | None = None
    listing_fields: Callable[[str], dict[str, Any]] | None = None


class SapBankingRoutesService(Protocol):
    """Build SAP banking routes while core retains all financial and identity data access."""

    def router(self, policy: BankingRoutePolicy) -> APIRouter: ...


class MigrationsService(Protocol):
    """Read or mark an owner-namespaced migration without exposing the migration store."""

    def applied(self, identifier: str) -> bool: ...
    def mark_applied(self, identifier: str) -> bool: ...


class _UnavailableSapBankingRoutes:
    def router(self, policy: BankingRoutePolicy) -> APIRouter:
        raise RuntimeError("core SAP banking routes service is unavailable")


class _UnavailableMigrations:
    def applied(self, identifier: str) -> bool:
        raise RuntimeError("core migration service is unavailable")

    def mark_applied(self, identifier: str) -> bool:
        raise RuntimeError("core migration service is unavailable")


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
    def verify(self, purpose: str, token: str) -> str | None: ...


class AgmSSORegistrationService(Protocol):
    def register_zoom_client(self, eligibility: Callable[[str], str | None]) -> bool: ...


class AgmImageLimitsService(Protocol):
    @property
    def max_scan_image_bytes(self) -> int: ...


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
    identity: IdentityService = field(default_factory=_UnavailableIdentity)
    quotas: QuotaService = field(default_factory=_UnavailableQuota)
    agm_sso: AgmSSORegistrationService = field(default_factory=_UnavailableAgmSSO)
    agm_review: AgmReviewService = field(default_factory=_UnavailableAgmReview)
    agm_image_limits: AgmImageLimitsService = field(default_factory=_UnavailableAgmImageLimits)
    sap_banking: SapBankingRoutesService = field(default_factory=_UnavailableSapBankingRoutes)
    migrations: MigrationsService = field(default_factory=_UnavailableMigrations)
    club_receipts: ClubReceiptsService = field(default_factory=_UnavailableClubReceipts)
    club_invitation_link: ClubInvitationLinkService = field(default_factory=_UnavailableClubInvitationLink)


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

    def register_scoped_role(self, identifier: str, value: Callable[[PluginServices], Callable[[Mapping[str, Any], str], bool]]) -> None:
        """A scoped role is held in one scope (a club ID) while `value(services)(account, scope)` is true."""
        self.register("scoped_role", identifier, value)

    def register_derived_role(self, identifier: str, value: Callable[[PluginServices], Callable[[Mapping[str, Any]], bool]]) -> None:
        """A derived role is held while `value(services)(account)` is true; it is declared in the manifest."""
        self.register("derived_role", identifier, value)

    def register_form(self, identifier: str, value: FormContribution) -> None:
        self.register("form", identifier, value)

    def register_submission_policy(self, identifier: str, value: SubmissionPolicy) -> None:
        self.register("submission_policy", identifier, value)

    def register_file_kind(self, identifier: str, value: FileKind | Callable[[PluginServices], FileKind]) -> None:
        self.register("file", identifier, value)

    def register_file_authorizer(
        self, identifier: str, value: FileAuthorization | Callable[[PluginServices], FileAuthorization],
    ) -> None:
        self.register("file_authorizer", identifier, value)

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
