import os
import importlib
import importlib.util
import subprocess
import sys
import tomllib
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_batch_modules_do_not_require_portal_dependencies(tmp_path):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "src/core")))}
    subprocess.run(
        [sys.executable, "-S", "-c",
         "import core.programs.agm.program, core.programs.sap.program, "
         "core.programs.senate.program, core.mail, core.std; "
         "import sys; assert 'fastapi' not in sys.modules"],
        cwd=tmp_path, env=env, check=True,
    )


@pytest.mark.parametrize(("statement", "forbidden"), [
    ("from core.authsvc import create_auth_app",
     ("core.app", "core.programs.")),
    ("from core import create_app",
     ("core.pipeline", "core.programs.sap.program", "core.programs.senate.program",
      "core.programs.agm.program", "sabp.program", "senate.program", "agm.program")),
])
def test_composition_imports_stay_within_their_boundary(tmp_path, statement, forbidden):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "src/core")))}
    subprocess.run(
        [sys.executable, "-c",
         f"{statement}; import sys; "
         f"leaked = [name for name in sys.modules if name.startswith({forbidden!r})]; "
         "assert not leaked, leaked"],
        cwd=tmp_path, env=env, check=True,
    )


def test_packaged_program_schemas_and_portal_assets():
    for package, filename in (
        ("core", "schema/uoft_data.sql"),
        ("core.programs.agm", "schema.sql"),
        ("core.programs.sap", "schema.sql"),
        ("core.programs.senate", "schema.sql"),
        ("core.programs.agm", "forms/agm_rsvp.yaml"),
        ("core.programs.senate", "forms/senate_application.yaml"),
        ("core.programs.sap", "forms/student_aid_bursary.yaml"),
        ("core.mail", "templates/live/verification_code.txt"),
        ("core", "plugin_manifests/builtin.json"),
        ("core", "plugin_manifests/protection.json"),
        ("core", "static/index.html"),
        ("core", "static/finance.html"),
        ("branding", "theme.json"),
        ("branding", "logo.svg"),
    ):
        assert resources.files(package).joinpath(filename).is_file()


def test_standard_wheel_is_core_only():
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    find = configuration["tool"]["setuptools"]["packages"]["find"]
    assert find["where"] == ["src/core"]
    assert find["include"] == ["core", "core.*"]
    package_data = configuration["tool"]["setuptools"]["package-data"]
    assert set(package_data) == {"core"}


def test_program_frontend_manifests_exist_for_split_wheels():
    for package in ("agm", "sabp", "clubs", "senate", "chat", "voting"):
        frontend = ROOT / "src" / package / "frontend"
        assert (frontend / "portal-frontend.json").is_file()


def test_legacy_schema_discovery_skips_only_uninstalled_programs(tmp_path):
    package = tmp_path / "agm"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "schema.sql").write_text("CREATE TABLE example (id TEXT);", encoding="utf-8")
    subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import sys; sys.path[:0] = sys.argv[1:]; "
         "from pathlib import Path; from core.resources import schema_dirs; "
         "assert schema_dirs('core.programs') == [Path(sys.argv[1]) / 'agm']",
         str(tmp_path), str(ROOT / "src/core")],
        cwd=tmp_path, check=True,
    )
    (package / "__init__.py").write_text("raise RuntimeError('broken program package')", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import sys; sys.path[:0] = sys.argv[1:]; "
         "from core.resources import schema_dirs; schema_dirs('core.programs')",
         str(tmp_path), str(ROOT / "src/core")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "broken program package" in result.stderr


@pytest.mark.parametrize("api_only", [True, False])
def test_core_only_starts_without_program_imports(tmp_path, api_only):
    static = tmp_path / "static"
    branding = tmp_path / "branding"
    if not api_only:
        static.mkdir()
        branding.mkdir()
        (static / "index.html").write_text("core frontend", encoding="utf-8")
        (branding / "theme.json").write_text("{}", encoding="utf-8")
    script = """
import importlib.abc
import sys

programs = {'agm', 'sabp', 'senate', 'clubs', 'chat', 'voting', 'branding'}
attempted = []

class MissingPrograms(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in programs:
            attempted.append(fullname)
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, MissingPrograms())
sys.path.insert(0, sys.argv[1])
from core import create_app
from core.settings import Settings
app = create_app(Settings(secret='s' * 32, enabled_plugins=(), api_only=sys.argv[4] == '1',
                          static_dir=sys.argv[2], branding_dir=sys.argv[3]))
assert not app.state.ctx.store.list('form_versions')
if sys.argv[4] == '0':
    from fastapi.testclient import TestClient
    client = TestClient(app)
    assert client.get('/').text == 'core frontend'
    assert client.get('/branding/theme.json').json() == {}
assert not attempted, attempted
"""
    subprocess.run(
        [sys.executable, "-I", "-c", script, str(ROOT / "src/core"), str(static), str(branding),
         "1" if api_only else "0"],
        cwd=tmp_path, check=True,
    )


def test_senate_legacy_imports_and_resources_resolve_to_extracted_package():
    for name in ("module", "program", "preferences", "seating"):
        assert importlib.import_module(f"core.programs.senate.{name}") is importlib.import_module(f"senate.{name}")
    for filename in ("schema.sql", "forms/senate_application.yaml"):
        assert resources.files("core.programs.senate").joinpath(filename).read_bytes() == (
            resources.files("senate").joinpath(filename).read_bytes()
        )


@pytest.mark.parametrize(("legacy", "package", "modules"), [
    ("discussion", "chat", ("contracts", "plugin")),
    ("voting", "voting", ("contracts", "plugin", "record", "tally")),
    ("clubs", "clubs", ("module",)),
    ("agm", "agm", ("models", "module", "program", "verify")),
    ("sap", "sabp", ("applications", "banking", "eft", "export_banking", "merge_docx", "module", "program",
                      "redaction", "review", "terms", "wfni")),
])
def test_program_legacy_imports_preserve_module_identity(legacy, package, modules):
    for name in modules:
        assert importlib.import_module(f"core.programs.{legacy}.{name}") is importlib.import_module(f"{package}.{name}")


@pytest.mark.parametrize(("legacy", "package", "form"), [
    ("agm", "agm", "agm_rsvp.yaml"),
    ("sap", "sabp", "student_aid_bursary.yaml"),
])
def test_program_legacy_resources_resolve_to_extracted_package(legacy, package, form):
    for filename in ("schema.sql", f"forms/{form}"):
        assert resources.files(f"core.programs.{legacy}").joinpath(filename).read_bytes() == (
            resources.files(package).joinpath(filename).read_bytes()
        )


def test_portal_factory_is_exported_from_core():
    from core import create_app
    from core.app import create_app as factory

    assert create_app is factory


@pytest.mark.parametrize("package_path", ["src/core/version.py", "src/core/version.py"])
def test_development_version_finds_git_root_after_core_relocation(tmp_path, monkeypatch, package_path):
    from core import version

    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").touch()
    source = tmp_path / package_path
    source.parent.mkdir(parents=True)
    source.touch()
    monkeypatch.setattr(version, "__file__", str(source))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="abcdef1234567890\n")

    monkeypatch.setattr(version.subprocess, "run", run)
    assert version.server_version()["commit"] == "abcdef123456"
    assert calls == [["git", "-C", str(tmp_path), "rev-parse", "HEAD"]]


def test_core_package_installs_portal_runtime_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = {item.split(">=")[0].lower() for item in project["dependencies"]}
    assert {"fastapi", "pydantic", "uvicorn", "pyyaml", "cryptography", "google-re2", "redis", "segno"} <= dependencies
    assert "portal" in project["optional-dependencies"]


def test_program_command_entry_points_are_retired():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert not ({"utsu-all", "utsu-agm", "utsu-sap", "utsu-senate"} & project.get("scripts", {}).keys())
    for module in (
        "core.cli",
        "core.programs.runner",
        "core.programs.__main__",
        "core.programs.agm.cli",
        "core.programs.agm.__main__",
        "core.programs.sap.cli",
        "core.programs.sap.__main__",
        "core.programs.senate.cli",
        "core.programs.senate.__main__",
        "core.mail.__main__",
    ):
        assert importlib.util.find_spec(module) is None
