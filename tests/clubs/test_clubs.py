import base64
import sys, os
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from fastapi.testclient import TestClient

from test_portal import activate, code, login, register  # noqa: F401
from utsu_core import create_app
from utsu_core.mailer import OutboxMailer
from utsu_core.modules.clubs import split_tax
from utsu_core.settings import Settings

PDF = lambda b: "data:application/pdf;base64," + base64.b64encode(b).decode()


@pytest.fixture
def env():
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca", "boss2@x.ca")), mailer=mail))
    heads = {}
    for i, u in enumerate(("boss", "boss2")):
        register(c, mail, u, f"{u}@x.ca", sn=f"900000000{i}", first="Bo", last="Ss")
        heads[u] = login(c, mail, u)
    register(c, mail, "ada", "ada@x.ca")
    heads["ada"] = login(c, mail, "ada")
    activate(c, heads["boss"], heads["ada"])
    return c, heads


def approved_club(c, h):
    cid = c.post("/api/clubs", json={"name": "Chess Club", "description": "We play chess weekly.", "category": "games"}, headers=h["ada"]).json()["id"]
    assert c.post(f"/api/admin/clubs/{cid}/decision", json={"approve": True}, headers=h["boss"]).json()["status"] == "approved"
    return cid


def receipt(c, h, cid, data=b"%PDF-1"):
    return c.post(f"/api/clubs/{cid}/receipts", json={"data_url": PDF(data)}, headers=h["ada"])


def tx(c, h, cid, rid, amount=11300, kind="expense", **kw):
    return c.post(f"/api/clubs/{cid}/transactions", json={"kind": kind, "amount_cents": amount, "category": "supplies", "description": "boards",
                                                          "date": "2026-10-01", "receipt_ids": [rid], **kw}, headers=h["ada"])


def test_split_tax():
    assert split_tax(11300, 1300, True) == (10000, 1300, 11300)
    assert split_tax(10000, 1300, False) == (10000, 1300, 11300)
    assert split_tax(500, 0, True) == (500, 0, 500)


def test_registration_needs_one_admin_and_records_approver(env):
    c, h = env
    cid = approved_club(c, h)
    ctx = c.app.state.ctx
    approver = ctx.uid("boss")
    approvals = c.get(f"/api/clubs/{cid}", headers=h["boss"]).json()["approvals"]
    assert len(approvals) == 1 and approvals[0]["by"] == approver and approvals[0]["approve"]
    assert any(event["actor"] == approver and event["action"] == "club.decision" and event["target"] == f"club:{cid}"
               for event in ctx.audit.entries())
    assert not c.get("/api/admin/clubs?status=pending", headers=h["boss"]).json()["items"]
    assert c.post(f"/api/admin/clubs/{cid}/decision", json={"approve": True}, headers=h["boss2"]).status_code == 409
    assert c.post("/api/clubs", json={"name": "chess  club!", "description": "dup dup dup dup", "category": "games"}, headers=h["ada"]).status_code == 409
    assert c.get(f"/api/clubs/{cid}", headers=h["boss"]).status_code == 200
    assert c.get(f"/api/clubs/{cid}", headers=h["boss2"]).json()["status"] == "approved"


def test_public_directory_only_exposes_current_approved_club_fields(env, monkeypatch):
    c, h = env
    ctx = c.app.state.ctx
    before = int(datetime(2026, 8, 31, 23, 59, tzinfo=ZoneInfo("America/Toronto")).timestamp())
    after = int(datetime(2026, 9, 1, tzinfo=ZoneInfo("America/Toronto")).timestamp())
    monkeypatch.setattr(ctx, "now", lambda: before)
    cid = approved_club(c, h)
    pending = c.post("/api/clubs", json={"name": "Go Club", "description": "We play go weekly.", "category": "games"},
                     headers=h["ada"]).json()["id"]
    rejected = c.post("/api/clubs", json={"name": "Art Club", "description": "We make art weekly.", "category": "arts"},
                      headers=h["ada"]).json()["id"]
    c.post(f"/api/admin/clubs/{rejected}/decision", json={"approve": False, "note": "private"}, headers=h["boss"])

    response = c.get("/api/clubs/directory")
    assert response.status_code == 200
    assert response.json() == {"items": [{"id": cid, "name": "Chess Club", "category": "games",
                                          "description": "We play chess weekly."}], "next_cursor": None}
    assert c.get(f"/api/clubs/{cid}").status_code == 401
    monkeypatch.setattr(ctx, "now", lambda: after)
    assert c.get("/api/clubs/directory").json()["items"] == []
    assert c.post(f"/api/admin/clubs/{pending}/decision", json={"approve": True}, headers=h["boss"]).status_code == 200
    assert c.get("/api/clubs/directory").json()["items"] == [
        {"id": pending, "name": "Go Club", "category": "games", "description": "We play go weekly."}]


def test_club_collections_paginate_and_reject_invalid_statuses(env):
    c, h = env
    names = ("Zebra Club", "Alpha Club", "Middle Club")
    club_ids = {}
    for name in names:
        response = c.post("/api/clubs", json={"name": name, "description": f"We enjoy {name}.", "category": "games"},
                          headers=h["ada"])
        assert response.status_code == 201
        cid = response.json()["id"]
        assert c.post(f"/api/admin/clubs/{cid}/decision", json={"approve": True}, headers=h["boss"]).status_code == 200
        club_ids[name] = cid

    mail = c.app.state.ctx.mailer
    register(c, mail, "bob", "bob@x.ca", sn="1234567891")
    h["bob"] = login(c, mail, "bob")
    activate(c, h["boss"], h["bob"])
    for cid in club_ids.values():
        assert c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "treasurer"},
                      headers=h["ada"]).status_code == 200

    def pages(path, headers=None, limit=1):
        cursor = ""
        items = []
        while True:
            query = f"limit={limit}" + (f"&cursor={cursor}" if cursor else "")
            response = c.get(f"{path}{'&' if '?' in path else '?'}{query}", headers=headers)
            assert response.status_code == 200, response.text
            page = response.json()
            items.extend(page["items"])
            if page["next_cursor"] is None:
                return items
            cursor = page["next_cursor"]

    directory = pages("/api/clubs/directory")
    assert [club["name"] for club in directory] == ["Alpha Club", "Middle Club", "Zebra Club"]
    assert [club["id"] for club in pages("/api/clubs/mine", h["ada"], limit=2)] == sorted(club_ids.values())
    assert [club["id"] for club in pages("/api/admin/clubs?status=approved", h["boss"], limit=2)] == sorted(club_ids.values())
    invitations = pages("/api/clubs/invitations", h["bob"])
    assert [invitation["club_id"] for invitation in invitations] == sorted(club_ids.values())

    ctx = c.app.state.ctx
    for tx_id, date, created_at in (
        ("club-tx-c", "2026-10-02", 1),
        ("club-tx-b", "2026-10-01", 2),
        ("club-tx-a", "2026-10-01", 2),
    ):
        ctx.store.put("transactions", tx_id, {
            "id": tx_id, "club_id": club_ids["Alpha Club"], "date": date, "created_at": created_at,
            "status": "approved",
        })
    path = f"/api/clubs/{club_ids['Alpha Club']}/transactions"
    assert [transaction["id"] for transaction in pages(path, h["ada"])] == ["club-tx-a", "club-tx-b", "club-tx-c"]
    assert [transaction["id"] for transaction in pages("/api/admin/transactions?status=approved", h["boss"], limit=2)] == [
        "club-tx-a", "club-tx-b", "club-tx-c",
    ]

    assert c.get("/api/admin/clubs?status=unsupported", headers=h["boss"]).status_code == 422
    assert c.get("/api/admin/transactions?status=unsupported", headers=h["boss"]).status_code == 422
    assert c.get("/api/admin/clubs?status=expired", headers=h["boss"]).status_code == 200
    assert c.get("/api/clubs/directory?limit=0").status_code == 422


def test_unapproved_club_cannot_transact(env):
    c, h = env
    cid = c.post("/api/clubs", json={"name": "Go Club", "description": "We play go weekly.", "category": "games"}, headers=h["ada"]).json()["id"]
    rid = receipt(c, h, cid).json()["id"]
    assert tx(c, h, cid, rid).status_code == 409


def test_expense_flow_receipts_and_tax(env):
    c, h = env
    cid = approved_club(c, h)
    r = receipt(c, h, cid)
    assert r.status_code == 201
    assert receipt(c, h, cid).status_code == 409  # duplicate file
    assert c.post(f"/api/clubs/{cid}/receipts", json={"data_url": "data:text/html;base64,AAAA"}, headers=h["ada"]).status_code == 422
    rid = r.json()["id"]
    no = c.post(f"/api/clubs/{cid}/transactions", json={"kind": "expense", "amount_cents": 100, "category": "x1", "description": "abc",
                                                      "date": "2026-10-01"}, headers=h["ada"])
    assert no.status_code == 422  # receipt required
    t = tx(c, h, cid, rid).json()
    assert (t["net_cents"], t["tax_cents"], t["required_approvals"], t["status"]) == (10000, 1300, 1, "pending")
    assert tx(c, h, cid, rid).status_code == 422  # receipt already used
    # no funds yet
    assert c.post(f"/api/admin/transactions/{t['id']}/decision", json={"approve": True}, headers=h["boss"]).status_code == 409
    assert c.post(f"/api/admin/clubs/{cid}/grants", json={"amount_cents": 50000}, headers=h["boss"]).status_code == 201
    # the 409 above must not have recorded the approval
    assert c.post(f"/api/admin/transactions/{t['id']}/decision", json={"approve": True}, headers=h["boss"]).json()["status"] == "approved"
    s = c.get(f"/api/clubs/{cid}/summary", headers=h["ada"]).json()
    assert (s["expense_cents"], s["tax_paid_cents"], s["balance_cents"]) == (11300, 1300, 50000 - 11300)
    assert s["expenses_by_category"] == {"supplies": 11300}


def test_summary_aggregates_pending_count_in_the_approved_transaction_scan(env, monkeypatch):
    c, h = env
    cid = approved_club(c, h)
    assert c.post(f"/api/admin/clubs/{cid}/grants", json={"amount_cents": 50000}, headers=h["boss"]).status_code == 201
    approved = tx(c, h, cid, receipt(c, h, cid, b"%PDF-approved").json()["id"]).json()
    assert c.post(f"/api/admin/transactions/{approved['id']}/decision", json={"approve": True},
                  headers=h["boss"]).json()["status"] == "approved"
    assert tx(c, h, cid, receipt(c, h, cid, b"%PDF-pending").json()["id"], amount=2260).status_code == 201

    ctx = c.app.state.ctx
    original_list = ctx.store.list
    transaction_scans = []

    def track_list(collection, **equals):
        if collection == "transactions" and equals.get("club_id") == cid:
            transaction_scans.append(equals)
        return original_list(collection, **equals)

    monkeypatch.setattr(ctx.store, "list", track_list)
    summary = c.get(f"/api/clubs/{cid}/summary", headers=h["ada"]).json()

    assert summary == {
        "income_cents": 50000,
        "expense_cents": 11300,
        "balance_cents": 38700,
        "tax_paid_cents": 1300,
        "tax_collected_cents": 0,
        "net_tax_position_cents": -1300,
        "expenses_by_category": {"supplies": 11300},
        "pending_expenses": 1,
    }
    assert len(transaction_scans) == 1


def test_external_receipt_route_preserves_permissions_and_enforces_total_allocation(env, tmp_path):
    c, h = env
    ctx = c.app.state.ctx
    ctx.settings.club_storage_year_bytes = 12
    ctx.services["receipt_storage"].root = tmp_path
    cid = approved_club(c, h)
    first = receipt(c, h, cid, b"%PDF-1").json()["id"]
    assert receipt(c, h, cid, b"%PDF-2").status_code == 201
    assert receipt(c, h, cid, b"x").status_code == 413
    assert c.get(f"/api/clubs/{cid}", headers=h["ada"]).json()["storage_usage"]["used_bytes"] == 12
    record = ctx.store.get("receipts", first)
    assert "data_url" not in record
    before = record["last_accessed_at"]
    assert c.get(f"/api/clubs/{cid}/receipts/{first}").status_code == 401
    assert ctx.store.get("receipts", first)["last_accessed_at"] == before
    response = c.get(f"/api/clubs/{cid}/receipts/{first}", headers=h["ada"])
    assert response.json()["data_url"] == PDF(b"%PDF-1") and "storage" not in response.json()
    assert tx(c, h, cid, first).status_code == 201
    assert len(list(tmp_path.iterdir())) == 2


def test_large_expense_needs_two_and_rejection_is_final(env):
    c, h = env
    cid = approved_club(c, h)
    c.post(f"/api/admin/clubs/{cid}/grants", json={"amount_cents": 500000}, headers=h["boss"])
    t = tx(c, h, cid, receipt(c, h, cid).json()["id"], amount=60000).json()
    assert t["required_approvals"] == 2
    assert c.post(f"/api/admin/transactions/{t['id']}/decision", json={"approve": True}, headers=h["boss"]).json()["status"] == "pending"
    assert c.post(f"/api/admin/transactions/{t['id']}/decision", json={"approve": False, "note": "no"}, headers=h["boss2"]).json()["status"] == "rejected"
    assert c.get(f"/api/clubs/{cid}/summary", headers=h["ada"]).json()["expense_cents"] == 0


def test_permissions(env):
    c, h = env
    cid = approved_club(c, h)
    assert c.post(f"/api/clubs/{cid}/members", json={"username": "nobody", "role": "treasurer"}, headers=h["ada"]).status_code == 422
    assert c.post("/api/admin/clubs/x/decision", json={"approve": True}, headers=h["ada"]).status_code == 403
    assert c.get(f"/api/clubs/{cid}/transactions", headers=h["boss"]).status_code == 200


def test_member_invitation_requires_recipient_acceptance(env):
    c, h = env
    mail = c.app.state.ctx.mailer
    register(c, mail, "bob", "bob@x.ca", sn="1234567891")
    h["bob"] = login(c, mail, "bob")
    activate(c, h["boss"], h["bob"])
    cid = approved_club(c, h)

    r = c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "treasurer"}, headers=h["ada"])
    assert r.json() == {"status": "pending"}
    assert mail.outbox[-1][0] == "bob@x.ca"
    assert "Chess Club" in mail.outbox[-1][2] and "treasurer" in mail.outbox[-1][2]
    invitations = c.get("/api/clubs/invitations", headers=h["bob"]).json()["items"]
    assert len(invitations) == 1 and invitations[0]["club_id"] == cid
    invite_id = invitations[0]["id"]
    assert c.get(f"/api/clubs/{cid}", headers=h["bob"]).status_code == 403
    assert c.post(f"/api/clubs/{cid}/receipts", json={"data_url": PDF(b"pending")}, headers=h["bob"]).status_code == 403
    assert c.get("/api/clubs/mine", headers=h["bob"]).json()["items"] == []
    assert c.get("/api/clubs/invitations", headers=h["ada"]).json()["items"] == []
    assert c.post(f"/api/clubs/{cid}/invitations/{invite_id}/decision", json={"approve": True}, headers=h["boss"]).status_code == 404
    assert c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "executive"}, headers=h["ada"]).status_code == 409
    assert c.post(f"/api/clubs/{cid}/invitations/{invite_id}/decision", json={"approve": True}, headers=h["bob"]).json()["status"] == "accepted"
    assert c.get(f"/api/clubs/{cid}", headers=h["bob"]).status_code == 200
    assert c.post(f"/api/clubs/{cid}/receipts", json={"data_url": PDF(b"accepted")}, headers=h["bob"]).status_code == 201
    assert c.get("/api/clubs/mine", headers=h["bob"]).json()["items"][0]["members"][c.app.state.ctx.uid("bob")] == "treasurer"
    assert c.post(f"/api/clubs/{cid}/invitations/{invite_id}/decision", json={"approve": True}, headers=h["bob"]).status_code == 404
    assert c.get("/api/clubs/invitations", headers=h["bob"]).json()["items"] == []

    assert c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "executive"}, headers=h["ada"]).status_code == 200
    next_id = c.get("/api/clubs/invitations", headers=h["bob"]).json()["items"][0]["id"]
    assert next_id != invite_id
    assert c.post(f"/api/clubs/{cid}/invitations/{invite_id}/decision", json={"approve": True}, headers=h["bob"]).status_code == 404
    assert c.post(f"/api/clubs/{cid}/invitations/{next_id}/decision", json={"approve": False}, headers=h["bob"]).json()["status"] == "declined"
    assert c.get("/api/clubs/mine", headers=h["bob"]).json()["items"][0]["members"][c.app.state.ctx.uid("bob")] == "treasurer"
    assert c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "executive"}, headers=h["ada"]).status_code == 200


def test_invitation_reports_unconfirmed_delivery_and_remains_actionable(env, monkeypatch):
    c, h = env
    mail = c.app.state.ctx.mailer
    register(c, mail, "bob", "bob@x.ca", sn="1234567891")
    h["bob"] = login(c, mail, "bob")
    activate(c, h["boss"], h["bob"])
    cid = approved_club(c, h)

    def fail(*args):
        raise OSError("mail unavailable")

    monkeypatch.setattr(mail, "send", fail)
    r = c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "executive"}, headers=h["ada"])
    assert r.status_code == 502 and "Invitation saved" in r.json()["detail"]
    invitation = c.get("/api/clubs/invitations", headers=h["bob"]).json()["items"][0]
    assert c.post(f"/api/clubs/{cid}/invitations/{invitation['id']}/decision",
                  json={"approve": False}, headers=h["bob"]).json()["status"] == "declined"


def test_approval_expires_at_academic_year_boundary_and_owner_requests_renewal(env, monkeypatch):
    c, h = env
    ctx = c.app.state.ctx
    toronto = ZoneInfo("America/Toronto")
    before = int(datetime(2026, 8, 31, 23, 59, tzinfo=toronto).timestamp())
    after = int(datetime(2026, 9, 1, 0, 0, tzinfo=toronto).timestamp())
    monkeypatch.setattr(ctx, "now", lambda: before)
    cid = approved_club(c, h)
    assert c.get(f"/api/clubs/{cid}", headers=h["ada"]).json()["approval_year"] == 2025
    assert c.get(f"/api/clubs/{cid}", headers=h["ada"]).json()["storage_usage"]["limit_bytes"] == 1024 ** 3
    assert not c.get("/api/admin/clubs?status=pending", headers=h["boss"]).json()["items"]
    assert c.post(f"/api/clubs/{cid}/renewal", headers=h["ada"]).status_code == 409

    monkeypatch.setattr(ctx, "now", lambda: after)
    assert c.get("/api/clubs/mine", headers=h["ada"]).json()["items"][0]["status"] == "expired"
    assert c.get("/api/admin/clubs?status=approved", headers=h["boss"]).json()["items"] == []
    assert c.get("/api/admin/clubs?status=pending", headers=h["boss"]).json()["items"] == []
    assert c.post(f"/api/admin/clubs/{cid}/grants", json={"amount_cents": 100}, headers=h["boss"]).status_code == 409
    assert tx(c, h, cid, receipt(c, h, cid).json()["id"]).status_code == 409
    assert c.post(f"/api/clubs/{cid}/renewal", headers=h["boss"]).status_code == 403
    assert c.post(f"/api/clubs/{cid}/renewal", headers=h["ada"]).json()["status"] == "pending"
    assert c.post(f"/api/clubs/{cid}/renewal", headers=h["ada"]).status_code == 409
    assert len(c.get("/api/admin/clubs?status=pending", headers=h["boss"]).json()["items"]) == 1
    final = c.post(f"/api/admin/clubs/{cid}/decision", json={"approve": True}, headers=h["boss"]).json()
    assert final["status"] == "approved" and final["approval_year"] == 2026
    club = c.get(f"/api/clubs/{cid}", headers=h["ada"]).json()
    assert club["registered_years"] == [2025, 2026]
    assert club["storage_usage"]["limit_bytes"] == 2 * 1024 ** 3
    assert c.get("/api/admin/clubs?status=pending", headers=h["boss"]).json()["items"] == []


def test_existing_approved_club_uses_final_vote_date(env, monkeypatch):
    c, h = env
    ctx = c.app.state.ctx
    monkeypatch.setattr(ctx, "now", lambda: int(datetime(2026, 8, 1, tzinfo=ZoneInfo("America/Toronto")).timestamp()))
    cid = approved_club(c, h)
    def legacy(current):
        current.pop("approval_year")
        return current
    ctx.store.update("clubs", cid, legacy)
    assert c.get(f"/api/clubs/{cid}", headers=h["ada"]).json()["approval_year"] == 2025
    monkeypatch.setattr(ctx, "now", lambda: int(datetime(2026, 9, 1, tzinfo=ZoneInfo("America/Toronto")).timestamp()))
    assert c.get(f"/api/clubs/{cid}", headers=h["ada"]).json()["status"] == "expired"
    assert c.post(f"/api/clubs/{cid}/renewal", headers=h["ada"]).status_code == 200


def test_previously_recorded_vote_can_finalize_pending_club(env):
    c, h = env
    ctx = c.app.state.ctx
    cid = c.post("/api/clubs", json={"name": "Go Club", "description": "We play go weekly.", "category": "games"}, headers=h["ada"]).json()["id"]
    approver = ctx.uid("boss")
    def previous_vote(current):
        current["approvals"].append({"by": approver, "approve": True, "note": "prior review", "at": ctx.now()})
        return current
    ctx.store.update("clubs", cid, previous_vote)
    assert c.post(f"/api/admin/clubs/{cid}/decision", json={"approve": True}, headers=h["boss"]).json()["status"] == "approved"
    votes = c.get(f"/api/clubs/{cid}", headers=h["boss"]).json()["approvals"]
    assert len(votes) == 1 and votes[0]["by"] == approver and votes[0]["note"] == "prior review"
