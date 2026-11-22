"""Field-level encryption at rest for sensitive documents (ID/face scans, receipts, audit snapshots)."""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .protection import JSON_SENSITIVE, SENSITIVE
from .pagination import PageOrder
from .store import Doc, Store

PREFIX = "enc:v1:"


def sensitive(col: str) -> tuple[str, ...]:
    return ("blob",) if col.startswith("audit_") else SENSITIVE.get(col, ())


class Crypter:
    """`keys` is a comma-separated list of Fernet keys: the first encrypts, all decrypt (so keys can be rotated).
    Without keys a key is derived from the portal secret, which is safe but ties data protection to that one secret."""

    def __init__(self, keys: str | None, fallback_secret: str) -> None:
        ks = [k.strip() for k in (keys or "").split(",") if k.strip()]
        if not ks:
            raw = HKDF(hashes.SHA256(), 32, None, b"utsu-portal-data-v1").derive(fallback_secret.encode())
            ks = [base64.urlsafe_b64encode(raw).decode()]
        self._f = MultiFernet([Fernet(k) for k in ks])

    def enc(self, s: str) -> str:
        return PREFIX + self._f.encrypt(s.encode()).decode()

    def dec(self, s: str) -> str:
        if not s.startswith(PREFIX):
            return s
        try:
            return self._f.decrypt(s[len(PREFIX):].encode()).decode()
        except InvalidToken as e:
            raise RuntimeError("cannot decrypt stored data: wrong or missing PORTAL_DATA_KEY") from e


class EncryptingStore(Store):
    def __init__(self, inner: Store, crypter: Crypter) -> None:
        self.inner, self.c = inner, crypter

    def _out(self, col: str, doc: Doc | None) -> Doc | None:
        if doc:
            for f in sensitive(col):
                if isinstance(doc.get(f), str):
                    value = self.c.dec(doc[f])
                    doc[f] = json.loads(value) if (col, f) in JSON_SENSITIVE else value
        return doc

    def _in(self, col: str, doc: Doc) -> Doc:
        out = dict(doc)
        for f in sensitive(col):
            if (col, f) in JSON_SENSITIVE and f in out:
                out[f] = self.c.enc(json.dumps(out[f], ensure_ascii=False))
            elif isinstance(out.get(f), str):
                out[f] = self.c.enc(out[f])
        return out

    def get(self, col, id):
        return self._out(col, self.inner.get(col, id))

    def put(self, col, id, doc):
        self.inner.put(col, id, self._in(col, doc))

    def delete(self, col, id):
        self.inner.delete(col, id)

    def create(self, col, id, doc):
        return self.inner.create(col, id, self._in(col, doc))

    def replace(self, col, id, doc, expected_rev):
        return self.inner.replace(col, id, self._in(col, doc), expected_rev)

    def list(self, col: str, **equals: Any) -> list[Doc]:
        protected = set(sensitive(col))
        protected_filters = {key: value for key, value in equals.items() if key in protected}
        safe_filters = {key: value for key, value in equals.items() if key not in protected}
        documents = (self._out(col, doc) for doc in self.inner.list(col, **safe_filters))
        return [doc for doc in documents if doc is not None
                and all(doc.get(key) == value for key, value in protected_filters.items())]

    def page(self, col: str, *, order_by: tuple[PageOrder, ...], after: tuple[Any, ...] | None,
             limit: int, equals: Mapping[str, Any] | None = None) -> list[Doc]:
        protected = set(sensitive(col))
        fields = [order.field for order in order_by]
        fields.extend((equals or {}).keys())
        if any(field.split(".", 1)[0] in protected for field in fields):
            return super().page(col, order_by=order_by, after=after, limit=limit, equals=equals)
        documents = self.inner.page(col, order_by=order_by, after=after, limit=limit, equals=equals)
        for document in documents:
            self._out(col, document)
        return documents


def fingerprint(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]
