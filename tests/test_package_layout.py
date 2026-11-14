import os
import importlib.util
import subprocess
import sys
import tomllib
from importlib import resources
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_batch_modules_do_not_require_portal_dependencies(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    subprocess.run(
        [sys.executable, "-S", "-c",
         "import utsu_core.programs.agm.program, utsu_core.programs.sap.program, "
         "utsu_core.programs.senate.program, utsu_core.mail, utsu_core.std; "
         "import sys; assert 'fastapi' not in sys.modules"],
        cwd=tmp_path, env=env, check=True,
    )


def test_packaged_program_schemas_and_portal_assets():
    for package, filename in (
        ("utsu_core", "schema/uoft_data.sql"),
        ("utsu_core.programs.agm", "schema.sql"),
        ("utsu_core.programs.sap", "schema.sql"),
        ("utsu_core.programs.senate", "schema.sql"),
        ("utsu_core", "forms/agm_rsvp.yaml"),
        ("utsu_core", "static/index.html"),
    ):
        assert resources.files(package).joinpath(filename).is_file()


def test_portal_factory_is_exported_from_core():
    from utsu_core import create_app
    from utsu_core.app import create_app as factory

    assert create_app is factory


def test_core_package_installs_portal_runtime_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = {item.split(">=")[0].lower() for item in project["dependencies"]}
    assert {"fastapi", "pydantic", "uvicorn", "pyyaml", "cryptography", "google-re2", "redis", "segno"} <= dependencies
    assert "portal" in project["optional-dependencies"]


def test_program_command_entry_points_are_retired():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert not ({"utsu-all", "utsu-agm", "utsu-sap", "utsu-senate"} & project.get("scripts", {}).keys())
    for module in (
        "utsu_core.cli",
        "utsu_core.programs.runner",
        "utsu_core.programs.__main__",
        "utsu_core.programs.agm.cli",
        "utsu_core.programs.agm.__main__",
        "utsu_core.programs.sap.cli",
        "utsu_core.programs.sap.__main__",
        "utsu_core.programs.senate.cli",
        "utsu_core.programs.senate.__main__",
        "utsu_core.mail.__main__",
    ):
        assert importlib.util.find_spec(module) is None
