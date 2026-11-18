"""Student aid program module assembler."""
from __future__ import annotations

import base64
import hashlib

from fastapi import APIRouter

from ...context import Context, roles_of
from ...contracts import FileKind, NotificationType, SubmissionPolicy
from ...services.forms import register_form_resource
from ...services.submissions import register_submission_policy
from .applications import (
    HistoricalDecision,
    HistoricalRating,
    StudentAidSubmissionPolicy,
    applications_router,
)
from .banking import (
    BANKING_ACTIVE,
    BANKING_FIELDS,
    CHEQUE_DATA_URL,
    AcceptBankingIn,
    BankingIn,
    DestroyBankingIn,
    ReverifyBankingIn,
    banking_router,
    parse_void_cheque,
)
from .review import (
    AssignReviewIn,
    DecisionIn,
    EMERGENCY_SUBSIDY,
    RatingIn,
    ResolveReviewIn,
    ReviewCountIn,
    ReviewHardshipIn,
    ReviewModule,
    WeightsIn,
    application_view,
    reviewer_view,
    review_stage,
    scoring,
)

__all__ = [
    "AcceptBankingIn",
    "AssignReviewIn",
    "BANKING_ACTIVE",
    "BANKING_FIELDS",
    "BankingIn",
    "CHEQUE_DATA_URL",
    "DecisionIn",
    "DestroyBankingIn",
    "EMERGENCY_SUBSIDY",
    "HistoricalDecision",
    "HistoricalRating",
    "RatingIn",
    "ReverifyBankingIn",
    "ResolveReviewIn",
    "ReviewCountIn",
    "ReviewHardshipIn",
    "SapModule",
    "WeightsIn",
    "application_view",
    "parse_void_cheque",
    "review_stage",
    "reviewer_view",
    "scoring",
]


class SapModule:
    name = "sap"

    def configure(self, ctx: Context) -> None:
        register_form_resource(ctx, self.name, "student_aid_bursary", __package__)

        def cheque(uid: str) -> dict | None:
            bank = ctx.store.get("sap_banking", uid)
            if not bank or bank.get("status") not in BANKING_ACTIVE:
                return None
            return ((bank.get("pending") or {}).get("void_cheque")
                    or bank.get("void_cheque"))

        def authorized(action: str, actor: str, uid: str, _reference: str) -> bool:
            account = ctx.identity.account(actor)
            return bool(
                action == "read"
                and account
                and roles_of(account, ctx.roles) & {"sap_admin", "sap_finance", "admin"}
                and cheque(uid)
            )

        def read(_reference: str, uid: str) -> bytes | None:
            stored = cheque(uid)
            if not stored:
                return None
            match = CHEQUE_DATA_URL.fullmatch(stored["data_url"])
            if not match:
                raise ValueError("stored void cheque is not a PDF data URL")
            return base64.b64decode(match.group(1), validate=True)

        policy = StudentAidSubmissionPolicy()
        register_submission_policy(
            ctx,
            self.name,
            "student_aid_bursary",
            SubmissionPolicy(save=policy.save, validate_status_change=policy.validate_status_change),
        )
        ctx.notification_types.register(
            self.name, self.name, NotificationType("student_aid_bursary", "sap_admin")
        )
        ctx.files.register_kind(
            "sap",
            "sap_void_cheque",
            FileKind(
                authorize=authorized,
                read=read,
                audit_action="sap.void_cheque_accessed",
                audit_target=lambda uid, _reference: f"user:{uid}",
                audit_extra=lambda _uid, _reference, content: {
                    "sha256": hashlib.sha256(content or b"").hexdigest()
                },
            ),
        )

    def router(self, ctx: Context) -> APIRouter:
        router = APIRouter()
        router.include_router(banking_router(ctx))
        router.include_router(ReviewModule().router(ctx))
        router.include_router(applications_router(ctx))
        return router
