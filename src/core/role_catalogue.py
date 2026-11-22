"""Core role metadata and compatibility catalogue loaded from static plug-in descriptors."""
from __future__ import annotations

import json
from importlib import resources
from typing import Iterable, cast

from .contracts import PluginManifest

CORE_ROLES = {
    "staff": "staff account; no operational or administration permissions",
    "admin": "everything, including granting roles",
    "verifier": "review identity (face + ID) verifications, portal and AGM",
    "forms_editor": "create/edit/publish forms; view and set submission statuses",
    "auditor": "read, verify and export the audit log",
}


def role_catalogue_for(manifests: Iterable[PluginManifest]) -> dict[str, str]:
    catalogue = dict(CORE_ROLES)
    owners = {role_id: "core" for role_id in catalogue}
    for manifest in manifests:
        for role_id, description in manifest.roles:
            owner = owners.get(role_id)
            if owner is not None:
                raise RuntimeError(f"role {role_id} is declared by both {owner} and {manifest.id}")
            catalogue[role_id] = description
            owners[role_id] = manifest.id
    return catalogue


def _default_catalogue() -> dict[str, str]:
    manifest = resources.files("utsu_core").joinpath("plugin_manifests/builtin.json")
    try:
        value: object = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("could not load built-in role metadata") from exc
    raw = cast(dict[object, object], value) if isinstance(value, dict) else {}
    plugins = raw.get("plugins")
    if not isinstance(plugins, list):
        raise RuntimeError("built-in plug-in manifest has no role catalogue")
    result = dict(CORE_ROLES)
    for item in cast(list[object], plugins):
        if not isinstance(item, dict):
            raise RuntimeError("built-in plug-in manifest entry is invalid")
        plugin = cast(dict[object, object], item)
        if plugin.get("default_enabled") is False:
            continue  # opt-in plug-ins contribute roles only when enabled (role_catalogue_for)
        roles = plugin.get("roles", {})
        if not isinstance(roles, dict):
            raise RuntimeError("built-in plug-in role metadata is invalid")
        for role_id_raw, description_raw in cast(dict[object, object], roles).items():
            role_id, description = role_id_raw, description_raw
            if not isinstance(role_id, str) or not isinstance(description, str) or not description.strip():
                raise RuntimeError("built-in plug-in role declaration is invalid")
            if role_id in result:
                raise RuntimeError(f"duplicate built-in role declaration: {role_id}")
            result[role_id] = description
    return result


# Compatibility catalogue for callers that inspect the default built-in profile.
ROLES = _default_catalogue()
