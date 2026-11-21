"""Separate ID verifier for AGM registrants who join online on the day.

It is deliberately independent of the account-activation review: it only answers "has this registrant freshly proven who
they are for the AGM?" and records a short-lived grant that the AGM module / SSO eligibility check consults.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
from binascii import Error as Base64Error

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import security
from ...context import Context
from ...context import roles_of
from ...contracts import FileKind

name = "agm_verify"
REVIEWER = "agm_id_reviewer"


def rsvp(ctx: Context, uid: str) -> dict | None:
    s = ctx.forms.for_owner("agm").submission("agm_rsvp", uid)
    return s if s and s["status"] != "rejected" else None


def is_online(ctx: Context, uid: str) -> bool:
    s = rsvp(ctx, uid)
    return bool(s and s["data"].get("attendance") == "Online")


def has_grant(ctx: Context, uid: str) -> bool:
    now = ctx.now()
    for check in ctx.store.list("agm_checks", uuid=uid, status="approved"):
        if not check.get("decided_by"):
            continue
        if "admitted_until" in check:
            if check["admitted_until"] > now:
                return True
        else:
            # Legacy manual approvals wrote a separate grant; a grant without a review never qualifies.
            grant = ctx.store.get("agm_grants", uid)
            decided_at = check.get("decided_at")
            if grant and decided_at is not None and decided_at <= grant.get("at", 0) and min(
                grant["exp"], decided_at + ctx.plugin_setting("agm", "agm_grant_ttl")
            ) > now:
                return True
    return False


class ScanIn(BaseModel):
    face: str
    id_card: str
    challenge: str


class Decision(BaseModel):
    approve: bool
    note: str = ""


class AgmVerifyModule:
    name = name

    def configure(self, ctx: Context) -> None:
        def authorized(action: str, actor: str, _resource: str, reference: str) -> bool:
            account = ctx.identity.account(actor)
            if not account or account["status"] != "verified" or not roles_of(account, ctx.roles) & {REVIEWER, "admin"}:
                return False
            check = ctx.store.get("agm_checks", reference)
            return bool(check and check["uuid"] != actor)

        def read(reference: str, _resource: str) -> bytes | None:
            check = ctx.store.get("agm_checks", reference)
            if not check or check["status"] != "pending":
                return None
            media = ctx.store.get("agm_media", reference)
            return json.dumps({k: v for k, v in media.items() if k not in {"id", "uuid"}}).encode() if media else None

        ctx.files.register_kind(
            "agm_verify",
            "agm_identity_scan",
            FileKind(
                authorize=authorized,
                read=read,
                delete=lambda reference, _resource: ctx.store.delete("agm_media", reference),
                audit_action="file.read",
                audit_target=lambda _resource, reference: f"agm:verification:{reference}",
            ),
        )

    def router(self, ctx: Context) -> APIRouter:
        r = APIRouter(prefix="/api")
        S, cfg = ctx.store, ctx.settings
        grant_ttl = ctx.plugin_setting("agm", "agm_grant_ttl")
        verified, admin = ctx.require_rostered(), ctx.require_role(REVIEWER)

        def queue() -> list[dict]:
            return sorted(S.list("agm_checks", status="pending"), key=lambda c: (c["submitted_at"], c["id"]))

        def queue_status(checks: list[dict], position: int) -> dict:
            return {"status": "pending", "position": position, "queue_length": len(checks),
                    "wait_after": min(60, 5 + position * 3 + len(checks) // 5)}

        def img(d: str) -> str:
            m = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", d or "")
            if not m:
                raise HTTPException(422, "image must be a base64 jpeg/png/webp data URL")
            try:
                size = len(base64.b64decode(m.group(2), validate=True))
            except Base64Error as exc:
                raise HTTPException(422, "image could not be decoded; capture it again") from exc
            if size > cfg.max_image_bytes:
                raise HTTPException(413, "image too large")
            return d

        @r.get("/agm/verify/challenge")
        def challenge(_: dict = verified):
            nonce = secrets.token_hex(3)
            return {"challenge": security.sign_token(cfg.secret, nonce, 600, "chal"), "phrase": nonce.upper()}

        @r.post("/agm/verify", status_code=201)
        def submit(b: ScanIn, a: dict = verified):
            uid = a["uuid"]
            if not is_online(ctx, uid):
                raise HTTPException(409, "you are not registered to attend online")
            if has_grant(ctx, uid):
                return {"status": "approved"}
            if security.read_token(cfg.secret, b.challenge, "chal") is None:
                raise HTTPException(422, "live-scan challenge missing or expired; capture again")
            face, idc = img(b.face), img(b.id_card)
            if any(S.list("agm_checks", uuid=uid, status="pending")):
                raise HTTPException(409, "a check is already pending")
            cid = secrets.token_hex(12)
            S.put("agm_media", cid, {"id": cid, "uuid": uid, "face": face, "id_card": idc})
            S.put("agm_checks", cid, {"id": cid, "uuid": uid, "status": "pending", "submitted_at": ctx.now()})
            checks = queue()
            position = next((i for i, check in enumerate(checks, 1) if check["id"] == cid), None)
            if position is not None:
                return {"id": cid, **queue_status(checks, position)}
            return {"id": cid, **status(a)}

        @r.get("/agm/verify")
        def status(a: dict = verified):
            uid = a["uuid"]
            if not is_online(ctx, uid):
                raise HTTPException(409, "you are not registered to attend online")
            if has_grant(ctx, uid):
                return {"status": "approved"}
            checks = queue()
            for position, check in enumerate(checks, 1):
                if check["uuid"] == uid:
                    return queue_status(checks, position)
            latest = max(S.list("agm_checks", uuid=uid), key=lambda c: (c["submitted_at"], c["id"]), default=None)
            if latest and latest["status"] == "rejected":
                return {"status": "rejected", "note": latest.get("note", "")}
            # A reviewer may have committed between the first grant read and the queue read.
            if has_grant(ctx, uid):
                return {"status": "approved"}
            return {"status": "none"}

        @r.get("/admin/agm/verifications")
        def pending(actor: dict = admin):
            out = []
            for position, c in enumerate(queue(), 1):
                u = ctx.identity.user(c["uuid"]) or {}
                out.append({"id": c["id"], "position": position, "submitted_at": c["submitted_at"],
                            "first_name": u.get("first_name"), "last_name": u.get("last_name"),
                            "student_number": u.get("student_number"), "can_review": c["uuid"] != actor["uuid"]})
            return out

        @r.get("/admin/agm/verifications/{cid}/media")
        def media(cid: str, actor: dict = admin):
            check = S.get("agm_checks", cid)
            if not check or check["status"] != "pending":
                raise HTTPException(404, "no pending check")
            try:
                body = ctx.files.read("agm_identity_scan", cid, actor["uuid"], cid)
            except FileNotFoundError as exc:
                raise HTTPException(404, "media gone") from exc
            except PermissionError as exc:
                raise HTTPException(403, "not permitted to read AGM media") from exc
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("stored AGM media is invalid") from exc

        @r.post("/admin/agm/verifications/{cid}/decision")
        def decide(cid: str, b: Decision, a: dict = admin):
            note = b.note.strip()[:500]
            if not b.approve and not note:
                raise HTTPException(422, "explain what the attendee needs to resubmit")

            def claim(c):  # optimistic lock: a check can be decided once
                if not c or c["status"] != "pending":
                    raise HTTPException(404, "no pending check")
                if c["uuid"] == a["uuid"]:
                    raise HTTPException(403, "cannot review your own check")
                if b.approve and (not is_online(ctx, c["uuid"]) or not S.get("agm_media", cid)):
                    raise HTTPException(409, "approval requires an online registration and a submitted scan")
                now = ctx.now()
                return {**c, "status": "approved" if b.approve else "rejected", "note": note,
                        "decided_by": a["uuid"], "decided_at": now, "admitted_until": now + grant_ttl if b.approve else 0}

            c = S.update("agm_checks", cid, claim)
            ctx.files.delete("agm_identity_scan", cid, a["uuid"], c["uuid"])
            ctx.audit.record(a["uuid"], "agm.verification_decided", f"user:{c['uuid']}", {"status": "pending"}, {"status": c["status"]},
                             {"check": cid, "note": note[:200]})
            return {"status": c["status"]}

        return r
