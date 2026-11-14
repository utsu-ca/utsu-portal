"""Senate application views over portal submissions and current roster checks."""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...context import Context
from ...services.exports import csv_response
from .seating import FACULTY, assign_seats, tier_index

PREFERENCES = {
    "pref_first_year": "First-Year Student", "pref_commuter": "Commuter", "pref_residence": "Residence",
    "pref_mature": "Mature Students and Student Parents", "pref_international": "International Students",
    "pref_first_generation": "First Generation Student", "pref_small_program": "Small and Specialized Program",
    "pref_general": "General Member", "pref_faculty": FACULTY,
}


class Seat(BaseModel):
    constituency: str = Field(min_length=1, max_length=100)
    division: str = Field(default="", max_length=100)
    seats: int = Field(ge=0, le=500)


class SeatConfig(BaseModel):
    seats: list[Seat] = Field(min_length=1, max_length=200)


class SeatingRequest(BaseModel):
    seed: int


class PublishRequest(SeatingRequest):
    inputs_hash: str = Field(min_length=64, max_length=64)


def valid_preference(value) -> bool:
    return isinstance(value, str) and (value == "Not interested" or tier_index(value) is not None)


def compute_seating(ctx: Context, seed: int) -> dict:
    config = ctx.store.get("senate_config", "seats")
    if not config:
        raise HTTPException(409, "configure the Senate seats before seating")
    applicants = []
    for submission in ctx.store.list("submissions", form_id="senate_application"):
        view = application_view(ctx, submission)
        if not view["eligible"]:
            continue
        data = view["data"]
        missing = [field for field in PREFERENCES if field not in data]
        if missing:
            raise HTTPException(409, {"error": "eligible applications need all seating preferences",
                                      "application": submission["id"], "missing": missing})
        if any(not valid_preference(data[field]) for field in PREFERENCES):
            raise HTTPException(409, {"error": "invalid seating preference", "application": submission["id"]})
        check = ctx.identity.roster_check(view["uuid"])
        if not check or not check.get("rostered") or check.get("division") is None:
            raise HTTPException(409, {"error": "verified roster record unavailable", "application": submission["id"]})
        prefs = {constituency: tier for field, constituency in PREFERENCES.items()
                 if (tier := tier_index(data[field])) is not None}
        applicants.append({"uuid": view["uuid"], "full_name": view["name"],
                           "division_code": check["division"],
                           "delegated": data.get("delegated", False), "prefs": prefs,
                           "public_listing": data.get("public_listing", False)})
    applicants.sort(key=lambda item: item["uuid"])
    seats = config["seats"]
    material = {"applicants": applicants, "seats": seats, "seed": seed}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    result = assign_seats(applicants, {(s["constituency"], s["division"]): s["seats"] for s in seats}, seed)
    visible = {a["uuid"]: a["public_listing"] for a in applicants}
    for row in result["seated"]:
        row["public_listing"] = visible[row["uuid"]]
    return {"inputs_hash": digest, "result": result}


def application_view(ctx: Context, submission: dict) -> dict:
    uid = submission["uuid"]
    user, account = ctx.identity.user(uid), ctx.identity.account(uid)
    if user is None or account is None:
        raise HTTPException(503, "identity unavailable for a Senate application")
    check = ctx.identity.roster_check(uid)
    return {
        "id": submission["id"],
        "uuid": uid,
        "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
        "data": submission["data"],
        "submission_status": submission["status"],
        "updated_at": submission["updated_at"],
        "eligible": account.get("status") == "verified" and bool(check and check.get("rostered"))
                    and submission["status"] != "rejected",
        "roster_reason": (check or {}).get("reason", "roster check unavailable"),
    }


def application_alerts(application: dict) -> list[dict]:
    alerts = []
    if not application["eligible"]:
        reason = ("submission rejected" if application["submission_status"] == "rejected"
                  else application["roster_reason"] or "account is not verified")
        alerts.append({"kind": "ineligible", "detail": reason})
    missing = [field for field in PREFERENCES if field not in application["data"]]
    invalid = [field for field in PREFERENCES if field in application["data"]
               and not valid_preference(application["data"][field])]
    if missing:
        alerts.append({"kind": "missing_preferences", "detail": "Missing preferences: " + ", ".join(missing)})
    if invalid:
        alerts.append({"kind": "invalid_preferences", "detail": "Invalid preferences: " + ", ".join(invalid)})
    return alerts


class SenateModule:
    name = "senate"

    def router(self, ctx: Context) -> APIRouter:
        router = APIRouter(prefix="/api")
        admin = ctx.require_role("senate_admin")

        @router.get("/admin/senate/applications")
        def applications(cursor: str = "", limit: int = 100, _: dict = admin):
            if not 1 <= limit <= 200:
                raise HTTPException(422, "limit must be between 1 and 200")
            submissions = sorted(
                (s for s in ctx.store.list("submissions", form_id="senate_application") if s["id"] > cursor),
                key=lambda s: s["id"],
            )
            items = [application_view(ctx, s) for s in submissions[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(submissions) > limit else None}

        @router.get("/admin/senate/alerts")
        def alerts(cursor: str = "", limit: int = 100, _: dict = admin):
            page = applications(cursor, limit, _)
            items = [{"id": item["id"], "uuid": item["uuid"], "name": item["name"], **alert}
                     for item in page["items"] for alert in application_alerts(item)]
            return {"items": items, "next_cursor": page["next_cursor"]}

        @router.get("/me/senate")
        def my_application(account: dict = ctx.require_verified()):
            submission = ctx.store.get("submissions", f"senate_application:{account['uuid']}")
            return application_view(ctx, submission) if submission else None

        @router.get("/admin/senate/seats")
        def get_seats(_: dict = admin):
            config = ctx.store.get("senate_config", "seats")
            return {"seats": config["seats"] if config else []}

        @router.put("/admin/senate/seats")
        def set_seats(body: SeatConfig, actor: dict = admin):
            rows, seen = [], set()
            for item in body.seats:
                constituency = item.constituency.strip()
                division = item.division.strip().upper()
                if constituency not in PREFERENCES.values():
                    raise HTTPException(422, f"unknown constituency: {constituency}")
                if (constituency == FACULTY) != bool(division):
                    raise HTTPException(422, "only Faculty/College seats require a division")
                key = (constituency, division)
                if key in seen:
                    raise HTTPException(422, "duplicate constituency and division")
                seen.add(key)
                rows.append({"constituency": constituency, "division": division, "seats": item.seats})
            if sum(row["seats"] for row in rows) > 500:
                raise HTTPException(422, "at most 500 seats can be configured")
            rows.sort(key=lambda row: (row["constituency"], row["division"]))
            before = ctx.store.get("senate_config", "seats")
            ctx.store.put("senate_config", "seats", {"seats": rows})
            ctx.audit.record(actor["uuid"], "senate.seats_updated", "senate:seats", before, {"seats": rows})
            return {"seats": rows}

        @router.post("/admin/senate/seating")
        def seating(body: SeatingRequest, _: dict = admin):
            return compute_seating(ctx, body.seed)

        @router.post("/admin/senate/seating/publish")
        def publish(body: PublishRequest, actor: dict = admin):
            preview = compute_seating(ctx, body.seed)
            if preview["inputs_hash"] != body.inputs_hash:
                raise HTTPException(409, "seating inputs changed; review a new preview")
            def save(current):
                versions = (current or {}).get("versions", [])
                snapshot = {**preview, "version": len(versions) + 1, "published_at": ctx.now(),
                            "published_by": actor["uuid"]}
                return {"versions": [*versions, snapshot]}
            record = ctx.store.update("senate_published", "history", save)
            snapshot = record["versions"][-1]
            ctx.audit.record(actor["uuid"], "senate.published", "senate:seating", None,
                             {"version": snapshot["version"], "inputs_hash": body.inputs_hash})
            return snapshot

        @router.get("/admin/senate/results")
        def results(_: dict = admin):
            return (ctx.store.get("senate_published", "history") or {}).get("versions", [])

        @router.get("/admin/senate/results.csv")
        def export_results(actor: dict = admin):
            versions = (ctx.store.get("senate_published", "history") or {}).get("versions", [])
            if not versions:
                raise HTTPException(404, "no published Senate results")
            latest = versions[-1]
            rows = latest["result"]["seated"]
            ctx.audit.record(actor["uuid"], "senate.results_exported", "senate:seating", None, None,
                             {"version": latest["version"], "count": len(rows)})
            return csv_response("senate-results.csv", ["uuid", "full_name", "constituency", "division", "public_listing"], rows)

        @router.get("/senate/current")
        def current():
            versions = (ctx.store.get("senate_published", "history") or {}).get("versions", [])
            if not versions:
                return None
            latest = versions[-1]
            return {"version": latest["version"], "seed": latest["result"]["seed"],
                    "inputs_hash": latest["inputs_hash"],
                    "seated": [{"constituency": row["constituency"], "division": row["division"],
                                "name": row["full_name"] if row["public_listing"] else "seat filled"}
                               for row in latest["result"]["seated"]]}

        return router
