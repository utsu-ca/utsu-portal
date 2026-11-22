"""Compatibility imports for student aid's shared financial profile workflow."""
from typing import Any

from fastapi import APIRouter, HTTPException

from utsu_core.context import Context
from utsu_core.contracts import FormsService
from utsu_core.permissions import PERM_SAP_APPLICATIONS, PERM_SAP_FINANCE
from utsu_core.services.banking import (
    BANKING_ACTIVE, BANKING_FIELDS, CHEQUE_DATA_URL,
    AcceptBankingIn, BankingIn, BankingRoutePolicy, DestroyBankingIn, ReverifyBankingIn,
    banking_router as shared_banking_router, parse_void_cheque,
)


def student_aid_banking_policy(forms: FormsService) -> BankingRoutePolicy:
    def validate_submission(uid: str) -> None:
        submission = forms.submission("student_aid_bursary", uid)
        if not submission or submission["status"] == "rejected":
            raise HTTPException(409, "an active student aid application is required")

    def listing_fields(uid: str) -> dict[str, Any]:
        submission: dict[str, Any] = forms.submission("student_aid_bursary", uid) or {}
        review: dict[str, Any] = submission.get("sap_review") or {}
        decision: dict[str, Any] = review.get("decision") or {}
        return {"approved": decision.get("approved"), "awarded_amount": decision.get("amount")}

    return BankingRoutePolicy(
        mine="/sap/banking", staff="/admin/sap/applications/{uid}/banking",
        finance_path="/finance/sap/banking", admin_permission=PERM_SAP_APPLICATIONS,
        finance_permission=PERM_SAP_FINANCE, audit_owner="sap", email_template="sap_banking_reverify",
        finance_detail_routes=True, validate_submission=validate_submission, listing_fields=listing_fields,
    )


def legacy_banking_policy(ctx: Context) -> BankingRoutePolicy:
    return student_aid_banking_policy(ctx.forms.for_owner("sap"))


def banking_router(ctx: Context, *, legacy: bool = False) -> APIRouter:
    return shared_banking_router(ctx, policy=legacy_banking_policy(ctx) if legacy else None)


__all__ = [
    "BANKING_ACTIVE", "BANKING_FIELDS", "CHEQUE_DATA_URL", "AcceptBankingIn",
    "BankingIn", "DestroyBankingIn", "ReverifyBankingIn", "banking_router", "parse_void_cheque",
]
