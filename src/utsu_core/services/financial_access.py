"""Mandatory, value-free access auditing for financial records."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from ..store import ACCESS, Store

if TYPE_CHECKING:
    from ..context import Context

FINANCIAL_COLLECTIONS = frozenset({
    "financial_profiles", "sap_banking", "orders", "payment_events",
    "transactions", "receipts", "submissions",
})


class FinancialAccessStore(Store):
    def __init__(self, inner: Store, ctx: Context):
        self.inner, self.ctx = inner, ctx

    def _audit(self, operation: str, collection: str, key: str | None = None) -> None:
        if collection not in FINANCIAL_COLLECTIONS:
            return
        if collection == "submissions" and key and not key.startswith("student_aid_bursary:"):
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
        if col != "submissions" or equals.get("form_id") in (None, "student_aid_bursary"):
            self._audit("list", col)
        return self.inner.list(col, **equals)

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
