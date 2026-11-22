"""Portal composition; standalone services share only the neutral bootstrap."""
from __future__ import annotations

from fastapi import FastAPI

from .bootstrap import Module, build_app, seed_forms as seed_forms
from .identity import Identity, RemoteIdentity, http_fetch
from .mailer import Mailer
from .plugin_host import load_modules
from .permissions import permission_catalogue_for, role_permissions_for
from .role_catalogue import role_catalogue_for
from .settings import Settings, plugin_settings_for
from .store import Store


def create_app(settings: Settings | None = None, store: Store | None = None, mailer: Mailer | None = None,
               extra_modules: tuple[Module, ...] = (), identity: Identity | None = None) -> FastAPI:
    """Assemble local or split-auth portal APIs and the static client."""
    settings = settings or Settings.from_env()
    if identity is None and settings.auth_url:
        if not (settings.auth_public_key and settings.service_token):
            raise RuntimeError("PORTAL_AUTH_URL requires PORTAL_AUTH_PUBLIC_KEY and PORTAL_SERVICE_TOKEN")
        identity = RemoteIdentity(settings.auth_public_key, http_fetch(settings.auth_url, settings.service_token))
    auth_mode = "local" if identity is None else "remote"
    manifests, modules = load_modules(auth_mode, settings.enabled_plugins)
    form_owners = {form_id: manifest.id for manifest in manifests for form_id in manifest.form_ids}
    role_catalogue = role_catalogue_for(manifests)
    permission_catalogue = permission_catalogue_for(manifests)
    role_permissions = role_permissions_for(manifests)
    return build_app(settings, store, mailer, (*modules, *extra_modules), identity,
                     seed=settings.seed_forms, ui=not settings.api_only, form_owners=form_owners,
                     role_catalogue=role_catalogue, permission_catalogue=permission_catalogue,
                     role_permissions=role_permissions, plugin_settings=plugin_settings_for(manifests, settings),
                     frontend_plugins=tuple(manifest.id for manifest in manifests if manifest.frontend))
