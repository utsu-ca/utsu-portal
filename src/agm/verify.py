"""Separate ID verifier for AGM registrants who join online on the day."""
from __future__ import annotations

import base64
import re
import secrets
from binascii import Error as Base64Error
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.contracts import FileKind, PluginManifest, PluginServices, RegistrationContext
from core.permissions import PERM_AGM_ID_REVIEW

from .state import AgmVerificationAccess


class ScanIn(BaseModel):
    face: str
    id_card: str
    challenge: str


class Decision(BaseModel):
    approve: bool
    note: str = ""


class AgmVerifyModule:
    manifest = PluginManifest(
        "agm_verify",
        "0.1.0",
        1,
        "agm.verify:AgmVerifyModule",
        dependencies=("agm",),
        order=30,
        interface="plugin",
        frontend=True,
        roles=(("agm_id_reviewer", "review AGM online ID scans and approve Zoom sign-on"),),
        services=("agm_image_limits", "agm_review", "audit", "auth", "files", "identity", "integrity", "now", "provided"),
        permissions=((PERM_AGM_ID_REVIEW, "Review AGM online ID scans and approve Zoom sign-on."),),
        role_permissions=(("agm_id_reviewer", (PERM_AGM_ID_REVIEW,)),),
    )

    def configure(self, registration: RegistrationContext) -> None:
        def file_kind(services: PluginServices) -> FileKind:
            state = cast(AgmVerificationAccess, services.provided["agm_verification"])

            def authorize(action: str, actor: str, _resource: str, reference: str) -> bool:
                account = services.identity.account(actor)
                check = state.check(reference)
                return bool(
                    action in {"read", "delete"}
                    and account
                    and services.auth.has_permission(account, PERM_AGM_ID_REVIEW)
                    and check
                    and check["uuid"] != actor
                )

            return FileKind(
                authorize=authorize,
                read=lambda reference, _resource: state.pending_media(reference),
                delete=lambda reference, _resource: state.delete_media(reference),
                audit_action="file.read",
                audit_target=lambda _resource, reference: f"agm:verification:{reference}",
            )

        registration.register_file_kind("agm_identity_scan", file_kind)

    def router(self, services: PluginServices) -> APIRouter:
        router = APIRouter(prefix="/api")
        state = cast(AgmVerificationAccess, services.provided["agm_verification"])
        grant_ttl = state.grant_ttl
        verified = services.auth.require_rostered()
        reviewer = services.auth.require_permission(PERM_AGM_ID_REVIEW)

        def queue() -> list[dict[str, Any]]:
            return state.pending_checks()

        def queue_status(checks: list[dict[str, Any]], position: int) -> dict[str, Any]:
            return {
                "status": "pending",
                "position": position,
                "queue_length": len(checks),
                "wait_after": min(60, 5 + position * 3 + len(checks) // 5),
            }

        def img(data_url: str) -> str:
            match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", data_url or "")
            if not match:
                raise HTTPException(422, "image must be a base64 jpeg/png/webp data URL")
            try:
                size = len(base64.b64decode(match.group(2), validate=True))
            except Base64Error as exc:
                raise HTTPException(422, "image could not be decoded; capture it again") from exc
            if size > services.agm_image_limits.max_scan_image_bytes:
                raise HTTPException(413, "image too large")
            return data_url

        @router.get("/agm/verify/challenge")
        def challenge(_: dict = verified):
            nonce = secrets.token_hex(3)
            return {"challenge": services.integrity.issue("agm-scan", nonce, 600), "phrase": nonce.upper()}

        @router.post("/agm/verify", status_code=201)
        def submit(body: ScanIn, account: dict = verified):
            uuid = account["uuid"]
            if not state.is_online(uuid):
                raise HTTPException(409, "you are not registered to attend online")
            if state.has_grant(uuid):
                return {"status": "approved"}
            if services.integrity.verify("agm-scan", body.challenge) is None:
                raise HTTPException(422, "live-scan challenge missing or expired; capture again")
            face, id_card = img(body.face), img(body.id_card)
            if any(check["status"] == "pending" for check in state.checks_for(uuid)):
                raise HTTPException(409, "a check is already pending")
            check_id = secrets.token_hex(12)
            state.save_scan(check_id, uuid, face, id_card, services.now())
            checks = queue()
            position = next((index for index, check in enumerate(checks, 1) if check["id"] == check_id), None)
            if position is not None:
                return {"id": check_id, **queue_status(checks, position)}
            return {"id": check_id, **status(account)}

        @router.get("/agm/verify")
        def status(account: dict = verified):
            uuid = account["uuid"]
            if not state.is_online(uuid):
                raise HTTPException(409, "you are not registered to attend online")
            if state.has_grant(uuid):
                return {"status": "approved"}
            checks = queue()
            for position, check in enumerate(checks, 1):
                if check["uuid"] == uuid:
                    return queue_status(checks, position)
            latest = max(
                state.checks_for(uuid),
                key=lambda check: (check["submitted_at"], check["id"]),
                default=None,
            )
            if latest and latest["status"] == "rejected":
                return {"status": "rejected", "note": latest.get("note", "")}
            if state.has_grant(uuid):
                return {"status": "approved"}
            return {"status": "none"}

        @router.get("/admin/agm/verifications")
        def pending(actor: dict = reviewer):
            result = []
            for position, check in enumerate(queue(), 1):
                person = services.agm_review.reviewer_person(actor["uuid"], check["uuid"])
                result.append(
                    {
                        "id": check["id"],
                        "position": position,
                        "submitted_at": check["submitted_at"],
                        "first_name": person.first_name if person else None,
                        "last_name": person.last_name if person else None,
                        "student_number": person.student_number if person else None,
                        "can_review": check["uuid"] != actor["uuid"],
                    }
                )
            return result

        @router.get("/admin/agm/verifications/{check_id}/media")
        def media(check_id: str, actor: dict = reviewer):
            check = state.check(check_id)
            if not check or check["status"] != "pending":
                raise HTTPException(404, "no pending check")
            try:
                body = services.files.read("agm_identity_scan", check_id, actor["uuid"], check_id)
            except FileNotFoundError as exc:
                raise HTTPException(404, "media gone") from exc
            except PermissionError as exc:
                raise HTTPException(403, "not permitted to read AGM media") from exc
            try:
                import json

                return json.loads(body)
            except ValueError as exc:
                raise RuntimeError("stored AGM media is invalid") from exc

        @router.post("/admin/agm/verifications/{check_id}/decision")
        def decide(check_id: str, body: Decision, actor: dict = reviewer):
            note = body.note.strip()[:500]
            if not body.approve and not note:
                raise HTTPException(422, "explain what the attendee needs to resubmit")

            def claim(check: dict[str, Any] | None) -> dict[str, Any]:
                if not check or check["status"] != "pending":
                    raise HTTPException(404, "no pending check")
                if check["uuid"] == actor["uuid"]:
                    raise HTTPException(403, "cannot review your own check")
                if body.approve and (not state.is_online(check["uuid"]) or not state.has_media(check_id)):
                    raise HTTPException(409, "approval requires an online registration and a submitted scan")
                now = services.now()
                return {
                    **check,
                    "status": "approved" if body.approve else "rejected",
                    "note": note,
                    "decided_by": actor["uuid"],
                    "decided_at": now,
                    "admitted_until": now + grant_ttl if body.approve else 0,
                }

            check = state.update_check(check_id, claim)
            if check is None:
                raise RuntimeError("AGM verification update returned no record")
            services.files.delete("agm_identity_scan", check_id, actor["uuid"], check["uuid"])
            services.audit.record(
                actor["uuid"],
                "agm.verification_decided",
                f"user:{check['uuid']}",
                {"status": "pending"},
                {"status": check["status"]},
                {"check": check_id, "note": note[:200]},
            )
            return {"status": check["status"]}

        return router
