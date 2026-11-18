"""Retained-data encryption policy, independent of which plug-ins are currently enabled."""
from __future__ import annotations

import json
from importlib import resources
from typing import cast


def _load_catalogue() -> tuple[dict[str, tuple[str, ...]], frozenset[tuple[str, str]]]:
    path = resources.files("utsu_core").joinpath("plugin_manifests/protection.json")
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("could not load retained-data protection catalogue") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("retained-data protection catalogue schema is unsupported")
    raw = cast(dict[object, object], payload)
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise RuntimeError("retained-data protection catalogue schema is unsupported")
    collections = raw.get("collections")
    if not isinstance(collections, dict):
        raise RuntimeError("retained-data protection catalogue collections are invalid")
    sensitive: dict[str, tuple[str, ...]] = {}
    for collection_raw, fields_raw in cast(dict[object, object], collections).items():
        collection, fields = collection_raw, fields_raw
        if not isinstance(collection, str) or not collection:
            raise RuntimeError("retained-data protection collection name is invalid")
        if not isinstance(fields, list):
            raise RuntimeError(f"retained-data fields for {collection} are invalid")
        field_values = cast(list[object], fields)
        if not all(isinstance(field, str) and field for field in field_values):
            raise RuntimeError(f"retained-data fields for {collection} are invalid")
        string_fields = [cast(str, field) for field in field_values]
        if len(set(string_fields)) != len(string_fields):
            raise RuntimeError(f"duplicate retained-data field for {collection}")
        sensitive[collection] = tuple(string_fields)
    json_fields = raw.get("json_fields")
    if not isinstance(json_fields, list):
        raise RuntimeError("retained-data JSON protection fields are invalid")
    parsed_json_fields: set[tuple[str, str]] = set()
    for item_raw in cast(list[object], json_fields):
        if not isinstance(item_raw, list):
            raise RuntimeError("retained-data JSON protection entry is invalid")
        item = cast(list[object], item_raw)
        if len(item) != 2:
            raise RuntimeError("retained-data JSON protection entry is invalid")
        if not all(isinstance(value, str) for value in item):
            raise RuntimeError("retained-data JSON protection entry is invalid")
        collection, field = cast(str, item[0]), cast(str, item[1])
        if field not in sensitive.get(collection, ()):
            raise RuntimeError(f"JSON protection field {collection}.{field} is not encrypted")
        parsed_json_fields.add((collection, field))
    return sensitive, frozenset(parsed_json_fields)


SENSITIVE, JSON_SENSITIVE = _load_catalogue()
