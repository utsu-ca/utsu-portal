"""Mandatory, value-free access auditing for financial records."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from ..pagination import PageOrder
from ..protection import FINANCIAL_AUDIT_COLLECTIONS, FINANCIAL_AUDIT_FORMS
from ..store import ACCESS, Doc, Store

if TYPE_CHECKING:
    from ..context import Context

class FinancialAccessStore(Store):
    def __init__(self, inner: Store, ctx: Context):
        self.inner, self.ctx = inner, ctx

    def _audit(self, operation: str, collection: str, key: str | None = None) -> None:
        if collection not in FINANCIAL_AUDIT_COLLECTIONS and collection != "submissions":
            return
        if collection == "submissions" and key and not any(
            key.startswith(f"{form_id}:") for form_id in FINANCIAL_AUDIT_FORMS
        ):
            return
        access = ACCESS.get()
        actor = access.requester if access else "system:financial"
        if actor.startswith("anonymous:"):
            raise HTTPException(401, "financial records require authentication")
        # Record before touching data; an unavailable audit log must prevent access.
        self.ctx.audit.record(actor, "financial.access", f"{collection}:{key or '*'}",
                              None, None, {"operation": operation})

    def get(self, col, id):
        self._audit("get", col, id)
        return self.inner.get(col, id)

    def list(self, col, **equals):
        form_id = equals.get("form_id")
        if col != "submissions" or form_id is None or any(form_id == form for form in FINANCIAL_AUDIT_FORMS):
            self._audit("list", col)
        return self.inner.list(col, **equals)

    def page(self, col: str, *, order_by: tuple[PageOrder, ...], after: tuple[Any, ...] | None,
             limit: int, equals: Mapping[str, Any] | None = None) -> list[Doc]:
        form_id = (equals or {}).get("form_id")
        if col != "submissions" or form_id is None or any(form_id == form for form in FINANCIAL_AUDIT_FORMS):
            self._audit("list", col)
        return self.inner.page(col, order_by=order_by, after=after, limit=limit, equals=equals)

    def put(self, col, id, doc):
        self._audit("put", col, id)
        return self.inner.put(col, id, doc)

    def create(self, col, id, doc):
        self._audit("create", col, id)
        return self.inner.create(col, id, doc)

    def replace(self, col, id, doc, expected_rev):
        self._audit("replace", col, id)
        return self.inner.replace(col, id, doc, expected_rev)

    def delete(self, col, id):
        self._audit("delete", col, id)
        return self.inner.delete(col, id)
