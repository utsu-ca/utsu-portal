"""Student aid program module assembler."""
from __future__ import annotations

from importlib import resources

from fastapi import APIRouter

from core.contracts import (
    FileAuthorization,
    FormContribution,
    NotificationType,
    PluginManifest,
    PluginServices,
    RegistrationContext,
    SubmissionPolicy,
)
from core.permissions import PERM_SAP_APPLICATIONS, PERM_SAP_APPLY, PERM_SAP_FINANCE, PERM_SAP_REVIEW
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
    student_aid_banking_policy,
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

_SAP_APPLICATIONS = "view student aid applications"
_SAP_FINANCE = "student aid banking: flag details that fail validation, review void cheques"
_SAP_REVIEW = "review student aid applications"

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
    manifest = PluginManifest(
        "sap",
        "0.1.0",
        1,
        "sabp.module:SapModule",
        dependencies=("forms", "profile"),
        order=60,
        form_ids=("student_aid_bursary",),
        interface="plugin",
        frontend=True,
        roles=(
            ("sap_admin", _SAP_APPLICATIONS),
            ("sap_finance", _SAP_FINANCE),
            ("sap_reviewer", _SAP_REVIEW),
        ),
        protection=(
            ("sap_banking", ("account_number", "institution_number", "pending", "transit_number", "void_cheque")),
            ("submissions", ("sap_previous", "sap_review", "sap_review_hardship")),
        ),
        json_protection=(
            ("sap_banking", ("pending", "void_cheque")),
            ("submissions", ("sap_previous", "sap_review")),
        ),
        settings=("sap_review_enabled",),
        services=(
            "audit",
            "auth",
            "file_authorizers",
            "forms",
            "identity",
            "migrations",
            "now",
            "sap_banking",
            "settings",
            "store",
        ),
        collections=("sap_config",),
        permissions=(
            (PERM_SAP_APPLICATIONS, "View and administer student aid applications."),
            (PERM_SAP_FINANCE, "Review and manage student aid banking details."),
            (PERM_SAP_REVIEW, "Review student aid applications."),
        ),
        role_permissions=(
            ("sap_admin", (PERM_SAP_APPLICATIONS,)),
            ("sap_finance", (PERM_SAP_FINANCE,)),
            ("sap_reviewer", (PERM_SAP_REVIEW,)),
        ),
        audit_collections=("sap_banking",),
        audit_forms=("student_aid_bursary",),
    )

    def configure(self, registration: RegistrationContext) -> None:
        form = resources.files(__package__).joinpath("forms", "student_aid_bursary.yaml").read_text(
            encoding="utf-8"
        )
        registration.register_form("student_aid_bursary", FormContribution(yaml=form))

        submission_policy = StudentAidSubmissionPolicy()
        registration.register_submission_policy(
            "student_aid_bursary",
            SubmissionPolicy(
                permission=PERM_SAP_APPLY,
                keep_membership=True,
                save=submission_policy.save,
                validate_status_change=submission_policy.validate_status_change,
            ),
        )
        registration.register_notification_type(
            "sap", NotificationType("student_aid_bursary", "sap_admin")
        )

        def file_authorizer(services: PluginServices) -> FileAuthorization:
            def authorize_cheque(action: str, actor: str, _uid: str, _reference: str) -> bool:
                account = services.identity.account(actor)
                return bool(
                    action == "read"
                    and account
                    and account.get("status") == "verified"
                    and services.auth.has_any_permission(
                        account, PERM_SAP_APPLICATIONS, PERM_SAP_FINANCE
                    )
                )

            return FileAuthorization(authorize_cheque)

        registration.register_file_authorizer("sap_void_cheque", file_authorizer)

    def router(self, services: PluginServices) -> APIRouter:
        router = APIRouter()
        router.include_router(
            services.sap_banking.router(student_aid_banking_policy(services.forms))
        )
        router.include_router(ReviewModule().router(services))
        router.include_router(applications_router(services))
        return router
