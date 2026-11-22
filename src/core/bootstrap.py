from __future__ import annotations

from importlib import resources, util
from pathlib import Path
from typing import Any, Mapping, Protocol

import hashlib
import hmac
import inspect
import logging
from collections.abc import Iterable
from contextlib import asynccontextmanager
from typing import cast
from fastapi import APIRouter, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import formdef
from .audit import AuditLog
from .crypto import Crypter, EncryptingStore
from .identity import Identity, LocalIdentity
from .events import bus_from_settings
from .anchor import provider_from_settings
from .ratelimit import RateLimiter, limiter_from_settings
from .context import Context
from .mailer import Mailer, mailer_from_env
from .role_catalogue import ROLES
from .services.account_check import check_accounts
from .settings import Settings
from .store import ACCESS, AccessLog, AuditedStore, Store, open_store
from .version import server_version
from .devadmin import ConsoleMailer, check_dev_settings, dev_local_present, seed_dev_admin
from .services.anchors import AnchorRuntime
from .services.email import EmailDeliveryService
from .services.files import DocumentService
from .services.forms import FormRegistry, FormsRuntime
from .services.messaging import MessagingRuntime, ThreadTypeRegistry
from .services.notifications import NotificationTypeRegistry
from .permissions import (
    permission_catalogue_for,
    role_permissions_for,
)
from .services.financial_access import FinancialAccessStore

STATIC = Path(__file__).parent / "static"
RATE_LIMITED = ("/api/auth/", "/api/recovery", "/sso/token", "/api/sso/authorize", "/mail/")
log = logging.getLogger("core")


class _RequestBodyTooLarge(Exception):
    pass


class Module(Protocol):
    name: str

    def router(self, ctx: Context) -> APIRouter: ...


def _route_methods(value: object) -> tuple[str, ...]:
    if not isinstance(value, (set, frozenset, list, tuple)):
        return ()
    return tuple(method for method in cast(Iterable[object], value) if isinstance(method, str))


async def _read_bounded_body(receive: Receive, maximum: int, expected_size: int | None = None) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            if size == 0 and expected_size in (None, 0):
                return b""
            raise ClientDisconnect()
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > maximum:
            raise _RequestBodyTooLarge
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


class RequestGuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        limiter: RateLimiter,
        rate_limit_auth: int,
        rate_window: int,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.limiter = limiter
        self.rate_limit_auth = rate_limit_auth
        self.rate_window = rate_window

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope["headers"]}
        path, method = scope["path"], scope["method"]
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        if method == "POST" and path.startswith(RATE_LIMITED):
            key = f"auth:{path}:{client_ip}"
            allowed = await run_in_threadpool(
                self.limiter.allow, key, self.rate_limit_auth, self.rate_window
            )
            if not allowed:
                await JSONResponse(
                    {"detail": "too many requests"},
                    429,
                    headers={"Retry-After": str(self.rate_window)},
                )(scope, receive, send)
                return

        raw_length = headers.get(b"content-length")
        declared_size = None
        if raw_length is not None:
            try:
                declared_size = int(raw_length)
            except ValueError:
                await JSONResponse({"detail": "invalid content length"}, 400)(scope, receive, send)
                return
            if declared_size < 0:
                await JSONResponse({"detail": "invalid content length"}, 400)(scope, receive, send)
                return
            if declared_size > self.max_body_bytes:
                await JSONResponse({"detail": "request too large"}, 413)(scope, receive, send)
                return

        if (method not in ("POST", "PUT", "PATCH", "DELETE")
                and not (declared_size and declared_size > 0)
                and b"transfer-encoding" not in headers):
            await self.app(scope, receive, send)
            return

        try:
            body = await _read_bounded_body(receive, self.max_body_bytes, declared_size)
        except _RequestBodyTooLarge:
            await JSONResponse({"detail": "request too large"}, 413)(scope, receive, send)
            return

        body_sent = False

        async def replay_body() -> Message:
            nonlocal body_sent
            if body_sent:
                return {"type": "http.disconnect"}
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_body, send)


def seed_forms(ctx: Context, directory: Path | None = None) -> None:
    """Seed enabled plug-in contributions; a changed definition requires a version bump."""
    if directory is None:
        contributions = tuple((form_id, contribution.yaml)
                              for form_id, contribution in ctx.form_registry.contributions())
    else:
        contributions = tuple(
            (path.stem, path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.yaml"))
            if ctx.form_registry.owner(path.stem) is not None
        )
    for form_id, text in contributions:
        schema = formdef.parse_yaml(text)
        if schema["id"] != form_id or ctx.form_registry.owner(form_id) is None:
            raise RuntimeError(f"form contribution {form_id} is not registered to an enabled plug-in")
        h = formdef.schema_hash(schema)
        key = f"{schema['id']}@{schema['version']}"
        ex = ctx.store.get("form_versions", key)
        if ex and ex["hash"] != h:
            raise RuntimeError(f"{form_id}: definition changed but version {schema['version']} was not bumped")
        f = ctx.store.get("forms", schema["id"]) or {"id": schema["id"], "rev": 0, "published_version": None}
        if not ex:
            ctx.store.put("form_versions", key, {"id": schema["id"], "version": schema["version"], "hash": h, "schema": schema,
                                                 "yaml": text, "published_at": ctx.now(), "by": "seed"})
            current = f.get("published_version")
            if current is None or schema["version"] > current:
                published = ctx.store.get("form_versions", f"{schema['id']}@{current}") if current else None
                unchanged_draft = not f.get("draft_yaml") or (published and f.get("draft_hash") == published["hash"])
                f.update(rev=f["rev"] + 1, published_version=schema["version"])
                if unchanged_draft:
                    f.update(draft_yaml=text, draft_hash=h, draft_version=schema["version"])
                ctx.store.put("forms", schema["id"], f)


def _module_routers(modules: tuple[Module, ...], ctx: Context, ui: bool) -> tuple[APIRouter, ...]:
    routers = []
    owners: dict[tuple[str, str], str] = {
        ("GET", "/healthz"): "core",
        ("GET", "/readyz"): "core",
        ("GET", "/api/plugins"): "core",
        ("GET", "/api/version"): "core",
    }
    if ui:
        owners[("GET", "/")] = "core"
    for module in modules:
        router = module.router(ctx)
        deferred = any(getattr(route, "path", None) is None for route in router.routes)
        route_entries: list[tuple[str, tuple[str, ...]]] = []
        if deferred:
            from fastapi.routing import iter_route_contexts
            for route_context in iter_route_contexts(router.routes):
                path = route_context.path
                if not isinstance(path, str):
                    raise RuntimeError(f"module {module.name} registered a route without a path")
                methods = _route_methods(route_context.methods)
                route_entries.append((path, methods or ("MOUNT",)))
        else:
            for route in router.routes:
                path = getattr(route, "path", None)
                if not isinstance(path, str):
                    raise RuntimeError(f"module {module.name} registered a route without a path")
                methods = _route_methods(getattr(route, "methods", ()))
                route_entries.append((path, methods or ("MOUNT",)))
        for path, methods in route_entries:
            for method in methods:
                key = (method, path)
                previous = owners.get(key)
                if previous is not None:
                    raise RuntimeError(
                        f"duplicate route {method} {path} owned by {previous} and module {module.name}"
                    )
                owners[key] = module.name
        routers.append(router)
    return tuple(routers)


async def _call_lifecycle(modules: tuple[Module, ...], ctx: Context, hook_name: str) -> list[tuple[str, Exception]]:
    failures = []
    for module in modules:
        hook = getattr(module, hook_name, None)
        if not callable(hook):
            continue
        try:
            result = hook(ctx)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            log.exception("module %s %s hook failed", module.name, hook_name)
            failures.append((module.name, exc))
    return failures


def _shutdown_error(failures: list[tuple[str, Exception]]) -> RuntimeError:
    names = ", ".join(name for name, _ in failures)
    error = RuntimeError(f"module shutdown failed: {names}")
    error.__cause__ = failures[0][1]
    return error


def build_app(settings: Settings, store: Store | None, mailer: Mailer | None, modules: tuple[Module, ...],
              identity: Identity | None = None,
              seed: bool = True, ui: bool = True, form_owners: Mapping[str, str] | None = None,
              role_catalogue: dict[str, str] | None = None,
              permission_catalogue: Mapping[str, str] | None = None,
              role_permissions: Mapping[str, frozenset[str]] | None = None,
              plugin_settings: Mapping[str, Mapping[str, Any]] | None = None,
              frontend_plugins: tuple[str, ...] | None = None) -> FastAPI:
    check_dev_settings(settings)
    mailer = mailer or mailer_from_env()
    if settings.dev_admin and not mailer.is_configured:
        mailer = ConsoleMailer()
    store = EncryptingStore(store or open_store(settings.store), Crypter(settings.data_key, settings.secret))
    if settings.audit_db_access:
        store = AuditedStore(store)
    if permission_catalogue is None or role_permissions is None:
        from .plugin_host import discover_manifests, select_manifests

        manifests = select_manifests(discover_manifests(), "local", settings.enabled_plugins)
        if permission_catalogue is None:
            permission_catalogue = permission_catalogue_for(manifests)
        if role_permissions is None:
            role_permissions = role_permissions_for(manifests)
    assert permission_catalogue is not None and role_permissions is not None
    ctx = Context(store, settings, mailer, bus=bus_from_settings(settings),
                  roles=ROLES if role_catalogue is None else role_catalogue,
                  permissions=permission_catalogue,
                  role_permissions=role_permissions,
                  plugin_settings=plugin_settings)
    ctx.identity = identity or LocalIdentity(ctx)
    if identity is None and store.get("roster_meta", "current"):
        check_accounts(store, ctx.now())
    ctx.audit, ctx.limiter = AuditLog(ctx), limiter_from_settings(settings)
    ctx.store = FinancialAccessStore(ctx.store, ctx)
    from .services.upload_quota import QuotaStore

    if settings.user_storage_bytes <= 0 or not 0 < settings.club_storage_year_bytes <= 10 * 1024 ** 3:
        raise ValueError("upload allocations must be positive; yearly club allocation cannot exceed 10 GiB")
    if settings.auth_url and not settings.quota_store:
        raise ValueError("split authentication requires PORTAL_QUOTA_STORE shared with the auth service")
    if settings.quota_store in {"memory", "sqlite:", "sqlite::memory:"}:
        raise ValueError("PORTAL_QUOTA_STORE must be persistent and shared, not an in-memory store")
    quota_store = open_store(settings.quota_store) if settings.quota_store else store
    ctx.upload_quotas = QuotaStore(ctx.store, quota_store, settings)
    ctx.store = ctx.upload_quotas
    ctx.files = DocumentService(ctx.audit)
    ctx.form_registry = FormRegistry(form_owners)
    ctx.forms = FormsRuntime(ctx.store, ctx.form_registry)
    ctx.notification_types = NotificationTypeRegistry(ctx.form_registry)
    ctx.email = EmailDeliveryService(ctx.store, ctx.audit, ctx.mailer, settings.secret, ctx.now)
    ctx.anchor = provider_from_settings(settings, store)
    ctx.anchors = AnchorRuntime(ctx.store, ctx.anchor, ctx.audit, ctx.now)
    ctx.thread_types = ThreadTypeRegistry()

    def display_name(uuid: str) -> str | None:
        user = ctx.identity.user(uuid) or {}
        return user.get("preferred_name") or user.get("first_name") or None

    ctx.messaging = MessagingRuntime(ctx.store, ctx.audit, ctx.bus, ctx.limiter, ctx.thread_types, display_name, ctx.now,
                                     ctx.identity.user)
    for module in modules:
        configure = getattr(module, "configure", None)
        if configure is not None:
            try:
                configure(ctx)
            except Exception as exc:
                log.exception("module %s configure hook failed", module.name)
                raise RuntimeError(f"module {module.name} configure failed") from exc
    ctx.upload_quotas.backfill()
    ctx.form_registry.freeze()
    ctx.notification_types.freeze()
    ctx.files.freeze()
    ctx.thread_types.freeze()
    for _, service in ctx.provided.values():
        freeze = getattr(service, "freeze", None)
        if callable(freeze):
            freeze()
    routers = _module_routers(modules, ctx, ui)
    if seed:
        seed_forms(ctx)
    if settings.dev_admin and identity is None:
        seed_dev_admin(ctx)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        started: list[Module] = []
        for module in modules:
            startup = getattr(module, "startup", None)
            if not callable(startup):
                continue
            try:
                result = startup(ctx)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                log.exception("module %s startup hook failed", module.name)
                failures = await _call_lifecycle(tuple(reversed(started)), ctx, "shutdown")
                if failures:
                    log.error("module shutdown also failed while handling startup failure: %s",
                              ", ".join(name for name, _ in failures))
                raise RuntimeError(f"module {module.name} startup failed") from exc
            started.append(module)
        try:
            yield
        finally:
            ctx.anchors.close()
            failures = await _call_lifecycle(tuple(reversed(started)), ctx, "shutdown")
            if failures:
                raise _shutdown_error(failures)

    app = FastAPI(title="UTSU applicant portal", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.ctx = ctx
    app.add_middleware(
        RequestGuardMiddleware,
        max_body_bytes=settings.max_body_bytes,
        limiter=ctx.limiter,
        rate_limit_auth=settings.rate_limit_auth,
        rate_window=settings.rate_window,
    )
    for router in routers:
        app.include_router(router)

    def actor_of(request: Request) -> str | None:
        raw = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return ctx.identity.subject(raw) if raw else None

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def audit_requests(request: Request, call_next):
        path = request.url.path
        who = actor_of(request)
        pid = hmac.new(settings.secret.encode(), client_ip(request).encode(), hashlib.sha256).hexdigest()[:12]
        access = AccessLog(who or f"anonymous:{pid}")
        token = ACCESS.set(access)  # every store call made while serving this request is attributed to the requester
        try:
            resp = await call_next(request)
        finally:
            ACCESS.reset(token)
        if settings.audit_db_access and access.ops and not path.startswith("/api/admin/auth-audit"):
            try:  # one digest entry per request: which identity touched which collections/ids (never values)
                await run_in_threadpool(ctx.audit.record, access.requester, "db.access", getattr(request.scope.get("route"), "path", path),
                                        None, None, {"ops": access.ops, "method": request.method, "status": resp.status_code})
            except Exception:
                log.exception("access audit failed")
        watched = request.method not in ("GET", "HEAD", "OPTIONS") or path.startswith(("/api/admin/", "/api/finance/", "/api/profile/financial", "/api/orders", "/api/sap/banking"))
        if watched and not path.startswith("/api/admin/audit") and not path.startswith("/static"):
            route = request.scope.get("route")
            try:  # no bodies, query strings or tokens are logged; IPs are keyed-hashed
                await run_in_threadpool(ctx.audit.record, who, f"http.{request.method}",
                                        getattr(route, "path", path), None, None,
                                        {"status": resp.status_code, "ip": pid, "params": {k: str(v)[:80] for k, v in request.path_params.items()}})
            except Exception:
                log.exception("audit write failed")
        return resp

    @app.middleware("http")
    async def headers(request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        csp = "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if settings.turnstile_site_key and settings.turnstile_secret_key:
            csp += "; script-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; connect-src 'self' https://challenges.cloudflare.com"
        h.setdefault("Content-Security-Policy", csp)
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        if settings.hsts:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith(("/api/", "/sso/")):
            h.setdefault("Cache-Control", "no-store")
        elif request.url.path.startswith("/branding/"):
            h.setdefault("Cache-Control", "no-cache")  # edits to the theme or logos show on the next page load
        return resp

    if ui:
        if settings.static_dir is not None and not settings.static_dir.strip():
            raise RuntimeError("PORTAL_STATIC_DIR must not be empty")
        if settings.static_dir is not None:
            static_dir = Path(settings.static_dir)
        elif util.find_spec("utsu_portal_frontend") is not None:
            static_dir = Path(str(resources.files("utsu_portal_frontend").joinpath("static")))
        else:
            static_dir = STATIC
        if not (static_dir / "index.html").is_file():
            raise RuntimeError(f"frontend bundle is missing index.html: {static_dir}")
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        branding = Path(settings.branding_dir) if settings.branding_dir else Path(str(resources.files("branding")))
        branding.mkdir(parents=True, exist_ok=True)
        app.mount("/branding", StaticFiles(directory=branding), name="branding")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    enabled_modules = sorted(module.name for module in modules)

    @app.get("/api/plugins", include_in_schema=False)
    def plugins():
        """Enabled module ids, so the UI can hide navigation for programs that are not deployed."""
        result = {"enabled": enabled_modules}
        if frontend_plugins is not None:
            result["frontend"] = sorted(frontend_plugins)
        return result

    build = server_version(settings.commit)

    @app.get("/api/version", include_in_schema=False)
    def version():
        return {**build, "dev_local": dev_local_present(ctx)}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        ok = {"store": True, "redis": ctx.bus.ping(), "identity": ctx.identity.ping()}
        try:
            ctx.store.get("health", "probe")
        except Exception:
            ok["store"] = False
        return JSONResponse(ok, 200 if all(ok.values()) else 503)

    if ui:
        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(static_dir / "index.html")

    return app
