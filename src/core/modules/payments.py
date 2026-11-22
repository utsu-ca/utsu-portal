"""Persistent, server-priced payment orders and verified Stripe webhooks."""
from __future__ import annotations

import hashlib
import logging
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError

from ..context import Context
from ..pagination import PageOrder, paginate_store
from ..permissions import PERM_PAYMENTS_MANAGE
from ..services.payments import PaymentGateway, StripeGateway
from ..store import ACCESS, AccessLog, Conflict

log = logging.getLogger("core.payments")
EVENT_STATES = {
    "checkout.session.completed": "processing",
    "checkout.session.async_payment_succeeded": "paid",
    "checkout.session.async_payment_failed": "failed",
    "checkout.session.expired": "expired",
}


class OrderIn(BaseModel):
    request_id: UUID
    uuid: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=200)
    amount_cents: int = Field(strict=True, ge=50, le=99_999_999)
    currency: Literal["cad"] = "cad"


class CheckoutSession(BaseModel):
    id: str = Field(min_length=1)
    object: Literal["checkout.session"]
    mode: Literal["payment"]
    client_reference_id: str
    metadata: dict[str, str]
    amount_total: int = Field(strict=True)
    currency: str
    payment_status: Literal["paid", "unpaid", "no_payment_required"]
    payment_intent: str | None = None
    status: Literal["open", "complete", "expired"]


class PaymentsModule:
    name = "payments"

    def configure(self, ctx: Context) -> None:
        key = ctx.plugin_setting(self.name, "stripe_secret_key")
        webhook = ctx.plugin_setting(self.name, "stripe_webhook_secret")
        if bool(key) != bool(webhook):
            raise ValueError("both Stripe secret key and webhook secret are required")
        if key:
            base = urlsplit(ctx.settings.sso_issuer)
            local = base.hostname in ("localhost", "127.0.0.1", "::1")
            if not base.hostname or (base.scheme != "https" and not (
                base.scheme == "http" and local and key.startswith(("sk_test_", "rk_test_"))
            )):
                raise ValueError("Stripe requires an HTTPS PORTAL_BASE_URL (test keys may use localhost)")
            if not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
                raise ValueError("invalid Stripe secret key format")
            if not webhook.startswith("whsec_"):
                raise ValueError("invalid Stripe webhook secret format")
            ctx.services["payments"] = StripeGateway(key, webhook)

    def router(self, ctx: Context) -> APIRouter:
        router = APIRouter(prefix="/api")
        staff = ctx.require_permission(PERM_PAYMENTS_MANAGE)
        verified = ctx.require_verified()

        def gateway() -> PaymentGateway:
            service = ctx.services.get("payments")
            if service is None:
                raise HTTPException(503, "Stripe payments are not configured")
            return service

        def view(order: dict) -> dict:
            return {field: order[field] for field in (
                "id", "uuid", "description", "amount_cents", "currency", "status", "created_at",
            )}

        def owned(oid: str, actor: dict) -> dict:
            order = ctx.store.get("orders", oid)
            if not order or order["uuid"] != actor["uuid"]:
                raise HTTPException(404, "order not found")
            return order

        @router.post("/finance/orders", status_code=201)
        def create_order(body: OrderIn, actor: dict = staff):
            gateway()
            account = ctx.identity.account(body.uuid)
            if not account or account.get("status") != "verified" or not account.get("email_verified"):
                raise HTTPException(422, "a verified recipient profile is required")
            oid = hashlib.sha256(f"{actor['uuid']}:{body.request_id}".encode()).hexdigest()
            values = body.model_dump(exclude={"request_id"})
            now = ctx.now()
            order = {
                **values, "id": oid, "created_by": actor["uuid"], "status": "awaiting_payment",
                "created_at": now, "checkout_expires": now + 23 * 3600,
            }
            if not ctx.store.create("orders", oid, order):
                existing = ctx.store.get("orders", oid)
                if not existing or any(existing.get(k) != v for k, v in values.items()):
                    raise HTTPException(409, "request id already used for a different order")
                order = existing
            return view(order)

        @router.get("/finance/orders")
        def finance_orders(cursor: str = Query("", max_length=2048), limit: int = Query(100, ge=1, le=200),
                           actor: dict = staff):
            page, next_cursor = paginate_store(
                ctx.store,
                "orders",
                cursor,
                limit,
                (PageOrder("created_at", float, descending=True), PageOrder("id", str)),
            )
            return {"items": [view(order) for order in page], "next_cursor": next_cursor}

        @router.get("/orders")
        def my_orders(cursor: str = Query("", max_length=2048), limit: int = Query(100, ge=1, le=200),
                      actor: dict = verified):
            page, next_cursor = paginate_store(
                ctx.store,
                "orders",
                cursor,
                limit,
                (PageOrder("created_at", float, descending=True), PageOrder("id", str)),
                equals={"uuid": actor["uuid"]},
            )
            return {"items": [view(order) for order in page], "next_cursor": next_cursor}

        @router.get("/orders/{oid}")
        def my_order(oid: str, actor: dict = verified):
            return view(owned(oid, actor))

        @router.post("/orders/{oid}/checkout")
        def checkout(oid: str, actor: dict = verified):
            provider = gateway()
            order = owned(oid, actor)
            if order["status"] not in ("awaiting_payment", "checkout"):
                raise HTTPException(409, "order cannot be paid in its current state")
            if order["checkout_expires"] - ctx.now() < 1800:
                raise HTTPException(409, "order checkout window ended; contact finance before creating a replacement")
            if order.get("checkout_url"):
                return {"url": order["checkout_url"]}
            # Fixed parameters + a fixed key let Stripe recover even if the process
            # crashes after Stripe creates the session but before we save its id.
            session = provider.checkout(order, ctx.settings.sso_issuer + "/static/index.html#orders")

            def save(current):
                if not current or current["status"] not in ("awaiting_payment", "checkout"):
                    raise HTTPException(409, "order state changed; refresh before paying")
                if current.get("session_id") and current["session_id"] != session["session_id"]:
                    raise HTTPException(409, "checkout session conflict")
                return {**current, **session, "status": "checkout"}

            try:
                ctx.store.update("orders", oid, save)
            except Conflict as exc:
                raise HTTPException(409, "order changed; retry this same order") from exc
            return {"url": session["checkout_url"]}

        def process_event(event: dict) -> None:
            eid, kind = event.get("id"), event.get("type")
            if not isinstance(eid, str) or not eid or not isinstance(kind, str):
                raise HTTPException(400, "invalid payment event")
            if kind not in EVENT_STATES:
                return
            key = ctx.plugin_setting(self.name, "stripe_secret_key")
            if event.get("livemode") is not key.startswith(("sk_live_", "rk_live_")):
                raise HTTPException(400, "payment event mode mismatch")
            if ctx.store.get("payment_events", eid):
                return
            try:
                session = CheckoutSession.model_validate((event.get("data") or {}).get("object"))
            except (ValidationError, AttributeError) as exc:
                raise HTTPException(400, "invalid checkout session event") from exc
            oid = session.metadata.get("order_id")
            if not oid:
                raise HTTPException(400, "payment event has no order reference")
            state = EVENT_STATES[kind]
            if kind == "checkout.session.completed":
                if session.status != "complete":
                    raise HTTPException(400, "checkout is not complete")
                if session.payment_status == "paid":
                    state = "paid"
            if state == "paid" and session.payment_status != "paid":
                raise HTTPException(400, "payment is not paid")
            if state == "expired" and session.status != "expired":
                raise HTTPException(400, "checkout is not expired")

            def apply(current):
                if not current or not current.get("session_id"):
                    raise HTTPException(409, "checkout order not ready; retry webhook")
                if (session.id != current["session_id"] or session.client_reference_id != oid
                        or session.amount_total != current["amount_cents"]
                        or session.currency != current["currency"]):
                    raise HTTPException(400, "payment event does not match order")
                if current["status"] == "paid":
                    return None
                if current["status"] in ("failed", "expired") and state != "paid":
                    return None
                return {**current, "status": state, "payment_intent": session.payment_intent,
                        "updated_at": ctx.now()}

            try:
                ctx.store.update("orders", oid, apply)
                ctx.store.create("payment_events", eid, {"id": eid, "order_id": oid, "type": kind, "processed_at": ctx.now()})
            except Conflict as exc:
                raise HTTPException(503, "payment update busy; retry webhook") from exc

        @router.post("/payments/stripe/webhook")
        async def webhook(request: Request):
            provider = gateway()
            payload = await request.body()
            if len(payload) > ctx.settings.max_body_bytes:
                raise HTTPException(413, "payment webhook too large")
            event = await run_in_threadpool(provider.event, payload, request.headers.get("stripe-signature", ""))
            token = ACCESS.set(AccessLog("stripe:webhook"))
            try:
                await run_in_threadpool(process_event, event)
            finally:
                ACCESS.reset(token)
            return {"received": True}

        return router
