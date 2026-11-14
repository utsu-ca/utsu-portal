"""Explicit, previewed program status notices."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..context import Context
from ..mailer import Mailer

log = logging.getLogger("utsu_core.notices")
PROGRAMS = {
    "agm": ("agm_rsvp", "AGM registration", "agm_admin"),
    "senate": ("senate_application", "Senate application", "senate_admin"),
    "sap": ("student_aid_bursary", "Student Aid Bursary application", "sap_admin"),
}


class NoticePreview(BaseModel):
    uid: str = Field(min_length=1, max_length=128)


class NoticeSend(NoticePreview):
    preview_hash: str = Field(min_length=64, max_length=64)
    confirm: bool


class NoticesModule:
    name = "notices"

    def router(self, ctx: Context) -> APIRouter:
        router = APIRouter(prefix="/api/admin/notices")

        def build(program: str, uid: str):
            fid, _, _ = PROGRAMS[program]
            submission = ctx.store.get("submissions", f"{fid}:{uid}")
            account, user = ctx.identity.account(uid), ctx.identity.user(uid)
            if not submission:
                raise HTTPException(404, "application not found")
            if not account or not user or not account.get("email_verified") or not user.get("email"):
                raise HTTPException(409, "verified applicant email unavailable")
            message = {"uid": uid, "program": program, "to": user["email"],
                       "subject": "Your UTSU portal account",
                       "body": "Please sign in to the UTSU portal to check your application status.\n",
                       "revision": submission.get("_rev", 0), "status": submission["status"]}
            encoded = json.dumps(message, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            digest = hmac.new(ctx.settings.secret.encode(), encoded, hashlib.sha256).hexdigest()
            return message, digest

        def add_program(program: str, role: str):
            authorized = ctx.require_role(role)

            @router.post(f"/{program}/preview", name=f"{program}_notice_preview")
            def preview(body: NoticePreview, _: dict = authorized):
                message, digest = build(program, body.uid)
                previous = ctx.store.get("notice_delivery", digest)
                return {**message, "preview_hash": digest, "delivery_status": (previous or {}).get("status")}

            @router.post(f"/{program}/send", name=f"{program}_notice_send")
            def send(body: NoticeSend, actor: dict = authorized):
                if not body.confirm:
                    raise HTTPException(422, "confirmation is required")
                if type(ctx.mailer).send is Mailer.send:
                    raise HTTPException(503, "email delivery is not configured")
                message, digest = build(program, body.uid)
                if not hmac.compare_digest(digest, body.preview_hash):
                    raise HTTPException(409, "notice changed; review a new preview")
                record = {"id": digest, "program": program, "uid": body.uid, "status": "sending",
                          "by": actor["uuid"], "at": ctx.now()}
                if not ctx.store.create("notice_delivery", digest, record):
                    raise HTTPException(409, "notice already sent or delivery is unresolved")
                try:
                    ctx.mailer.send(message["to"], message["subject"], message["body"])
                except Exception as exc:
                    log.exception("notice delivery failed or is uncertain")
                    ctx.store.update("notice_delivery", digest, lambda current: {**current, "status": "unknown"})
                    ctx.audit.record(actor["uuid"], "notice.delivery_unknown", f"user:{body.uid}", None, None,
                                     {"program": program, "notice": digest})
                    raise HTTPException(502, "delivery could not be confirmed; do not retry without checking") from exc
                ctx.store.update("notice_delivery", digest, lambda current: {**current, "status": "sent", "sent_at": ctx.now()})
                ctx.audit.record(actor["uuid"], "notice.sent", f"user:{body.uid}", None, None,
                                 {"program": program, "notice": digest})
                return {"status": "sent"}

        for program, (_, _, role) in PROGRAMS.items():
            add_program(program, role)
        return router
