from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from fastapi import HTTPException

T = TypeVar("T")
_CURSOR = re.compile(r"[A-Za-z0-9_-]{1,2048}\Z")


@dataclass(frozen=True)
class PageOrder:
    field: str
    value_type: type
    descending: bool = False
    casefold: bool = False


def firestore_order_field(order: PageOrder) -> str:
    if not order.casefold:
        return order.field
    suffix = hashlib.sha256(order.field.encode()).hexdigest()[:16]
    return f"_page_sort_{suffix}"


def page_field_value(item: dict[str, Any], field: str) -> Any:
    value: Any = item
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def page_key(item: dict[str, Any], order_by: tuple[PageOrder, ...]) -> tuple[Any, ...]:
    values = []
    for order in order_by:
        value = page_field_value(item, order.field)
        if order.value_type is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{order.field} must be a finite number")
            value = float(value)
        elif value is not None and type(value) is not order.value_type:
            raise ValueError(f"{order.field} must be a {order.value_type.__name__}")
        if order.casefold and value is not None:
            if not isinstance(value, str):
                raise ValueError(f"{order.field} must be a string")
            value = value.casefold()
        values.append(value)
    return tuple(values)


def compare_page_keys(
    left: tuple[Any, ...], right: tuple[Any, ...], order_by: tuple[PageOrder, ...]
) -> int:
    for a, b, order in zip(left, right, order_by):
        if a == b:
            continue
        result = -1 if a is None else 1 if b is None else (1 if a > b else -1)
        return -result if order.descending else result
    return 0


def encode_sort_key(values: tuple[Any, ...], order_by: tuple[PageOrder, ...]) -> bytes:
    encoded = bytearray()
    for value, order in zip(values, order_by):
        if value is None:
            component = b"\x00"
        elif order.value_type is str:
            raw = value.encode("utf-8").replace(b"\x00", b"\x00\xff")
            component = b"\x01" + raw + b"\x00\x00"
        elif order.value_type is bool:
            component = b"\x01" + (b"\x01" if value else b"\x00")
        elif order.value_type is int:
            number = int(value)
            magnitude = abs(number)
            raw = magnitude.to_bytes(max(1, (magnitude.bit_length() + 7) // 8), "big")
            if number < 0:
                component = b"\x01" + b"\x00" + (0xFFFFFFFF - len(raw)).to_bytes(4, "big")
                component += bytes(byte ^ 0xFF for byte in raw)
            else:
                component = b"\x01" + b"\x01" + len(raw).to_bytes(4, "big") + raw
        elif order.value_type is float:
            number = float(value)
            if number == 0:
                number = 0.0
            raw = bytearray(struct.pack(">d", number))
            if raw[0] & 0x80:
                raw = bytearray(byte ^ 0xFF for byte in raw)
            else:
                raw[0] ^= 0x80
            component = b"\x01" + bytes(raw)
        else:
            raise TypeError(f"unsupported page key type {order.value_type!r}")
        if order.descending:
            component = bytes(byte ^ 0xFF for byte in component)
        encoded.extend(component)
    return bytes(encoded)


def encode_filter_key(fields: tuple[str, ...], equals: dict[str, Any]) -> bytes:
    return json.dumps(
        [[field, equals[field]] for field in fields],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=False,
        allow_nan=False,
    ).encode()


def page_index_spec(order_by: tuple[PageOrder, ...], filter_fields: tuple[str, ...]) -> tuple[str, str, str]:
    order_json = json.dumps(
        [
            {
                "field": order.field,
                "value_type": order.value_type.__name__,
                "descending": order.descending,
                "casefold": order.casefold,
            }
            for order in order_by
        ],
        separators=(",", ":"),
    )
    filters_json = json.dumps(list(filter_fields), separators=(",", ":"))
    spec_id = hashlib.sha256(f"{filters_json}\n{order_json}".encode()).hexdigest()
    return spec_id, filters_json, order_json


def order_from_json(raw: str) -> tuple[PageOrder, ...]:
    type_map = {"str": str, "int": int, "float": float, "bool": bool}
    try:
        values = json.loads(raw)
        return tuple(
            PageOrder(
                field=value["field"],
                value_type=type_map[value["value_type"]],
                descending=value["descending"],
                casefold=value["casefold"],
            )
            for value in values
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid stored page index") from exc


def _decode_cursor(cursor: str, key_types: tuple[type, ...]) -> tuple[Any, ...] | None:
    if not cursor:
        return None
    if not _CURSOR.fullmatch(cursor):
        raise HTTPException(422, "invalid pagination cursor")
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        values = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "invalid pagination cursor") from exc
    if not isinstance(values, list) or len(values) != len(key_types):
        raise HTTPException(422, "invalid pagination cursor")

    decoded: list[Any] = []
    for value, expected in zip(values, key_types):
        if expected is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise HTTPException(422, "invalid pagination cursor")
            decoded.append(float(value))
        elif type(value) is expected:
            decoded.append(value)
        else:
            raise HTTPException(422, "invalid pagination cursor")
    return tuple(decoded)


def _encode_cursor(key: tuple[Any, ...]) -> str:
    raw = json.dumps(list(key), separators=(",", ":"), allow_nan=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def paginate_store(
    store: Any,
    collection: str,
    cursor: str,
    limit: int,
    order_by: tuple[PageOrder, ...],
    *,
    equals: dict[str, Any] | None = None,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    filters = equals or {}
    return paginate_page(
        lambda after, page_limit: store.page(
            collection, order_by=order_by, after=after, limit=page_limit, equals=filters
        ),
        cursor,
        limit,
        order_by,
        predicate=predicate,
    )


def paginate_page(
    fetch_page: Callable[[tuple[Any, ...] | None, int], list[dict[str, Any]]],
    cursor: str,
    limit: int,
    order_by: tuple[PageOrder, ...],
    *,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be between 1 and 200")
    key_types = tuple(order.value_type for order in order_by)
    marker = _decode_cursor(cursor, key_types)
    if predicate is None:
        records = fetch_page(marker, limit + 1)
        has_more = len(records) > limit
        page = records[:limit]
        next_cursor = _encode_cursor(page_key(page[-1], order_by)) if has_more else None
        return page, next_cursor

    page: list[dict[str, Any]] = []
    scan_after = marker
    batch_size = 200
    while True:
        records = fetch_page(scan_after, batch_size)
        if not records:
            return page, None
        for record in records:
            scan_after = page_key(record, order_by)
            if not predicate(record):
                continue
            if len(page) == limit:
                return page, _encode_cursor(page_key(page[-1], order_by))
            page.append(record)
        if len(records) < batch_size:
            return page, None


def paginate_items(
    items: Iterable[T],
    cursor: str,
    limit: int,
    key: Callable[[T], tuple[Any, ...]],
    key_types: tuple[type, ...],
) -> tuple[list[T], str | None]:
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit must be between 1 and 200")
    marker = _decode_cursor(cursor, key_types)
    ordered = sorted(items, key=key)
    if marker is not None:
        ordered = [item for item in ordered if key(item) > marker]
    has_more = len(ordered) > limit
    page = ordered[:limit]
    next_cursor = _encode_cursor(key(page[-1])) if has_more else None
    return page, next_cursor
