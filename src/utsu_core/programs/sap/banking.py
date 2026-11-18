"""Student aid banking submission and re-verification endpoints."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from ...context import Context
from ...services.email import EmailDeliveryUnknown, EmailNotConfigured

BANKING_FIELDS = ("institution_number", "transit_number", "account_number")
BANKING_ACTIVE = ("on_file", "reverify_requested", "reverify_received")
CHEQUE_DATA_URL = re.compile(r"data:application/pdf;base64,([A-Za-z0-9+/=]+)")

class BankingIn(BaseModel):
    institution_number: str = Field(pattern=r"^[0-9]{3}$")
    transit_number: str = Field(pattern=r"^[0-9]{5}$")
    account_number: str = Field(pattern=r"^[0-9]{1,12}$")


class DestroyBankingIn(BaseModel):
    confirm: bool


class ReverifyBankingIn(BankingIn):
    token: str = Field(min_length=1, max_length=128)
    void_cheque: str = Field(min_length=1, max_length=6_000_000)


class AcceptBankingIn(BaseModel):
    revision: int = Field(ge=0)
    confirm: bool


def parse_void_cheque(ctx: Context, data_url: str) -> dict:
    """A void cheque must be a PDF (data URL): checked for type, signature and size before anything is stored."""
    match = CHEQUE_DATA_URL.fullmatch(data_url)
    if not match:
        raise HTTPException(422, "the void cheque must be a PDF file")
    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except ValueError as exc:
        raise HTTPException(422, "the void cheque could not be read") from exc
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(422, "the void cheque must be a PDF file")
    if len(raw) > ctx.plugin_setting("sap", "max_cheque_bytes"):
        raise HTTPException(413, "the void cheque is too large")
    return {"data_url": data_url, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
            "uploaded_at": ctx.now()}


def banking_router(ctx: Context) -> APIRouter:
    router = APIRouter(prefix="/api")
    forms = ctx.forms.for_owner("sap")
    admin = ctx.require_role("sap_admin")

    @router.put("/sap/banking")
    def submit_banking(body: BankingIn, actor: dict = ctx.require_verified()):
        uid = actor["uuid"]
        submission = forms.submission("student_aid_bursary", uid)
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
        result = {"status": bank["status"] if bank else "not_submitted"}
        if bank and bank["status"] == "reverify_requested":
            result["expires_at"] = bank.get("request_expires")
        return result

    def banking_numbers(uid: str, actor: dict) -> dict:
        bank = ctx.store.get("sap_banking", uid)
        if not bank or bank["status"] not in BANKING_ACTIVE:
            raise HTTPException(404, "banking details unavailable")
        ctx.audit.record(actor["uuid"], "sap.banking_accessed", f"user:{uid}", None, None)
        return {field: bank[field] for field in BANKING_FIELDS}

    def destroy_details(uid: str, body: DestroyBankingIn, actor: dict) -> dict:
        if not body.confirm:
            raise HTTPException(422, "confirmation required")
        def destroy(current):
            if not current or current.get("status") not in BANKING_ACTIVE:
                raise HTTPException(404, "banking details unavailable")
            return {"uuid": uid, "status": "destroyed", "destroyed_at": ctx.now(), "destroyed_by": actor["uuid"]}
        ctx.store.update("sap_banking", uid, destroy)
        ctx.audit.record(actor["uuid"], "sap.banking_destroyed", f"user:{uid}", {"status": "on_file"},
                         {"status": "destroyed"})
        return {"status": "destroyed"}

    def flag_validation_failed(uid: str, actor: dict) -> dict:
        """One click: the applicant is emailed a single-use link to the banking verification page."""
        if not ctx.email.is_configured:
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
        try:
            ctx.email.send_template(
                user["email"], "sap_banking_reverify", {"link": link}, actor=actor["uuid"]
            )
        except EmailNotConfigured as exc:
            raise HTTPException(503, "email delivery is not configured") from exc
        except EmailDeliveryUnknown as exc:
            raise HTTPException(502, "delivery could not be confirmed; do not retry without checking") from exc
        return {"status": "reverify_requested", "expires_at": expires}

    def compare_pending(uid: str, actor: dict) -> dict:
        bank = ctx.store.get("sap_banking", uid)
        if not bank or bank.get("status") != "reverify_received":
            raise HTTPException(404, "pending banking details unavailable")
        ctx.audit.record(actor["uuid"], "sap.banking_pending_accessed", f"user:{uid}", None, None)
        cheque = bank["pending"]["void_cheque"]
        return {"active": {field: bank[field] for field in BANKING_FIELDS},
                "pending": {field: bank["pending"][field] for field in BANKING_FIELDS},
                "void_cheque": {key: cheque[key] for key in ("sha256", "size", "uploaded_at")},
                "revision": bank.get("_rev", 0)}

    def accept_pending(uid: str, body: AcceptBankingIn, actor: dict) -> dict:
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

    def void_cheque_pdf(uid: str, actor: dict) -> Response:
        try:
            raw = ctx.files.read("sap_void_cheque", uid, actor["uuid"], uid)
        except FileNotFoundError as exc:
            raise HTTPException(404, "no void cheque on file") from exc
        except PermissionError as exc:
            raise HTTPException(403, "not permitted to read void cheque") from exc
        return Response(raw, media_type="application/pdf",
                        headers={"Cache-Control": "no-store", "Content-Disposition": 'inline; filename="void-cheque.pdf"',
                                 "X-Content-Type-Options": "nosniff"})

    @router.get("/admin/sap/applications/{uid}/banking")
    def get_banking(uid: str, actor: dict = admin):
        return banking_numbers(uid, actor)

    @router.delete("/admin/sap/applications/{uid}/banking")
    def destroy_banking(uid: str, body: DestroyBankingIn, actor: dict = admin):
        return destroy_details(uid, body, actor)

    @router.post("/admin/sap/applications/{uid}/banking/reverify")
    def request_banking_reverification(uid: str, actor: dict = admin):
        return flag_validation_failed(uid, actor)

    @router.put("/sap/banking/reverify")
    def reverify_banking(body: ReverifyBankingIn, actor: dict = ctx.require_verified()):
        uid = actor["uuid"]
        cheque = parse_void_cheque(ctx, body.void_cheque)
        digest = hmac.new(ctx.settings.secret.encode(), f"sap-banking:{uid}:{body.token}".encode(), hashlib.sha256).hexdigest()
        pending = {**body.model_dump(exclude={"token", "void_cheque"}), "void_cheque": cheque}
        def receive(current):
            if (not current or current.get("status") != "reverify_requested"
                    or current.get("request_expires", 0) <= ctx.now()
                    or not hmac.compare_digest(current.get("request_hash", ""), digest)):
                raise HTTPException(409, "invalid, expired, or already used re-verification link")
            return {**{k: v for k, v in current.items() if k not in ("request_hash", "request_expires")},
                    "status": "reverify_received", "pending": pending, "received_at": ctx.now()}
        ctx.store.update("sap_banking", uid, receive)
        ctx.audit.record(uid, "sap.banking_reverify_received", f"user:{uid}", None, {"cheque_sha256": cheque["sha256"]})
        return {"status": "reverify_received"}

    @router.get("/admin/sap/applications/{uid}/banking/pending")
    def pending_banking(uid: str, actor: dict = admin):
        return compare_pending(uid, actor)

    @router.get("/admin/sap/applications/{uid}/banking/cheque")
    def admin_void_cheque(uid: str, actor: dict = admin):
        return void_cheque_pdf(uid, actor)

    @router.post("/admin/sap/applications/{uid}/banking/accept")
    def accept_banking(uid: str, body: AcceptBankingIn, actor: dict = admin):
        return accept_pending(uid, body, actor)

    # ---- finance controller portal: banking only, no applications, hardship statements or reviews ----
    finance = ctx.require_role("sap_finance")

    @router.get("/finance/sap/banking")
    def finance_banking_list(cursor: str = "", limit: int = 100, _: dict = finance):
        if not 1 <= limit <= 200:
            raise HTTPException(422, "limit must be between 1 and 200")
        records = sorted((b for b in ctx.store.list("sap_banking") if b["uuid"] > cursor), key=lambda b: b["uuid"])
        items = []
        for bank in records[:limit]:
            uid = bank["uuid"]
            user = ctx.identity.user(uid) or {}
            submission = forms.submission("student_aid_bursary", uid) or {}
            decision = (submission.get("sap_review") or {}).get("decision") or {}
            items.append({"uuid": uid, "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
                          "status": bank["status"], "request_expires": bank.get("request_expires"),
                          "received_at": bank.get("received_at"),
                          "has_cheque": bool(bank.get("void_cheque") or (bank.get("pending") or {}).get("void_cheque")),
                          "approved": decision.get("approved"), "awarded_amount": decision.get("amount")})
        return {"items": items, "next_cursor": items[-1]["uuid"] if len(records) > limit else None}

    @router.get("/finance/sap/banking/{uid}")
    def finance_banking_details(uid: str, actor: dict = finance):
        return banking_numbers(uid, actor)

    @router.post("/finance/sap/banking/{uid}/validation-failed")
    def finance_validation_failed(uid: str, actor: dict = finance):
        return flag_validation_failed(uid, actor)

    @router.get("/finance/sap/banking/{uid}/pending")
    def finance_pending(uid: str, actor: dict = finance):
        return compare_pending(uid, actor)

    @router.get("/finance/sap/banking/{uid}/cheque")
    def finance_void_cheque(uid: str, actor: dict = finance):
        return void_cheque_pdf(uid, actor)

    @router.post("/finance/sap/banking/{uid}/accept")
    def finance_accept(uid: str, body: AcceptBankingIn, actor: dict = finance):
        return accept_pending(uid, body, actor)


    return router
