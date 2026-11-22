import importlib.util
import json
import os
import subprocess
import sys
import sysconfig
import tomllib
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_split_distributions", ROOT / "tools/build_split_distributions.py")
assert spec is not None and spec.loader is not None
build_split_distributions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_split_distributions)


def make_frontend(directory, text, *, full=False):
    assets = directory / "assets"
    assets.mkdir(parents=True)
    (assets / "bundle.js").write_text("", encoding="utf-8")
    (assets / "bundle.css").write_text("", encoding="utf-8")
    html = (
        '<script src="/static/assets/bundle.js"></script>'
        f'<link href="/static/assets/bundle.css"><body>{text}</body>'
    )
    pages = ("index.html", "sso.html", "checkin.html", "finance.html") if full else ("index.html", "sso.html")
    for page in pages:
        (directory / page).write_text(html, encoding="utf-8")


def test_split_cli_rejects_unknown_flag(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/build_split_distributions.py"),
         "--experimental", "--version", "999999.0", "--out-dir", str(tmp_path),
         "--core-static-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr
    assert not list(tmp_path.iterdir())


def test_split_build_rejects_legacy_program_before_building(tmp_path, monkeypatch):
    root = tmp_path / "source"
    manifest = root / "src/core/plugin_manifests/builtin.json"
    manifest.parent.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nversion = "1.0"\n', encoding="utf-8")
    manifest.write_text(json.dumps({"plugins": [{"id": "example", "kind": "program"}]}), encoding="utf-8")
    monkeypatch.setattr(build_split_distributions, "ROOT", root)
    monkeypatch.setattr(build_split_distributions, "PROGRAMS", {"example": ("example",)})

    output = tmp_path / "wheels"
    with pytest.raises(ValueError, match="example must use the typed plug-in interface"):
        build_split_distributions.build("2.0", output, tmp_path)
    assert not list(output.iterdir())


def test_split_build_rejects_full_frontend_as_core_bundle(tmp_path):
    with pytest.raises(ValueError, match="must not be the full portal bundle"):
        build_split_distributions.build("999999.0", tmp_path, ROOT / "src/core/static")
    assert not list(tmp_path.iterdir())


def test_split_build_rejects_missing_frontend_assets(tmp_path):
    core_static = tmp_path / "core-static"
    make_frontend(core_static, "core")
    (core_static / "assets/bundle.js").unlink()
    with pytest.raises(ValueError, match="missing or invalid asset"):
        build_split_distributions.build("999999.0", tmp_path / "wheels", core_static)
    assert not list((tmp_path / "wheels").iterdir())


def test_split_build_rejects_overlapping_wheels_without_publishing(tmp_path, monkeypatch):
    root = tmp_path / "source"
    manifest = root / "src/core/plugin_manifests/builtin.json"
    manifest.parent.mkdir(parents=True)
    (root / "src/example").mkdir()
    (root / "src/branding").mkdir()
    full_static = root / "src/core/static"
    make_frontend(full_static, "full", full=True)
    core_static = tmp_path / "core-static"
    make_frontend(core_static, "core")
    (root / "LICENSE").write_text("license", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nversion = "1.0"\nrequires-python = ">=3.12"\ndependencies = []\n', encoding="utf-8"
    )
    manifest.write_text(json.dumps({"plugins": [{
        "id": "example", "kind": "program", "interface": "plugin", "factory": "example:create"
    }]}), encoding="utf-8")
    monkeypatch.setattr(build_split_distributions, "ROOT", root)
    monkeypatch.setattr(build_split_distributions, "PROGRAMS", {"example": ("example",)})
    calls = []

    def fake_wheel(command, *, check):
        assert check
        wheelhouse = Path(command[command.index("--wheel-dir") + 1])
        artifact = wheelhouse / f"wheel{len(calls)}.whl"
        with ZipFile(artifact, "w") as archive:
            archive.writestr("shared/file.py", "")
        calls.append(artifact)

    monkeypatch.setattr(build_split_distributions.subprocess, "run", fake_wheel)
    output = tmp_path / "wheels"
    with pytest.raises(RuntimeError, match="owned by both"):
        build_split_distributions.build("2.0", output, core_static)
    assert not list(output.iterdir())


def test_split_wheels_have_separate_packages_and_discoverable_manifests(tmp_path):
    core_static = tmp_path / "core-static"
    make_frontend(core_static, "core-only frontend")
    build_split_distributions.build("999999.0", tmp_path, core_static)
    artifacts = list(tmp_path.glob("*.whl"))
    assert len(artifacts) == len(build_split_distributions.PROGRAMS) + 4
    owners = set()
    for artifact in artifacts:
        with ZipFile(artifact) as wheel:
            files = {name for name in wheel.namelist() if not name.endswith("/")}
            assert not files.intersection(owners)
            owners.update(files)
            assert any(name.endswith((".dist-info/LICENSE", ".dist-info/licenses/LICENSE")) for name in files)
            metadata_file = next(name for name in files if name.endswith(".dist-info/METADATA"))
            metadata = Parser().parsestr(wheel.read(metadata_file).decode())
            if metadata["Name"] == "utsu-core":
                catalogue = json.loads(wheel.read("core/plugin_manifests/builtin.json"))
                assert not any(item.get("kind") == "program" for item in catalogue["plugins"])
                assert b"core-only frontend" in wheel.read("core/static/index.html")
                assert "core/static/checkin.html" not in files
            elif metadata["Name"] == "utsu-portal":
                assert all(".dist-info/" in name for name in files)
                requirements = metadata.get_all("Requires-Dist")
                assert requirements is not None
                assert "utsu-program-voting==999999.0" in requirements
                assert "utsu-portal-frontend==999999.0" in requirements
            elif metadata["Name"] == "utsu-portal-frontend":
                assert "utsu_portal_frontend/static/index.html" in files
                assert "utsu_portal_frontend/static/checkin.html" in files
            elif metadata["Name"].startswith("utsu-program-"):
                package = metadata["Name"].removeprefix("utsu-program-")
                for identifier in build_split_distributions.PROGRAMS[package]:
                    manifest = json.loads(wheel.read(f"{package}/{identifier}.utsu-plugin.json"))
                    assert manifest["plugin"]["id"] == identifier
                    assert manifest["plugin"]["interface"] == "plugin"
                frontend = ROOT / "src" / package / "frontend"
                assert (frontend / "portal-frontend.json").is_file()
                for source in frontend.rglob("*"):
                    if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                        assert f"{package}/{source.relative_to(frontend.parent).as_posix()}" in files

    installed = tmp_path / "installed"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(installed),
         *(str(artifact) for artifact in artifacts)],
        check=True,
    )
    script = """
import sys
from pathlib import Path
sys.path[:0] = sys.argv[1:3]
import core
assert Path(core.__file__).is_relative_to(Path(sys.argv[1]))
from core import create_app
from core.settings import Settings
from core.plugin_host import discover_manifests
from fastapi.testclient import TestClient
assert {'agm', 'agm_verify', 'sap', 'clubs', 'senate', 'discussion', 'voting'} <= discover_manifests().keys()
create_app(Settings(secret='s' * 32, api_only=True))
create_app(Settings(secret='s' * 32, enabled_plugins=(), api_only=True))
assert 'id="app"' in TestClient(create_app(Settings(secret='s' * 32))).get('/').text
"""
    subprocess.run(
        [sys.executable, "-S", "-c", script, str(installed), sysconfig.get_path("purelib")],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, check=True,
    )

    core_only = tmp_path / "core-only"
    branding = tmp_path / "branding"
    branding.mkdir()
    (branding / "theme.json").write_text("{}", encoding="utf-8")
    core_wheel = next(artifact for artifact in artifacts if artifact.name.startswith("core-"))
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(core_only),
         str(core_wheel)],
        check=True,
    )
    retained_script = """
import sys
from pathlib import Path
sys.path[:0] = sys.argv[1:3]
import core
assert Path(core.__file__).is_relative_to(Path(sys.argv[1]))
from core import create_app
from core.crypto import Crypter
from core.settings import Settings
from core.store import MemoryStore
from fastapi.testclient import TestClient
store = MemoryStore()
secret = 's' * 32
crypter = Crypter(None, secret)
store.put('financial_profiles', 'account', {'uuid': 'account', 'account_number': crypter.enc('123456789')})
store.put('clubs', 'retained', {'id': 'retained', 'registered_years': [2025]})
store.put('receipts', 'receipt', {
    'id': 'receipt', 'club_id': 'retained', 'data_url': crypter.enc('data:application/pdf;base64,YQ==')
})
app = create_app(Settings(secret=secret, enabled_plugins=(), api_only=True, seed_forms=False), store=store)
ctx = app.state.ctx
assert ctx.store.get('financial_profiles', 'account')['account_number'] == '123456789'
assert ctx.upload_quotas.usage('club:retained')['used_bytes'] == 1
before = sum(entry['action'] == 'financial.access' and entry['target'] == 'receipts:missing'
             for entry in ctx.audit.entries())
ctx.store.get('receipts', 'missing')
after = sum(entry['action'] == 'financial.access' and entry['target'] == 'receipts:missing'
            for entry in ctx.audit.entries())
assert after == before + 1
assert not any(name.split('.')[0] in {'agm', 'sabp', 'clubs', 'senate', 'chat', 'voting', 'branding'}
               for name in sys.modules)
ui = create_app(Settings(secret=secret, enabled_plugins=(), branding_dir=sys.argv[3]))
assert 'core-only frontend' in TestClient(ui).get('/').text
"""
    subprocess.run(
        [sys.executable, "-S", "-c", retained_script, str(core_only), sysconfig.get_path("purelib"), str(branding)],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, check=True,
    )
