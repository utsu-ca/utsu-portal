"""Program-owned hooks for submission lifecycle rules."""
from __future__ import annotations

from fastapi import HTTPException

from ..context import Context
from ..contracts import SubmissionPolicy


def register_submission_policy(ctx: Context, owner: str, form_id: str, policy: SubmissionPolicy) -> None:
    ctx.form_registry.register_policy(owner, form_id, policy)


def get_submission_policy(ctx: Context, form_id: str) -> SubmissionPolicy | None:
    return ctx.form_registry.policy(form_id)


def validate_submission_eligibility(ctx: Context, policy: SubmissionPolicy | None, account: dict) -> None:
    if not policy or not policy.require_roster:
        return
    check = ctx.identity.roster_check(account["uuid"])
    if not check or not check.get("rostered"):
        reason = (check or {}).get("reason") or "no roster record"
        raise HTTPException(403, f"We could not verify your student status ({reason}). Check the name and "
                                 f"student number on your account, then submit again.")
