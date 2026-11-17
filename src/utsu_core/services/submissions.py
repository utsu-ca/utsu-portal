"""Program-owned hooks for submission lifecycle rules."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException

from ..context import Context


@dataclass(frozen=True)
class SubmissionPolicy:
    require_roster: bool = False
    save: Callable[[dict | None, dict], dict] | None = None
    validate_status_change: Callable[[dict], None] | None = None


def register_submission_policy(ctx: Context, form_id: str, policy: SubmissionPolicy) -> None:
    policies = ctx.services.setdefault("submission_policies", {})
    if form_id in policies:
        raise ValueError(f"a submission policy is already registered for {form_id}")
    policies[form_id] = policy


def get_submission_policy(ctx: Context, form_id: str) -> SubmissionPolicy | None:
    return ctx.services.get("submission_policies", {}).get(form_id)


def validate_submission_eligibility(ctx: Context, policy: SubmissionPolicy | None, account: dict) -> None:
    if not policy or not policy.require_roster:
        return
    check = ctx.identity.roster_check(account["uuid"])
    if not check or not check.get("rostered"):
        reason = (check or {}).get("reason") or "no roster record"
        raise HTTPException(403, f"We could not verify your student status ({reason}). Check the name and "
                                 f"student number on your account, then submit again.")
