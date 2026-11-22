"""Database-agnostic document store. Implement `Store` to add a backend."""
from __future__ import annotations

import contextlib
import contextvars
import json
import sqlite3
import random
import secrets
import threading
import time
from abc import ABC, abstractmethod
from functools import cmp_to_key
from typing import Any, Callable, Iterator, Mapping

from .pagination import (
    PageOrder,
    compare_page_keys,
    encode_filter_key,
    encode_sort_key,
    page_field_value,
    page_index_spec,
    page_key,
    firestore_order_field,
    order_from_json,
)

Doc = dict[str, Any]
REV = "_rev"  # optimistic-lock revision carried by every document written through create()/replace()/update()
_MAX_PAGE_INDEX_COMPONENT_BYTES = 700  # Bounds composite keys for SQL Server's 1,700-byte limit.


class Conflict(Exception):
    """Another writer changed the document since it was read (optimistic lock failed)."""


class _MissingChunk(Conflict):
    """A document generation was replaced while its chunk data was being read."""


class Store(ABC):
    @abstractmethod
    def get(self, col: str, id: str) -> Doc | None: ...

    @abstractmethod
    def put(self, col: str, id: str, doc: Doc) -> None: ...

    @abstractmethod
    def delete(self, col: str, id: str) -> None: ...

    @abstractmethod
    def list(self, col: str, **equals: Any) -> list[Doc]:
        """All documents in `col` whose top-level fields equal the given values."""

    def page(
        self,
        col: str,
        *,
        order_by: tuple[PageOrder, ...],
        after: tuple[Any, ...] | None,
        limit: int,
        equals: Mapping[str, Any] | None = None,
    ) -> list[Doc]:
        """Return a keyset page; equality filters may use dotted paths into nested values."""
        if not 1 <= limit <= 500:
            raise ValueError("page limit must be between 1 and 500")
        filters = dict(equals or {})
        if any("." in field for field in filters):
            records = [
                doc for doc in self.list(col)
                if all(page_field_value(doc, field) == value for field, value in filters.items())
            ]
        else:
            records = self.list(col, **filters)
        if after is not None:
            records = [
                doc for doc in records
                if compare_page_keys(page_key(doc, order_by), after, order_by) > 0
            ]
        records.sort(key=cmp_to_key(
            lambda left, right: compare_page_keys(page_key(left, order_by), page_key(right, order_by), order_by)
        ))
        return records[:limit]

    def create(self, col: str, id: str, doc: Doc) -> bool:
        """Insert only if absent. Backends should override with an atomic version."""
        if self.get(col, id) is not None:
            return False
        self.put(col, id, doc)
        return True


    @abstractmethod
    def replace(self, col: str, id: str, doc: Doc, expected_rev: int) -> bool:
        """Compare-and-swap: write `doc` (with `_rev` = expected_rev + 1) only if the stored `_rev` is still
        `expected_rev` (a missing `_rev` counts as 0). Must be atomic; returns False on conflict or if the document is gone."""

    def update(self, col: str, id: str, fn: Callable[[Doc | None], Doc | None], retries: int = 10) -> Doc | None:
        """Optimistic read-modify-write. `fn` gets the current document (None if absent) and returns the new one, or None
        to leave it unchanged; it may raise to abort. It is re-run on a fresh read when another writer got in first, so it
        must be free of side effects. Raises `Conflict` if the retries are exhausted."""
        for attempt in range(retries):
            cur = self.get(col, id)
            new = fn(json.loads(json.dumps(cur)) if cur is not None else None)
            if new is None:
                return cur
            if cur is None:
                new = {**new, REV: 1}
                if self.create(col, id, new):
                    return new
            else:
                rev = int(cur.get(REV, 0))
                new = {**new, REV: rev + 1}
                if self.replace(col, id, new, rev):
                    return new
            time.sleep(min(0.002 * 2 ** attempt, 0.05) * random.random())
        raise Conflict(f"{col}/{id}: too much contention")


def _match(doc: Doc, equals: dict) -> bool:
    return all(doc.get(k) == v for k, v in equals.items())


def _page_index_record(
    doc: Doc, filter_fields: tuple[str, ...], order_by: tuple[PageOrder, ...]
) -> tuple[bytes, bytes]:
    filters = {field: page_field_value(doc, field) for field in filter_fields}
    filter_key = encode_filter_key(filter_fields, filters)
    sort_key = encode_sort_key(page_key(doc, order_by), order_by)
    if len(filter_key) > _MAX_PAGE_INDEX_COMPONENT_BYTES:
        raise ValueError("page equality key exceeds the relational index size limit")
    if len(sort_key) > _MAX_PAGE_INDEX_COMPONENT_BYTES:
        raise ValueError("page sort key exceeds the relational index size limit")
    return filter_key, sort_key


class MemoryStore(Store):
    def __init__(self) -> None:
        self._d: dict[str, dict[str, Doc]] = {}
        self._lock = threading.RLock()

    def get(self, col, id):
        with self._lock:
            d = self._d.get(col, {}).get(id)
            return json.loads(json.dumps(d)) if d is not None else None

    def put(self, col, id, doc):
        with self._lock:
            self._d.setdefault(col, {})[id] = json.loads(json.dumps(doc))

    def delete(self, col, id):
        with self._lock:
            self._d.get(col, {}).pop(id, None)

    def list(self, col, **equals):
        with self._lock:
            return [json.loads(json.dumps(d)) for d in self._d.get(col, {}).values() if _match(d, equals)]

    def create(self, col, id, doc):
        with self._lock:
            if id in self._d.get(col, {}):
                return False
            self.put(col, id, doc)
            return True

    def replace(self, col, id, doc, expected_rev):
        with self._lock:
            cur = self._d.get(col, {}).get(id)
            if cur is None or int(cur.get(REV, 0)) != expected_rev:
                return False
            self.put(col, id, {**doc, REV: expected_rev + 1})
            return True


class SQLiteStore(Store):
    def __init__(self, path: str = ":memory:") -> None:
        self._c = sqlite3.connect(path, check_same_thread=False)
        self._c.execute("CREATE TABLE IF NOT EXISTS docs (col TEXT, id TEXT, body TEXT, PRIMARY KEY (col, id))")
        self._c.execute(
            "CREATE TABLE IF NOT EXISTS portal_page_specs "
            "(col TEXT NOT NULL, spec TEXT NOT NULL, filter_fields TEXT NOT NULL, order_fields TEXT NOT NULL, "
            "PRIMARY KEY (col, spec))"
        )
        self._c.execute(
            "CREATE TABLE IF NOT EXISTS portal_page_keys "
            "(col TEXT NOT NULL, id TEXT NOT NULL, spec TEXT NOT NULL, filter_key BLOB NOT NULL, "
            "sort_key BLOB NOT NULL, PRIMARY KEY (col, spec, id))"
        )
        self._c.execute(
            "CREATE INDEX IF NOT EXISTS idx_portal_page_keys_lookup "
            "ON portal_page_keys (col, spec, filter_key, sort_key)"
        )
        self._lock = threading.RLock()

    def _sync_page_keys(self, col: str, id: str, doc: Doc | None) -> None:
        specs = self._c.execute(
            "SELECT spec, filter_fields, order_fields FROM portal_page_specs WHERE col=?", (col,)
        ).fetchall()
        for spec, raw_filters, raw_order in specs:
            if doc is None:
                self._c.execute(
                    "DELETE FROM portal_page_keys WHERE col=? AND spec=? AND id=?", (col, spec, id)
                )
                continue
            filters = tuple(json.loads(raw_filters))
            order_by = order_from_json(raw_order)
            filter_key, sort_key = _page_index_record(doc, filters, order_by)
            self._c.execute(
                "INSERT OR REPLACE INTO portal_page_keys VALUES (?,?,?,?,?)",
                (col, id, spec, filter_key, sort_key),
            )

    def _ensure_page_index(
        self,
        col: str,
        spec: str,
        filter_fields: str,
        order_fields: str,
        order_by: tuple[PageOrder, ...],
    ) -> None:
        with self._lock, self._c:
            inserted = self._c.execute(
                "INSERT OR IGNORE INTO portal_page_specs VALUES (?,?,?,?)",
                (col, spec, filter_fields, order_fields),
            ).rowcount
            if not inserted:
                return
            rows = self._c.execute("SELECT id, body FROM docs WHERE col=?", (col,)).fetchall()
            values = []
            filters = tuple(json.loads(filter_fields))
            for id, body in rows:
                filter_key, sort_key = _page_index_record(json.loads(body), filters, order_by)
                values.append((col, id, spec, filter_key, sort_key))
            self._c.executemany("INSERT INTO portal_page_keys VALUES (?,?,?,?,?)", values)

    def get(self, col, id):
        with self._lock:
            r = self._c.execute("SELECT body FROM docs WHERE col=? AND id=?", (col, id)).fetchone()
        return json.loads(r[0]) if r else None

    def put(self, col, id, doc):
        with self._lock, self._c:
            self._c.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?)", (col, id, json.dumps(doc)))
            self._sync_page_keys(col, id, doc)

    def delete(self, col, id):
        with self._lock, self._c:
            self._c.execute("DELETE FROM docs WHERE col=? AND id=?", (col, id))
            self._sync_page_keys(col, id, None)

    def list(self, col, **equals):
        with self._lock:
            rows = self._c.execute("SELECT body FROM docs WHERE col=?", (col,)).fetchall()
        return [d for d in (json.loads(r[0]) for r in rows) if _match(d, equals)]

    def create(self, col, id, doc):
        with self._lock, self._c:
            cur = self._c.execute("INSERT OR IGNORE INTO docs VALUES (?,?,?)", (col, id, json.dumps(doc)))
            if cur.rowcount == 1:
                self._sync_page_keys(col, id, doc)
            return cur.rowcount == 1

    def replace(self, col, id, doc, expected_rev):
        with self._lock, self._c:  # the lock serialises threads; the body-equality guard protects against other processes
            r = self._c.execute("SELECT body FROM docs WHERE col=? AND id=?", (col, id)).fetchone()
            if not r or int(json.loads(r[0]).get(REV, 0)) != expected_rev:
                return False
            cur = self._c.execute("UPDATE docs SET body=? WHERE col=? AND id=? AND body=?",
                                  (json.dumps({**doc, REV: expected_rev + 1}), col, id, r[0]))
            if cur.rowcount == 1:
                self._sync_page_keys(col, id, {**doc, REV: expected_rev + 1})
            return cur.rowcount == 1

    def page(
        self,
        col: str,
        *,
        order_by: tuple[PageOrder, ...],
        after: tuple[Any, ...] | None,
        limit: int,
        equals: Mapping[str, Any] | None = None,
    ) -> list[Doc]:
        if not 1 <= limit <= 500:
            raise ValueError("page limit must be between 1 and 500")
        if not order_by:
            raise ValueError("page ordering must not be empty")
        filters = equals or {}
        filter_fields = tuple(sorted(filters))
        spec, filters_json, order_json = page_index_spec(order_by, filter_fields)
        self._ensure_page_index(col, spec, filters_json, order_json, order_by)
        filter_key = encode_filter_key(filter_fields, dict(filters))
        query = (
            "SELECT d.body FROM docs d JOIN portal_page_keys p "
            "ON d.col=p.col AND d.id=p.id "
            "WHERE p.col=? AND p.spec=? AND p.filter_key=?"
        )
        params: list[Any] = [col, spec, filter_key]
        if after is not None:
            query += " AND p.sort_key>?"
            params.append(encode_sort_key(after, order_by))
        query += " ORDER BY p.sort_key LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._c.execute(query, params).fetchall()
        return [json.loads(row[0]) for row in rows]


class FirestoreStore(Store):
    """Firestore adapter (requires `google-cloud-firestore`, imported lazily)."""

    def __init__(self, client=None) -> None:
        if client is None:
            from google.cloud import firestore  # type: ignore

            client = firestore.Client()
        self._db = client

    @staticmethod
    def _public_doc(doc: Doc | None) -> Doc | None:
        if doc is None:
            return None
        return {key: value for key, value in doc.items() if not key.startswith("_page_sort_")}

    def _active_page_orders(self, col: str) -> tuple[PageOrder, ...]:
        orders: dict[tuple[str, type, bool, bool], PageOrder] = {}
        specs = self._db.collection("_portal_page_specs").where("col", "==", col).stream()
        for spec in specs:
            for order in order_from_json(spec.get("order_fields")):
                orders[(order.field, order.value_type, order.descending, order.casefold)] = order
        return tuple(orders.values())

    def _with_page_fields(self, col: str, doc: Doc) -> Doc:
        result = dict(doc)
        for order in self._active_page_orders(col):
            if order.casefold:
                value = page_field_value(doc, order.field)
                if isinstance(value, str):
                    result[firestore_order_field(order)] = value.casefold()
        return result

    def _ensure_casefold_fields(self, col: str, order_by: tuple[PageOrder, ...]) -> None:
        fields = {firestore_order_field(order): order for order in order_by if order.casefold}
        for alias, order in fields.items():
            state_id = f"{col}:{alias}"
            state = self._db.collection("_portal_page_state").document(state_id)
            if state.get().exists:
                continue
            batch = self._db.batch()
            pending = 0
            for snapshot in self._db.collection(col).stream():
                doc = snapshot.to_dict() or {}
                value = page_field_value(doc, order.field)
                if not isinstance(value, str) or doc.get(alias) == value.casefold():
                    continue
                batch.update(snapshot.reference, {alias: value.casefold()})
                pending += 1
                if pending == 450:
                    batch.commit()
                    batch = self._db.batch()
                    pending = 0
            if pending:
                batch.commit()
            state.set({"done": True})

    def get(self, col, id):
        s = self._db.collection(col).document(id).get()
        return self._public_doc(s.to_dict()) if s.exists else None

    def put(self, col, id, doc):
        self._db.collection(col).document(id).set(self._with_page_fields(col, doc))

    def delete(self, col, id):
        self._db.collection(col).document(id).delete()

    def list(self, col, **equals):
        q = self._db.collection(col)
        for k, v in equals.items():
            q = q.where(k, "==", v)
        return [self._public_doc(s.to_dict()) or {} for s in q.stream()]

    def page(
        self,
        col: str,
        *,
        order_by: tuple[PageOrder, ...],
        after: tuple[Any, ...] | None,
        limit: int,
        equals: Mapping[str, Any] | None = None,
    ) -> list[Doc]:
        if not 1 <= limit <= 500:
            raise ValueError("page limit must be between 1 and 500")
        if not order_by:
            raise ValueError("page ordering must not be empty")
        filters = dict(equals or {})
        spec, filters_json, order_json = page_index_spec(order_by, tuple(sorted(filters)))
        self._db.collection("_portal_page_specs").document(f"{col}:{spec}").set(
            {"col": col, "spec": spec, "filter_fields": filters_json, "order_fields": order_json},
            merge=True,
        )
        self._ensure_casefold_fields(col, order_by)
        query = self._db.collection(col)
        for field, value in filters.items():
            query = query.where(field, "==", value)
        for order in order_by:
            direction = "DESCENDING" if order.descending else "ASCENDING"
            query = query.order_by(firestore_order_field(order), direction=direction)
        if after is not None:
            query = query.start_after({
                firestore_order_field(order): value for order, value in zip(order_by, after)
            })
        return [
            self._public_doc(snapshot.to_dict()) or {}
            for snapshot in query.limit(limit).stream()
        ]

    def create(self, col, id, doc):
        from google.api_core.exceptions import AlreadyExists  # type: ignore

        try:
            self._db.collection(col).document(id).create(self._with_page_fields(col, doc))
            return True
        except AlreadyExists:
            return False

    def replace(self, col, id, doc, expected_rev):
        from google.cloud import firestore  # type: ignore

        ref = self._db.collection(col).document(id)
        updated = self._with_page_fields(col, {**doc, REV: expected_rev + 1})

        @firestore.transactional
        def txn(t) -> bool:
            snap = ref.get(transaction=t)
            if not snap.exists or int((snap.to_dict() or {}).get(REV, 0)) != expected_rev:
                return False
            t.set(ref, updated)
            return True

        return txn(self._db.transaction())


class ChunkingStore(Store):
    """Wraps any store whose documents have a size cap (Firestore: 1 MiB) by sharding large top-level strings."""

    MARK = "\x00chunked:"

    def __init__(self, inner: Store, max_value: int = 400_000) -> None:
        self.inner, self.max = inner, max_value

    def _pack(self, col: str, id: str, doc: Doc) -> Doc:
        out, nonce = dict(doc), secrets.token_hex(4)  # unique chunk names per write: a losing writer can never clobber the winner's chunks
        for k, v in doc.items():
            if isinstance(v, str) and len(v) > self.max:
                owner = f"{id}~{nonce}"
                parts = [v[i:i + self.max] for i in range(0, len(v), self.max)]
                for i, part in enumerate(parts):
                    self.inner.put(f"{col}__chunks", f"{owner}.{k}.{i}", {"d": part})
                out[k] = f"{self.MARK}{len(parts)}:{owner}"
        return out

    def _unpack(self, col: str, id: str, doc: Doc | None) -> Doc | None:
        if doc is None:
            return None
        for k, v in list(doc.items()):
            if isinstance(v, str) and v.startswith(self.MARK):
                n, _, owner = v[len(self.MARK):].partition(":")
                parts = []
                for i in range(int(n)):
                    chunk = self.inner.get(f"{col}__chunks", f"{owner}.{k}.{i}")
                    if chunk is None or "d" not in chunk:
                        raise _MissingChunk(f"{col}/{id}: chunk generation changed during read")
                    parts.append(chunk["d"])
                doc[k] = "".join(parts)
        return doc

    def _drop_doc(self, col: str, old: Doc | None) -> None:
        for k, v in (old or {}).items():
            if isinstance(v, str) and v.startswith(self.MARK):
                n, _, owner = v[len(self.MARK):].partition(":")
                for i in range(int(n)):
                    self.inner.delete(f"{col}__chunks", f"{owner}.{k}.{i}")

    def get(self, col, id):
        for _ in range(3):
            doc = self.inner.get(col, id)
            try:
                return self._unpack(col, id, doc)
            except _MissingChunk as exc:
                latest = self.inner.get(col, id)
                if latest is None:
                    return None
                if latest == doc:
                    raise Conflict(f"{col}/{id}: chunk data is missing") from exc
        raise Conflict(f"{col}/{id}: document changed repeatedly while reading chunks")

    def put(self, col, id, doc):
        old = self.inner.get(col, id)
        self.inner.put(col, id, self._pack(col, id, doc))
        self._drop_doc(col, old)

    def delete(self, col, id):
        old = self.inner.get(col, id)
        self.inner.delete(col, id)
        self._drop_doc(col, old)

    def list(self, col, **equals):
        for _ in range(3):
            docs = self.inner.list(col, **equals)
            try:
                return [self._unpack(col, "", d) for d in docs]
            except _MissingChunk:
                continue
        raise Conflict(f"{col}: documents changed repeatedly while reading chunks")

    def page(self, col, *, order_by, after, limit, equals=None):
        for _ in range(3):
            docs = self.inner.page(
                col, order_by=order_by, after=after, limit=limit, equals=equals
            )
            try:
                return [self._unpack(col, "", doc) for doc in docs]
            except _MissingChunk:
                continue
        raise Conflict(f"{col}: documents changed repeatedly while reading chunks")

    def create(self, col, id, doc):
        if self.inner.get(col, id) is not None:
            return False
        packed = self._pack(col, id, doc)
        if self.inner.create(col, id, packed):
            return True
        self._drop_doc(col, packed)
        return False

    def replace(self, col, id, doc, expected_rev):
        old, packed = self.inner.get(col, id), self._pack(col, id, doc)
        if old is not None and self.inner.replace(col, id, packed, expected_rev):
            self._drop_doc(col, old)
            return True
        self._drop_doc(col, packed)
        return False


class SQLStore(Store):
    """Portable relational store (SQLAlchemy): PostgreSQL/Supabase, Azure SQL / SQL Server, MySQL, SQLite.

    One table of JSON documents keyed by (col, id), so adding a collection never needs a migration.
    """

    def __init__(self, url: str) -> None:
        import sqlalchemy as sa  # imported lazily: only needed for this backend

        self._sa = sa
        self._e = sa.create_engine(url, pool_pre_ping=True)
        m = sa.MetaData()
        self._t = sa.Table("portal_docs", m, sa.Column("col", sa.String(64), primary_key=True),
                           sa.Column("id", sa.String(255), primary_key=True), sa.Column("body", sa.UnicodeText, nullable=False))
        self._page_specs = sa.Table(
            "portal_page_specs",
            m,
            sa.Column("col", sa.String(64), primary_key=True),
            sa.Column("spec", sa.String(64), primary_key=True),
            sa.Column("filter_fields", sa.UnicodeText, nullable=False),
            sa.Column("order_fields", sa.UnicodeText, nullable=False),
        )
        self._page_keys = sa.Table(
            "portal_page_keys",
            m,
            sa.Column("col", sa.String(64), primary_key=True),
            sa.Column("spec", sa.String(64), primary_key=True),
            sa.Column("id", sa.String(255), primary_key=True),
            sa.Column("filter_key", sa.LargeBinary(_MAX_PAGE_INDEX_COMPONENT_BYTES), nullable=False),
            sa.Column("sort_key", sa.LargeBinary(_MAX_PAGE_INDEX_COMPONENT_BYTES), nullable=False),
        )
        sa.Index(
            "idx_portal_page_keys_lookup",
            self._page_keys.c.col,
            self._page_keys.c.spec,
            self._page_keys.c.filter_key,
            self._page_keys.c.sort_key,
        )
        m.create_all(self._e)
        self._page_lock = threading.RLock()

    def _sync_page_keys(self, conn, col: str, id: str, doc: Doc | None) -> None:
        sa, keys, specs = self._sa, self._page_keys, self._page_specs
        rows = conn.execute(
            sa.select(specs.c.spec, specs.c.filter_fields, specs.c.order_fields).where(specs.c.col == col)
        ).all()
        for spec, raw_filters, raw_order in rows:
            conn.execute(sa.delete(keys).where(keys.c.col == col, keys.c.spec == spec, keys.c.id == id))
            if doc is None:
                continue
            filters = tuple(json.loads(raw_filters))
            order_by = order_from_json(raw_order)
            filter_key, sort_key = _page_index_record(doc, filters, order_by)
            conn.execute(keys.insert().values(
                col=col, spec=spec, id=id, filter_key=filter_key, sort_key=sort_key
            ))

    def _ensure_page_index(
        self,
        col: str,
        spec: str,
        filter_fields: str,
        order_fields: str,
        order_by: tuple[PageOrder, ...],
    ) -> None:
        sa, specs, keys = self._sa, self._page_specs, self._page_keys
        with self._page_lock:
            with self._e.connect() as conn:
                if conn.execute(
                    sa.select(specs.c.spec).where(specs.c.col == col, specs.c.spec == spec)
                ).first():
                    return
            try:
                with self._e.begin() as conn:
                    conn.execute(specs.insert().values(
                        col=col, spec=spec, filter_fields=filter_fields, order_fields=order_fields
                    ))
                    rows = conn.execute(
                        sa.select(self._t.c.id, self._t.c.body).where(self._t.c.col == col)
                    ).all()
                    filters = tuple(json.loads(filter_fields))
                    values = []
                    for id, body in rows:
                        filter_key, sort_key = _page_index_record(json.loads(body), filters, order_by)
                        values.append({
                            "col": col, "spec": spec, "id": id,
                            "filter_key": filter_key, "sort_key": sort_key,
                        })
                    if values:
                        conn.execute(keys.insert(), values)
            except sa.exc.IntegrityError:
                with self._e.connect() as conn:
                    if not conn.execute(
                        sa.select(specs.c.spec).where(specs.c.col == col, specs.c.spec == spec)
                    ).first():
                        raise

    def get(self, col, id):
        t = self._t
        with self._e.connect() as c:
            r = c.execute(self._sa.select(t.c.body).where(t.c.col == col, t.c.id == id)).first()
        return json.loads(r[0]) if r else None

    def put(self, col, id, doc):
        t, body = self._t, json.dumps(doc)
        with self._e.begin() as c:
            if c.execute(t.update().where(t.c.col == col, t.c.id == id).values(body=body)).rowcount == 0:
                try:
                    with c.begin_nested():
                        c.execute(t.insert().values(col=col, id=id, body=body))
                except self._sa.exc.IntegrityError:  # lost a race with another writer: update instead
                    c.execute(t.update().where(t.c.col == col, t.c.id == id).values(body=body))
            self._sync_page_keys(c, col, id, doc)

    def create(self, col, id, doc):
        try:
            with self._e.begin() as c:
                c.execute(self._t.insert().values(col=col, id=id, body=json.dumps(doc)))
                self._sync_page_keys(c, col, id, doc)
            return True
        except self._sa.exc.IntegrityError:
            return False

    def replace(self, col, id, doc, expected_rev):
        t = self._t
        with self._e.begin() as c:
            raw = c.execute(self._sa.select(t.c.body).where(t.c.col == col, t.c.id == id)).scalar()
            if raw is None or int(json.loads(raw).get(REV, 0)) != expected_rev:
                return False
            # the WHERE on the exact body we read makes this a compare-and-swap on every SQL dialect, with no extra column
            n = c.execute(t.update().where(t.c.col == col, t.c.id == id, t.c.body == raw)
                          .values(body=json.dumps({**doc, REV: expected_rev + 1}))).rowcount
            if n == 1:
                self._sync_page_keys(c, col, id, {**doc, REV: expected_rev + 1})
            return n == 1

    def delete(self, col, id):
        t = self._t
        with self._e.begin() as c:
            c.execute(t.delete().where(t.c.col == col, t.c.id == id))
            self._sync_page_keys(c, col, id, None)

    def list(self, col, **equals):
        t = self._t
        with self._e.connect() as c:
            rows = c.execute(self._sa.select(t.c.body).where(t.c.col == col)).all()
        return [d for d in (json.loads(r[0]) for r in rows) if _match(d, equals)]

    def page(
        self,
        col: str,
        *,
        order_by: tuple[PageOrder, ...],
        after: tuple[Any, ...] | None,
        limit: int,
        equals: Mapping[str, Any] | None = None,
    ) -> list[Doc]:
        if not 1 <= limit <= 500:
            raise ValueError("page limit must be between 1 and 500")
        if not order_by:
            raise ValueError("page ordering must not be empty")
        sa, t, keys = self._sa, self._t, self._page_keys
        filters = dict(equals or {})
        filter_fields = tuple(sorted(filters))
        spec, filters_json, order_json = page_index_spec(order_by, filter_fields)
        self._ensure_page_index(col, spec, filters_json, order_json, order_by)
        filter_key = encode_filter_key(filter_fields, filters)
        query = (
            sa.select(t.c.body)
            .select_from(t.join(keys, sa.and_(t.c.col == keys.c.col, t.c.id == keys.c.id)))
            .where(keys.c.col == col, keys.c.spec == spec, keys.c.filter_key == filter_key)
        )
        if after is not None:
            query = query.where(keys.c.sort_key > encode_sort_key(after, order_by))
        query = query.order_by(keys.c.sort_key).limit(limit)
        with self._e.connect() as conn:
            rows = conn.execute(query).all()
        return [json.loads(row[0]) for row in rows]


def open_store(spec: str) -> Store:
    """`memory` | `sqlite:<path>` | `sql:<sqlalchemy url>` (or `sql` + DATABASE_URL) | `firestore` | `pkg.module:factory`."""
    from .settings import env_secret

    if spec == "memory":
        return MemoryStore()
    if spec.startswith("sqlite:"):
        return SQLiteStore(spec[7:] or ":memory:")
    if spec == "sql" or spec.startswith("sql:"):
        url = spec[4:] or env_secret("DATABASE_URL", "")
        if not url:
            raise ValueError("sql store needs a SQLAlchemy URL (sql:<url> or DATABASE_URL)")
        return SQLStore(url)
    if spec == "firestore":
        return ChunkingStore(FirestoreStore())
    if ":" in spec and "/" not in spec:  # custom backend: "my_pkg.module:factory" returning a Store
        import importlib

        mod, _, fn = spec.partition(":")
        store = getattr(importlib.import_module(mod), fn)()
        if not isinstance(store, Store):
            raise TypeError(f"{spec} did not return a Store")
        return store
    raise ValueError(f"unknown store {spec!r}")


class AccessLog:
    """Per-request record of store operations; `AuditedStore` appends, the HTTP middleware flushes it as ONE audit entry."""

    def __init__(self, requester: str) -> None:
        self.requester, self.ops, self._lock = requester, {}, threading.Lock()

    def add(self, op: str, col: str, ident: str | None) -> None:
        with self._lock:
            e = self.ops.setdefault(f"{op}:{col}", {"n": 0, "ids": []})
            e["n"] += 1
            if ident and len(e["ids"]) < 20 and ident not in e["ids"]:
                e["ids"].append(ident)


ACCESS: contextvars.ContextVar[AccessLog | None] = contextvars.ContextVar("portal_access", default=None)
_UNLINKED: contextvars.ContextVar[bool] = contextvars.ContextVar("portal_unlinked", default=False)


@contextlib.contextmanager
def unlinked_access() -> Iterator[None]:
    """Store calls inside are counted in the access log without document ids (e.g. secret ballots)."""
    token = _UNLINKED.set(True)
    try:
        yield
    finally:
        _UNLINKED.reset(token)


class AuditedStore(Store):
    """Records which identity read or wrote which collection/ids (never values). Ops are batched per request into a single
    audit entry, which keeps full traceability ("who touched what") without a log row per query. Audit tables are exempt."""

    def __init__(self, inner: Store) -> None:
        self.inner = inner

    def _note(self, op: str, col: str, ident: str | None = None) -> None:
        a = ACCESS.get()
        if a is not None and not col.startswith("audit"):
            a.add(op, col, None if _UNLINKED.get() else ident)

    def get(self, col, id):
        self._note("read", col, id)
        return self.inner.get(col, id)

    def put(self, col, id, doc):
        self._note("write", col, id)
        self.inner.put(col, id, doc)

    def delete(self, col, id):
        self._note("delete", col, id)
        self.inner.delete(col, id)

    def create(self, col, id, doc):
        self._note("write", col, id)
        return self.inner.create(col, id, doc)

    def replace(self, col, id, doc, expected_rev):
        self._note("write", col, id)
        return self.inner.replace(col, id, doc, expected_rev)

    def list(self, col, **equals):
        self._note("query", col)
        return self.inner.list(col, **equals)

    def page(self, col, *, order_by, after, limit, equals=None):
        self._note("query", col)
        return self.inner.page(
            col, order_by=order_by, after=after, limit=limit, equals=equals
        )
