from unittest.mock import patch

import pytest

from core.permissions import (
    PERM_CLUB_BALLOTS_MANAGE, PERM_CLUB_DISCUSS, PERM_CLUB_MEMBERS_MANAGE, PERM_CLUB_VOTE,
)
from test_clubs import approved_club, env  # noqa: F401
from test_portal import activate, login, register


def add_bob(c, h, name="bob", sn="1234567891"):
    mail = c.app.state.ctx.mailer
    register(c, mail, name, f"{name}@x.ca", sn=sn)
    h[name] = login(c, mail, name)
    activate(c, h["boss"], h[name])
    ctx = c.app.state.ctx
    return ctx.store.get("accounts", ctx.uid(name))


def test_join_request_makes_an_ordinary_member_without_officer_access(env):
    c, h = env
    cid = approved_club(c, h)
    bob = add_bob(c, h)
    r = c.post(f"/api/clubs/{cid}/join-requests", json={"message": "hi"}, headers=h["bob"]); assert r.status_code == 201, r.text
    assert c.post(f"/api/clubs/{cid}/join-requests", json={}, headers=h["bob"]).status_code == 409
    assert c.get(f"/api/clubs/{cid}/join-requests", headers=h["bob"]).status_code == 403  # only officers see requests
    pending = c.get(f"/api/clubs/{cid}/join-requests", headers=h["ada"]).json()["items"]
    assert [item["uuid"] for item in pending] == [bob["uuid"]] and pending[0]["name"] == "Ada L."
    assert c.post(f"/api/clubs/{cid}/join-requests/{bob['uuid']}/decision", json={"approve": True},
                  headers=h["ada"]).json()["status"] == "approved"
    assert c.get(f"/api/clubs/{cid}/join-requests", headers=h["ada"]).json()["items"] == []
    assert [club["id"] for club in c.get("/api/clubs/joined", headers=h["bob"]).json()["items"]] == [cid]
    # a member is not an officer: no ledger, receipts or member list
    assert c.get(f"/api/clubs/{cid}", headers=h["bob"]).status_code == 403
    assert c.get("/api/clubs/mine", headers=h["bob"]).json()["items"] == []
    assert c.get(f"/api/clubs/{cid}/members", headers=h["bob"]).status_code == 403
    members = c.get(f"/api/clubs/{cid}/members", headers=h["ada"]).json()
    assert {(m["role"], m["source"]) for m in members["items"]} == {("owner", "officer"), ("member", "request")}
    assert c.post(f"/api/clubs/{cid}/join-requests", json={}, headers=h["bob"]).status_code == 409  # already a member


def test_roles_are_scoped_to_one_club(env):
    c, h = env
    ctx = c.app.state.ctx
    chess = approved_club(c, h)
    bob = add_bob(c, h)
    go = c.post("/api/clubs", json={"name": "Go Club", "description": "We play go weekly.", "category": "games"},
                headers=h["bob"]).json()["id"]
    assert c.post(f"/api/admin/clubs/{go}/decision", json={"approve": True}, headers=h["boss"]).status_code == 200
    ada = ctx.store.get("accounts", ctx.uid("ada"))
    can = lambda account, permission, scope: ctx.has_permission(account, permission, scope=scope)
    assert can(ada, PERM_CLUB_MEMBERS_MANAGE, chess) and can(ada, PERM_CLUB_BALLOTS_MANAGE, chess)
    assert not can(ada, PERM_CLUB_MEMBERS_MANAGE, go) and not can(ada, PERM_CLUB_VOTE, go)  # owner of chess only
    assert can(bob, PERM_CLUB_MEMBERS_MANAGE, go) and not can(bob, PERM_CLUB_MEMBERS_MANAGE, chess)
    assert c.get(f"/api/clubs/{go}/join-requests", headers=h["ada"]).status_code == 403
    with pytest.raises(ValueError, match="need a scope"):
        ctx.has_permission(ada, PERM_CLUB_VOTE)
    boss = ctx.store.get("accounts", ctx.uid("boss"))
    assert not can(boss, PERM_CLUB_VOTE, chess) and not can(boss, PERM_CLUB_MEMBERS_MANAGE, chess)  # admin is not a member
    assert not can(ada, PERM_CLUB_MEMBERS_MANAGE, "no-such-club")


def test_club_permissions_follow_membership_and_the_clubs_approval(env):
    c, h = env
    ctx = c.app.state.ctx
    cid = approved_club(c, h)
    bob = add_bob(c, h)
    assert not ctx.has_permission(bob, PERM_CLUB_VOTE, scope=cid)
    c.post(f"/api/clubs/{cid}/join-requests", json={}, headers=h["bob"])
    c.post(f"/api/clubs/{cid}/join-requests/{bob['uuid']}/decision", json={"approve": True}, headers=h["ada"])
    assert ctx.has_permission(bob, PERM_CLUB_VOTE, PERM_CLUB_DISCUSS, scope=cid)
    assert not ctx.has_permission(bob, PERM_CLUB_BALLOTS_MANAGE, scope=cid)
    with patch.object(ctx, "now", return_value=ctx.now() + 400 * 24 * 3600):  # the club's approval has lapsed
        assert not ctx.has_permission(bob, PERM_CLUB_VOTE, scope=cid)
        ada = ctx.store.get("accounts", ctx.uid("ada"))
        assert ctx.has_permission(ada, PERM_CLUB_MEMBERS_MANAGE, scope=cid)  # owners can still run an expired club
        assert not ctx.has_permission(ada, PERM_CLUB_BALLOTS_MANAGE, scope=cid)
    assert c.delete(f"/api/clubs/{cid}/members/{bob['uuid']}", headers=h["ada"]).json()["status"] == "removed"
    assert not ctx.has_permission(bob, PERM_CLUB_VOTE, scope=cid)
    assert c.delete(f"/api/clubs/{cid}/members/{bob['uuid']}", headers=h["ada"]).status_code == 404


def test_member_can_leave_and_officers_cannot(env):
    c, h = env
    cid = approved_club(c, h)
    bob = add_bob(c, h)
    c.post(f"/api/clubs/{cid}/join-requests", json={}, headers=h["bob"])
    assert c.delete(f"/api/clubs/{cid}/join-requests/mine", headers=h["bob"]).status_code == 200
    c.post(f"/api/clubs/{cid}/join-requests", json={}, headers=h["bob"])
    c.post(f"/api/clubs/{cid}/join-requests/{bob['uuid']}/decision", json={"approve": False}, headers=h["ada"])
    assert c.get("/api/clubs/joined", headers=h["bob"]).json()["items"] == []
    c.post(f"/api/clubs/{cid}/join-requests", json={}, headers=h["bob"])
    c.post(f"/api/clubs/{cid}/join-requests/{bob['uuid']}/decision", json={"approve": True}, headers=h["ada"])
    assert c.post(f"/api/clubs/{cid}/leave", headers=h["bob"]).json()["status"] == "left"
    assert c.post(f"/api/clubs/{cid}/leave", headers=h["bob"]).status_code == 404
    assert c.post(f"/api/clubs/{cid}/leave", headers=h["ada"]).status_code == 409


def test_officers_invite_ordinary_members_but_only_the_owner_invites_officers(env):
    c, h = env
    cid = approved_club(c, h)
    bob = add_bob(c, h)
    carol = add_bob(c, h, "carol", "1234567892")
    assert c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "member"}, headers=h["carol"]).status_code == 403
    assert c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "member"}, headers=h["ada"]).json() == {"status": "pending"}
    invitation = c.get("/api/clubs/invitations", headers=h["bob"]).json()["items"][0]
    assert invitation["role"] == "member"
    assert c.post(f"/api/clubs/{cid}/invitations/{invitation['id']}/decision", json={"approve": True},
                  headers=h["bob"]).json()["status"] == "accepted"
    assert c.get(f"/api/clubs/{cid}", headers=h["bob"]).status_code == 403  # still not an officer
    assert [club["id"] for club in c.get("/api/clubs/joined", headers=h["bob"]).json()["items"]] == [cid]
    assert c.post(f"/api/clubs/{cid}/members", json={"username": "bob", "role": "member"}, headers=h["ada"]).status_code == 409
    assert c.post(f"/api/clubs/{cid}/members", json={"username": "carol", "role": "treasurer"}, headers=h["bob"]).status_code == 403
    assert carol["status"] == "verified"


def test_officers_find_people_by_email_or_student_number_and_grant_honorary_membership(env):
    c, h = env
    ctx = c.app.state.ctx
    cid = approved_club(c, h)
    bob = add_bob(c, h)
    assert c.get(f"/api/clubs/{cid}/people/lookup?email=bob@x.ca", headers=h["bob"]).status_code == 403
    assert c.get(f"/api/clubs/{cid}/people/lookup", headers=h["ada"]).status_code == 422
    assert c.get(f"/api/clubs/{cid}/people/lookup?email=nobody@x.ca", headers=h["ada"]).status_code == 404
    found = c.get(f"/api/clubs/{cid}/people/lookup?email=BOB@x.ca", headers=h["ada"]).json()
    assert found == {"uuid": bob["uuid"], "name": "Ada L.", "verified": True, "relation": "none"}  # no email or number
    assert c.get(f"/api/clubs/{cid}/people/lookup?student_number=1234567891", headers=h["ada"]).json()["uuid"] == bob["uuid"]

    granted = c.put(f"/api/clubs/{cid}/members/{bob['uuid']}/honorary", json={"reason": "Founding member"}, headers=h["ada"])
    assert granted.status_code == 200 and granted.json()["session"]
    assert c.put(f"/api/clubs/{cid}/members/{bob['uuid']}/honorary", json={"reason": "x"}, headers=h["ada"]).status_code == 422
    assert c.get(f"/api/clubs/{cid}/people/lookup?email=bob@x.ca", headers=h["ada"]).json()["relation"] == "honorary"
    assert ctx.has_permission(bob, PERM_CLUB_VOTE, scope=cid)
    with patch.object(ctx, "now", return_value=ctx.now() + 150 * 24 * 3600):  # honorary lasts the session only
        assert not ctx.has_permission(bob, PERM_CLUB_VOTE, scope=cid)
        assert c.get("/api/clubs/joined", headers=h["bob"]).json()["items"] == []
    ada = ctx.store.get("accounts", ctx.uid("ada"))
    assert c.put(f"/api/clubs/{cid}/members/{ada['uuid']}/honorary", json={"reason": "owner"}, headers=h["ada"]).status_code == 409
    actions = {e["action"] for e in ctx.audit.entries()}
    assert {"club.people_lookup", "club.honorary_granted"} <= actions
