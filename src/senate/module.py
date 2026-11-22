"""Senate application views over portal submissions and current roster checks."""
from __future__ import annotations

import hashlib
import json
from importlib import resources

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.contracts import (
    FormContribution, NotificationType, PluginManifest, PluginServices, RegistrationContext, SubmissionPolicy,
)
from core.pagination import PageOrder, paginate_items, paginate_page
from core.services.exports import csv_response
from core.permissions import PERM_SENATE_APPLICATIONS
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


def compute_seating(services: PluginServices, seed: int) -> dict:
    config = services.store.get("senate_config", "seats")
    if not config:
        raise HTTPException(409, "configure the Senate seats before seating")
    applicants = []
    for submission in services.forms.submissions("senate_application"):
        view = application_view(services, submission)
        if not view["eligible"]:
            continue
        data = view["data"]
        missing = [field for field in PREFERENCES if field not in data]
        if missing:
            raise HTTPException(409, {"error": "eligible applications need all seating preferences",
                                      "application": submission["id"], "missing": missing})
        if any(not valid_preference(data[field]) for field in PREFERENCES):
            raise HTTPException(409, {"error": "invalid seating preference", "application": submission["id"]})
        check = services.identity.roster(view["uuid"])
        if not check or not check.rostered or check.division is None:
            raise HTTPException(409, {"error": "verified roster record unavailable", "application": submission["id"]})
        prefs = {constituency: tier for field, constituency in PREFERENCES.items()
                 if (tier := tier_index(data[field])) is not None}
        applicants.append({"uuid": view["uuid"], "full_name": view["name"],
                           "division_code": check.division,
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


def application_view(services: PluginServices, submission: dict) -> dict:
    uid = submission["uuid"]
    person = services.identity.person(uid)
    if person is None:
        raise HTTPException(503, "identity unavailable for a Senate application")
    check = services.identity.roster(uid)
    return {
        "id": submission["id"],
        "uuid": uid,
        "name": f"{person.first_name} {person.last_name}".strip(),
        "data": submission["data"],
        "submission_status": submission["status"],
        "updated_at": submission["updated_at"],
        "eligible": person.status == "verified" and bool(check and check.rostered)
                    and submission["status"] != "rejected",
        "roster_reason": check.reason if check else "roster check unavailable",
    }


class SenateModule:
    manifest = PluginManifest("senate", "0.1.0", 1, "senate.module:SenateModule")

    def configure(self, registration: RegistrationContext) -> None:
        raw = resources.files(__package__).joinpath("forms", "senate_application.yaml").read_text(encoding="utf-8")
        registration.register_form("senate_application", FormContribution(yaml=raw))
        registration.register_submission_policy("senate_application", SubmissionPolicy(require_roster=True))
        registration.register_notification_type("senate", NotificationType("senate_application", "senate_admin"))

    def router(self, services: PluginServices) -> APIRouter:
        router = APIRouter(prefix="/api")
        forms = services.forms
        admin = services.auth.require_permission(PERM_SENATE_APPLICATIONS)

        @router.get("/admin/senate/applications")
        def applications(
            cursor: str = Query("", max_length=2048),
            limit: int = Query(100, ge=1, le=200),
            _: dict = admin,
        ):
            order_by = (PageOrder("id", str),)
            page, next_cursor = paginate_page(
                lambda after, page_limit: forms.page_submissions(
                    "senate_application", order_by=order_by, after=after, limit=page_limit
                ),
                cursor,
                limit,
                order_by,
            )
            return {"items": [application_view(services, submission) for submission in page],
                    "next_cursor": next_cursor}

        @router.get("/me/senate")
        def my_application(account: dict = services.auth.require_verified()):
            submission = forms.submission("senate_application", account["uuid"])
            return application_view(services, submission) if submission else None

        @router.get("/admin/senate/seats")
        def get_seats(_: dict = admin):
            config = services.store.get("senate_config", "seats")
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
            before = services.store.get("senate_config", "seats")
            services.store.put("senate_config", "seats", {"seats": rows})
            services.audit.record(actor["uuid"], "senate.seats_updated", "senate:seats", before, {"seats": rows})
            return {"seats": rows}

        @router.post("/admin/senate/seating")
        def seating(body: SeatingRequest, _: dict = admin):
            return compute_seating(services, body.seed)

        @router.post("/admin/senate/seating/publish")
        def publish(body: PublishRequest, actor: dict = admin):
            preview = compute_seating(services, body.seed)
            if preview["inputs_hash"] != body.inputs_hash:
                raise HTTPException(409, "seating inputs changed; review a new preview")
            def save(current):
                versions = (current or {}).get("versions", [])
                snapshot = {**preview, "version": len(versions) + 1, "published_at": services.now(),
                            "published_by": actor["uuid"]}
                return {"versions": [*versions, snapshot]}
            record = services.store.update("senate_published", "history", save)
            if record is None:
                raise RuntimeError("Senate publication was not saved")
            snapshot = record["versions"][-1]
            services.audit.record(actor["uuid"], "senate.published", "senate:seating", None,
                             {"version": snapshot["version"], "inputs_hash": body.inputs_hash})
            return snapshot

        @router.get("/admin/senate/results")
        def results(
            cursor: str = Query("", max_length=2048),
            limit: int = Query(100, ge=1, le=200),
            _: dict = admin,
        ):
            page, next_cursor = paginate_items(
                (services.store.get("senate_published", "history") or {}).get("versions", []),
                cursor,
                limit,
                key=lambda version: (version["version"],),
                key_types=(int,),
            )
            return {"items": page, "next_cursor": next_cursor}

        @router.get("/admin/senate/results.csv")
        def export_results(actor: dict = admin):
            versions = (services.store.get("senate_published", "history") or {}).get("versions", [])
            if not versions:
                raise HTTPException(404, "no published Senate results")
            latest = versions[-1]
            rows = latest["result"]["seated"]
            services.audit.record(actor["uuid"], "senate.results_exported", "senate:seating", None, None,
                             {"version": latest["version"], "count": len(rows)})
            return csv_response("senate-results.csv", ["uuid", "full_name", "constituency", "division", "public_listing"], rows)

        @router.get("/senate/current")
        def current():
            versions = (services.store.get("senate_published", "history") or {}).get("versions", [])
            if not versions:
                return None
            latest = versions[-1]
            return {"version": latest["version"], "seed": latest["result"]["seed"],
                    "inputs_hash": latest["inputs_hash"],
                    "seated": [{"constituency": row["constituency"], "division": row["division"],
                                "name": row["full_name"] if row["public_listing"] else "seat filled"}
                               for row in latest["result"]["seated"]]}

        return router
