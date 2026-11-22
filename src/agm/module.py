"""AGM registration, invitation, Zoom SSO wiring and admin check-in."""
from __future__ import annotations

import io
import re
from importlib import resources
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from utsu_core.contracts import (
    FormContribution,
    NotificationType,
    PluginManifest,
    PluginServices,
    RegistrationContext,
    SubmissionPolicy,
)
from utsu_core.pagination import PageOrder, paginate_page, paginate_store
from utsu_core.permissions import PERM_AGM_CHECKIN, PERM_AGM_REGISTRATIONS
from utsu_core.services.exports import csv_response

from .state import AgmVerificationState

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class CheckinIn(BaseModel):
    uuid: str


class VerifyIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    student_number: str = Field(default="", max_length=32)
    division: str = Field(default="", max_length=100)


def qr_svg(text: str) -> str | None:
    try:
        import segno  # type: ignore
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, xmldecl=False, nl=False)
    return buf.getvalue().decode()


class AgmModule:
    manifest = PluginManifest(
        "agm",
        "0.1.0",
        1,
        "agm.module:AgmModule",
        dependencies=("forms", "sso"),
        order=40,
        form_ids=("agm_rsvp",),
        interface="plugin",
        frontend=True,
        roles=(
            ("agm_admin", "view AGM registrations and verify roster matches"),
            ("checkin", "AGM check-in and attendance"),
        ),
        protection=(("agm_media", ("face", "id_card")),),
        settings=("agm_grant_ttl", "zoom_sso_url", "zoom_url"),
        services=("agm_review", "agm_sso", "audit", "auth", "forms", "identity", "now", "settings", "store"),
        collections=("agm_attendance", "agm_checks", "agm_grants", "agm_media"),
        provides=("agm_verification",),
        permissions=(
            (PERM_AGM_CHECKIN, "Manage AGM check-in and attendance."),
            (PERM_AGM_REGISTRATIONS, "View AGM registrations and verify roster matches."),
        ),
        role_permissions=(
            ("agm_admin", (PERM_AGM_REGISTRATIONS,)),
            ("checkin", (PERM_AGM_CHECKIN,)),
        ),
    )

    def configure(self, registration: RegistrationContext) -> None:
        yaml = resources.files(__package__).joinpath("forms", "agm_rsvp.yaml").read_text(encoding="utf-8")
        registration.register_form("agm_rsvp", FormContribution(yaml=yaml))
        registration.register_submission_policy("agm_rsvp", SubmissionPolicy(require_roster=True))
        registration.register_notification_type("agm", NotificationType("agm_rsvp", "agm_admin"))
        registration.provide("agm_verification", AgmVerificationState)

    def router(self, services: PluginServices) -> APIRouter:
        router = APIRouter(prefix="/api")
        store = services.store
        state = AgmVerificationState(services)
        submissions = services.forms
        zoom_url = services.settings["zoom_url"]
        zoom_sso_url = services.settings["zoom_sso_url"]
        verified = services.auth.require_rostered()
        checkin_admin = services.auth.require_permission(PERM_AGM_CHECKIN)
        agm_admin = services.auth.require_permission(PERM_AGM_REGISTRATIONS)

        def zoom_eligibility(uuid: str) -> str | None:
            roster = services.identity.roster(uuid)
            if not roster or not roster.rostered:
                return "not on the student roster"
            if not state.is_online(uuid):
                return "not registered to attend online"
            return None if state.has_grant(uuid) else "online ID verification required"

        services.agm_sso.register_zoom_client(zoom_eligibility)

        @router.get("/agm/invitation")
        def invitation(account: dict = verified):
            submission = state.rsvp(account["uuid"])
            if not submission:
                return {"registered": False, "form": "agm_rsvp"}
            online = submission["data"].get("attendance") == "Online"
            result = {
                "registered": True,
                "mode": "online" if online else "in_person",
                "rsvp_status": submission["status"],
                "checked_in": store.get("agm_attendance", account["uuid"]) is not None,
            }
            if online:
                grant = state.has_grant(account["uuid"])
                result.update(id_verified=grant, zoom_sso_url=zoom_sso_url if grant else "")
            return result

        @router.get("/agm/qr")
        def qr(account: dict = verified):
            if not state.rsvp(account["uuid"]):
                raise HTTPException(409, "register for the AGM first")
            return {"payload": account["uuid"], "svg": qr_svg(account["uuid"])}

        @router.post("/agm/zoom/join")
        def zoom_join(account: dict = verified):
            if not state.is_online(account["uuid"]):
                raise HTTPException(409, "you are not registered to attend online")
            if not state.has_grant(account["uuid"]):
                raise HTTPException(403, "complete online ID verification first")
            if not zoom_url:
                raise HTTPException(503, "Zoom link not configured")
            if store.create(
                "agm_attendance",
                account["uuid"],
                {"uuid": account["uuid"], "mode": "online", "at": services.now(), "by": account["uuid"]},
            ):
                services.audit.record(
                    account["uuid"], "agm.online_join", f"user:{account['uuid']}", None, {"mode": "online"}
                )
            return {"zoom_url": zoom_url, "sso_url": zoom_sso_url}

        @router.post("/admin/agm/checkin")
        def checkin(body: CheckinIn, account: dict = checkin_admin):
            uuid = body.uuid.strip().lower()
            person = services.identity.person(uuid) if UUID.match(uuid) else None
            if person is None or person.status != "verified":
                raise HTTPException(404, "unknown or unverified attendee")
            roster = services.identity.roster(uuid)
            if not roster or not roster.rostered:
                raise HTTPException(403, "attendee is not on the student roster")
            submission = state.rsvp(uuid)
            if not submission:
                raise HTTPException(
                    409, f"{services.auth.display_name(uuid) or ''} {person.last_name or ''} has no AGM registration"
                )
            seen: dict[str, bool] = {}

            def arrive(record: dict[str, Any] | None) -> dict[str, Any] | None:
                seen["first"] = not record or record["mode"] != "in_person"
                return (
                    {"uuid": uuid, "mode": "in_person", "at": services.now(), "by": account["uuid"]}
                    if seen["first"]
                    else None
                )

            record = store.update("agm_attendance", uuid, arrive)
            first = seen.get("first", False)
            if record is None:
                raise RuntimeError("AGM attendance update returned no record")
            if first:
                services.audit.record(account["uuid"], "agm.checkin", f"user:{uuid}", None, {"mode": "in_person"})
            data = submission["data"]
            preferred_name = services.auth.display_name(uuid) or person.first_name or ""
            last_name = person.last_name or ""
            display_name = f"{preferred_name} {last_name}".strip()
            legal_name = f"{person.first_name or ''} {last_name}".strip()
            return {
                "uuid": uuid,
                "name": display_name,
                "legal_name": legal_name,
                "pronouns": data.get("pronouns", ""),
                "proxy": bool(data.get("proxy")),
                "already_checked_in": not first,
                "checked_in_at": record["at"],
                "message": f"{'Checked in' if first else 'Already checked in'}: {display_name}",
            }

        @router.get("/admin/agm/attendance")
        def attendance(
            cursor: str = Query("", max_length=2048),
            limit: int = Query(100, ge=1, le=200),
            _: dict = checkin_admin,
        ):
            order_by = (PageOrder("uuid", str),)
            page, next_cursor = paginate_store(store, "agm_attendance", cursor, limit, order_by)
            return {"items": page, "next_cursor": next_cursor}

        @router.get("/admin/agm/rsvps")
        def registrations(
            cursor: str = Query("", max_length=2048),
            limit: int = Query(100, ge=1, le=200),
            _: dict = agm_admin,
        ):
            order_by = (PageOrder("id", str),)
            page, next_cursor = paginate_page(
                lambda after, page_limit: submissions.page_submissions(
                    "agm_rsvp", order_by=order_by, after=after, limit=page_limit
                ),
                cursor,
                limit,
                order_by,
            )
            result = []
            for submission in page:
                uuid = submission["uuid"]
                person = services.identity.person(uuid)
                account = services.identity.account(uuid)
                if person is None or account is None:
                    raise HTTPException(503, "identity unavailable for an AGM registration")
                roster = services.identity.roster(uuid)
                result.append(
                    {
                        "id": submission["id"],
                        "uuid": uuid,
                        "name": f"{person.first_name or ''} {person.last_name or ''}".strip(),
                        "attendance": submission["data"].get("attendance", ""),
                        "accessibility": submission["data"].get("accessibility", ""),
                        "proxy": bool(submission["data"].get("proxy")),
                        "proxy_name": submission["data"].get("proxy_name", ""),
                        "submission_status": submission["status"],
                        "eligible": account.get("status") == "verified"
                        and bool(roster and roster.rostered)
                        and submission["status"] != "rejected",
                        "roster_reason": roster.reason if roster else "roster check unavailable",
                        "checked_in": store.get("agm_attendance", uuid) is not None,
                    }
                )
            return {"items": result, "next_cursor": next_cursor}

        @router.get("/admin/agm/rsvps.csv")
        def export_registrations(actor: dict = agm_admin):
            cursor, rows = "", []
            while True:
                page = registrations(cursor, 200, actor)
                rows.extend(page["items"])
                if page["next_cursor"] is None:
                    break
                cursor = page["next_cursor"]
            services.audit.record(
                actor["uuid"], "agm.rsvps_exported", "agm:rsvps", None, None, {"count": len(rows)}
            )
            return csv_response(
                "agm-registrations.csv",
                [
                    "id",
                    "uuid",
                    "name",
                    "attendance",
                    "accessibility",
                    "proxy",
                    "proxy_name",
                    "submission_status",
                    "eligible",
                    "roster_reason",
                    "checked_in",
                ],
                rows,
            )

        @router.post("/admin/agm/verify")
        def verify_person(body: VerifyIn, actor: dict = agm_admin):
            match = services.agm_review.roster_match(
                actor["uuid"],
                {"full_name": body.full_name, "id": body.student_number, "division": body.division},
            )
            if match is None:
                raise HTTPException(409, "student roster has not been loaded")
            services.audit.record(
                actor["uuid"],
                "agm.roster_check",
                "roster",
                None,
                None,
                {"verified": match["verified"], "method": match["method"]},
            )
            return match

        return router
