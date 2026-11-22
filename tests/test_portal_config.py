import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tools/{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


portal_config = load_tool("portal_config")


def write(tmp_path, text, branding=True):
    if branding:
        (tmp_path / "branding").mkdir(exist_ok=True)
        (tmp_path / "branding/theme.json").write_text("{}", encoding="utf-8")
    path = tmp_path / "portal.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_program_map_matches_the_split_distribution_builder():
    builder = load_tool("build_split_distributions")
    assert portal_config.PROGRAMS == {name: tuple(ids) for name, ids in builder.PROGRAMS.items()}


def test_every_plugin_id_is_a_builtin_program():
    catalogue = json.loads((ROOT / "src/core/plugin_manifests/builtin.json").read_text("utf-8"))
    programs = {item["id"] for item in catalogue["plugins"] if item["kind"] == "program"}
    assert {i for ids in portal_config.PROGRAMS.values() for i in ids} == programs


def test_shipped_configuration_enables_every_program_and_its_branding_exists():
    config = portal_config.load()
    assert config.composition == "full"
    assert set(config.programs) == set(portal_config.PROGRAMS)
    assert (config.branding / "theme.json").is_file()


def test_enabled_programs_become_plugin_and_frontend_ids(tmp_path):
    config = portal_config.load(write(tmp_path, "programs: {agm: true, chat: true, voting: true, sabp: false}"))
    assert config.programs == ("agm", "chat", "voting")
    assert config.plugin_ids == ("agm", "agm_verify", "discussion", "voting")
    assert config.frontend_ids == ("agm", "discussion", "voting")
    assert config.composition == "selected"


def test_no_programs_is_a_core_only_build(tmp_path):
    config = portal_config.load(write(tmp_path, "core: true\nprograms: {}"))
    assert (config.programs, config.composition) == ((), "core-only")


@pytest.mark.parametrize("text, message", [
    ("core: false", "core cannot be disabled"),
    ("programs: {nope: true}", "unknown programs: nope"),
    ("programs: {agm: yes please}", "must be true or false"),
    ("programs: {voting: true}", "voting requires chat"),
    ("programs: [agm]", "must map each program"),
    ("extra: 1", "unknown configuration keys: extra"),
    ("programs: {agm: false}\nplugin_config: {agm: {}}", "not enabled"),
    ("- not a mapping", "must contain a mapping"),
])
def test_invalid_configuration_is_rejected(tmp_path, text, message):
    with pytest.raises(portal_config.ConfigError, match=message):
        portal_config.load(write(tmp_path, text))


def test_branding_directory_must_contain_a_theme(tmp_path):
    with pytest.raises(portal_config.ConfigError, match="theme.json"):
        portal_config.load(write(tmp_path, "programs: {}", branding=False))


def test_shell_output_is_quoted_and_round_trips(tmp_path):
    config = portal_config.load(write(
        tmp_path, "programs: {sabp: true}\nplugin_config:\n  sap: {note: \"it's $(unsafe)\"}"
    ))
    values = dict(shlex.split(line)[0].split("=", 1) for line in portal_config.shell_assignments(config).splitlines())
    assert values["PORTAL_ENABLED_PLUGINS"] == "sap"
    assert json.loads(values["PORTAL_PLUGIN_CONFIG"]) == {"sap": {"note": "it's $(unsafe)"}}
