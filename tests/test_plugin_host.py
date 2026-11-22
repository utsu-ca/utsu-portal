import json
import os
import subprocess
import sys
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from utsu_core import create_app
from utsu_core.bootstrap import build_app
from utsu_core.context import Context
from utsu_core.contracts import (
    FileAuthorization, FileKind, FormsService, PluginManifest, PluginServices, RegistrationContext, StoreService,
    ThreadAccess, ThreadType,
)
from utsu_core.pagination import PageOrder
from utsu_core.plugin_host import (
    PluginHostError,
    discover_manifests,
    load_modules,
    parse_manifest,
    select_manifests,
)
from utsu_core.settings import Settings
from utsu_core.store import MemoryStore, SQLiteStore, Store

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_metadata_reports_only_enabled_programs(monkeypatch: pytest.MonkeyPatch):
    from utsu_core import app as app_module

    original = app_module.load_modules
    external = parse_manifest({
        "id": "external-ui", "version": "1", "core_api_major": 1,
        "factory": "example:Plugin", "frontend": True,
    }, "test")
    backend_only = parse_manifest({
        "id": "backend-only", "version": "1", "core_api_major": 1,
        "factory": "example:Plugin",
    }, "test")

    class Program:
        def __init__(self, name: str):
            self.name = name

        def configure(self, ctx: Context) -> None:
            pass

        def router(self, ctx: Context) -> APIRouter:
            return APIRouter()

    def load_with_external(auth_mode, enabled_plugins):
        manifests, modules = original(auth_mode, enabled_plugins)
        return (*manifests, external, backend_only), (*modules, Program(external.id), Program(backend_only.id))

    monkeypatch.setattr(app_module, "load_modules", load_with_external)
    app = create_app(Settings(secret="s" * 32, enabled_plugins=("agm", "agm_verify")))
    result = TestClient(app).get("/api/plugins").json()
    assert result["frontend"] == ["agm", "agm_verify", "external-ui"]
    assert "backend-only" in result["enabled"]
    assert "clubs" not in result["frontend"]

    with pytest.raises(PluginHostError, match="frontend must be a boolean"):
        parse_manifest({"id": "example", "version": "1", "core_api_major": 1,
                        "factory": "example:Plugin", "frontend": "yes"}, "test")


def test_scoped_file_creation_authorizes_and_audits_before_writing():
    from utsu_core.services.files import DocumentService

    audit = Mock()
    written: list[tuple[str, str, str, bytes]] = []
    files = DocumentService(audit)
    files.register_kind("sample", "document", FileKind(
        authorize=lambda action, actor, resource, reference: (
            action == "create" and actor == "writer" and resource == "owner" and reference == "doc"
        ),
        read=lambda _reference, _resource: None,
        create=lambda reference, resource, actor, content: written.append((reference, resource, actor, content)),
    ))
    files.freeze()
    owner = files.for_owner("sample")
    with pytest.raises(PermissionError):
        files.for_owner("other").create("document", "doc", "writer", "owner", b"private")
    with pytest.raises(PermissionError):
        owner.create("document", "doc", "other", "owner", b"private")
    with pytest.raises(TypeError, match="must be bytes"):
        owner.create("document", "doc", "writer", "owner", "not bytes")  # type: ignore[arg-type]
    assert written == []
    audit.record.assert_not_called()

    audit.record.side_effect = RuntimeError("audit unavailable")
    with pytest.raises(RuntimeError, match="audit unavailable"):
        owner.create("document", "doc", "writer", "owner", b"private")
    assert written == []

    audit.record.side_effect = None
    owner.create("document", "doc", "writer", "owner", b"private")
    assert written == [("doc", "owner", "writer", b"private")]
    audit.record.assert_called_with("writer", "file.create", "file:document:doc", None, None,
                                    {"kind": "document", "reference": "doc"})


def test_file_delegation_requires_opt_in_and_declared_owner_dependency():
    from utsu_core.services.files import DocumentService

    files = DocumentService(Mock())
    grant = FileAuthorization(lambda _action, actor, _resource, _reference: actor == "staff")
    files.register_kind("profile", "private", FileKind(authorize=lambda *_: False, read=lambda *_: b"private"))
    files.register_kind("profile", "shared", FileKind(
        authorize=lambda *_: False, read=lambda *_: b"shared",
        create=lambda *_: None, delete=lambda *_: None,
        authorizer_actions=frozenset({"read"}), dependent_actions=frozenset({"read"}),
    ))
    files.register_kind("profile", "grant-only", FileKind(
        authorize=lambda *_: False, read=lambda *_: b"grant", authorizer_actions=frozenset({"read"}),
    ))
    files.register_kind("profile", "consume-only", FileKind(
        authorize=lambda action, actor, _resource, _reference: action == "read" and actor == "staff",
        read=lambda *_: b"consume", dependent_actions=frozenset({"read"}),
    ))
    with pytest.raises(ValueError, match="unknown action"):
        files.register_kind("profile", "invalid", FileKind(
            authorize=lambda *_: False, read=lambda *_: None, dependent_actions=frozenset({"rename"}),
        ))
    with pytest.raises(TypeError, match="frozen sets"):
        files.register_kind("profile", "mutable", FileKind(
            authorize=lambda *_: False, read=lambda *_: None, dependent_actions={"read"},  # type: ignore[arg-type]
        ))
    with pytest.raises(PermissionError):
        files.register_authorizer("sap", "private", ("profile",), grant)
    with pytest.raises(PermissionError):
        files.register_authorizer("sap", "shared", (), grant)
    with pytest.raises(PermissionError):
        files.register_authorizer("sap", "unknown", ("profile",), grant)
    with pytest.raises(PermissionError):
        files.register_authorizer("sap", "consume-only", ("profile",), grant)
    with pytest.raises(PermissionError):
        files.register_authorizer("sap", "shared", ("profile",), FileAuthorization(
            authorize=lambda *_: True, actions=frozenset({"delete"}),
        ))
    with pytest.raises(TypeError, match="frozen set"):
        files.register_authorizer("sap", "shared", ("profile",), FileAuthorization(
            authorize=lambda *_: True, actions={"read"},  # type: ignore[arg-type]
        ))
    files.register_authorizer("sap", "shared", ("profile",), grant)
    files.register_authorizer("sap", "grant-only", ("profile",), grant)
    with pytest.raises(ValueError, match="duplicate"):
        files.register_authorizer("sap", "shared", ("profile",), grant)
    files.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        files.register_authorizer("other", "shared", ("profile",), grant)
    with pytest.raises(PermissionError):
        files.for_owner("other").read("shared", "doc", "staff", "person")
    with pytest.raises(PermissionError):
        files.for_owner("sap", ("profile",)).read("private", "doc", "staff", "person")
    with pytest.raises(PermissionError):
        files.for_owner("sap", ("profile",)).read("grant-only", "doc", "staff", "person")
    with pytest.raises(PermissionError):
        files.for_owner("sap", ("profile",)).read("shared", "doc", "other", "person")
    with pytest.raises(PermissionError):
        files.for_owner("sap", ("profile",)).create("shared", "doc", "staff", "person", b"x")
    with pytest.raises(PermissionError):
        files.for_owner("profile").delete("shared", "doc", "staff", "person")
    assert files.for_owner("sap", ("profile",)).read("shared", "doc", "staff", "person") == b"shared"
    assert files.for_owner("profile").read("grant-only", "doc", "staff", "person") == b"grant"
    assert files.for_owner("sap", ("profile",)).read("consume-only", "doc", "staff", "person") == b"consume"


def test_typed_plugin_contributes_file_authorizer_for_a_dependency(monkeypatch: pytest.MonkeyPatch):
    from utsu_core import plugin_host

    provider = PluginManifest("provider", "1.0", 1, "provider:Plugin", services=("files",))
    consumer = PluginManifest("consumer", "1.0", 1, "consumer:Plugin", dependencies=("provider",),
                              services=("file_authorizers",))

    class Provider:
        def configure(self, registration):
            registration.register_file_kind("shared", FileKind(
                authorize=lambda *_: False, read=lambda *_: b"shared",
                authorizer_actions=frozenset({"read"}), dependent_actions=frozenset({"read"}),
            ))

        def router(self, services):
            router = APIRouter()

            @router.get("/shared-file")
            def read_file():
                return {"content": services.files.read("shared", "doc", "staff", "person").decode()}
            return router

    class Consumer:
        def configure(self, registration):
            registration.register_file_authorizer("shared", lambda _services: FileAuthorization(
                lambda action, actor, _resource, _reference: action == "read" and actor == "staff",
            ))

        def router(self, services):
            return APIRouter()

    Provider.manifest = provider
    Consumer.manifest = consumer
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {"provider": provider, "consumer": consumer})
    monkeypatch.setattr(plugin_host.importlib, "import_module",
                        lambda name: SimpleNamespace(Plugin=Provider if name == "provider" else Consumer))
    _, modules = load_modules("local", ("consumer", "provider"))
    app = build_app(Settings(secret="s" * 32), MemoryStore(), None, modules, seed=False, ui=False,
                    role_catalogue={}, permission_catalogue={}, role_permissions={})
    assert TestClient(app).get("/shared-file").json() == {"content": "shared"}


def test_sap_typed_manifest_keeps_banking_routes_core_owned():
    from utsu_core.plugin_host import PluginHostError

    manifest = discover_manifests()["sap"]
    assert manifest.interface == "plugin"
    assert "sap_banking" in manifest.services
    assert "file_authorizers" in manifest.services and "files" not in manifest.services
    assert manifest.collections == ("sap_config",)
    with pytest.raises(PluginHostError, match="restricted to sap"):
        parse_manifest({
            "id": "other",
            "version": "1.0",
            "core_api_major": 1,
            "factory": "other:Plugin",
            "interface": "plugin",
            "services": ["sap_banking"],
        }, "test")


def test_clubs_typed_manifest_and_receipt_capabilities_are_scoped():
    from clubs.module import ClubsModule
    from utsu_core.plugin_host import PluginHostError
    from utsu_core.plugin_runtime import _plugin_services

    manifests = discover_manifests()
    manifest = manifests["clubs"]
    assert ClubsModule.manifest == manifest
    assert {"club_receipts", "club_invitation_link"} <= set(manifest.services)
    assert manifest.collections == ("clubs", "receipts", "transactions")
    assert manifest.quota_domains == ("club",)

    for service in ("club_receipts", "club_invitation_link"):
        with pytest.raises(PluginHostError, match="restricted to clubs"):
            parse_manifest({
                "id": "other",
                "version": "1.0",
                "core_api_major": 1,
                "factory": "other:Plugin",
                "interface": "plugin",
                "services": [service],
            }, "test")

    app = create_app(Settings(secret="s" * 32, enabled_plugins=("clubs",), api_only=True))
    ctx = app.state.ctx
    receipt_service = _plugin_services(ctx, manifest).club_receipts
    assert receipt_service is not ctx.services["receipt_storage"]
    assert callable(receipt_service.create) and callable(receipt_service.read)
    assert not any(hasattr(receipt_service, attribute) for attribute in ("settings", "root", "crypter", "migrate", "archive"))


def test_core_only_profile_does_not_import_or_mount_programs():
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "src/core")))}
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from utsu_core.app import create_app; "
            "from utsu_core.settings import Settings; "
            "app = create_app(Settings(secret='s' * 32, enabled_plugins=(), api_only=True)); "
            "import sys; "
            "assert not any(name.startswith('utsu_core.programs.') for name in sys.modules); "
            "assert not any(name.split('.')[0] in {'agm', 'sabp', 'clubs', 'senate', 'chat', 'voting'} "
            "for name in sys.modules); "
            "paths = {route.path for route in app.routes if hasattr(route, 'path')}; "
            "assert not any(path.startswith(('/api/agm', '/api/senate', '/api/sap', '/api/clubs')) for path in paths); "
            "assert not app.state.ctx.store.list('form_versions')",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def test_disabled_form_owners_hide_routes_without_deleting_submissions():
    from utsu_core.app import create_app
    from utsu_core.store import MemoryStore

    store = MemoryStore()
    full = create_app(Settings(secret="s" * 32), store=store)
    full.state.ctx.forms.for_owner("agm").update_submission(
        "agm_rsvp", "student-1",
        lambda _: {"id": "agm_rsvp:student-1", "form_id": "agm_rsvp", "uuid": "student-1",
                   "status": "submitted", "data": {"attendance": "Online"}},
    )
    with pytest.raises(PermissionError, match="outside this forms service scope"):
        full.state.ctx.forms.for_owner("sap").submission("agm_rsvp", "student-1")
    core = create_app(Settings(secret="s" * 32, enabled_plugins=(), seed_forms=False), store=store)
    with TestClient(core) as client:
        assert client.get("/api/forms").json() == []
        assert client.get("/api/forms/agm_rsvp/static").status_code == 404
        assert client.post("/api/admin/notices/agm/preview", json={"uid": "student-1"}).status_code == 404
    assert store.get("submissions", "agm_rsvp:student-1") is not None
    with pytest.raises(PermissionError, match="outside this forms service scope"):
        core.state.ctx.forms.core_view().submission("agm_rsvp", "student-1")


@pytest.mark.parametrize("store_factory", [MemoryStore, SQLiteStore])
def test_typed_forms_paging_preserves_form_ownership(store_factory: Callable[[], Store]) -> None:
    from utsu_core.services.forms import FormRegistry, FormsRuntime

    store = store_factory()
    registry = FormRegistry({"student_aid_bursary": "sap", "senate_application": "senate"})
    for form_id, uid in (("student_aid_bursary", "a"), ("student_aid_bursary", "b"),
                         ("senate_application", "c")):
        key = f"{form_id}:{uid}"
        store.put("submissions", key, {"id": key, "form_id": form_id, "uuid": uid})
    forms = FormsRuntime(store, registry).for_owner("sap")
    order = (PageOrder("id", str),)

    def first_page(service: FormsService) -> list[dict[str, Any]]:
        return service.page_submissions("student_aid_bursary", order_by=order, after=None, limit=1)

    first = first_page(forms)
    second = forms.page_submissions("student_aid_bursary", order_by=order, after=(first[0]["id"],), limit=1)
    assert [first[0]["uuid"], second[0]["uuid"]] == ["a", "b"]
    with pytest.raises(PermissionError, match="outside this forms service scope"):
        forms.page_submissions("senate_application", order_by=order, after=None, limit=1)
    with pytest.raises(ValueError, match="controlled by the forms service"):
        forms.page_submissions("student_aid_bursary", order_by=order, after=None, limit=1,
                               filters={"form_id": "senate_application"})


def test_builtin_full_profile_preserves_order_and_form_ownership():
    manifests, modules = load_modules("local")
    assert [module.name for module in modules] == [
        "sso", "auth", "profile", "payments", "forms", "agm", "agm_verify", "senate", "sap",
        "messaging", "content", "notices", "admin", "clubs", "audit", "roles",
    ]
    assert {form_id for manifest in manifests for form_id in manifest.form_ids} == {
        "agm_rsvp", "senate_application", "student_aid_bursary",
    }


def test_disabled_and_missing_dependencies_are_rejected():
    manifests = discover_manifests()
    with pytest.raises(PluginHostError, match="requires disabled or missing"):
        select_manifests(manifests, "local", ("agm_verify",))
    with pytest.raises(PluginHostError, match="unknown enabled plug-in"):
        select_manifests(manifests, "local", ("missing",))
    with pytest.raises(PluginHostError, match="unavailable in remote"):
        select_manifests(manifests, "remote", ("auth",))


def test_manifest_api_compatibility_and_dependency_cycles_fail_explicitly():
    raw: dict[str, object] = {
        "id": "sample",
        "version": "1.0",
        "core_api_major": 2,
        "factory": "sample.plugin:Plugin",
    }
    with pytest.raises(PluginHostError, match="incompatible"):
        parse_manifest(raw, "test")

    manifests = {
        "a": PluginManifest("a", "1.0", 1, "sample:A", dependencies=("b",)),
        "b": PluginManifest("b", "1.0", 1, "sample:B", dependencies=("a",)),
    }
    with pytest.raises(PluginHostError, match="dependency cycle"):
        select_manifests(manifests, "local", ("a", "b"))


def test_manifest_permission_grants_use_oauth_scope_tokens_and_declared_roles():
    raw: dict[str, object] = {
        "id": "sample",
        "version": "1.0",
        "core_api_major": 1,
        "factory": "sample.plugin:Plugin",
        "roles": {"editor": "edit sample resources"},
        "permissions": {"utsu:sample:resource:edit": "Edit sample resources."},
        "role_permissions": {"editor": ["utsu:sample:resource:edit"]},
    }
    manifest = parse_manifest(raw, "test")
    assert manifest.permissions == (("utsu:sample:resource:edit", "Edit sample resources."),)
    assert manifest.role_permissions == (("editor", ("utsu:sample:resource:edit",)),)

    with pytest.raises(PluginHostError, match="OAuth scope tokens"):
        parse_manifest({**raw, "permissions": {'utsu:sample:"resource': "Invalid."}}, "test")
    with pytest.raises(PluginHostError, match="undeclared role"):
        parse_manifest({**raw, "role_permissions": {"reviewer": ["utsu:sample:resource:edit"]}}, "test")
    with pytest.raises(PluginHostError, match="undeclared permissions"):
        parse_manifest({**raw, "role_permissions": {"editor": ["utsu:sample:resource:delete"]}}, "test")


@pytest.mark.parametrize("setting", [
    "google_client_secret", "turnstile_secret_key", "stripe_secret_key", "stripe_webhook_secret",
])
def test_program_manifest_cannot_claim_core_secrets(setting: str) -> None:
    with pytest.raises(PluginHostError, match="platform secrets"):
        parse_manifest({"id": "sample", "version": "1.0", "core_api_major": 1, "factory": "sample:Plugin",
                        "settings": [setting], "services": ["settings"]}, "test")


def test_typed_store_requires_declared_exclusive_collections():
    raw: dict[str, object] = {
        "id": "sample",
        "version": "1.0",
        "core_api_major": 1,
        "factory": "sample.plugin:Plugin",
        "services": ["store"],
    }
    with pytest.raises(PluginHostError, match="declare collections"):
        parse_manifest(raw, "test")

    raw["collections"] = ["sample_records"]
    first = parse_manifest(raw, "first")
    second = PluginManifest(
        "other",
        "1.0",
        1,
        "other.plugin:Plugin",
        services=("store",),
        collections=("sample_records",),
    )
    with pytest.raises(PluginHostError, match="collection sample_records is declared by both"):
        select_manifests({"sample": first, "other": second}, "local", ("sample", "other"))

    with pytest.raises(PluginHostError, match="core collections are reached through core services: messages"):
        parse_manifest({**raw, "collections": ["messages"]}, "test")
    with pytest.raises(PluginHostError, match="unlinkable collections must be declared"):
        parse_manifest({**raw, "unlinkable": ["elsewhere"]}, "test")


def test_legacy_plugin_services_constructor_explicitly_lacks_identity():
    service = Mock()
    services = PluginServices(service, service, service, service, service, service, service, service, service,
                              service, service, lambda: 0, {}, {})
    with pytest.raises(RuntimeError, match="identity service is unavailable"):
        services.identity.person("user-1")
    with pytest.raises(RuntimeError, match="identity service is unavailable"):
        services.identity.account("user-1")
    with pytest.raises(RuntimeError, match="identity service is unavailable"):
        services.identity.resolve_contact("user-1")
    with pytest.raises(RuntimeError, match="quota service is unavailable"):
        services.quotas.usage("user", "user-1")


def test_typed_quota_service_requires_declared_domains_and_valid_resource(monkeypatch: pytest.MonkeyPatch):
    from utsu_core import plugin_host

    raw = {"id": "sample", "version": "1.0", "core_api_major": 1, "factory": "sample:Plugin",
           "services": ["auth", "quotas"], "quota_domains": ["user"]}
    manifest = parse_manifest(raw, "test")
    with pytest.raises(PluginHostError, match="quota domains"):
        parse_manifest({**raw, "quota_domains": []}, "test")
    with pytest.raises(PluginHostError, match="quota domains"):
        parse_manifest({**raw, "services": []}, "test")
    with pytest.raises(PluginHostError, match="quota domain"):
        parse_manifest({**raw, "quota_domains": ["user:other"]}, "test")

    quota_services = []

    class Sample:
        def configure(self, registration):
            pass

        def router(self, services):
            router = APIRouter()
            quota_services.append(services.quotas)

            @router.get("/quota")
            def quota(_account: Any = services.auth.require_verified()):
                return services.quotas.usage("user", "user-1")
            return router

    Sample.manifest = manifest
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {"sample": manifest})
    monkeypatch.setattr(plugin_host.importlib, "import_module", lambda _name: SimpleNamespace(Plugin=Sample))
    _, modules = load_modules("local", ("sample",))
    app = build_app(Settings(secret="s" * 32), MemoryStore(), None, modules, seed=False, ui=False,
                    role_catalogue={}, permission_catalogue={}, role_permissions={})
    with pytest.raises(PermissionError):
        quota_services[0].usage("club", "club-1")
    with pytest.raises(ValueError):
        quota_services[0].usage("user", "bad:identifier")
    app.state.ctx.store.put("accounts", "user-1", {
        "uuid": "user-1", "status": "verified", "email_verified": True, "role": "applicant", "roles": [],
    })
    token = app.state.ctx.identity.issue("user-1")
    assert TestClient(app).get("/quota", headers={"Authorization": f"Bearer {token}"}).json() == {
        "used_bytes": 0, "limit_bytes": 15_000_000, "remaining_bytes": 15_000_000, "reserved_files": 0,
    }


@pytest.mark.parametrize("collection", [
    "accounts", "users", "otps", "roster_name", "form_versions", "upload_quotas", "audit_2026_01",
])
def test_typed_store_cannot_claim_shared_core_collections(collection: str) -> None:
    with pytest.raises(PluginHostError, match="core collections are reached through core services"):
        parse_manifest({"id": "sample", "version": "1.0", "core_api_major": 1, "factory": "sample:Plugin",
                        "services": ["store"], "collections": [collection]}, "test")


@pytest.mark.parametrize("store_factory", [MemoryStore, SQLiteStore])
def test_typed_store_put_and_compare_and_swap_are_scoped(
    store_factory: Callable[[], Store], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from utsu_core import plugin_host

    inner = store_factory()
    stores: list[StoreService] = []
    manifest = PluginManifest("sample", "1.0", 1, "sample:Plugin", services=("store",),
                              collections=("sample_records",))

    class Sample:
        manifest: PluginManifest

        def configure(self, registration: RegistrationContext) -> None:
            pass

        def router(self, services: PluginServices) -> APIRouter:
            stores.append(services.store)
            return APIRouter()

    def import_sample(_name: str) -> SimpleNamespace:
        return SimpleNamespace(Plugin=Sample)

    Sample.manifest = manifest
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {manifest.id: manifest})
    monkeypatch.setattr(plugin_host.importlib, "import_module", import_sample)
    _, modules = load_modules("local", ("sample",))
    build_app(Settings(secret="s" * 32), inner, None, modules, seed=False, ui=False,
              role_catalogue={}, permission_catalogue={}, role_permissions={})
    store = stores[0]
    store.put("sample_records", "one", {"value": "initial"})
    assert store.replace("sample_records", "one", {"value": "updated"}, 0)
    assert not store.replace("sample_records", "one", {"value": "stale"}, 0)
    assert store.get("sample_records", "one") == {"value": "updated", "_rev": 1}
    assert not store.replace("sample_records", "missing", {"value": "new"}, 0)
    with pytest.raises(PermissionError, match="not available"):
        store.put("other_records", "one", {"value": "forbidden"})
    with pytest.raises(PermissionError, match="not available"):
        store.replace("other_records", "one", {"value": "forbidden"}, 0)


def test_typed_auth_dependencies_return_only_account_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    from utsu_core import plugin_host
    from utsu_core.identity import Identity

    manifest = PluginManifest("sample", "1.0", 1, "sample:Plugin", services=("auth", "identity", "messaging"))
    thread_accounts: list[dict[str, Any]] = []

    class Sample:
        manifest: PluginManifest

        def configure(self, registration: RegistrationContext) -> None:
            def authorize(account: Any, record_id: str) -> ThreadAccess:
                thread_accounts.append(dict(account))
                return ThreadAccess()
            registration.register_thread_type("sample-thread", ThreadType(authorize=authorize))

        def router(self, services: PluginServices) -> APIRouter:
            router = APIRouter()

            @router.get("/user")
            def user(account: Any = services.auth.require_user()) -> Any:
                return account

            @router.get("/verified")
            def verified(account: Any = services.auth.require_verified()) -> Any:
                return account

            @router.get("/rostered")
            def rostered(account: Any = services.auth.require_rostered()) -> Any:
                return account

            @router.get("/role")
            def role(account: Any = services.auth.require_role("reviewer")) -> Any:
                return account

            @router.get("/permission")
            def permission(account: Any = services.auth.require_permission("utsu:sample:review")) -> Any:
                return account

            @router.get("/identity/{uuid}")
            def identity(uuid: str, account: Any = services.auth.require_verified()) -> Any:
                return {"account": services.identity.account(uuid), "person": services.identity.person(uuid),
                        "roster": services.identity.roster(uuid)}

            @router.get("/contact/{username}")
            def contact(username: str, account: Any = services.auth.require_verified()) -> Any:
                return services.identity.resolve_contact(username)

            return router

    class SampleIdentity(Identity):
        def authenticate(self, raw: str) -> dict[str, Any] | None:
            if raw not in ("valid", "pending"):
                return None
            return {"uuid": "user-1", "status": "verified" if raw == "valid" else "pending",
                    "role": "applicant", "roles": ["reviewer"],
                    "email_verified": True, "recovery_email": "private@example.test", "recovery_version": "secret"}

        def roster_check(self, uuid: str) -> dict[str, Any] | None:
            return ({"rostered": True, "division": "UC", "reason": "match", "private": "secret"}
                    if uuid == "user-1" else None)

        def account(self, uuid: str) -> dict[str, Any] | None:
            return ({"uuid": uuid, "status": "verified", "role": "applicant", "roles": ["reviewer"],
                     "email_verified": True, "recovery_version": "secret"} if uuid == "user-1" else None)

        def user(self, uuid: str) -> dict[str, Any] | None:
            return {"first_name": "Ada", "last_name": "Lovelace", "email": "private@example.test"} if uuid == "user-1" else None

        def resolve(self, username: str) -> dict[str, Any] | None:
            if username == "no-email":
                return {"uuid": "user-2", "account": {"status": "pending"}, "user": None}
            if username != "ada":
                return None
            return {"uuid": "user-1", "account": {"status": "verified", "email_verified": True,
                                                    "recovery_version": "secret"},
                    "user": {"email": "private@example.test", "private": "secret"}}

    def import_sample(_name: str) -> SimpleNamespace:
        return SimpleNamespace(Plugin=Sample)

    Sample.manifest = manifest
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {manifest.id: manifest})
    monkeypatch.setattr(plugin_host.importlib, "import_module", import_sample)
    _, modules = load_modules("local", ("sample",))
    app = build_app(Settings(secret="s" * 32), MemoryStore(), None, modules, identity=SampleIdentity(),
                    seed=False, ui=False, role_catalogue={"reviewer": "review"},
                    permission_catalogue={"utsu:sample:review": "review"},
                    role_permissions={"reviewer": frozenset({"utsu:sample:review"})})
    with TestClient(app) as client:
        for path in ("/user", "/verified", "/rostered", "/role", "/permission"):
            assert client.get(path).status_code == 401
            response = client.get(path, headers={"Authorization": "Bearer valid"})
            assert response.status_code == 200
            assert response.json() == {"uuid": "user-1", "status": "verified", "role": "applicant",
                                       "roles": ["reviewer"], "email_verified": True}
        assert client.get("/identity/user-1", headers={"Authorization": "Bearer valid"}).json() == {
            "account": {"uuid": "user-1", "status": "verified", "role": "applicant", "roles": ["reviewer"],
                        "email_verified": True},
            "person": {"status": "verified", "first_name": "Ada", "last_name": "Lovelace"},
            "roster": {"rostered": True, "division": "UC", "reason": "match"},
        }
        assert client.get("/identity/missing", headers={"Authorization": "Bearer valid"}).json() == {
            "account": None, "person": None, "roster": None,
        }
        assert client.get("/contact/ada", headers={"Authorization": "Bearer valid"}).json() == {
            "uuid": "user-1", "status": "verified", "email_verified": True, "email": "private@example.test",
        }
        assert client.get("/contact/no-email", headers={"Authorization": "Bearer valid"}).json() == {
            "uuid": "user-2", "status": "pending", "email_verified": False, "email": None,
        }
        assert client.get("/contact/missing", headers={"Authorization": "Bearer valid"}).json() is None
        assert client.get("/user", headers={"Authorization": "Bearer pending"}).status_code == 200
        assert client.get("/verified", headers={"Authorization": "Bearer pending"}).status_code == 403
    account = SampleIdentity().authenticate("valid")
    assert account is not None
    app.state.ctx.messaging.resolve("sample-thread", "record-1", account)
    assert thread_accounts == [{"uuid": "user-1", "status": "verified", "role": "applicant",
                                "roles": ["reviewer"], "email_verified": True}]


def test_opt_in_plugins_stay_off_by_default_and_provided_services_follow_dependencies(monkeypatch: pytest.MonkeyPatch):
    from utsu_core import plugin_host

    manifests = discover_manifests()
    assert {"discussion", "voting"}.isdisjoint(m.id for m in select_manifests(manifests, "local", None))
    assert [m.id for m in select_manifests(manifests, "local", ("voting", "discussion"))][-2:] == ["discussion", "voting"]

    manifest = PluginManifest("nosy", "1.0", 1, "nosy_sample:Plugin", services=("provided",))

    class Nosy:
        def configure(self, registration):
            registration.require("voting")

        def router(self, services):
            return APIRouter()

    Nosy.manifest = manifest
    real_import = plugin_host.importlib.import_module
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {**manifests, manifest.id: manifest})
    monkeypatch.setattr(plugin_host.importlib, "import_module",
                        lambda name: SimpleNamespace(Plugin=Nosy) if name == "nosy_sample" else real_import(name))
    with pytest.raises(RuntimeError, match="module nosy configure failed") as failure:
        create_app(Settings(secret="s" * 32, enabled_plugins=("discussion", "voting", "nosy")), MemoryStore())
    assert "not available from a declared dependency" in str(failure.value.__cause__)


def test_typed_plugins_must_use_forms_service_for_shared_submissions():
    raw: dict[str, object] = {
        "id": "sample",
        "version": "1.0",
        "core_api_major": 1,
        "factory": "sample.plugin:Plugin",
        "services": ["store"],
        "collections": ["submissions"],
    }
    with pytest.raises(PluginHostError, match="owner-scoped forms service"):
        parse_manifest(raw, "test")


def test_manifest_protection_must_be_covered_by_retained_catalogue():
    raw: dict[str, object] = {
        "id": "sample",
        "version": "1.0",
        "core_api_major": 1,
        "factory": "sample.plugin:Plugin",
        "protection": {"sap_banking": ["pending"]},
        "json_protection": {"sap_banking": ["pending"]},
    }
    manifest = parse_manifest(raw, "test")
    assert manifest.protection == (("sap_banking", ("pending",)),)
    assert manifest.json_protection == (("sap_banking", ("pending",)),)

    raw["protection"] = {"sap_banking": ["new_unprotected_field"]}
    with pytest.raises(PluginHostError, match="retained protection is missing"):
        parse_manifest(raw, "test")

    raw["protection"] = {"sap_banking": ["account_number"]}
    with pytest.raises(PluginHostError, match="must also be protected"):
        parse_manifest(raw, "test")


def test_program_financial_audit_declarations_require_retained_policy():
    raw: dict[str, object] = {
        "id": "sample", "version": "1.0", "core_api_major": 1, "factory": "sample:Plugin",
        "services": ["forms"], "form_ids": ["student_aid_bursary"],
        "financial_access": {"collections": ["sap_banking"], "submission_forms": ["student_aid_bursary"]},
    }
    manifest = parse_manifest(raw, "test")
    assert manifest.audit_collections == ("sap_banking",)
    assert manifest.audit_forms == ("student_aid_bursary",)
    with pytest.raises(PluginHostError, match="retained financial access policy"):
        parse_manifest({**raw, "financial_access": {"collections": ["unknown_records"]}}, "test")
    with pytest.raises(PluginHostError, match="undeclared form"):
        parse_manifest({**raw, "form_ids": [], "financial_access": {
            "submission_forms": ["student_aid_bursary"],
        }}, "test")


def test_disabled_program_keeps_legacy_data_encryption():
    from utsu_core.crypto import Crypter, EncryptingStore
    from utsu_core.store import MemoryStore

    manifests = discover_manifests()
    selected = select_manifests(manifests, "local", ())
    assert "sap" not in {manifest.id for manifest in selected}

    inner = MemoryStore()
    crypter = Crypter(None, "s" * 40)
    legacy_ciphertext = crypter.enc("123456789")
    inner.put("sap_banking", "account", {"account_number": legacy_ciphertext})
    protected = EncryptingStore(inner, crypter)
    assert protected.get("sap_banking", "account")["account_number"] == "123456789"
    protected.put("sap_banking", "account", {"account_number": "987654321"})
    assert inner.get("sap_banking", "account")["account_number"].startswith("enc:v1:")
    assert protected.get("sap_banking", "account")["account_number"] == "987654321"


def test_disabled_programs_keep_financial_access_audits():
    app = create_app(Settings(secret="s" * 32, enabled_plugins=(), api_only=True, seed_forms=False),
                     store=MemoryStore())
    ctx = app.state.ctx
    ctx.store.get("receipts", "missing")
    ctx.store.get("submissions", "student_aid_bursary:missing")
    targets = {entry["target"] for entry in ctx.audit.entries() if entry["action"] == "financial.access"}
    assert {"receipts:missing", "submissions:student_aid_bursary:missing"} <= targets


def test_settings_are_scoped_to_selected_plugins():
    from utsu_core.app import create_app

    clubs = create_app(Settings(secret="s" * 32, enabled_plugins=("clubs",), api_only=True,
                                max_receipt_bytes=1234))
    ctx = clubs.state.ctx
    assert ctx.plugin_setting("clubs", "max_receipt_bytes") == 1234
    with pytest.raises(RuntimeError, match="not registered for this composition"):
        ctx.plugin_setting("sap", "max_cheque_bytes")

    core = create_app(Settings(secret="s" * 32, enabled_plugins=(), api_only=True))
    assert "clubs" not in core.state.ctx.plugin_settings

    legacy = build_app(
        Settings(secret="s" * 32, max_image_bytes=1234),
        None,
        None,
        (),
        seed=False,
        ui=False,
    )
    assert legacy.state.ctx.plugin_setting("legacy", "max_image_bytes") == 1234


def test_external_manifest_discovery_does_not_import_its_factory(tmp_path: Path):
    manifest_path = tmp_path / "utsu-plugin.json"
    manifest_path.write_text(
        '{"schema_version":1,"plugin":{"id":"external-sample","version":"1.0","core_api_major":1,'
        '"factory":"missing_external_package.plugin:Plugin"}}',
        encoding="utf-8",
    )
    def locate_file(_path: Path) -> Path:
        return manifest_path

    distribution = SimpleNamespace(
        files=[Path("utsu-plugin.json")],
        locate_file=locate_file,
    )
    entry_point = cast(metadata.EntryPoint, SimpleNamespace(
        name="external-sample",
        value="missing_external_package.plugin:Plugin",
        dist=distribution,
    ))
    manifests = discover_manifests([entry_point])
    assert manifests["external-sample"].id == "external-sample"
    assert manifests["external-sample"].interface == "plugin"
    assert "missing_external_package.plugin" not in sys.modules


def test_distribution_can_declare_multiple_named_plugins(tmp_path: Path):
    entries = []
    files = []
    for identifier in ("external-first", "external-second"):
        filename = f"{identifier}.utsu-plugin.json"
        factory = f"missing_external_package.{identifier.replace('-', '_')}:Plugin"
        (tmp_path / filename).write_text(
            json.dumps({"schema_version": 1, "plugin": {
                "id": identifier, "version": "1.0", "core_api_major": 1, "factory": factory,
            }}), encoding="utf-8",
        )
        files.append(Path(filename))
        entries.append((identifier, factory))
    distribution = SimpleNamespace(files=files, locate_file=lambda path: tmp_path / path)
    entry_points = [
        cast(metadata.EntryPoint, SimpleNamespace(name=name, value=factory, dist=distribution))
        for name, factory in entries
    ]

    manifests = discover_manifests(entry_points)
    assert {manifests[name].factory for name, _ in entries} == {factory for _, factory in entries}
    assert "missing_external_package" not in sys.modules

    distribution.files.remove(Path("external-second.utsu-plugin.json"))
    with pytest.raises(PluginHostError, match="exactly one matching"):
        discover_manifests(entry_points)
    distribution.files.append(Path("external-second.utsu-plugin.json"))
    distribution.files.append(Path("utsu-plugin.json"))
    with pytest.raises(PluginHostError, match="mixes named and generic"):
        discover_manifests(entry_points)


def test_named_manifests_are_discovered_from_distribution_metadata(tmp_path: Path):
    dist_info = tmp_path / "example_plugins-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: example-plugins\nVersion: 1.0\n", encoding="utf-8")
    (dist_info / "entry_points.txt").write_text(
        "[utsu.portal.plugins]\nexternal-alpha = example.alpha:Plugin\n"
        "external-beta = example.beta:Plugin\n", encoding="utf-8",
    )
    names = ("external-alpha", "external-beta")
    for name in names:
        factory = f"example.{name.removeprefix('external-')}:Plugin"
        (tmp_path / f"{name}.utsu-plugin.json").write_text(json.dumps({
            "schema_version": 1,
            "plugin": {"id": name, "version": "1.0", "core_api_major": 1, "factory": factory},
        }), encoding="utf-8")
    (dist_info / "RECORD").write_text(
        "".join(f"{name}.utsu-plugin.json,,\n" for name in names), encoding="utf-8",
    )
    distribution = next(metadata.distributions(path=[str(tmp_path)]))

    manifests = discover_manifests(distribution.entry_points)
    assert {manifests[name].factory for name in names} == {"example.alpha:Plugin", "example.beta:Plugin"}
    assert "example" not in sys.modules


def test_program_owned_configuration_is_scoped_and_validated():
    from utsu_core.settings import plugin_settings_for

    def manifest(name: str) -> PluginManifest:
        return parse_manifest({"id": name, "version": "1.0", "core_api_major": 1, "factory": "sample:Plugin",
                               "services": ["settings"], "config": ["limit"]}, "test")

    first, second = manifest("first"), manifest("second")
    settings = Settings(secret="s" * 32, plugin_config={"first": {"limit": 1}, "second": {"limit": 2}})
    values = plugin_settings_for((first, second), settings)
    assert values["first"] == {"limit": 1}
    assert values["second"] == {"limit": 2}
    assert plugin_settings_for((first,), settings) == {"first": {"limit": 1}}
    with pytest.raises(RuntimeError, match="undeclared"):
        plugin_settings_for((first,), Settings(secret="s" * 32, plugin_config={"first": {"other": 1}}))
    with pytest.raises(PluginHostError, match="settings service"):
        parse_manifest({"id": "sample", "version": "1.0", "core_api_major": 1, "factory": "sample:Plugin",
                        "config": ["limit"]}, "test")


def test_program_configuration_environment_and_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from utsu_core.settings import plugin_settings_for

    config = tmp_path / "plugin-config.json"
    config.write_text('{"sample":{"limit":5}}', encoding="utf-8")
    monkeypatch.setenv("PORTAL_SECRET", "s" * 32)
    monkeypatch.setenv("PORTAL_PLUGIN_CONFIG", '{"sample":{"limit":2}}')
    monkeypatch.setenv("PORTAL_PLUGIN_CONFIG_FILE", str(config))
    manifest = parse_manifest({"id": "sample", "version": "1.0", "core_api_major": 1, "factory": "sample:Plugin",
                               "services": ["settings"], "config": ["limit"]}, "test")
    assert plugin_settings_for((manifest,), Settings.from_env())["sample"]["limit"] == 5
    config.write_text("invalid", encoding="utf-8")
    with pytest.raises(RuntimeError, match="plug-in configuration"):
        Settings.from_env()
    config.write_text('{"sample":1}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="object of program objects"):
        Settings.from_env()


def test_typed_plugin_gets_scoped_core_services_and_file_registration(monkeypatch: pytest.MonkeyPatch):
    from utsu_core import plugin_host
    from utsu_core.settings import plugin_settings_for

    manifest = PluginManifest(
        "typed-sample",
        "1.0",
        1,
        "typed_sample:Plugin",
        interface="plugin",
        settings=("max_image_bytes",),
        config=("custom_limit",),
        services=("files", "now", "settings", "store"),
        collections=("sample_records",),
    )
    created = []

    class TypedSample:
        def configure(self, registration):
            def file_kind(services):
                def create(reference, resource, actor, content):
                    created.append((reference, resource, actor, content))
                    services.store.put("sample_records", reference, {"content": content.decode()})

                return FileKind(
                    authorize=lambda action, actor, _resource, _reference: action in {"read", "create"} and actor == "staff",
                    read=lambda _reference, _resource: b"sample",
                    create=create,
                )
            registration.register_file_kind("sample-document", file_kind)
            registration.register_file_kind("static-document", FileKind(
                authorize=lambda action, actor, _resource, _reference: action == "read" and actor == "staff",
                read=lambda _reference, _resource: b"static",
            ))

        def router(self, services):
            router = APIRouter()

            @router.get("/typed-sample")
            def sample():
                data = services.files.read("sample-document", "doc-1", "staff", "user-1")
                static = services.files.read("static-document", "doc-1", "staff", "user-1")
                services.files.create("sample-document", "doc-2", "staff", "user-1", b"upload")
                uploaded = services.store.get("sample_records", "doc-2")
                services.store.create("sample_records", "sample-1", {"value": "stored"})
                scoped = services.store.get("sample_records", "sample-1")
                try:
                    services.store.get("accounts", "account-1")
                except PermissionError:
                    storage_is_scoped = True
                else:
                    storage_is_scoped = False
                return {"content": data.decode(), "static": static.decode(), "limit": services.settings["max_image_bytes"],
                        "custom_limit": services.settings["custom_limit"], "time": services.now(),
                        "stored": scoped["value"], "uploaded": uploaded["content"],
                        "storage_is_scoped": storage_is_scoped}

            return router

    TypedSample.manifest = manifest
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {manifest.id: manifest})
    monkeypatch.setattr(plugin_host.importlib, "import_module", lambda _name: SimpleNamespace(Plugin=TypedSample))
    settings = Settings(secret="s" * 32, max_image_bytes=2048,
                        plugin_config={"typed-sample": {"custom_limit": 17}})
    selected, modules = load_modules("local", ("typed-sample",))
    assert [item.id for item in selected] == ["typed-sample"]

    app = build_app(
        settings,
        None,
        None,
        modules,
        seed=False,
        ui=False,
        role_catalogue={},
        plugin_settings=plugin_settings_for(selected, settings),
    )
    with TestClient(app) as client:
        response = client.get("/typed-sample")
    assert response.status_code == 200
    assert response.json()["content"] == "sample"
    assert response.json()["static"] == "static"
    assert response.json()["limit"] == 2048
    assert response.json()["custom_limit"] == 17
    assert response.json()["time"] > 0
    assert response.json()["stored"] == "stored"
    assert response.json()["uploaded"] == "upload"
    assert response.json()["storage_is_scoped"] is True
    assert created == [("doc-2", "user-1", "staff", b"upload")]
    with pytest.raises(PermissionError, match="not available"):
        app.state.ctx.files.for_owner("other").read("sample-document", "doc-1", "staff", "user-1")


def test_typed_plugin_requiring_unavailable_service_fails_before_import(monkeypatch: pytest.MonkeyPatch):
    from utsu_core import plugin_host

    manifest = PluginManifest(
        "typed-sample",
        "1.0",
        1,
        "missing_plugin:Plugin",
        interface="plugin",
        services=("notifications",),
    )
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {manifest.id: manifest})
    monkeypatch.setattr(plugin_host.importlib, "import_module", lambda _name: pytest.fail("factory imported"))

    with pytest.raises(PluginHostError, match="requires unavailable core services: notifications"):
        load_modules("local", ("typed-sample",))


def test_registration_context_rejects_duplicates_and_freezes():
    registrations = RegistrationContext("sample")
    registrations.register_form("sample_form", {"version": 1})
    with pytest.raises(ValueError, match="duplicate form registration"):
        registrations.register_form("sample_form", {"version": 2})
    frozen = registrations.freeze()
    with pytest.raises(TypeError):
        cast(Any, frozen)[("form", "other")] = {}
    with pytest.raises(RuntimeError, match="frozen"):
        registrations.register_form("other", {})


def test_duplicate_routes_are_rejected_before_startup():
    class Duplicate:
        def __init__(self, name: str):
            self.name = name

        def router(self, ctx: Context):
            router = APIRouter()

            @router.get("/duplicate")
            def duplicate() -> dict[str, bool]:
                return {}

            return router

    with pytest.raises(RuntimeError, match="duplicate route GET /duplicate"):
        build_app(
            Settings(secret="s" * 32),
            None,
            None,
            (Duplicate("one"), Duplicate("two")),
            seed=False,
            ui=False,
        )

    with pytest.raises(RuntimeError, match="duplicate route GET /healthz"):
        class HealthRoute:
            name = "health-route"

            def router(self, ctx: Context):
                router = APIRouter()

                @router.get("/healthz")
                def healthz():
                    return {"ok": True}

                return router

        build_app(
            Settings(secret="s" * 32),
            None,
            None,
            (HealthRoute(),),
            seed=False,
            ui=False,
        )


def test_lifecycle_startup_failure_closes_started_modules():
    events = []

    class Lifecycle:
        def __init__(self, name: str, fail: bool = False):
            self.name = name
            self.fail = fail

        def router(self, ctx: Context):
            return APIRouter()

        def startup(self, ctx: Context):
            events.append(f"start:{self.name}")
            if self.fail:
                raise ValueError("startup failed")

        def shutdown(self, ctx: Context):
            events.append(f"stop:{self.name}")

    app = build_app(
        Settings(secret="s" * 32),
        None,
        None,
        (Lifecycle("first"), Lifecycle("second", fail=True)),
        seed=False,
        ui=False,
    )
    with pytest.raises(RuntimeError, match="module second startup failed"):
        with TestClient(app):
            pass
    assert events == ["start:first", "start:second", "stop:first"]


def test_enabled_plugin_environment_parses_empty_and_lists(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PORTAL_SECRET", "s" * 32)
    monkeypatch.setenv("PORTAL_ENABLED_PLUGINS", " agm, clubs ,,sap ")
    assert Settings.from_env().enabled_plugins == ("agm", "clubs", "sap")
    monkeypatch.setenv("PORTAL_ENABLED_PLUGINS", "")
    assert Settings.from_env().enabled_plugins == ()
