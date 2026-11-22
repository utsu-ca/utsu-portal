import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from utsu_core import create_app
from utsu_core.bootstrap import build_app
from utsu_core.context import Context
from utsu_core.contracts import FileKind, PluginManifest, RegistrationContext
from utsu_core.plugin_host import (
    PluginHostError,
    discover_manifests,
    load_modules,
    parse_manifest,
    select_manifests,
)
from utsu_core.settings import Settings
from utsu_core.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]


def test_core_only_profile_does_not_import_or_mount_programs():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from utsu_core.app import create_app; "
            "from utsu_core.settings import Settings; "
            "app = create_app(Settings(secret='s' * 32, enabled_plugins=(), api_only=True)); "
            "import sys; "
            "assert not any(name.startswith('utsu_core.programs.') for name in sys.modules); "
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


def test_typed_plugin_gets_scoped_core_services_and_file_registration(monkeypatch: pytest.MonkeyPatch):
    from utsu_core import plugin_host

    manifest = PluginManifest(
        "typed-sample",
        "1.0",
        1,
        "typed_sample:Plugin",
        interface="plugin",
        settings=("max_image_bytes",),
        services=("files", "now", "settings", "store"),
        collections=("sample_records",),
    )

    class TypedSample:
        def configure(self, registration):
            registration.register_file_kind(
                "sample-document",
                FileKind(
                    authorize=lambda action, actor, _resource, _reference: action == "read" and actor == "staff",
                    read=lambda _reference, _resource: b"sample",
                ),
            )

        def router(self, services):
            router = APIRouter()

            @router.get("/typed-sample")
            def sample():
                data = services.files.read("sample-document", "doc-1", "staff", "user-1")
                services.store.create("sample_records", "sample-1", {"value": "stored"})
                scoped = services.store.get("sample_records", "sample-1")
                try:
                    services.store.get("accounts", "account-1")
                except PermissionError:
                    storage_is_scoped = True
                else:
                    storage_is_scoped = False
                return {"content": data.decode(), "limit": services.settings["max_image_bytes"],
                        "time": services.now(), "stored": scoped["value"], "storage_is_scoped": storage_is_scoped}

            return router

    TypedSample.manifest = manifest
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {manifest.id: manifest})
    monkeypatch.setattr(plugin_host.importlib, "import_module", lambda _name: SimpleNamespace(Plugin=TypedSample))
    settings = Settings(secret="s" * 32)
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
        plugin_settings={"typed-sample": {"max_image_bytes": 2048}},
    )
    with TestClient(app) as client:
        response = client.get("/typed-sample")
    assert response.status_code == 200
    assert response.json()["content"] == "sample"
    assert response.json()["limit"] == 2048
    assert response.json()["time"] > 0
    assert response.json()["stored"] == "stored"
    assert response.json()["storage_is_scoped"] is True
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
