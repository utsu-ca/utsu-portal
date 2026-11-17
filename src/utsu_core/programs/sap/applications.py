"""Student aid application and historical-decision endpoints."""
from __future__ import annotations

from datetime import date, datetime, time, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...context import Context
from ...security import clean_text
from ...services.exports import csv_response
from .review import application_view
from .terms import normalized_terms

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


class StudentAidSubmissionPolicy:
    def save(self, previous: dict | None, candidate: dict) -> dict:
        previous_apps = (previous or {}).get("sap_previous", [])
        completed = list(previous_apps)
        new_application = bool(previous and previous.get("sap_review", {}).get("decision"))
        if new_application:
            completed.append(previous)
        try:
            requested_terms = normalized_terms(candidate["data"])
            overlapping = any(requested_terms.intersection(
                normalized_terms(item.get("data") if isinstance(item, dict) else None))
                for item in completed)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if overlapping:
            raise HTTPException(409, "a completed student aid application already covers a requested term")
        if new_application:
            previous_apps = [*previous_apps, {
                k: value for k, value in previous.items() if k not in ("sap_previous", "_rev")
            }]
        if previous and not new_application:
            if previous["status"] in ("accepted", "rejected"):
                raise HTTPException(409, "submission is final")
            if previous.get("sap_review"):
                raise HTTPException(409, "student aid application is under review")
        if new_application:
            candidate = {**candidate, "history": [candidate["history"][-1]]}
        if previous_apps:
            candidate = {**candidate, "sap_previous": previous_apps}
        return candidate

    def validate_status_change(self, current: dict) -> None:
        if current.get("sap_review"):
            raise HTTPException(409, "use the student aid decision workflow for this submission")


def applications_router(ctx: Context) -> APIRouter:
    router = APIRouter(prefix="/api")
    admin = ctx.require_role("sap_admin")

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
