"""Adapters from the legacy application context to the public plug-in contracts."""
from __future__ import annotations

import contextlib
import hashlib
import hmac
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any, Callable, cast

from fastapi import APIRouter
from fastapi.params import Depends as AuthDependency

from . import security
from .context import Context, roles_of
from .contracts import (
    AnchorService,
    AuditService,
    AuthService,
    EmailService,
    FileService,
    FormContribution,
    FormsService,
    IntegrityService,
    MessagingService,
    NotificationService,
    NotificationType,
    Plugin,
    PluginManifest,
    PluginServices,
    RegistrationContext,
    SubmissionPolicy,
    StoreService,
    ThreadType,
)
from .pagination import PageOrder
from .store import Doc, Store, unlinked_access

SUPPORTED_PLUGIN_SERVICES = frozenset({
    "auth", "audit", "store", "files", "forms", "email", "integrity", "events", "now", "settings",
    "messaging", "anchor", "provided",
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


class _AuthAdapter:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def require_verified(self) -> AuthDependency:
        return self._ctx.require_verified()

    def require_rostered(self) -> AuthDependency:
        return self._ctx.require_rostered()

    def require_role(self, *roles: str) -> AuthDependency:
        return self._ctx.require_role(*roles)

    def require_permission(self, *permissions: str) -> AuthDependency:
        return self._ctx.require_permission(*permissions)

    def has_permission(self, account: Mapping[str, Any], *permissions: str) -> bool:
        return self._ctx.has_permission(account, *permissions)

    def has_any_permission(self, account: Mapping[str, Any], *permissions: str) -> bool:
        return self._ctx.has_any_permission(account, *permissions)

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

    def update(self, collection: str, key: str, fn: Callable[[Doc | None], Doc | None]) -> Doc | None:
        with self._check(collection):
            return self._store.update(collection, key, fn)

    def delete(self, collection: str, key: str) -> None:
        with self._check(collection):
            self._store.delete(collection, key)


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


def _plugin_services(ctx: Context, manifest: PluginManifest) -> PluginServices:
    declared = set(manifest.services)
    if ctx.plugin_settings is None:
        settings = {name: getattr(ctx.settings, name) for name in manifest.settings}
    else:
        settings = ctx.plugin_settings.get(manifest.id)
        if settings is None and manifest.settings:
            raise RuntimeError(f"settings for plug-in {manifest.id} are not registered")
        settings = settings or {}
        unexpected = set(settings) - set(manifest.settings)
        missing = set(manifest.settings) - set(settings)
        if unexpected or missing:
            raise RuntimeError(f"settings for plug-in {manifest.id} do not match its manifest")
    return PluginServices(
        auth=cast(AuthService, _AuthAdapter(ctx) if "auth" in declared else _UnavailableService("auth")),
        store=cast(
            StoreService,
            _StoreAdapter(ctx.store, set(manifest.collections), set(manifest.unlinkable))
            if "store" in declared
            else _UnavailableService("store"),
        ),
        audit=cast(AuditService, ctx.audit if "audit" in declared else _UnavailableService("audit")),
        files=cast(
            FileService,
            ctx.files.for_owner(manifest.id) if "files" in declared else _UnavailableService("files"),
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
            elif kind == "file":
                if "files" not in self.manifest.services:
                    raise CapabilityUnavailable(f"plug-in {self.name} must declare the files service")
                from .contracts import FileKind
                if not isinstance(value, FileKind):
                    raise TypeError(f"plug-in {self.name} file registration {identifier} is not a FileKind")
                ctx.files.register_kind(self.name, identifier, value)
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
                ctx.thread_types.register(self.name, identifier, value)
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
