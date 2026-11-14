"""Student aid application views over portal submissions."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...context import Context, roles_of
from ...mailer import Mailer
from ...security import clean_text
from ...services.exports import csv_response
from .program import redact
from .terms import normalized_terms
from .wfni import QUESTIONS, WeightsError, compute_wfni, parse_weights

EMERGENCY_SUBSIDY = "Emergency Bursary (expedited processing)"


class WeightsIn(BaseModel):
    csv: str = Field(min_length=1, max_length=100000)


class RatingIn(BaseModel):
    level: float = Field(ge=0, le=5, allow_inf_nan=False)


class DecisionIn(BaseModel):
    approved: bool
    amount: float = Field(ge=0, le=5000, allow_inf_nan=False)


class HistoricalRating(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    level: float = Field(ge=0, le=5, allow_inf_nan=False)
    notes: str = Field(default="", max_length=1000)


class HistoricalDecision(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    terms: list[str] = Field(min_length=1, max_length=10)
    approved: bool
    amount: float = Field(ge=0, le=1000000, allow_inf_nan=False)
    decided_on: date
    ratings: list[HistoricalRating] = Field(default_factory=list, max_length=20)
    revision: int = Field(ge=0)
    confirm: bool


class BankingIn(BaseModel):
    institution_number: str = Field(pattern=r"^[0-9]{3}$")
    transit_number: str = Field(pattern=r"^[0-9]{5}$")
    account_number: str = Field(pattern=r"^[0-9]{1,12}$")


class DestroyBankingIn(BaseModel):
    confirm: bool


class ReverifyBankingIn(BankingIn):
    token: str = Field(min_length=1, max_length=128)


class AcceptBankingIn(BaseModel):
    revision: int = Field(ge=0)
    confirm: bool


class ReviewCountIn(BaseModel):
    required_reviews: int = Field(ge=2, le=5)


class ResolveReviewIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    revision: int = Field(ge=0)


class ReviewHardshipIn(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    confirm_redacted: bool
    revision: int = Field(ge=0)


class AssignReviewIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=128)


def review_stage(review: dict, now: int) -> str:
    if review.get("decision"):
        return "decided"
    ratings = [entry["level"] for entry in review["reviews"] if "level" in entry]
    if len(ratings) >= review.get("required_reviews", 2):
        return "discuss" if max(ratings) - min(ratings) >= 2 and not review.get("resolution") else "reconciled"
    return "in_review" if any("level" in entry or entry["lease_until"] > now for entry in review["reviews"]) else "ready"


def reviewer_view(submission: dict, now: int, *, include_hardship: bool = False) -> dict:
    data, review = submission["data"], submission["sap_review"]
    emergency = (EMERGENCY_SUBSIDY in data["lc_subsidies"] if "lc_subsidies" in data
                 else None) if data.get("bursary_type") == "Living-costs Bursary" else False
    result = {"review_id": review["id"], "stage": review_stage(review, now),
            "amount_requested": data["amount_requested"], "enrolment": data["enrolment"],
            "monthly_income": data["monthly_income"], "monthly_expenses": data["monthly_expenses"],
            "bursary_type": data.get("bursary_type"), "wfni": review["wfni"],
            "emergency": emergency,
            "submitted_hour": submission["updated_at"] // 3600 * 3600}
    if include_hardship:
        result["hardship"] = submission["sap_review_hardship"]
    return result


def scoring(ctx: Context, data: dict) -> dict:
    config = ctx.store.get("sap_config", "weights")
    if data.get("bursary_type") != "Living-costs Bursary":
        return {"score": None, "status": "not_applicable" if data.get("bursary_type") else "stream_missing"}
    if not config:
        return {"score": None, "status": "weights_not_configured"}
    missing = [field for field in QUESTIONS if not data.get(field)]
    if missing:
        return {"score": None, "status": "incomplete", "missing": missing}
    weights = parse_weights(config["csv"], "stored SAP weights")
    score, unknown = compute_wfni(data, weights)
    if unknown:
        return {"score": None, "status": "unrecognized_answers", "missing": unknown, "weights_hash": weights.sha256}
    return {"score": score, "status": "complete", "weights_hash": weights.sha256,
            "high_need": score >= weights.high_threshold}


def application_view(ctx: Context, submission: dict) -> dict:
    uid = submission["uuid"]
    user, account = ctx.identity.user(uid), ctx.identity.account(uid)
    if user is None or account is None:
        raise HTTPException(503, "identity unavailable for a student aid application")
    check = ctx.identity.roster_check(uid)
    return {
        "id": submission["id"],
        "uuid": uid,
        "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
        "preferred_name": user.get("preferred_name", ""),
        "data": submission["data"],
        "submission_status": submission["status"],
        "updated_at": submission["updated_at"],
        "revision": submission.get("_rev", 0),
        "review_hardship_prepared": bool(submission.get("sap_review_hardship")),
        "eligible": account.get("status") == "verified" and bool(check and check.get("rostered"))
                    and submission["status"] != "rejected",
        "roster_reason": (check or {}).get("reason", "roster check unavailable"),
        "wfni": scoring(ctx, submission["data"]),
        "review_stage": review_stage(submission["sap_review"], ctx.now()) if submission.get("sap_review") else None,
    }


class SapModule:
    name = "sap"

    def router(self, ctx: Context) -> APIRouter:
        router = APIRouter(prefix="/api")
        admin = ctx.require_role("sap_admin")
        reviewer = ctx.require_role("sap_reviewer")

        def find_review(review_id: str) -> dict:
            matches = [s for s in ctx.store.list("submissions", form_id="student_aid_bursary")
                       if s.get("sap_review", {}).get("id") == review_id]
            if len(matches) != 1:
                raise HTTPException(404, "review not found")
            return matches[0]

        def require_eligible(submission: dict) -> None:
            if not application_view(ctx, submission)["eligible"]:
                raise HTTPException(409, "applicant is not eligible")

        def has_conflict(submission: dict, uid: str) -> bool:
            if submission["uuid"] == uid:
                return True
            if uid in submission.get("sap_review", {}).get("conflicts", []):
                return True
            applicant_check = ctx.identity.roster_check(submission["uuid"])
            reviewer_check = ctx.identity.roster_check(uid)
            applicant_id = (applicant_check or {}).get("roster_uuid")
            return bool(applicant_id and applicant_id == (reviewer_check or {}).get("roster_uuid"))

        def require_review_enabled() -> None:
            if not ctx.settings.sap_review_enabled:
                raise HTTPException(503, "experimental student aid review workflow is disabled")

        def reserve(review_id: str, reviewer_id: str, assigned_by: str | None = None) -> dict:
            submission = find_review(review_id)
            require_eligible(submission)
            if has_conflict(submission, reviewer_id):
                raise HTTPException(403, "reviewer has a conflict of interest")
            now = ctx.now()
            def update(current):
                review = current.get("sap_review") if current else None
                if not review or review["id"] != review_id or review.get("decision"):
                    raise HTTPException(409, "review is closed")
                if review.get("resolution"):
                    raise HTTPException(409, "review has been resolved")
                if not current.get("sap_review_hardship"):
                    raise HTTPException(409, "redacted hardship statement is not prepared")
                if current["uuid"] == reviewer_id or reviewer_id in review.get("conflicts", []):
                    raise HTTPException(403, "reviewer has a conflict of interest")
                reviews = [entry for entry in review["reviews"] if "level" in entry or entry["lease_until"] > now]
                if any(entry["reviewer"] == reviewer_id for entry in reviews):
                    raise HTTPException(409, "already claimed or rated")
                if len(reviews) >= review.get("required_reviews", 2):
                    raise HTTPException(409, "review slots are full")
                reviews.append({"reviewer": reviewer_id, "claimed_at": now, "lease_until": now + 48 * 3600,
                                "assigned_by": assigned_by})
                return {**current, "sap_review": {**review, "reviews": reviews}}
            return ctx.store.update("submissions", submission["id"], update)

        @router.put("/sap/banking")
        def submit_banking(body: BankingIn, actor: dict = ctx.require_verified()):
            uid = actor["uuid"]
            submission = ctx.store.get("submissions", f"student_aid_bursary:{uid}")
            if not submission or submission["status"] == "rejected":
                raise HTTPException(409, "an active student aid application is required")
            def save(current):
                if current:
                    raise HTTPException(409, "banking details already submitted or destroyed")
                return {"uuid": uid, **body.model_dump(), "status": "on_file", "submitted_at": ctx.now()}
            ctx.store.update("sap_banking", uid, save)
            ctx.audit.record(uid, "sap.banking_submitted", f"user:{uid}", None, {"status": "on_file"})
            return {"status": "on_file"}

        @router.get("/sap/banking")
        def my_banking(actor: dict = ctx.require_verified()):
            bank = ctx.store.get("sap_banking", actor["uuid"])
            return {"status": bank["status"] if bank else "not_submitted"}

        @router.get("/admin/sap/applications/{uid}/banking")
        def get_banking(uid: str, actor: dict = admin):
            bank = ctx.store.get("sap_banking", uid)
            if not bank or bank["status"] not in ("on_file", "reverify_requested", "reverify_received"):
                raise HTTPException(404, "banking details unavailable")
            ctx.audit.record(actor["uuid"], "sap.banking_accessed", f"user:{uid}", None, None)
            return {field: bank[field] for field in ("institution_number", "transit_number", "account_number")}

        @router.delete("/admin/sap/applications/{uid}/banking")
        def destroy_banking(uid: str, body: DestroyBankingIn, actor: dict = admin):
            if not body.confirm:
                raise HTTPException(422, "confirmation required")
            def destroy(current):
                if not current or current.get("status") not in ("on_file", "reverify_requested", "reverify_received"):
                    raise HTTPException(404, "banking details unavailable")
                return {"uuid": uid, "status": "destroyed", "destroyed_at": ctx.now(), "destroyed_by": actor["uuid"]}
            ctx.store.update("sap_banking", uid, destroy)
            ctx.audit.record(actor["uuid"], "sap.banking_destroyed", f"user:{uid}", {"status": "on_file"},
                             {"status": "destroyed"})
            return {"status": "destroyed"}

        @router.post("/admin/sap/applications/{uid}/banking/reverify")
        def request_banking_reverification(uid: str, actor: dict = admin):
            if type(ctx.mailer).send is Mailer.send:
                raise HTTPException(503, "email delivery is not configured")
            user = ctx.identity.user(uid)
            account = ctx.identity.account(uid)
            if not user or not account or not account.get("email_verified") or not user.get("email"):
                raise HTTPException(409, "verified applicant email unavailable")
            token = secrets.token_urlsafe(32)
            digest = hmac.new(ctx.settings.secret.encode(), f"sap-banking:{uid}:{token}".encode(), hashlib.sha256).hexdigest()
            expires = ctx.now() + 48 * 3600
            def request(current):
                if not current or current.get("status") not in ("on_file", "reverify_requested"):
                    raise HTTPException(409, "active banking details without a pending replacement are required")
                return {**current, "status": "reverify_requested", "request_hash": digest, "request_expires": expires}
            ctx.store.update("sap_banking", uid, request)
            ctx.audit.record(actor["uuid"], "sap.banking_reverify_requested", f"user:{uid}", None,
                             {"expires_at": expires})
            link = f"{ctx.settings.sso_issuer}/static/index.html#sap-banking={token}"
            ctx.mailer.send(user["email"], "Confirm your UTSU banking details",
                            f"Sign in to your UTSU account and use this link to re-enter your banking details. "
                            f"The link expires in 48 hours and can be used once:\n\n{link}")
            return {"status": "reverify_requested", "expires_at": expires}

        @router.put("/sap/banking/reverify")
        def reverify_banking(body: ReverifyBankingIn, actor: dict = ctx.require_verified()):
            uid = actor["uuid"]
            digest = hmac.new(ctx.settings.secret.encode(), f"sap-banking:{uid}:{body.token}".encode(), hashlib.sha256).hexdigest()
            pending = body.model_dump(exclude={"token"})
            def receive(current):
                if (not current or current.get("status") != "reverify_requested"
                        or current.get("request_expires", 0) <= ctx.now()
                        or not hmac.compare_digest(current.get("request_hash", ""), digest)):
                    raise HTTPException(409, "invalid, expired, or already used re-verification link")
                return {**{k: v for k, v in current.items() if k not in ("request_hash", "request_expires")},
                        "status": "reverify_received", "pending": pending, "received_at": ctx.now()}
            ctx.store.update("sap_banking", uid, receive)
            ctx.audit.record(uid, "sap.banking_reverify_received", f"user:{uid}", None, None)
            return {"status": "reverify_received"}

        @router.get("/admin/sap/applications/{uid}/banking/pending")
        def pending_banking(uid: str, actor: dict = admin):
            bank = ctx.store.get("sap_banking", uid)
            if not bank or bank.get("status") != "reverify_received":
                raise HTTPException(404, "pending banking details unavailable")
            ctx.audit.record(actor["uuid"], "sap.banking_pending_accessed", f"user:{uid}", None, None)
            return {"active": {field: bank[field] for field in ("institution_number", "transit_number", "account_number")},
                    "pending": bank["pending"], "revision": bank.get("_rev", 0)}

        @router.post("/admin/sap/applications/{uid}/banking/accept")
        def accept_banking(uid: str, body: AcceptBankingIn, actor: dict = admin):
            if not body.confirm:
                raise HTTPException(422, "confirmation required")
            def accept(current):
                if not current or current.get("status") != "reverify_received":
                    raise HTTPException(409, "no pending banking details")
                if current.get("_rev", 0) != body.revision:
                    raise HTTPException(409, "banking details changed; compare again")
                return {**{k: v for k, v in current.items() if k not in ("pending", "received_at")},
                        **current["pending"], "status": "on_file", "accepted_at": ctx.now(), "accepted_by": actor["uuid"]}
            ctx.store.update("sap_banking", uid, accept)
            ctx.audit.record(actor["uuid"], "sap.banking_reverify_accepted", f"user:{uid}", None, None)
            return {"status": "on_file"}

        @router.post("/admin/sap/applications/{uid}/enqueue")
        def enqueue(uid: str, actor: dict = admin):
            require_review_enabled()
            key = f"student_aid_bursary:{uid}"
            snapshot = ctx.store.get("submissions", key)
            if not snapshot:
                raise HTTPException(404, "application not found")
            view = application_view(ctx, snapshot)
            if not view["eligible"] or view["wfni"]["status"] not in ("complete", "not_applicable"):
                raise HTTPException(409, "application must be eligible and have complete scoring inputs")
            if snapshot["data"].get("bursary_type") not in ("Living-costs Bursary", "Academic Pursuits Bursary"):
                raise HTTPException(409, "application stream is missing")
            review_id = secrets.token_hex(16)
            rev = snapshot.get("_rev", 0)
            def start(current):
                if not current or current.get("_rev", 0) != rev:
                    raise HTTPException(409, "application changed; review it again")
                if current.get("sap_review"):
                    raise HTTPException(409, "application already in review")
                return {**current, "sap_review": {"id": review_id, "reviews": [], "wfni": view["wfni"]}}
            ctx.store.update("submissions", key, start)
            ctx.audit.record(actor["uuid"], "sap.enqueued", f"user:{uid}", None, {"review_id": review_id})
            return {"review_id": review_id}

        @router.get("/sap/queue")
        def queue(actor: dict = reviewer):
            require_review_enabled()
            rows = []
            now = ctx.now()
            for submission in ctx.store.list("submissions", form_id="student_aid_bursary"):
                review = submission.get("sap_review")
                if not review or not submission.get("sap_review_hardship") or review.get("decision") or has_conflict(submission, actor["uuid"]):
                    continue
                if not application_view(ctx, submission)["eligible"]:
                    continue
                active = [entry for entry in review["reviews"] if "level" in entry or entry["lease_until"] > now]
                if (len(active) >= review.get("required_reviews", 2)
                        or any(entry["reviewer"] == actor["uuid"] for entry in active)):
                    continue
                rows.append((submission["updated_at"], reviewer_view(submission, now)))
            rows.sort(key=lambda item: (not item[1]["emergency"], item[0], item[1]["review_id"]))
            return [row for _, row in rows]

        @router.get("/sap/my-reviews")
        def my_reviews(actor: dict = reviewer):
            require_review_enabled()
            now = ctx.now()
            rows = []
            for submission in ctx.store.list("submissions", form_id="student_aid_bursary"):
                review = submission.get("sap_review")
                if not review or review.get("decision") or not submission.get("sap_review_hardship"):
                    continue
                if has_conflict(submission, actor["uuid"]) or not application_view(ctx, submission)["eligible"]:
                    continue
                entry = next((e for e in review["reviews"] if e["reviewer"] == actor["uuid"]), None)
                if entry and ("level" in entry or entry["lease_until"] > now):
                    rows.append(reviewer_view(submission, now))
            return sorted(rows, key=lambda row: (row["submitted_hour"], row["review_id"]))

        @router.put("/admin/sap/applications/{uid}/review-hardship")
        def prepare_hardship(uid: str, body: ReviewHardshipIn, actor: dict = admin):
            require_review_enabled()
            if not body.confirm_redacted:
                raise HTTPException(422, "confirmation of redaction is required")
            text = unicodedata.normalize("NFC", body.text.strip())
            if not text:
                raise HTTPException(422, "redacted hardship statement cannot be empty")
            user = ctx.identity.user(uid)
            if user is None:
                raise HTTPException(503, "identity unavailable for a student aid application")
            names = [user.get(field) or "" for field in ("first_name", "last_name", "preferred_name")]
            if redact(text, names) != text:
                raise HTTPException(422, "Automatic checks found a name, student number, or contact detail. "
                                        "Remove it and review the statement before confirming.")
            key = f"student_aid_bursary:{uid}"
            def update(current):
                if not current or not current.get("sap_review"):
                    raise HTTPException(404, "application not in review")
                if current.get("_rev", 0) != body.revision:
                    raise HTTPException(409, "application changed; review it again")
                review = current["sap_review"]
                if review.get("decision") or review["reviews"]:
                    raise HTTPException(409, "hardship statement is locked after reviews begin")
                return {**current, "sap_review_hardship": text}
            result = ctx.store.update("submissions", key, update)
            ctx.audit.record(actor["uuid"], "sap.hardship_prepared", f"user:{uid}", None, None)
            return {"revision": result["_rev"], "review_hardship_prepared": True}

        @router.post("/sap/queue/{review_id}/claim")
        def claim(review_id: str, actor: dict = reviewer):
            require_review_enabled()
            result = reserve(review_id, actor["uuid"])
            ctx.audit.record(actor["uuid"], "sap.claimed", f"review:{review_id}", None, None)
            return reviewer_view(result, ctx.now(), include_hardship=True)

        @router.post("/admin/sap/review/{review_id}/assign")
        def assign(review_id: str, body: AssignReviewIn, actor: dict = admin):
            require_review_enabled()
            account = ctx.identity.account(body.reviewer)
            if not account or account.get("status") != "verified" or not account.get("email_verified") or not (
                    roles_of(account) & {"sap_reviewer", "admin"}):
                raise HTTPException(422, "reviewer must be a verified account with the reviewer role")
            reserve(review_id, body.reviewer, actor["uuid"])
            ctx.audit.record(actor["uuid"], "sap.assigned", f"review:{review_id}", None,
                             {"reviewer": body.reviewer})
            return {"status": "assigned"}

        @router.post("/sap/review/{review_id}/conflict")
        def flag_conflict(review_id: str, actor: dict = reviewer):
            require_review_enabled()
            submission = find_review(review_id)
            def update(current):
                review = current.get("sap_review") if current else None
                if not review or review["id"] != review_id or review.get("decision"):
                    raise HTTPException(409, "review is closed")
                entries = review["reviews"]
                if any(entry["reviewer"] == actor["uuid"] and "level" in entry for entry in entries):
                    raise HTTPException(409, "a submitted review requires administrator intervention")
                conflicts = review.get("conflicts", [])
                if actor["uuid"] in conflicts:
                    return None
                return {**current, "sap_review": {**review,
                    "conflicts": [*conflicts, actor["uuid"]],
                    "reviews": [entry for entry in entries if entry["reviewer"] != actor["uuid"]]}}
            ctx.store.update("submissions", submission["id"], update)
            ctx.audit.record(actor["uuid"], "sap.conflict_flagged", f"review:{review_id}", None, None)
            return {"status": "conflict"}

        @router.get("/sap/review/{review_id}")
        def get_review(review_id: str, actor: dict = reviewer):
            require_review_enabled()
            submission = find_review(review_id)
            require_eligible(submission)
            if has_conflict(submission, actor["uuid"]):
                raise HTTPException(403, "cannot review your own application")
            review = submission["sap_review"]
            entry = next((e for e in review["reviews"] if e["reviewer"] == actor["uuid"]), None)
            if (review.get("decision") or not submission.get("sap_review_hardship") or not entry
                    or ("level" not in entry and entry["lease_until"] <= ctx.now())):
                raise HTTPException(409, "active claim required")
            return reviewer_view(submission, ctx.now(), include_hardship=True)

        @router.put("/sap/review/{review_id}")
        def rate(review_id: str, body: RatingIn, actor: dict = reviewer):
            require_review_enabled()
            submission = find_review(review_id)
            require_eligible(submission)
            if has_conflict(submission, actor["uuid"]):
                raise HTTPException(403, "cannot review your own application")
            now = ctx.now()
            def update(current):
                review = current.get("sap_review") if current else None
                if not review or review["id"] != review_id or review.get("decision"):
                    raise HTTPException(409, "review is closed")
                if review.get("resolution"):
                    raise HTTPException(409, "review has been resolved")
                if not current.get("sap_review_hardship"):
                    raise HTTPException(409, "redacted hardship statement is not prepared")
                if current["uuid"] == actor["uuid"] or actor["uuid"] in review.get("conflicts", []):
                    raise HTTPException(403, "reviewer has a conflict of interest")
                entries = review["reviews"]
                entry = next((e for e in entries if e["reviewer"] == actor["uuid"]), None)
                if not entry or ("level" not in entry and entry["lease_until"] <= now):
                    raise HTTPException(409, "active claim required")
                return {**current, "sap_review": {**review, "reviews": [
                    {**e, "level": body.level, "submitted_at": now} if e["reviewer"] == actor["uuid"] else e
                    for e in entries]}}
            result = ctx.store.update("submissions", submission["id"], update)
            ctx.audit.record(actor["uuid"], "sap.rated", f"review:{review_id}", None, {"level": body.level})
            return reviewer_view(result, ctx.now(), include_hardship=True)

        @router.post("/sap/queue/{review_id}/release")
        def release(review_id: str, actor: dict = reviewer):
            require_review_enabled()
            submission = find_review(review_id)
            def update(current):
                review = current.get("sap_review") if current else None
                if not review or review["id"] != review_id or review.get("decision"):
                    raise HTTPException(409, "review is closed")
                entry = next((e for e in review["reviews"] if e["reviewer"] == actor["uuid"]), None)
                if not entry or "level" in entry:
                    raise HTTPException(409, "an unrated claim is required")
                return {**current, "sap_review": {**review, "reviews": [
                    e for e in review["reviews"] if e["reviewer"] != actor["uuid"]]}}
            result = ctx.store.update("submissions", submission["id"], update)
            ctx.audit.record(actor["uuid"], "sap.released", f"review:{review_id}", None, None)
            return reviewer_view(result, ctx.now())

        @router.get("/admin/sap/review-report")
        def review_report(_: dict = admin):
            return [{"uuid": s["uuid"], "review_id": s["sap_review"]["id"],
                     "stage": review_stage(s["sap_review"], ctx.now()), "reviews": s["sap_review"]["reviews"],
                     "resolution": s["sap_review"].get("resolution"), "decision": s["sap_review"].get("decision")}
                    for s in ctx.store.list("submissions", form_id="student_aid_bursary") if s.get("sap_review")]

        @router.put("/admin/sap/applications/{uid}/review-count")
        def set_review_count(uid: str, body: ReviewCountIn, actor: dict = admin):
            require_review_enabled()
            key = f"student_aid_bursary:{uid}"
            def update(current):
                review = current.get("sap_review") if current else None
                if not review:
                    raise HTTPException(404, "application not in review")
                if review.get("decision"):
                    raise HTTPException(409, "review is closed")
                if review.get("resolution"):
                    raise HTTPException(409, "review has been resolved")
                if body.required_reviews <= review.get("required_reviews", 2):
                    raise HTTPException(422, "required review count must increase")
                return {**current, "sap_review": {**review, "required_reviews": body.required_reviews}}
            result = ctx.store.update("submissions", key, update)
            ctx.audit.record(actor["uuid"], "sap.review_count_increased", f"user:{uid}", None,
                             {"required_reviews": body.required_reviews})
            return {"required_reviews": result["sap_review"]["required_reviews"]}

        @router.post("/admin/sap/applications/{uid}/resolve-review")
        def resolve_review(uid: str, body: ResolveReviewIn, actor: dict = admin):
            require_review_enabled()
            if uid == actor["uuid"]:
                raise HTTPException(403, "cannot resolve your own application")
            reason = clean_text(body.reason, 500)
            if not reason:
                raise HTTPException(422, "a resolution reason is required")
            key = f"student_aid_bursary:{uid}"
            snapshot = ctx.store.get("submissions", key)
            if not snapshot:
                raise HTTPException(404, "application not found")
            require_eligible(snapshot)
            if has_conflict(snapshot, actor["uuid"]):
                raise HTTPException(403, "administrator has a conflict of interest")
            def update(current):
                review = current.get("sap_review") if current else None
                if not review or review_stage(review, ctx.now()) != "discuss":
                    raise HTTPException(409, "only a disputed review can be resolved")
                if current.get("_rev", 0) != body.revision:
                    raise HTTPException(409, "application changed; review it again")
                if actor["uuid"] in review.get("conflicts", []):
                    raise HTTPException(403, "administrator has a conflict of interest")
                if any(entry["reviewer"] == actor["uuid"] for entry in review["reviews"]):
                    raise HTTPException(403, "a reviewer cannot resolve their own review")
                resolution = {"reason": reason, "by": actor["uuid"], "at": ctx.now()}
                return {**current, "sap_review": {**review, "resolution": resolution}}
            result = ctx.store.update("submissions", key, update)
            ctx.audit.record(actor["uuid"], "sap.review_resolved", f"user:{uid}", None,
                             result["sap_review"]["resolution"])
            return result["sap_review"]["resolution"]

        @router.post("/admin/sap/applications/{uid}/decision")
        def decide(uid: str, body: DecisionIn, actor: dict = admin):
            require_review_enabled()
            if uid == actor["uuid"]:
                raise HTTPException(403, "cannot decide your own application")
            key = f"student_aid_bursary:{uid}"
            snapshot = ctx.store.get("submissions", key)
            if not snapshot:
                raise HTTPException(404, "application not found")
            if not application_view(ctx, snapshot)["eligible"]:
                raise HTTPException(409, "applicant is not eligible")
            def update(current):
                review = current.get("sap_review") if current else None
                if not review or review_stage(review, ctx.now()) != "reconciled":
                    raise HTTPException(409, "two reconciled reviews are required")
                if body.amount > current["data"]["amount_requested"] or (not body.approved and body.amount != 0):
                    raise HTTPException(422, "invalid decision amount")
                status = "accepted" if body.approved else "rejected"
                decision = {"approved": body.approved, "amount": body.amount, "by": actor["uuid"], "at": ctx.now()}
                return {**current, "status": status, "sap_review": {**review, "decision": decision},
                        "history": [*current["history"], {"at": ctx.now(), "status": status, "note": ""}]}
            result = ctx.store.update("submissions", key, update)
            ctx.audit.record(actor["uuid"], "sap.decided", f"user:{uid}", None, result["sap_review"]["decision"])
            return result["sap_review"]["decision"]

        @router.put("/admin/sap/weights")
        def set_weights(body: WeightsIn, actor: dict = admin):
            try:
                weights = parse_weights(body.csv, "SAP weights")
            except WeightsError as exc:
                raise HTTPException(422, str(exc)) from exc
            previous = ctx.store.get("sap_config", "weights")
            ctx.store.put("sap_config", "weights", {"csv": body.csv, "hash": weights.sha256,
                                                    "by": actor["uuid"], "at": ctx.now()})
            ctx.audit.record(actor["uuid"], "sap.weights_updated", "sap:weights",
                             {"hash": previous["hash"]} if previous else None, {"hash": weights.sha256})
            return {"hash": weights.sha256, "max_score": weights.max_score, "high_threshold": weights.high_threshold}

        @router.get("/admin/sap/weights")
        def get_weights(_: dict = admin):
            config = ctx.store.get("sap_config", "weights")
            if not config:
                raise HTTPException(404, "SAP weights are not configured")
            return config

        @router.get("/admin/sap/applications")
        def applications(cursor: str = "", limit: int = 100, _: dict = admin):
            if not 1 <= limit <= 200:
                raise HTTPException(422, "limit must be between 1 and 200")
            submissions = sorted(
                (s for s in ctx.store.list("submissions", form_id="student_aid_bursary") if s["id"] > cursor),
                key=lambda s: s["id"],
            )
            items = [application_view(ctx, s) for s in submissions[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(submissions) > limit else None}

        @router.get("/admin/sap/applications.csv")
        def export_applications(actor: dict = admin):
            rows = []
            submissions = sorted(ctx.store.list("submissions", form_id="student_aid_bursary"), key=lambda s: s["id"])
            for submission in submissions:
                item = application_view(ctx, submission)
                terms = item["data"].get("terms") or []
                if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
                    raise HTTPException(409, "application terms require administrator review before export")
                decision = (submission.get("sap_review") or {}).get("decision") or {}
                rows.append({"id": item["id"], "uuid": item["uuid"], "name": item["name"],
                             "terms": "; ".join(terms), "bursary_type": item["data"].get("bursary_type", ""),
                             "amount_requested": item["data"].get("amount_requested", ""),
                             "submission_status": item["submission_status"], "eligible": item["eligible"],
                             "roster_reason": item["roster_reason"], "review_stage": item["review_stage"],
                             "wfni": item["wfni"].get("score"), "wfni_status": item["wfni"]["status"],
                             "approved": decision.get("approved"), "awarded_amount": decision.get("amount")})
            ctx.audit.record(actor["uuid"], "sap.applications_exported", "sap:applications", None, None,
                             {"count": len(rows)})
            return csv_response("sap-applications.csv",
                                ["id", "uuid", "name", "terms", "bursary_type", "amount_requested",
                                 "submission_status", "eligible", "roster_reason", "review_stage",
                                 "wfni", "wfni_status", "approved", "awarded_amount"], rows)

        @router.get("/me/sap")
        def my_application(account: dict = ctx.require_verified()):
            submission = ctx.store.get("submissions", f"student_aid_bursary:{account['uuid']}")
            return application_view(ctx, submission) if submission else None

        @router.get("/me/sap/history")
        def my_history(account: dict = ctx.require_verified()):
            submission = ctx.store.get("submissions", f"student_aid_bursary:{account['uuid']}")
            return [{k: item[k] for k in ("version", "status", "updated_at", "data", "history")}
                    | {"decision": {k: item["sap_review"]["decision"][k] for k in ("approved", "amount", "at")}}
                    for item in (submission or {}).get("sap_previous", [])]

        @router.get("/admin/sap/applications/{uid}/history")
        def application_history(uid: str, _: dict = admin):
            submission = ctx.store.get("submissions", f"student_aid_bursary:{uid}")
            if not submission:
                raise HTTPException(404, "application not found")
            return submission.get("sap_previous", [])

        @router.post("/admin/sap/applications/{uid}/history")
        def import_history(uid: str, body: HistoricalDecision, actor: dict = admin):
            if uid == actor["uuid"]:
                raise HTTPException(403, "cannot import your own student aid decision")
            source = clean_text(body.source, 200).strip()
            if not body.confirm or not source or (not body.approved and body.amount != 0):
                raise HTTPException(422, "confirmation, a source reference and a valid decision amount are required")
            if body.decided_on > datetime.fromtimestamp(ctx.now(), timezone.utc).date():
                raise HTTPException(422, "decision date cannot be in the future")
            if any(len(term) > 100 for term in body.terms):
                raise HTTPException(422, "historical terms must be at most 100 characters")
            try:
                terms = [clean_text(term, 100).strip() for term in body.terms]
                requested = normalized_terms({"terms": terms})
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if len(requested) != len(terms):
                raise HTTPException(422, "duplicate terms are not allowed")
            ratings = []
            for rating in body.ratings:
                reviewer = clean_text(rating.reviewer, 100).strip()
                if not reviewer:
                    raise HTTPException(422, "reviewer name cannot be blank")
                ratings.append({"reviewer": reviewer, "level": rating.level,
                                "notes": clean_text(rating.notes, 1000, multiline=True)})
            at = int(datetime.combine(body.decided_on, time.min, tzinfo=timezone.utc).timestamp())
            now = ctx.now()
            status = "accepted" if body.approved else "rejected"
            snapshot = {"version": None, "status": status, "updated_at": at, "data": {"terms": terms},
                        "history": [{"at": at, "status": status, "note": ""}],
                        "sap_review": {"decision": {"approved": body.approved, "amount": body.amount, "at": at},
                                       "reviews": ratings},
                        "legacy": {"source": source, "imported_by": actor["uuid"], "imported_at": now}}

            def save(current):
                if not current:
                    raise HTTPException(404, "an existing portal application is required")
                if current.get("_rev", 0) != body.revision:
                    raise HTTPException(409, "application changed; reload before importing")
                previous = current.get("sap_previous", [])
                if not isinstance(previous, list):
                    raise HTTPException(409, "historical applications require administrator review")
                for item in previous:
                    if not isinstance(item, dict):
                        raise HTTPException(409, "historical applications require administrator review")
                    legacy = item.get("legacy", {})
                    if not isinstance(legacy, dict) or not isinstance(legacy.get("source", ""), str):
                        raise HTTPException(409, "historical applications require administrator review")
                    if legacy.get("source", "").casefold() == source.casefold():
                        raise HTTPException(409, "source reference already imported for this account")
                try:
                    overlaps = any(requested & normalized_terms(item.get("data")) for item in [*previous, current])
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
                if overlaps:
                    raise HTTPException(409, "historical terms overlap an existing application")
                return {**current, "sap_previous": [*previous, snapshot]}

            result = ctx.store.update("submissions", f"student_aid_bursary:{uid}", save)
            ctx.audit.record(actor["uuid"], "sap.history_imported", f"user:{uid}", None, None,
                             {"source": source, "terms": terms, "revision": result["_rev"]})
            return {"status": "imported", "revision": result["_rev"]}

        return router
