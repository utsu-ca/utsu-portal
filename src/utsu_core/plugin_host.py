"""Static plug-in discovery and deterministic, lazy implementation loading."""
from __future__ import annotations

import importlib
import json
import re
from collections.abc import Iterable
from dataclasses import fields
from importlib import metadata, resources
from pathlib import Path
from typing import cast

from .bootstrap import Module
from .contracts import CORE_PLUGIN_API_MAJOR, PLUGIN_MANIFEST_SCHEMA_VERSION, PluginManifest
from .protection import JSON_SENSITIVE, SENSITIVE
from .plugin_runtime import KNOWN_PLUGIN_SERVICES, SUPPORTED_PLUGIN_SERVICES, TypedPluginAdapter
from .permissions import is_oauth_scope_token
from .settings import Settings

ENTRY_POINT_GROUP = "utsu.portal.plugins"
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
_FACTORY = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_AUTH_MODES = {"local", "remote"}
_PLUGIN_SETTINGS = {item.name for item in fields(Settings)}
_RESTRICTED_SETTINGS = {
    "secret", "data_key", "auth_signing_key", "auth_public_key", "service_token", "sso_key_pem", "anchor_token",
    "anchor_evm_key",
}
_CORE_COLLECTIONS = frozenset({
    "messages", "message_threads", "message_reads", "anchors", "anchor_ledger", "public_content",
    "financial_profiles", "orders", "payment_events",
})


class PluginHostError(RuntimeError):
    """Invalid or incompatible plug-in configuration."""


def _object_mapping(value: object, source: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PluginHostError(f"{source}: expected an object")
    result: dict[str, object] = {}
    raw = cast(dict[object, object], value)
    for key, item in raw.items():
        if not isinstance(key, str):
            raise PluginHostError(f"{source}: object keys must be strings")
        result[key] = item
    return result


def _string_tuple(raw: dict[str, object], key: str, default: tuple[str, ...], source: str) -> tuple[str, ...]:
    value = raw.get(key, default)
    if not isinstance(value, (list, tuple)):
        raise PluginHostError(f"{source}: {key} must be a list of strings")
    result: list[str] = []
    for item in cast(Iterable[object], value):
        if not isinstance(item, str):
            raise PluginHostError(f"{source}: {key} must be a list of strings")
        result.append(item)
    return tuple(result)


def parse_manifest(value: object, source: str, default_interface: str = "plugin") -> PluginManifest:
    raw = _object_mapping(value, source)
    allowed = {
        "id", "version", "core_api_major", "factory", "kind", "auth_modes", "dependencies", "order", "form_ids",
        "interface", "roles", "protection", "json_protection", "settings",
        "services", "collections", "provides", "unlinkable", "default_enabled",
        "permissions", "role_permissions",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise PluginHostError(f"{source}: unknown manifest fields: {', '.join(sorted(unknown))}")

    identifier = raw.get("id")
    version = raw.get("version")
    core_api_major = raw.get("core_api_major")
    factory = raw.get("factory")
    kind = raw.get("kind", "program")
    order = raw.get("order", 1000)
    interface = raw.get("interface", default_interface)
    roles_raw = raw.get("roles", {})
    permissions_raw = raw.get("permissions", {})
    role_permissions_raw = raw.get("role_permissions", {})
    protection_raw = raw.get("protection", {})
    json_protection_raw = raw.get("json_protection", {})
    settings = _string_tuple(raw, "settings", (), source)
    services = _string_tuple(raw, "services", (), source)
    collections = _string_tuple(raw, "collections", (), source)
    provides = _string_tuple(raw, "provides", (), source)
    unlinkable = _string_tuple(raw, "unlinkable", (), source)
    default_enabled = raw.get("default_enabled", True)
    if not isinstance(identifier, str) or not _PLUGIN_ID.fullmatch(identifier):
        raise PluginHostError(f"{source}: invalid plug-in id")
    if not isinstance(version, str) or not version.strip():
        raise PluginHostError(f"{source}: plug-in version must be a non-empty string")
    if type(core_api_major) is not int or core_api_major != CORE_PLUGIN_API_MAJOR:
        raise PluginHostError(
            f"{source}: core API major {core_api_major!r} is incompatible "
            f"(host supports {CORE_PLUGIN_API_MAJOR})"
        )
    if not isinstance(factory, str) or not _FACTORY.fullmatch(factory):
        raise PluginHostError(f"{source}: factory must use module:attribute syntax")
    if not isinstance(kind, str) or kind not in {"core", "program"}:
        raise PluginHostError(f"{source}: kind must be 'core' or 'program'")
    if not isinstance(interface, str) or interface not in {"plugin", "legacy-context"}:
        raise PluginHostError(f"{source}: interface must be 'plugin' or 'legacy-context'")
    if type(order) is not int:
        raise PluginHostError(f"{source}: order must be an integer")
    if type(default_enabled) is not bool:
        raise PluginHostError(f"{source}: default_enabled must be a boolean")
    if kind == "core" and not default_enabled:
        raise PluginHostError(f"{source}: core plug-ins are always enabled")

    auth_modes = _string_tuple(raw, "auth_modes", ("local", "remote"), source)
    dependencies = _string_tuple(raw, "dependencies", (), source)
    form_ids = _string_tuple(raw, "form_ids", (), source)
    if not auth_modes or not set(auth_modes) <= _AUTH_MODES:
        raise PluginHostError(f"{source}: auth_modes must contain local and/or remote")
    if len(set(auth_modes)) != len(auth_modes):
        raise PluginHostError(f"{source}: duplicate auth mode")
    if any(not _PLUGIN_ID.fullmatch(item) for item in dependencies):
        raise PluginHostError(f"{source}: invalid dependency id")
    if len(set(dependencies)) != len(dependencies) or identifier in dependencies:
        raise PluginHostError(f"{source}: duplicate or self dependency")
    if any(not _PLUGIN_ID.fullmatch(item) for item in form_ids):
        raise PluginHostError(f"{source}: invalid form id")
    if len(set(form_ids)) != len(form_ids):
        raise PluginHostError(f"{source}: duplicate form id")
    if any(not _PLUGIN_ID.fullmatch(setting) for setting in settings) or len(set(settings)) != len(settings):
        raise PluginHostError(f"{source}: invalid or duplicate setting declaration")
    if len(set(services)) != len(services) or set(services) - KNOWN_PLUGIN_SERVICES:
        raise PluginHostError(f"{source}: invalid or unknown service declaration")
    if len(set(collections)) != len(collections) or any(
        not re.fullmatch(r"[a-z][a-z0-9_]*", collection) for collection in collections
    ):
        raise PluginHostError(f"{source}: invalid or duplicate collection declaration")
    if len(set(provides)) != len(provides) or any(not _PLUGIN_ID.fullmatch(name) for name in provides):
        raise PluginHostError(f"{source}: invalid or duplicate provided service name")
    if len(set(unlinkable)) != len(unlinkable) or not set(unlinkable) <= set(collections):
        raise PluginHostError(f"{source}: unlinkable collections must be declared, exclusive collections")
    if provides and interface != "plugin":
        raise PluginHostError(f"{source}: only typed plug-ins can provide services")
    if interface == "plugin":
        if ("store" in services) != bool(collections):
            raise PluginHostError(f"{source}: typed plug-ins must declare collections exactly when using the store service")
        if "submissions" in collections:
            raise PluginHostError(f"{source}: shared submissions must be accessed through the owner-scoped forms service")
        reserved = set(collections) & _CORE_COLLECTIONS
        if reserved:
            raise PluginHostError(f"{source}: core collections are reached through core services: "
                                  f"{', '.join(sorted(reserved))}")
        if settings and "settings" not in services:
            raise PluginHostError(f"{source}: typed plug-in settings require the settings service")
        if form_ids and "forms" not in services:
            raise PluginHostError(f"{source}: typed plug-in form contributions require the forms service")
    unknown_settings = set(settings) - _PLUGIN_SETTINGS
    if unknown_settings:
        raise PluginHostError(f"{source}: unknown settings: {', '.join(sorted(unknown_settings))}")
    restricted_settings = set(settings) & _RESTRICTED_SETTINGS
    if restricted_settings:
        raise PluginHostError(f"{source}: platform secrets cannot be exposed as plug-in settings: "
                              f"{', '.join(sorted(restricted_settings))}")
    role_mapping = _object_mapping(roles_raw, f"{source} roles")
    roles: list[tuple[str, str]] = []
    for role_id, description in role_mapping.items():
        if not _PLUGIN_ID.fullmatch(role_id) or not isinstance(description, str) or not description.strip():
            raise PluginHostError(f"{source}: invalid role declaration")
        roles.append((role_id, description))
    permission_mapping = _object_mapping(permissions_raw, f"{source} permissions")
    permissions: list[tuple[str, str]] = []
    for scope, description in permission_mapping.items():
        if not is_oauth_scope_token(scope) or not scope.startswith("utsu:"):
            raise PluginHostError(f"{source}: permission identifiers must be namespaced OAuth scope tokens")
        if not isinstance(description, str) or not description.strip():
            raise PluginHostError(f"{source}: invalid permission description for {scope}")
        permissions.append((scope, description))
    role_permission_mapping = _object_mapping(role_permissions_raw, f"{source} role_permissions")
    role_permissions: list[tuple[str, tuple[str, ...]]] = []
    declared_roles, declared_permissions = {role for role, _ in roles}, {scope for scope, _ in permissions}
    for role, scope_values in role_permission_mapping.items():
        if role not in declared_roles:
            raise PluginHostError(f"{source}: permissions reference undeclared role {role}")
        scopes = _string_tuple({"scopes": scope_values}, "scopes", (), source)
        if not scopes or len(set(scopes)) != len(scopes) or not set(scopes) <= declared_permissions:
            raise PluginHostError(f"{source}: role {role} references invalid or undeclared permissions")
        role_permissions.append((role, tuple(sorted(scopes))))
    protection_mapping = _object_mapping(protection_raw, f"{source} protection")
    protection: list[tuple[str, tuple[str, ...]]] = []
    for collection, fields_raw in protection_mapping.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", collection):
            raise PluginHostError(f"{source}: invalid protected collection name")
        fields = _string_tuple({"fields": fields_raw}, "fields", (), source)
        if not fields or len(set(fields)) != len(fields) or any(not _PLUGIN_ID.fullmatch(field) for field in fields):
            raise PluginHostError(f"{source}: invalid protected field declaration for {collection}")
        retained = set(SENSITIVE.get(collection, ()))
        missing = set(fields) - retained
        if missing:
            raise PluginHostError(
                f"{source}: retained protection is missing {collection}.{', '.join(sorted(missing))}"
            )
        protection.append((collection, tuple(sorted(fields))))
    json_protection_mapping = _object_mapping(json_protection_raw, f"{source} JSON protection")
    json_protection: list[tuple[str, tuple[str, ...]]] = []
    protected_fields = dict(protection)
    for collection, fields_raw in json_protection_mapping.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", collection):
            raise PluginHostError(f"{source}: invalid JSON-protected collection name")
        fields = _string_tuple({"fields": fields_raw}, "fields", (), source)
        if not fields or len(set(fields)) != len(fields) or any(not _PLUGIN_ID.fullmatch(field) for field in fields):
            raise PluginHostError(f"{source}: invalid JSON protection declaration for {collection}")
        if not set(fields) <= set(protected_fields.get(collection, ())):
            raise PluginHostError(f"{source}: JSON-protected fields for {collection} must also be protected")
        missing = {(collection, field) for field in fields} - JSON_SENSITIVE
        if missing:
            names = ", ".join(f"{collection}.{field}" for collection, field in sorted(missing))
            raise PluginHostError(f"{source}: retained JSON protection is missing {names}")
        json_protection.append((collection, tuple(sorted(fields))))
    return PluginManifest(identifier, version, core_api_major, factory, kind, auth_modes,
                          dependencies, order, form_ids, interface, tuple(sorted(roles)), tuple(sorted(protection)),
                          tuple(sorted(json_protection)), tuple(sorted(settings)), tuple(sorted(services)),
                          tuple(sorted(collections)), tuple(sorted(provides)), tuple(sorted(unlinkable)),
                          default_enabled, tuple(sorted(permissions)), tuple(sorted(role_permissions)))


def _read_builtin_manifests() -> list[PluginManifest]:
    path = resources.files("utsu_core").joinpath("plugin_manifests/builtin.json")
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginHostError("could not read built-in plug-in manifests") from exc
    raw = _object_mapping(payload, "built-in plug-in manifests")
    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version != PLUGIN_MANIFEST_SCHEMA_VERSION:
        raise PluginHostError("built-in plug-in manifest schema version is unsupported")
    items = raw.get("plugins")
    if not isinstance(items, list):
        raise PluginHostError("built-in plug-in manifest list is invalid")
    return [
        parse_manifest(item, "built-in manifest", default_interface="legacy-context")
        for item in cast(list[object], items)
    ]


def _read_external_manifest(entry_point: metadata.EntryPoint) -> PluginManifest:
    distribution = entry_point.dist
    if distribution is None or distribution.files is None:
        raise PluginHostError(f"{entry_point.name}: distribution has no file manifest")
    matches = [path for path in distribution.files if Path(str(path)).name == "utsu-plugin.json"]
    if len(matches) != 1:
        raise PluginHostError(f"{entry_point.name}: distribution must contain exactly one utsu-plugin.json")
    path = distribution.locate_file(matches[0])
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginHostError(f"{entry_point.name}: could not read utsu-plugin.json") from exc
    raw = _object_mapping(payload, f"{entry_point.name} manifest")
    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version != PLUGIN_MANIFEST_SCHEMA_VERSION:
        raise PluginHostError(f"{entry_point.name}: plug-in manifest schema version is unsupported")
    manifest = parse_manifest(raw.get("plugin"), f"{entry_point.name} manifest")
    if manifest.id != entry_point.name:
        raise PluginHostError(f"{entry_point.name}: entry-point name does not match manifest id {manifest.id!r}")
    if manifest.factory != entry_point.value:
        raise PluginHostError(f"{entry_point.name}: entry-point factory does not match its static manifest")
    if manifest.kind != "program":
        raise PluginHostError(f"{entry_point.name}: external distributions may only provide program plug-ins")
    if manifest.interface != "plugin":
        raise PluginHostError(f"{entry_point.name}: external plug-ins must use the typed core API")
    return manifest


def discover_manifests(
    external_entry_points: Iterable[metadata.EntryPoint] | None = None,
) -> dict[str, PluginManifest]:
    """Read built-in and installed manifests without importing plug-in implementations."""
    manifests = _read_builtin_manifests()
    entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP) if external_entry_points is None else external_entry_points
    for entry_point in entry_points:
        manifests.append(_read_external_manifest(entry_point))
    result: dict[str, PluginManifest] = {}
    for manifest in manifests:
        if manifest.id in result:
            raise PluginHostError(f"duplicate plug-in id: {manifest.id}")
        result[manifest.id] = manifest
    return result


def select_manifests(
    manifests: dict[str, PluginManifest],
    auth_mode: str,
    enabled_programs: tuple[str, ...] | None = None,
) -> tuple[PluginManifest, ...]:
    """Select the mandatory host modules plus an explicit program allowlist."""
    if auth_mode not in _AUTH_MODES:
        raise PluginHostError(f"unsupported auth mode: {auth_mode}")
    if enabled_programs is not None and len(set(enabled_programs)) != len(enabled_programs):
        raise PluginHostError("enabled plug-in list contains duplicate ids")
    available_programs = {
        identifier
        for identifier, manifest in manifests.items()
        if manifest.kind == "program" and auth_mode in manifest.auth_modes
    }
    if enabled_programs is None:
        selected_programs = {identifier for identifier in available_programs if manifests[identifier].default_enabled}
    else:
        requested = set(enabled_programs)
        unknown = requested - manifests.keys()
        if unknown:
            raise PluginHostError(f"unknown enabled plug-in ids: {', '.join(sorted(unknown))}")
        unavailable = requested - available_programs
        if unavailable:
            raise PluginHostError(
                f"plug-ins unavailable in {auth_mode} auth mode: {', '.join(sorted(unavailable))}"
            )
        selected_programs = requested
    selected_ids = {
        identifier
        for identifier, manifest in manifests.items()
        if manifest.kind == "core" and auth_mode in manifest.auth_modes
    } | selected_programs
    for identifier in selected_ids:
        manifest = manifests[identifier]
        missing = set(manifest.dependencies) - selected_ids
        if missing:
            raise PluginHostError(
                f"plug-in {identifier} requires disabled or missing plug-ins: {', '.join(sorted(missing))}"
            )
    result: list[PluginManifest] = []
    completed: set[str] = set()
    pending = set(selected_ids)
    while pending:
        ready = [
            manifests[identifier]
            for identifier in pending
            if set(manifests[identifier].dependencies) <= completed
        ]
        if not ready:
            raise PluginHostError(f"plug-in dependency cycle: {', '.join(sorted(pending))}")
        ready.sort(key=lambda manifest: (manifest.order, manifest.id))
        current = ready[0]
        result.append(current)
        completed.add(current.id)
        pending.remove(current.id)
    form_owners: dict[str, str] = {}
    role_owners: dict[str, str] = {}
    setting_owners: dict[str, str] = {}
    collection_owners: dict[str, str] = {}
    service_owners: dict[str, str] = {}
    for manifest in result:
        for name in manifest.provides:
            owner = service_owners.get(name)
            if owner is not None:
                raise PluginHostError(f"service {name} is provided by both {owner} and {manifest.id}")
            service_owners[name] = manifest.id
        for form_id in manifest.form_ids:
            owner = form_owners.get(form_id)
            if owner is not None:
                raise PluginHostError(f"form {form_id} is declared by both {owner} and {manifest.id}")
            form_owners[form_id] = manifest.id
        for role_id in dict(manifest.roles):
            owner = role_owners.get(role_id)
            if owner is not None:
                raise PluginHostError(f"role {role_id} is declared by both {owner} and {manifest.id}")
            role_owners[role_id] = manifest.id
        for setting in manifest.settings:
            owner = setting_owners.get(setting)
            if owner is not None:
                raise PluginHostError(f"setting {setting} is declared by both {owner} and {manifest.id}")
            setting_owners[setting] = manifest.id
        for collection in manifest.collections:
            owner = collection_owners.get(collection)
            if owner is not None:
                raise PluginHostError(f"collection {collection} is declared by both {owner} and {manifest.id}")
            collection_owners[collection] = manifest.id
    return tuple(result)


def _instantiate(manifest: PluginManifest) -> Module:
    module_name, attribute = manifest.factory.split(":", 1)
    try:
        target = getattr(importlib.import_module(module_name), attribute)
        instance = target()
    except Exception as exc:
        raise PluginHostError(f"plug-in {manifest.id} failed to load: {exc}") from exc
    if manifest.interface == "plugin":
        declared_manifest = getattr(instance, "manifest", None)
        if (
            not isinstance(declared_manifest, PluginManifest)
            or declared_manifest.id != manifest.id
            or declared_manifest.version != manifest.version
            or declared_manifest.core_api_major != manifest.core_api_major
            or declared_manifest.interface != "plugin"
        ):
            raise PluginHostError(f"plug-in {manifest.id} factory returned an invalid typed manifest")
        if not callable(getattr(instance, "router", None)):
            raise PluginHostError(f"plug-in {manifest.id} has no router(services) implementation")
        return cast(Module, TypedPluginAdapter(manifest, instance))
    name = getattr(instance, "name", None)
    router = getattr(instance, "router", None)
    if name != manifest.id:
        raise PluginHostError(f"plug-in {manifest.id} factory returned module with name {name!r}")
    if not callable(router):
        raise PluginHostError(f"plug-in {manifest.id} has no router(ctx) implementation")
    return cast(Module, instance)


def load_modules(
    auth_mode: str,
    enabled_programs: tuple[str, ...] | None = None,
) -> tuple[tuple[PluginManifest, ...], tuple[Module, ...]]:
    manifests = discover_manifests()
    selected = select_manifests(manifests, auth_mode, enabled_programs)
    for manifest in selected:
        if manifest.interface == "plugin":
            if ("store" in manifest.services) != bool(manifest.collections):
                raise PluginHostError(
                    f"plug-in {manifest.id} must declare collections exactly when using the store service"
                )
            if manifest.settings and "settings" not in manifest.services:
                raise PluginHostError(f"plug-in {manifest.id} settings require the settings service")
            unavailable = set(manifest.services) - SUPPORTED_PLUGIN_SERVICES
            if unavailable:
                raise PluginHostError(
                    f"plug-in {manifest.id} requires unavailable core services: {', '.join(sorted(unavailable))}"
                )
    return selected, tuple(_instantiate(manifest) for manifest in selected)
