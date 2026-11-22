"""Profile-associated financial records, separate from ordinary user profiles."""
from __future__ import annotations

import base64
import hashlib

from fastapi import APIRouter

from ..context import Context
from ..contracts import FileKind
from ..services.banking import BANKING_ACTIVE, CHEQUE_DATA_URL, banking_router


class ProfileModule:
    name = "profile"

    def configure(self, ctx: Context) -> None:
        finance_permissions = tuple(sorted({
            scope for role in ("sap_admin", "sap_finance", "profile_finance") if role in ctx.roles
            for scope in ctx.role_permissions.get(role, ())
        }))
        for bank in ctx.store.list("sap_banking"):
            uid = bank["uuid"]
            if not ctx.store.create("financial_profiles", uid, bank):
                current = ctx.store.get("financial_profiles", uid)
                if current != bank:
                    raise RuntimeError(f"conflicting legacy financial profile for {uid}")
            ctx.store.delete("sap_banking", uid)

        def cheque(uid: str) -> dict | None:
            bank = ctx.store.get("financial_profiles", uid)
            if not bank or bank.get("status") not in BANKING_ACTIVE:
                return None
            return (bank.get("pending") or {}).get("void_cheque") or bank.get("void_cheque")

        def authorized(action: str, actor: str, uid: str, _reference: str) -> bool:
            account = ctx.identity.account(actor)
            return bool(
                action == "read" and account and account.get("status") == "verified"
                and ctx.has_any_permission(account, *finance_permissions)
            )

        def read(_reference: str, uid: str) -> bytes | None:
            stored = cheque(uid)
            if not stored:
                return None
            match = CHEQUE_DATA_URL.fullmatch(stored["data_url"])
            if not match:
                raise ValueError("stored void cheque is not a PDF data URL")
            return base64.b64decode(match.group(1), validate=True)

        ctx.files.register_kind(
            self.name, "sap_void_cheque",
            FileKind(
                authorize=authorized, read=read, audit_action="sap.void_cheque_accessed",
                audit_target=lambda uid, _reference: f"user:{uid}",
                audit_extra=lambda _uid, _reference, content: {
                    "sha256": hashlib.sha256(content or b"").hexdigest()
                },
            ),
        )

    def router(self, ctx: Context) -> APIRouter:
        router = banking_router(ctx)

        @router.get("/api/profile/storage")
        def storage_usage(account: dict = ctx.require_verified()):
            return ctx.upload_quotas.usage(f"user:{account['uuid']}")

        return router
