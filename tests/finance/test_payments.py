import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from utsu_core import create_app
from utsu_core.crypto import PREFIX
from utsu_core.mailer import OutboxMailer
from utsu_core.settings import Settings
from utsu_core.store import MemoryStore
from test_financial_profiles import account


@pytest.fixture
def env(monkeypatch):
    pytest.importorskip("stripe")
    inner = MemoryStore()
    app = create_app(Settings(secret="s" * 32, enabled_plugins=(), stripe_secret_key="sk_test_fake",
                              stripe_webhook_secret="whsec_fake"), store=inner, mailer=OutboxMailer())
    ctx = app.state.ctx
    user = account(ctx, "student")
    other = account(ctx, "other")
    staff = account(ctx, "finance", roles=("payments_finance",))
    requests = []

    def create(params, options):
        from stripe import StripeObject
        requests.append((params, options))
        return StripeObject.construct_from({"id": "cs_test_order", "url": "https://checkout.stripe.com/c/pay/test"}, None)

    monkeypatch.setattr(ctx.services["payments"].client.v1.checkout.sessions, "create", create)
    return TestClient(app), ctx, inner, user, other, staff, requests


def make_order(env):
    c, _, _, _, _, staff, _ = env
    body = {"request_id": str(uuid4()), "uuid": "student", "description": "Membership fee",
            "amount_cents": 1500, "currency": "cad"}
    response = c.post("/api/finance/orders", headers=staff, json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def event(order, *, eid="evt_test_paid", kind="checkout.session.completed", paid=True):
    return {"id": eid, "type": kind, "livemode": False, "data": {"object": {
        "id": "cs_test_order", "object": "checkout.session", "mode": "payment",
        "client_reference_id": order["id"], "metadata": {"order_id": order["id"]},
        "amount_total": order["amount_cents"], "currency": order["currency"],
        "payment_status": "paid" if paid else "unpaid", "payment_intent": "pi_test_order",
        "status": "expired" if kind.endswith("expired") else "complete",
    }}}


def send_event(c, value, *, secret="whsec_fake", timestamp=None):
    payload = json.dumps(value).encode()
    ts = int(time.time()) if timestamp is None else timestamp
    signature = hmac.new(secret.encode(), str(ts).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return c.post("/api/payments/stripe/webhook", content=payload,
                  headers={"Stripe-Signature": f"t={ts},v1={signature}"})


def test_orders_are_server_priced_private_and_retry_safe(env):
    c, ctx, inner, user, other, staff, requests = env
    order, body = make_order(env)
    oid = order["id"]
    assert c.post("/api/finance/orders", headers=staff, json=body).json()["id"] == oid
    assert c.post("/api/finance/orders", headers=staff, json={**body, "amount_cents": 1600}).status_code == 409
    assert c.post("/api/finance/orders", headers=user, json=body).status_code == 403
    assert c.post("/api/finance/orders", headers=staff, json={**body, "amount_cents": 1.5}).status_code == 422
    assert c.get(f"/api/orders/{oid}").status_code == 401
    assert c.get(f"/api/orders/{oid}", headers=other).status_code == 404
    assert c.get("/api/orders", headers=other).json() == {"items": [], "next_cursor": None}
    path = f"/api/orders/{oid}/checkout"
    assert c.post(path, headers=other).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: c.post(path, headers=user), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert all(params["line_items"][0]["price_data"]["unit_amount"] == 1500 for params, _ in requests)
    assert len({opts["idempotency_key"] for _, opts in requests}) == 1
    assert len(ctx.store.list("orders")) == 1
    assert inner.get("orders", oid)["description"].startswith(PREFIX)
    assert inner.get("orders", oid)["checkout_url"].startswith(PREFIX)
    assert "checkout_url" not in c.get(f"/api/orders/{oid}", headers=user).json()


def test_order_lists_paginate_with_stable_ties_and_validate_parameters():
    app = create_app(Settings(secret="s" * 32, enabled_plugins=()), mailer=OutboxMailer())
    ctx = app.state.ctx
    user = account(ctx, "student")
    other = account(ctx, "other")
    staff = account(ctx, "finance", roles=("payments_finance",))
    c = TestClient(app)
    orders = []
    for i in range(205):
        order = {"id": f"order-{i:03}", "uuid": "student" if i < 203 else "other",
                 "description": "Fee", "amount_cents": 100, "currency": "cad",
                 "status": "awaiting_payment", "created_at": i // 3, "private": "secret"}
        ctx.store.put("orders", order["id"], order)
        orders.append(order)

    for path, headers, expected in (
        ("/api/orders", user, [o for o in orders if o["uuid"] == "student"]),
        ("/api/finance/orders", staff, orders),
    ):
        expected = sorted(expected, key=lambda o: (-float(o["created_at"]), o["id"]))
        first = c.get(path, headers=headers).json()
        assert len(first["items"]) == 100 and first["next_cursor"]
        large = c.get(path, headers=headers, params={"limit": 200}).json()
        assert len(large["items"]) == 200 and large["next_cursor"]
        second = c.get(path, headers=headers, params={"cursor": first["next_cursor"], "limit": 200}).json()
        assert second["next_cursor"] is None
        combined = first["items"] + second["items"]
        assert [o["id"] for o in combined] == [o["id"] for o in expected]
        assert all(set(o) == {"id", "uuid", "description", "amount_cents", "currency", "status", "created_at"}
                   for o in combined)
        one = c.get(path, headers=headers, params={"limit": 1}).json()
        following = c.get(path, headers=headers, params={"cursor": one["next_cursor"], "limit": 1}).json()
        assert [one["items"][0]["id"], following["items"][0]["id"]] == [o["id"] for o in expected[:2]]
        for params in ({"limit": 0}, {"limit": 201}, {"limit": "bad"}, {"cursor": "not!valid"},
                       {"cursor": "WzFd"}, {"cursor": "a" * 2049}):
            assert c.get(path, headers=headers, params=params).status_code == 422
    assert c.get("/api/finance/orders", headers=other).status_code == 403


def test_signed_webhooks_validate_order_and_do_not_regress_paid_status(env):
    c, ctx, _, user, _, _, _ = env
    order, _ = make_order(env)
    paid = event(order)
    assert send_event(c, paid).status_code == 409  # provider response has not been persisted yet
    assert ctx.store.get("payment_events", paid["id"]) is None
    assert c.post(f"/api/orders/{order['id']}/checkout", headers=user).status_code == 200
    assert send_event(c, paid, secret="wrong").status_code == 400
    assert send_event(c, paid, timestamp=int(time.time()) - 600).status_code == 400
    assert send_event(c, []).status_code == 400
    assert send_event(c, {**paid, "livemode": True}).status_code == 400
    for field, value in (("amount_total", 1), ("currency", "usd"), ("id", "cs_other"),
                         ("client_reference_id", "another-order")):
        bad = deepcopy(paid)
        bad["data"]["object"][field] = value
        assert send_event(c, bad).status_code == 400
    assert send_event(c, paid).status_code == 200
    revision = ctx.store.get("orders", order["id"])["_rev"]
    assert send_event(c, paid).status_code == 200
    assert ctx.store.get("orders", order["id"])["_rev"] == revision
    late = event(order, eid="evt_late_expired", kind="checkout.session.expired", paid=False)
    assert send_event(c, late).status_code == 200
    assert c.get(f"/api/orders/{order['id']}", headers=user).json()["status"] == "paid"
    assert c.post(f"/api/orders/{order['id']}/checkout", headers=user).status_code == 409
    assert any(e["actor"] == "stripe:webhook" and e["action"] == "financial.access" for e in ctx.audit.entries())


def test_async_payments_and_checkout_window(env, monkeypatch):
    c, ctx, _, user, _, _, requests = env
    order, _ = make_order(env)
    assert c.post(f"/api/orders/{order['id']}/checkout", headers=user).status_code == 200
    assert send_event(c, event(order, paid=False)).status_code == 200
    assert ctx.store.get("orders", order["id"])["status"] == "processing"
    assert send_event(c, event(order, eid="evt_async", kind="checkout.session.async_payment_succeeded")).status_code == 200
    assert ctx.store.get("orders", order["id"])["status"] == "paid"
    second, _ = make_order(env)
    now = ctx.store.get("orders", second["id"])["checkout_expires"]
    monkeypatch.setattr(ctx, "now", lambda: now)
    count = len(requests)
    assert c.post(f"/api/orders/{second['id']}/checkout", headers=user).status_code == 409
    assert len(requests) == count


def test_webhook_recovers_after_receipt_write_failure(env, monkeypatch):
    c, ctx, inner, user, _, _, _ = env
    order, _ = make_order(env)
    assert c.post(f"/api/orders/{order['id']}/checkout", headers=user).status_code == 200
    original = inner.create
    failed = False

    def unavailable_once(col, key, doc):
        nonlocal failed
        if col == "payment_events" and not failed:
            failed = True
            raise RuntimeError("receipt storage unavailable")
        return original(col, key, doc)

    monkeypatch.setattr(inner, "create", unavailable_once)
    with pytest.raises(RuntimeError, match="receipt storage unavailable"):
        send_event(c, event(order))
    assert ctx.store.get("orders", order["id"])["status"] == "paid"
    revision = ctx.store.get("orders", order["id"])["_rev"]
    assert send_event(c, event(order)).status_code == 200
    assert ctx.store.get("orders", order["id"])["_rev"] == revision
    assert ctx.store.get("payment_events", "evt_test_paid") is not None


def test_unconfigured_payments_and_incomplete_config_fail_explicitly():
    app = create_app(Settings(secret="s" * 32, enabled_plugins=()), mailer=OutboxMailer())
    staff = account(app.state.ctx, "finance", roles=("payments_finance",))
    c = TestClient(app)
    assert c.post("/api/finance/orders", headers=staff, json={
        "request_id": str(uuid4()), "uuid": "finance", "description": "Test", "amount_cents": 100,
    }).status_code == 503
    assert c.post("/api/payments/stripe/webhook", content=b"{}").status_code == 503
    with pytest.raises(RuntimeError, match="payments configure failed"):
        create_app(Settings(secret="s" * 32, enabled_plugins=(), stripe_secret_key="sk_test_fake"))
