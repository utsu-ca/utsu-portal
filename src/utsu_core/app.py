"""Portal composition; standalone services share only the neutral bootstrap."""
from __future__ import annotations

from fastapi import FastAPI

from .bootstrap import Module, build_app, seed_forms as seed_forms
from .identity import RemoteIdentity, http_fetch
from .mailer import Mailer
from .modules.admin import AdminModule
from .modules.audit import AuditModule
from .modules.auth import AuthModule
from .modules.forms import FormsModule
from .modules.notices import NoticesModule
from .modules.roles import RolesModule
from .modules.sso import SSOModule
from .programs.agm.module import AgmModule
from .programs.agm.verify import AgmVerifyModule
from .programs.clubs.module import ClubsModule
from .programs.sap.module import SapModule
from .programs.senate.module import SenateModule
from .settings import Settings
from .store import Store

CORE_MODULES = (
    SSOModule(), AuthModule(), AgmVerifyModule(), AgmModule(), SenateModule(), SapModule(),
    FormsModule(), NoticesModule(), AdminModule(), ClubsModule(), AuditModule(), RolesModule(),
)
REMOTE_MODULES = (
    SSOModule(), AgmVerifyModule(), AgmModule(), SenateModule(), SapModule(), FormsModule(),
    NoticesModule(), ClubsModule(), AuditModule(),
)


def create_app(settings: Settings | None = None, store: Store | None = None, mailer: Mailer | None = None,
               extra_modules: tuple[Module, ...] = (), identity=None) -> FastAPI:
    """Assemble local or split-auth portal APIs and the static client."""
    settings = settings or Settings.from_env()
    if identity is None and settings.auth_url:
        if not (settings.auth_public_key and settings.service_token):
            raise RuntimeError("PORTAL_AUTH_URL requires PORTAL_AUTH_PUBLIC_KEY and PORTAL_SERVICE_TOKEN")
        identity = RemoteIdentity(settings.auth_public_key, http_fetch(settings.auth_url, settings.service_token))
    modules = CORE_MODULES if identity is None else REMOTE_MODULES
    return build_app(settings, store, mailer, (*modules, *extra_modules), identity, seed=settings.seed_forms, ui=True)
