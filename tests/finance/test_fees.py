from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from core import create_app
from core.mailer import OutboxMailer
from core.services.fees import TORONTO
from core.settings import Settings
from test_financial_profiles import BANK, account


def at(year, month, day):
    return int(datetime(year, month, day, 12, tzinfo=TORONTO).timestamp())


@pytest.fixture
def env():
    app = create_app(Settings(secret="s" * 32, enabled_plugins=()), mailer=OutboxMailer())
    ctx = app.state.ctx
    ctx.now = lambda: at(2027, 9, 10)
    people = {}
    for uid, roles in (("admin", ("fees_admin",)), ("finance", ("fees_finance", "profile_finance")),
                       ("levy", ("levy_group",)), ("other-levy", ("levy_group",)), ("ann", ()), ("bob", ()),
                       ("cat", ())):
        people[uid] = account(ctx, uid, roles=roles)
        ctx.store.put("users", uid, {"uuid": uid, "email": f"{uid}@example.test", "first_name": uid.title(),
                                     "last_name": "Test"})
    for uid in ("ann", "bob", "cat"):
        ctx.store.put("roster_checks", uid, {"rostered": True})
    ctx.store.put("roster_meta", "current", {"count": 1000})
    return TestClient(app), ctx, people


def setup_fees(c, people):
    group = c.post("/api/admin/fees/groups", headers=people["admin"],
                   json={"name": "Campus Radio", "members": ["levy"]}).json()
    fee = c.post("/api/admin/fees", headers=people["admin"], json={
        "name": "Radio levy", "amounts_cents": {"fall": 1000, "summer": 500}, "levy_group_id": group["id"],
    }).json()
    fixed = c.post("/api/admin/fees", headers=people["admin"], json={
        "name": "Yearbook", "amounts_cents": {"fall": 2500}, "refundable": False,
    }).json()
    return group, fee, fixed


def test_admin_manages_fees_windows_and_levy_groups(env):
    c, ctx, people = env
    assert c.get("/api/admin/fees", headers=people["ann"]).status_code == 403
    group, fee, fixed = setup_fees(c, people)
    assert group["members"] == [{"uuid": "levy", "name": "Levy Test"}]
    overview = c.get("/api/admin/fees", headers=people["admin"]).json()
    assert [f["name"] for f in overview["fees"]] == ["Radio levy", "Yearbook"]
    fall, summer = overview["windows"]
    assert (fall["start_month"], fall["length_months"], fall["open"]) == (9, 1, True)
    assert (summer["start_month"], summer["open"], summer["session"]) == (5, False, "summer:2028")
    assert overview["sessions"][0]["roster_count"] == 1000

    assert c.put("/api/admin/fees/windows/fall", headers=people["admin"],
                 json={"start_month": 4, "length_months": 2}).status_code == 422  # overlaps May
    assert c.put("/api/admin/fees/windows/fall", headers=people["admin"],
                 json={"start_month": 12, "length_months": 2}).status_code == 422
    moved = c.put("/api/admin/fees/windows/fall", headers=people["admin"], json={"start_month": 10, "length_months": 1})
    assert moved.json()["open"] is False
    assert c.post("/api/admin/fees", headers=people["admin"],
                  json={"name": "x", "amounts_cents": {"fall": 0}}).status_code == 422
    assert c.post("/api/admin/fees", headers=people["admin"],
                  json={"name": "x", "amounts_cents": {"fall": 100}, "levy_group_id": "missing"}).status_code == 404
    edited = c.put(f"/api/admin/fees/{fee['id']}", headers=people["admin"], json={
        "name": "Radio levy", "amounts_cents": {"fall": 1200}, "levy_group_id": group["id"]})
    assert edited.json()["amounts_cents"] == {"fall": 1200} and "_rev" not in edited.json()
    assert c.delete(f"/api/admin/fees/groups/{group['id']}", headers=people["admin"]).status_code == 409
    assert c.delete(f"/api/admin/fees/{fixed['id']}", headers=people["admin"]).status_code == 200
    assert any(e["action"] == "fees.fee_changed" for e in ctx.audit.entries())


def test_members_opt_out_only_while_the_window_is_open(env):
    c, ctx, people = env
    _, fee, fixed = setup_fees(c, people)
    offer = c.get("/api/fees/optout", headers=people["ann"]).json()
    fall = next(w for w in offer["windows"] if w["id"] == "fall")
    assert fall["open"] and [f["name"] for f in fall["fees"]] == ["Radio levy", "Yearbook"]
    assert next(w for w in offer["windows"] if w["id"] == "summer")["fees"] == []
    assert c.get("/api/fees/optout", headers=account(ctx, "stranger")).status_code == 403

    body = {"window": "fall", "items": [{"fee_id": fee["id"], "already_paid": False}]}
    done = c.post("/api/fees/optout", headers=people["ann"], json=body)
    assert done.status_code == 201
    request = done.json()["requests"][0]
    assert (request["status"], request["amount_cents"], request["session_label"]) == (
        "opted_out", 1000, "Fall-Winter Session 2027-28")
    assert c.post("/api/fees/optout", headers=people["ann"], json=body).status_code == 409
    assert c.post("/api/fees/optout", headers=people["ann"], json={
        "window": "summer", "items": [{"fee_id": fee["id"], "already_paid": False}]}).status_code == 409
    assert c.post("/api/fees/optout", headers=people["ann"], json={
        "window": "fall", "items": [{"fee_id": fixed["id"], "already_paid": True}]}).status_code == 422

    assert c.delete(f"/api/fees/optout/{request['id']}", headers=people["bob"]).status_code == 404
    assert c.delete(f"/api/fees/optout/{request['id']}", headers=people["ann"]).json()["status"] == "withdrawn"
    assert c.post("/api/fees/optout", headers=people["ann"], json=body).status_code == 201  # may opt out again

    ctx.now = lambda: at(2027, 10, 1)
    assert c.post("/api/fees/optout", headers=people["bob"], json=body).status_code == 409
    assert c.delete(f"/api/fees/optout/{request['id']}", headers=people["ann"]).status_code == 409


def test_summer_window_follows_its_own_session_and_adjustable_months(env):
    c, ctx, people = env
    _, fee, _ = setup_fees(c, people)
    ctx.now = lambda: at(2028, 5, 20)
    summer = next(w for w in c.get("/api/fees/optout", headers=people["ann"]).json()["windows"] if w["id"] == "summer")
    assert summer["open"] and summer["session_label"] == "Summer Session 2028"
    assert summer["fees"][0]["amount_cents"] == 500
    c.put("/api/admin/fees/windows/summer", headers=people["admin"], json={"start_month": 6, "length_months": 1})
    assert c.post("/api/fees/optout", headers=people["ann"], json={
        "window": "summer", "items": [{"fee_id": fee["id"], "already_paid": False}]}).status_code == 409


def test_refund_requests_use_core_banking_and_finance_decides(env):
    c, ctx, people = env
    _, fee, _ = setup_fees(c, people)
    body = {"window": "fall", "items": [{"fee_id": fee["id"], "already_paid": True}]}
    assert c.post("/api/fees/optout", headers=people["ann"], json=body).status_code == 409  # no banking yet
    assert c.put("/api/profile/financial", headers=people["ann"], json=BANK).status_code == 200
    assert c.get("/api/fees/optout", headers=people["ann"]).json()["banking_status"] == "on_file"
    request = c.post("/api/fees/optout", headers=people["ann"], json=body).json()["requests"][0]
    assert request["status"] == "refund_requested"

    assert c.get("/api/finance/fees/refunds", headers=people["ann"]).status_code == 403
    queue = c.get("/api/finance/fees/refunds", headers=people["finance"]).json()["items"]
    assert [(r["name"], r["banking_status"], r["amount_cents"]) for r in queue] == [("Ann Test", "on_file", 1000)]
    rid = request["id"]
    assert c.get(f"/api/finance/fees/refunds/{rid}/banking", headers=people["ann"]).status_code == 403
    assert c.get(f"/api/finance/fees/refunds/{rid}/banking", headers=people["finance"]).json() == BANK
    assert any(e["action"] == "fees.banking_accessed" for e in ctx.audit.entries())

    decision = f"/api/finance/fees/refunds/{rid}/decision"
    assert c.post(decision, headers=people["finance"], json={"decision": "denied"}).status_code == 422
    paid = c.post(decision, headers=people["finance"], json={"decision": "refunded", "note": "e-transfer"})
    assert paid.json()["status"] == "refunded"
    assert c.post(decision, headers=people["finance"], json={"decision": "refunded"}).status_code == 409
    assert c.delete(f"/api/fees/optout/{rid}", headers=people["ann"]).status_code == 409
    assert c.get("/api/finance/fees/refunds?status=refunded", headers=people["finance"]).json()["items"][0]["id"] == rid
    assert c.get("/api/finance/fees/refunds", headers=people["finance"]).json()["items"] == []


def test_levy_groups_see_their_own_opt_outs_and_predicted_payments(env):
    c, ctx, people = env
    group, fee, fixed = setup_fees(c, people)
    c.put("/api/admin/fees/sessions/fall:2027", headers=people["admin"], json={"expected_enrolment": 200})
    c.put("/api/profile/financial", headers=people["bob"], json=BANK)
    c.post("/api/fees/optout", headers=people["ann"], json={
        "window": "fall", "items": [{"fee_id": fee["id"], "already_paid": False},
                                    {"fee_id": fixed["id"], "already_paid": False}]})
    c.post("/api/fees/optout", headers=people["bob"], json={
        "window": "fall", "items": [{"fee_id": fee["id"], "already_paid": True}]})
    c.post("/api/fees/optout", headers=people["cat"], json={
        "window": "fall", "items": [{"fee_id": fee["id"], "already_paid": False}]})
    c.delete(f"/api/fees/optout/fall:2027:{fee['id']}:cat", headers=people["cat"])

    assert c.get("/api/levy-groups", headers=people["ann"]).status_code == 403
    assert c.get("/api/levy-groups", headers=people["other-levy"]).json()["items"] == []
    assert c.get(f"/api/levy-groups/{group['id']}", headers=people["other-levy"]).status_code == 404
    assert [g["name"] for g in c.get("/api/levy-groups", headers=people["levy"]).json()["items"]] == ["Campus Radio"]

    report = c.get(f"/api/levy-groups/{group['id']}", headers=people["levy"]).json()
    assert report["session"] == "fall:2027" and report["enrolment"] == {"value": 200, "source": "admin"}
    assert [s["session"] for s in report["sessions"]] == ["summer:2028", "fall:2027"]
    [row] = report["fees"]  # the yearbook belongs to no group
    assert (row["gross_cents"], row["opted_out"], row["refund_requested"], row["opt_out_count"]) == (200_000, 1, 1, 2)
    assert (row["confirmed_net_cents"], row["predicted_net_cents"]) == (199_000, 198_000)
    assert report["totals"]["predicted_net_cents"] == 198_000

    people_out = c.get(f"/api/levy-groups/{group['id']}/optouts?session=fall:2027", headers=people["levy"]).json()
    assert sorted((r["name"], r["status"]) for r in people_out["items"]) == [
        ("Ann Test", "opted_out"), ("Bob Test", "refund_requested")]
    assert c.get(f"/api/levy-groups/{group['id']}?session=fall:1999", headers=people["levy"]).status_code == 404

    c.put("/api/admin/fees/sessions/fall:2027", headers=people["admin"], json={"expected_enrolment": None})
    fallback = c.get(f"/api/levy-groups/{group['id']}", headers=people["admin"]).json()
    assert fallback["enrolment"] == {"value": 1000, "source": "roster"}  # admins see every group
