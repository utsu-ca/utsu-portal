"""Program-owned hooks for submission lifecycle rules."""
from __future__ import annotations

from fastapi import HTTPException

from ..context import Context
from ..contracts import SubmissionPolicy
from .account_check import is_active_member
from .roster import membership_session


def register_submission_policy(ctx: Context, owner: str, form_id: str, policy: SubmissionPolicy) -> None:
    ctx.form_registry.register_policy(owner, form_id, policy)


def get_submission_policy(ctx: Context, form_id: str) -> SubmissionPolicy | None:
    return ctx.form_registry.policy(form_id)


def validate_submission_eligibility(ctx: Context, policy: SubmissionPolicy | None, account: dict) -> dict | None:
    """
    Refuse a submission from an account that lacks the policy's permission. With `keep_membership` it returns the
    membership held now, to be stored on the submission so it stays eligible if the applicant later leaves the roster.
    """
    if not policy or not policy.permission:
        return None
    if not ctx.has_permission(account, policy.permission):
        check = ctx.identity.roster_check(account["uuid"])
        if check and check.get("rostered"):  # on a roster, but not the one of this membership session
            raise HTTPException(403, f"You are not an active member for {membership_session(ctx.now())}: the student "
                                     f"roster has not been updated for this session yet. Try again once it has.")
        reason = (check or {}).get("reason") or "no roster record"
        raise HTTPException(403, f"We could not verify your student status ({reason}). Check the name and "
                                 f"student number on your account, then submit again.")
    check = ctx.identity.roster_check(account["uuid"])
    if not policy.keep_membership or not is_active_member(check, ctx.now()):
        return None
    return {"session": membership_session(ctx.now()), "division": check.get("division"), "at": ctx.now()}
