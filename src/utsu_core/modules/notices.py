"""Explicit, previewed program status notices."""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..context import Context
from ..services.email import EmailDeliveryConflict, EmailDeliveryUnknown, EmailNotConfigured


class NoticePreview(BaseModel):
    uid: str = Field(min_length=1, max_length=128)


class NoticeSend(NoticePreview):
    preview_hash: str = Field(min_length=64, max_length=64)
    confirm: bool


class NoticesModule:
    name = "notices"

    def router(self, ctx: Context) -> APIRouter:
        router = APIRouter(prefix="/api/admin/notices")
        forms = ctx.forms.core_view()

        def build(identifier: str, definition, uid: str):
            submission = forms.submission(definition.form_id, uid)
            account, user = ctx.identity.account(uid), ctx.identity.user(uid)
            if not submission:
                raise HTTPException(404, "application not found")
            if not account or not user or not account.get("email_verified") or not user.get("email"):
                raise HTTPException(409, "verified applicant email unavailable")
            rendered = ctx.email.render_template(definition.email_template, {})
            message = {"uid": uid, "program": identifier, "to": user["email"],
                       "subject": rendered.subject, "body": rendered.body,
                       "revision": submission.get("_rev", 0), "status": submission["status"]}
            encoded = json.dumps(message, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            digest = hmac.new(ctx.settings.secret.encode(), encoded, hashlib.sha256).hexdigest()
            return message, digest

        def add_notice(identifier: str, definition) -> None:
            authorized = ctx.require_role(definition.role)

            @router.post(f"/{identifier}/preview", name=f"{identifier}_notice_preview")
            def preview(body: NoticePreview, _: dict = authorized):
                message, digest = build(identifier, definition, body.uid)
                previous = ctx.store.get("notice_delivery", digest)
                return {**message, "preview_hash": digest, "delivery_status": (previous or {}).get("status")}

            @router.post(f"/{identifier}/send", name=f"{identifier}_notice_send")
            def send(body: NoticeSend, actor: dict = authorized):
                if not body.confirm:
                    raise HTTPException(422, "confirmation is required")
                if not ctx.email.is_configured:
                    raise HTTPException(503, "email delivery is not configured")
                message, digest = build(identifier, definition, body.uid)
                if not hmac.compare_digest(digest, body.preview_hash):
                    raise HTTPException(409, "notice changed; review a new preview")
                record = {"id": digest, "program": identifier, "uid": body.uid, "status": "sending",
                          "by": actor["uuid"], "at": ctx.now()}
                if not ctx.store.create("notice_delivery", digest, record):
                    raise HTTPException(409, "notice already sent or delivery is unresolved")
                try:
                    delivery_id = ctx.email.send_template(
                        message["to"], definition.email_template, {}, dedupe_key=f"notice:{digest}",
                        actor=actor["uuid"],
                    )
                except EmailNotConfigured as exc:
                    ctx.store.update("notice_delivery", digest,
                                     lambda current: {**current, "status": "failed"})
                    raise HTTPException(503, "email delivery is not configured") from exc
                except EmailDeliveryUnknown as exc:
                    ctx.store.update("notice_delivery", digest,
                                     lambda current: {**current, "status": "unknown"})
                    ctx.audit.record(actor["uuid"], "notice.delivery_unknown", f"user:{body.uid}", None, None,
                                     {"program": identifier, "notice": digest})
                    raise HTTPException(502, "delivery could not be confirmed; do not retry without checking") from exc
                except EmailDeliveryConflict as exc:
                    ctx.store.update("notice_delivery", digest,
                                     lambda current: {**current, "status": "unknown"})
                    ctx.audit.record(actor["uuid"], "notice.delivery_unknown", f"user:{body.uid}", None, None,
                                     {"program": identifier, "notice": digest})
                    raise HTTPException(409, "delivery is unresolved; do not retry without checking") from exc
                ctx.store.update(
                    "notice_delivery", digest,
                    lambda current: {**current, "status": "sent", "sent_at": ctx.now(),
                                     "email_delivery": delivery_id},
                )
                ctx.audit.record(actor["uuid"], "notice.sent", f"user:{body.uid}", None, None,
                                 {"program": identifier, "notice": digest})
                return {"status": "sent"}

        for notice_type in ctx.notification_types.registered():
            add_notice(notice_type.identifier, notice_type.definition)
        return router
