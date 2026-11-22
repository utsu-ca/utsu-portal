"""Read config/portal.yaml and translate it into build and deployment settings.

One file decides which programs are installed in an image (scripts/build.sh) and which are enabled when it runs
(scripts/deploy.sh). Core is always installed and enabled.
"""
from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "portal.yaml"

# Distribution package -> backend plug-in ids. Frontend contribution ids are the same minus `agm_verify`, which
# shares the AGM frontend. Keep in step with PROGRAMS in tools/build_split_distributions.py.
PROGRAMS: dict[str, tuple[str, ...]] = {
    "agm": ("agm", "agm_verify"),
    "sabp": ("sap",),
    "clubs": ("clubs",),
    "senate": ("senate",),
    "chat": ("discussion",),
    "voting": ("voting",),
}
# A program cannot be enabled without the programs it builds on.
REQUIRES: dict[str, tuple[str, ...]] = {"voting": ("chat",)}
KNOWN_KEYS = {"core", "programs", "branding", "plugin_config"}


class ConfigError(ValueError):
    """The portal configuration is invalid."""


@dataclass(frozen=True)
class PortalConfig:
    programs: tuple[str, ...]  # distribution package names, in a stable order
    branding: Path
    plugin_config: dict[str, Any]

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(identifier for program in self.programs for identifier in PROGRAMS[program])

    @property
    def frontend_ids(self) -> tuple[str, ...]:
        return tuple(identifier for identifier in self.plugin_ids if identifier != "agm_verify")

    @property
    def composition(self) -> str:
        if not self.programs:
            return "core-only"
        return "full" if set(self.programs) == set(PROGRAMS) else "selected"


def load(path: Path = DEFAULT_CONFIG) -> PortalConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping")
    unknown = set(raw) - KNOWN_KEYS
    if unknown:
        raise ConfigError(f"unknown configuration keys: {', '.join(sorted(map(str, unknown)))}")
    if raw.get("core", True) is not True:
        raise ConfigError("core cannot be disabled")

    programs = raw.get("programs") or {}
    if not isinstance(programs, dict):
        raise ConfigError("programs must map each program name to true or false")
    unknown_programs = set(programs) - set(PROGRAMS)
    if unknown_programs:
        raise ConfigError(
            f"unknown programs: {', '.join(sorted(map(str, unknown_programs)))} (known: {', '.join(PROGRAMS)})"
        )
    for name, value in programs.items():
        if not isinstance(value, bool):
            raise ConfigError(f"programs.{name} must be true or false, not {value!r}")
    enabled = tuple(name for name in PROGRAMS if programs.get(name, False))
    for name in enabled:
        missing = [need for need in REQUIRES.get(name, ()) if need not in enabled]
        if missing:
            raise ConfigError(f"{name} requires {', '.join(missing)} to be enabled")

    branding = raw.get("branding", "branding")
    if not isinstance(branding, str) or not branding:
        raise ConfigError("branding must be a directory path relative to the configuration file")
    branding_path = (path.parent / branding).resolve()
    if not (branding_path / "theme.json").is_file():
        raise ConfigError(f"branding directory {branding_path} must contain theme.json")

    plugin_config = raw.get("plugin_config") or {}
    if not isinstance(plugin_config, dict):
        raise ConfigError("plugin_config must map plug-in ids to their settings")
    ids = {identifier for name in enabled for identifier in PROGRAMS[name]}
    stray = set(plugin_config) - ids
    if stray:
        raise ConfigError(f"plugin_config is set for programs that are not enabled: {', '.join(sorted(map(str, stray)))}")
    return PortalConfig(enabled, branding_path, plugin_config)


def shell_assignments(config: PortalConfig) -> str:
    """KEY=value lines safe to `eval` in a POSIX shell."""
    import json

    values = {
        "PORTAL_PROGRAMS": ",".join(config.programs),
        "PORTAL_ENABLED_PLUGINS": ",".join(config.plugin_ids),
        "PORTAL_FRONTEND_COMPOSITION": config.composition,
        "PORTAL_FRONTEND_PROGRAMS": ",".join(config.frontend_ids),
        "PORTAL_BRANDING_PATH": str(config.branding),
        "PORTAL_PLUGIN_CONFIG": json.dumps(config.plugin_config, separators=(",", ":")) if config.plugin_config else "",
    }
    return "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        print(shell_assignments(load(args.config)))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
