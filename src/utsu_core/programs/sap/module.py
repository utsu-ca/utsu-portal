"""Student aid program module assembler."""
from __future__ import annotations

from fastapi import APIRouter

from ...context import Context
from ...services.submissions import SubmissionPolicy, register_submission_policy
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
        policy = StudentAidSubmissionPolicy()
        register_submission_policy(
            ctx,
            "student_aid_bursary",
            SubmissionPolicy(save=policy.save, validate_status_change=policy.validate_status_change),
        )

    def router(self, ctx: Context) -> APIRouter:
        router = APIRouter()
        router.include_router(banking_router(ctx))
        router.include_router(ReviewModule().router(ctx))
        router.include_router(applications_router(ctx))
        return router
