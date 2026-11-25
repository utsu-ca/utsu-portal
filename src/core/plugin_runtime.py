"""Adapters from the legacy application context to the public plug-in contracts."""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import re
from collections.abc import Iterator, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Callable, cast

from fastapi import APIRouter, Depends
from fastapi.params import Depends as AuthDependency

from . import security
from .context import Context, roles_of
from .contracts import (
    AgmImageLimitsService,
    AgmReviewService,
    AgmReviewerPerson,
    AgmSSORegistrationService,
    AnchorService,
    AuditService,
    AuthService,
    BankingRoutePolicy,
    ClubInvitationLinkService,
    ClubReceiptsService,
    ContactSummary,
    EmailService,
    FileAuthorization,
    FileService,
    FormContribution,
    FormsService,
    IdentityService,
    IntegrityService,
    MessagingService,
    MigrationsService,
    NotificationService,
    NotificationType,
    Plugin,
    PluginManifest,
    PluginServices,
    PersonSummary,
    QuotaService,
    RegistrationContext,
    RosterSummary,
    SapBankingRoutesService,
    SubmissionPolicy,
    StoreService,
    ThreadAccess,
    ThreadType,
)
from .pagination import PageOrder
from .permissions import PERM_AGM_ID_REVIEW, PERM_AGM_REGISTRATIONS
from .settings import plugin_settings_for
from .store import Doc, Store, unlinked_access

SUPPORTED_PLUGIN_SERVICES = frozenset({
    "auth", "identity", "audit", "store", "files", "forms", "email", "integrity", "events", "now", "settings",
    "messaging", "anchor", "provided", "quotas", "agm_sso", "agm_review", "agm_image_limits",
    "file_authorizers", "migrations", "sap_banking", "club_receipts", "club_invitation_link",
})
KNOWN_PLUGIN_SERVICES = SUPPORTED_PLUGIN_SERVICES | {"notifications"}


class CapabilityUnavailable(RuntimeError):
    """A declared plug-in capability is not implemented in this host stage."""


class _UnavailableService:
    def __init__(self, capability: str) -> None:
        self._capability = capability

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def unavailable(*_args: Any, **_kwargs: Any) -> Any:
            raise CapabilityUnavailable(
                f"core service {self._capability}.{name} is unavailable in this host"
            )
        return unavailable


class _UnavailableCallable:
    def __init__(self, capability: str) -> None:
        self._capability = capability

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise CapabilityUnavailable(f"core service {self._capability} is unavailable in this host")


class _UnavailableSettings(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        raise CapabilityUnavailable("core service settings is unavailable to this plug-in")

    def __iter__(self) -> Iterator[str]:
        raise CapabilityUnavailable("core service settings is unavailable to this plug-in")

    def __len__(self) -> int:
        raise CapabilityUnavailable("core service settings is unavailable to this plug-in")


def _plugin_account(account: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: account[key] for key in ("uuid", "status", "role", "roles", "email_verified",
                                             "backup_session") if key in account}
    if isinstance(result.get("roles"), list):
        result["roles"] = list(result["roles"])
    return result


class _AuthAdapter:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    @staticmethod
    def _project(dependency: AuthDependency) -> AuthDependency:
        def project(account: Any = dependency) -> dict[str, Any]:
            return _plugin_account(account)
        return Depends(project)

    def require_user(self) -> AuthDependency:
        return self._project(self._ctx.require_user())

    def require_verified(self) -> AuthDependency:
        return self._project(self._ctx.require_verified())

    def require_rostered(self) -> AuthDependency:
        return self._project(self._ctx.require_rostered())

    def require_role(self, *roles: str) -> AuthDependency:
        return self._project(self._ctx.require_role(*roles))

    def require_permission(self, *permissions: str) -> AuthDependency:
        return self._project(self._ctx.require_permission(*permissions))

    def has_permission(self, account: Mapping[str, Any], *permissions: str, scope: str | None = None) -> bool:
        return self._ctx.has_permission(account, *permissions, scope=scope)

    def has_any_permission(self, account: Mapping[str, Any], *permissions: str, scope: str | None = None) -> bool:
        return self._ctx.has_any_permission(account, *permissions, scope=scope)

    def has_role(self, account: Mapping[str, Any], *roles: str) -> bool:
        return account.get("status") == "verified" and bool(roles_of(account, self._ctx.roles) & {"admin", *roles})

    def is_rostered(self, account: Mapping[str, Any]) -> bool:
        if account.get("status") != "verified":
            return False
        check = self._ctx.identity.roster_check(account["uuid"])
        return bool(check and check.get("rostered"))

    def display_name(self, uuid: str) -> str | None:
        return _display_name(self._ctx, uuid)


def _display_name(ctx: Context, uuid: str) -> str | None:
    user = ctx.identity.user(uuid) or {}
    return user.get("preferred_name") or user.get("first_name") or None


class _IdentityAdapter:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def account(self, uuid: str) -> Mapping[str, Any] | None:
        account = self._ctx.identity.account(uuid)
        return _plugin_account(account) if account is not None else None

    def person(self, uuid: str) -> PersonSummary | None:
        user, account = self._ctx.identity.user(uuid), self._ctx.identity.account(uuid)
        if user is None or account is None:
            return None
        return PersonSummary(account.get("status"), user.get("first_name", ""), user.get("last_name", ""))

    def roster(self, uuid: str) -> RosterSummary | None:
        check = self._ctx.identity.roster_check(uuid)
        if check is None:
            return None
        return RosterSummary(bool(check.get("rostered")), check.get("division"),
                             check.get("reason", "roster check unavailable"))

    def same_roster_person(self, first_uuid: str, second_uuid: str) -> bool:
        first = self._ctx.identity.roster_check(first_uuid)
        second = self._ctx.identity.roster_check(second_uuid)
        first_roster_uuid = first.get("roster_uuid") if first else None
        return bool(first_roster_uuid and first_roster_uuid == (second or {}).get("roster_uuid"))

    def preferred_name(self, uuid: str) -> str | None:
        user = self._ctx.identity.user(uuid)
        name = user.get("preferred_name") if user else None
        return name if isinstance(name, str) else None

    def resolve_contact(self, username: str) -> ContactSummary | None:
        return self._ctx.identity.resolve_contact(username)

    def find_contact(self, email: str = "", student_number: str = "") -> ContactSummary | None:
        return self._ctx.identity.find_contact(email, student_number)


class _AgmSSORegistrationAdapter:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def register_zoom_client(self, eligibility: Callable[[str], str | None]) -> bool:
        secret = self._ctx.settings.zoom_client_secret
        if not secret:
            return False
        provider = self._ctx.services.get("sso")
        register_client = getattr(provider, "register_client", None)
        if not callable(register_client):
            raise CapabilityUnavailable("core SSO provider is unavailable")

        def check(account: Mapping[str, Any], _user: Mapping[str, Any]) -> str | None:
            uuid = account.get("uuid")
            return eligibility(uuid) if isinstance(uuid, str) else "account identity unavailable"

        register_client(
            self._ctx.settings.zoom_client_id,
            secret,
            self._ctx.settings.zoom_redirect_uris,
            check,
        )
        return True


class _AgmReviewAdapter:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def _require_permission(self, actor_uuid: str, permission: str) -> None:
        account = self._ctx.identity.account(actor_uuid)
        if account is None or not self._ctx.has_permission(account, permission):
            from fastapi import HTTPException

            raise HTTPException(403, "insufficient permission")

    def roster_match(self, actor_uuid: str, person: Mapping[str, str]) -> Mapping[str, Any] | None:
        self._require_permission(actor_uuid, PERM_AGM_REGISTRATIONS)
        return self._ctx.identity.roster_match(dict(person))

    def reviewer_person(self, actor_uuid: str, subject_uuid: str) -> AgmReviewerPerson | None:
        self._require_permission(actor_uuid, PERM_AGM_ID_REVIEW)
        user = self._ctx.identity.user(subject_uuid)
        if user is None:
            return None
        return AgmReviewerPerson(
            user.get("first_name"),
            user.get("last_name"),
            user.get("student_number"),
        )


class _AgmImageLimitsAdapter:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    @property
    def max_scan_image_bytes(self) -> int:
        return self._ctx.settings.max_image_bytes


class _StoreAdapter:
    def __init__(self, store: Store, collections: set[str], unlinkable: set[str] | None = None) -> None:
        self._store = store
        self._collections = collections
        self._unlinkable = unlinkable or set()

    def _check(self, collection: str) -> contextlib.AbstractContextManager[None]:
        if collection not in self._collections:
            raise PermissionError(f"collection {collection} is not available to this plug-in")
        return unlinked_access() if collection in self._unlinkable else contextlib.nullcontext()

    def get(self, collection: str, key: str) -> Doc | None:
        with self._check(collection):
            return self._store.get(collection, key)

    def list(self, collection: str, **equals: Any) -> list[Doc]:
        with self._check(collection):
            return self._store.list(collection, **equals)

    def page(
        self,
        collection: str,
        *,
        order_by: tuple[PageOrder, ...],
        after: tuple[Any, ...] | None,
        limit: int,
        equals: Mapping[str, Any] | None = None,
    ) -> list[Doc]:
        with self._check(collection):
            return self._store.page(
                collection, order_by=order_by, after=after, limit=limit, equals=equals
            )

    def create(self, collection: str, key: str, value: Doc) -> bool:
        with self._check(collection):
            return self._store.create(collection, key, value)

    def put(self, collection: str, key: str, value: Doc) -> None:
        with self._check(collection):
            self._store.put(collection, key, value)

    def replace(self, collection: str, key: str, value: Doc, expected_rev: int) -> bool:
        with self._check(collection):
            return self._store.replace(collection, key, value, expected_rev)

    def update(self, collection: str, key: str, fn: Callable[[Doc | None], Doc | None]) -> Doc | None:
        with self._check(collection):
            return self._store.update(collection, key, fn)

    def delete(self, collection: str, key: str) -> None:
        with self._check(collection):
            self._store.delete(collection, key)


class _QuotaAdapter:
    def __init__(self, ctx: Context, domains: tuple[str, ...]) -> None:
        self._ctx = ctx
        self._domains = frozenset(domains)

    def usage(self, domain: str, resource: str) -> dict[str, int]:
        if domain not in self._domains:
            raise PermissionError(f"quota domain {domain} is not available to this plug-in")
        if not resource or ":" in resource:
            raise ValueError("quota resource must be a non-empty identifier without a colon")
        return self._ctx.upload_quotas.usage(f"{domain}:{resource}")


class _MigrationsAdapter:
    def __init__(self, ctx: Context, owner: str) -> None:
        self._ctx = ctx
        self._owner = owner

    def _check(self, identifier: str) -> None:
        if (not re.fullmatch(r"[a-z][a-z0-9_]*", identifier)
                or not identifier.startswith(f"{self._owner}_")):
            raise PermissionError(f"migration identifier is outside the {self._owner} namespace")

    def applied(self, identifier: str) -> bool:
        self._check(identifier)
        return self._ctx.store.get("schema_migrations", identifier) is not None

    def mark_applied(self, identifier: str) -> bool:
        self._check(identifier)
        return self._ctx.store.create("schema_migrations", identifier, {"id": identifier})


class _SapBankingRoutesAdapter:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def router(self, policy: BankingRoutePolicy) -> APIRouter:
        from .services.banking import banking_router

        return banking_router(self._ctx, policy=policy)


class _ClubReceiptsAdapter:
    def __init__(self, ctx: Context) -> None:
        storage = ctx.services.get("receipt_storage")
        if storage is None:
            from .services.receipt_storage import ReceiptStorage, academic_year

            storage = ReceiptStorage(
                _StoreAdapter(ctx.store, {"receipts"}),
                ctx.settings,
                lambda: ctx.now(),
                ctx.audit,
                academic_year=academic_year,
            )
            ctx.services["receipt_storage"] = storage
        if not all(callable(getattr(storage, name, None)) for name in ("create", "read", "migrate", "archive")):
            raise TypeError("core receipt storage service is invalid")
        self._storage = storage

    def create(self, receipt: dict[str, Any]) -> None:
        self._storage.create(receipt)

    def read(self, reference: str, club_id: str) -> bytes | None:
        return self._storage.read(reference, club_id)


class _ClubInvitationLinkAdapter:
    def __init__(self, ctx: Context) -> None:
        self._issuer = ctx.settings.sso_issuer

    def portal_link(self) -> str:
        return f"{self._issuer}/static/"


class _ProvidedServices(Mapping[str, Any]):
    """Services offered by this plug-in's declared dependencies, each scoped to this plug-in."""

    def __init__(self, ctx: Context, manifest: PluginManifest) -> None:
        self._ctx = ctx
        self._consumer = manifest.id
        self._dependencies = set(manifest.dependencies)
        self._views: dict[str, Any] = {}

    def _names(self) -> list[str]:
        return sorted(name for name, (owner, _) in self._ctx.provided.items() if owner in self._dependencies)

    def __getitem__(self, name: str) -> Any:
        if name not in self._views:
            entry = self._ctx.provided.get(name)
            if entry is None or entry[0] not in self._dependencies:
                raise KeyError(f"provided service {name} is not available from a declared dependency")
            self._views[name] = entry[1].for_consumer(self._consumer)
        return self._views[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._names())

    def __len__(self) -> int:
        return len(self._names())


class _IntegrityAdapter:
    def __init__(self, owner: str, secret: str) -> None:
        self._owner = owner
        self._secret = secret.encode()

    def issue(self, purpose: str, subject: str, ttl: int) -> str:
        if not purpose or not subject or ttl <= 0:
            raise ValueError("integrity token purpose, subject and positive TTL are required")
        return security.sign_token(self._secret.decode(), f"{self._owner}:{subject}", ttl, f"plugin:{purpose}")

    def digest(self, purpose: str, value: bytes) -> str:
        if not purpose:
            raise ValueError("integrity digest purpose is required")
        scoped_purpose = f"plugin:{self._owner}:{purpose}".encode() + b"\0"
        return hmac.new(self._secret, scoped_purpose + value, hashlib.sha256).hexdigest()

    def verify(self, purpose: str, token: str) -> str | None:
        if not purpose:
            raise ValueError("integrity token purpose is required")
        subject = security.read_token(self._secret.decode(), token, f"plugin:{purpose}")
        prefix = f"{self._owner}:"
        return subject[len(prefix):] if subject and subject.startswith(prefix) else None


def _plugin_services(ctx: Context, manifest: PluginManifest) -> PluginServices:
    declared = set(manifest.services)
    if ctx.plugin_settings is None:
        settings = plugin_settings_for((manifest,), ctx.settings)[manifest.id]
    else:
        settings = ctx.plugin_settings.get(manifest.id)
        if settings is None and manifest.settings:
            raise RuntimeError(f"settings for plug-in {manifest.id} are not registered")
        settings = settings or {}
        unexpected = set(settings) - set(manifest.settings) - set(manifest.config)
        missing = set(manifest.settings) - set(settings)
        if unexpected or missing:
            raise RuntimeError(f"settings for plug-in {manifest.id} do not match its manifest")
    return PluginServices(
        auth=cast(AuthService, _AuthAdapter(ctx) if "auth" in declared else _UnavailableService("auth")),
        identity=cast(IdentityService, _IdentityAdapter(ctx) if "identity" in declared else _UnavailableService("identity")),
        quotas=cast(QuotaService, _QuotaAdapter(ctx, manifest.quota_domains)
                    if "quotas" in declared else _UnavailableService("quotas")),
        store=cast(
            StoreService,
            _StoreAdapter(ctx.store, set(manifest.collections), set(manifest.unlinkable))
            if "store" in declared
            else _UnavailableService("store"),
        ),
        audit=cast(AuditService, ctx.audit if "audit" in declared else _UnavailableService("audit")),
        files=cast(
            FileService,
            ctx.files.for_owner(manifest.id, manifest.dependencies) if "files" in declared else _UnavailableService("files"),
        ),
        forms=cast(
            FormsService,
            ctx.forms.for_owner(manifest.id) if "forms" in declared else _UnavailableService("forms"),
        ),
        notifications=cast(NotificationService, _UnavailableService("notifications")),
        messaging=cast(
            MessagingService,
            ctx.messaging.for_owner(manifest.id) if "messaging" in declared else _UnavailableService("messaging"),
        ),
        anchor=cast(
            AnchorService,
            ctx.anchors.for_owner(manifest.id) if "anchor" in declared else _UnavailableService("anchor"),
        ),
        email=cast(EmailService, ctx.email if "email" in declared else _UnavailableService("email")),
        integrity=cast(
            IntegrityService,
            _IntegrityAdapter(manifest.id, ctx.settings.secret)
            if "integrity" in declared
            else _UnavailableService("integrity"),
        ),
        events=ctx.bus if "events" in declared else _UnavailableService("events"),
        now=(lambda: ctx.now()) if "now" in declared else cast(Callable[[], int], _UnavailableCallable("now")),
        settings=MappingProxyType(dict(settings)) if "settings" in declared else _UnavailableSettings(),
        provided=_ProvidedServices(ctx, manifest) if "provided" in declared else MappingProxyType({}),
        agm_sso=cast(AgmSSORegistrationService, _AgmSSORegistrationAdapter(ctx)
                     if "agm_sso" in declared else _UnavailableService("agm_sso")),
        agm_review=cast(AgmReviewService, _AgmReviewAdapter(ctx)
                        if "agm_review" in declared else _UnavailableService("agm_review")),
        agm_image_limits=cast(AgmImageLimitsService, _AgmImageLimitsAdapter(ctx)
                              if "agm_image_limits" in declared else _UnavailableService("agm_image_limits")),
        sap_banking=cast(SapBankingRoutesService, _SapBankingRoutesAdapter(ctx)
                         if "sap_banking" in declared else _UnavailableService("sap_banking")),
        migrations=cast(MigrationsService, _MigrationsAdapter(ctx, manifest.id)
                        if "migrations" in declared else _UnavailableService("migrations")),
        club_receipts=cast(ClubReceiptsService, _ClubReceiptsAdapter(ctx)
                           if "club_receipts" in declared else _UnavailableService("club_receipts")),
        club_invitation_link=cast(ClubInvitationLinkService, _ClubInvitationLinkAdapter(ctx)
                                  if "club_invitation_link" in declared else _UnavailableService("club_invitation_link")),
    )


class TypedPluginAdapter:
    """Present a typed plug-in through the bootstrap's legacy module lifecycle."""

    def __init__(self, manifest: PluginManifest, implementation: Plugin) -> None:
        self.manifest = manifest
        self.name = manifest.id
        self._implementation = implementation
        self._services: PluginServices | None = None

    def configure(self, ctx: Context) -> None:
        services = _plugin_services(ctx, self.manifest)
        configure = getattr(self._implementation, "configure", None)
        if not callable(configure):
            raise TypeError(f"plug-in {self.name} has no configure(registration) method")
        provided = services.provided

        def resolve(name: str) -> Any:
            if "provided" not in self.manifest.services:
                raise CapabilityUnavailable(f"plug-in {self.name} must declare the provided service")
            return provided[name]

        registration = RegistrationContext(self.name, _resolver=resolve)
        configure(registration)
        values = registration.freeze()
        declared_roles = dict(self.manifest.roles)
        factories: dict[str, Callable[[PluginServices], Any]] = {}
        for (kind, identifier), value in values.items():
            if kind == "role":
                if identifier not in declared_roles:
                    raise ValueError(f"plug-in {self.name} registered undeclared role {identifier}")
            elif kind == "scoped_role":
                if identifier not in dict(self.manifest.scoped_roles):
                    raise ValueError(f"plug-in {self.name} registered undeclared scoped role {identifier}")
                if not callable(value):
                    raise TypeError(f"plug-in {self.name} scoped role {identifier} needs a factory")
                holds_in = value(services)
                ctx.scoped_roles[identifier] = lambda account, scope, holds_in=holds_in: bool(holds_in(account, scope))
            elif kind == "derived_role":
                if identifier not in dict(self.manifest.derived_roles):
                    raise ValueError(f"plug-in {self.name} registered undeclared derived role {identifier}")
                if not callable(value):
                    raise TypeError(f"plug-in {self.name} derived role {identifier} needs a factory")
                holds = value(services)
                ctx.derived_roles[identifier] = lambda account, holds=holds: bool(holds(account))
            elif kind == "file":
                if "files" not in self.manifest.services:
                    raise CapabilityUnavailable(f"plug-in {self.name} must declare the files service")
                from .contracts import FileKind
                if callable(value) and not isinstance(value, FileKind):
                    value = value(services)
                if not isinstance(value, FileKind):
                    raise TypeError(f"plug-in {self.name} file registration {identifier} is not a FileKind")
                ctx.files.register_kind(self.name, identifier, value)
            elif kind == "file_authorizer":
                if not {"files", "file_authorizers"} & set(self.manifest.services):
                    raise CapabilityUnavailable(f"plug-in {self.name} must declare file authorizer registration")
                if callable(value) and not isinstance(value, FileAuthorization):
                    value = value(services)
                if not isinstance(value, FileAuthorization):
                    raise TypeError(f"plug-in {self.name} file authorizer {identifier} is not a FileAuthorization")
                ctx.files.register_authorizer(self.name, identifier, self.manifest.dependencies, value)
            elif kind == "form":
                if "forms" not in self.manifest.services:
                    raise CapabilityUnavailable(f"plug-in {self.name} must declare the forms service")
                if identifier not in self.manifest.form_ids:
                    raise ValueError(f"plug-in {self.name} registered undeclared form {identifier}")
                if not isinstance(value, FormContribution):
                    raise TypeError(f"plug-in {self.name} form registration {identifier} is not a FormContribution")
                ctx.form_registry.register_form(self.name, identifier, value)
            elif kind == "submission_policy":
                if "forms" not in self.manifest.services or identifier not in self.manifest.form_ids:
                    raise CapabilityUnavailable(f"plug-in {self.name} must declare form {identifier} and the forms service")
                if not isinstance(value, SubmissionPolicy):
                    raise TypeError(f"plug-in {self.name} submission policy {identifier} is invalid")
                ctx.form_registry.register_policy(self.name, identifier, value)
            elif kind == "notification":
                if "forms" not in self.manifest.services:
                    raise CapabilityUnavailable(f"plug-in {self.name} must declare the forms service")
                if not isinstance(value, NotificationType):
                    raise TypeError(f"plug-in {self.name} notification registration {identifier} is invalid")
                if value.form_id not in self.manifest.form_ids or value.role not in declared_roles:
                    raise ValueError(f"plug-in {self.name} notification {identifier} uses an undeclared form or role")
                ctx.notification_types.register(self.name, identifier, value)
            elif kind == "thread":
                if "messaging" not in self.manifest.services:
                    raise CapabilityUnavailable(f"plug-in {self.name} must declare the messaging service")
                if not isinstance(value, ThreadType):
                    raise TypeError(f"plug-in {self.name} thread registration {identifier} is not a ThreadType")
                def authorize(account: Mapping[str, Any], record_id: str,
                              definition: ThreadType = value) -> ThreadAccess | None:
                    return definition.authorize(_plugin_account(account), record_id)
                ctx.thread_types.register(self.name, identifier, replace(value, authorize=authorize))
            elif kind == "provide":
                if identifier not in self.manifest.provides:
                    raise ValueError(f"plug-in {self.name} provided undeclared service {identifier}")
                if not callable(value):
                    raise TypeError(f"plug-in {self.name} service {identifier} needs a factory")
                factories[identifier] = value
            else:
                raise CapabilityUnavailable(
                    f"plug-in registration kind {kind} is unavailable in this host"
                )
        missing = set(self.manifest.provides) - set(factories)
        if missing:
            raise ValueError(f"plug-in {self.name} did not provide {', '.join(sorted(missing))}")
        for name, factory in sorted(factories.items()):
            instance = factory(services)
            if not callable(getattr(instance, "for_consumer", None)):
                raise TypeError(f"plug-in {self.name} service {name} has no for_consumer(consumer)")
            ctx.provided[name] = (self.name, instance)
        self._services = services

    def router(self, _ctx: Context) -> APIRouter:
        if self._services is None:
            raise RuntimeError(f"plug-in {self.name} services were not initialized")
        return self._implementation.router(self._services)

    def startup(self, _ctx: Context) -> Any:
        if self._services is None:
            raise RuntimeError(f"plug-in {self.name} services were not initialized")
        hook = getattr(self._implementation, "startup", None)
        return hook(self._services) if callable(hook) else None

    def shutdown(self, _ctx: Context) -> Any:
        if self._services is None:
            raise RuntimeError(f"plug-in {self.name} services were not initialized")
        hook = getattr(self._implementation, "shutdown", None)
        return hook(self._services) if callable(hook) else None
