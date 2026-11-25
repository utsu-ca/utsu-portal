import pytest
from fastapi.testclient import TestClient

from core import create_app
from core.mailer import OutboxMailer
from core.settings import Settings
from core.store import MemoryStore
from test_portal import activate, login, register

PLUGINS = ("clubs", "discussion", "voting", "club_governance")


def make_club(c, h, owner, name):
    cid = c.post("/api/clubs", json={"name": name, "description": f"We are the {name}.", "category": "games"},
                 headers=h[owner]).json()["id"]
    assert c.post(f"/api/admin/clubs/{cid}/decision", json={"approve": True}, headers=h["boss"]).status_code == 200
    return cid


def join(c, h, user, cid, owner):
    uid = c.app.state.ctx.uid(user)
    assert c.post(f"/api/clubs/{cid}/join-requests", json={}, headers=h[user]).status_code == 201
    assert c.post(f"/api/clubs/{cid}/join-requests/{uid}/decision", json={"approve": True}, headers=h[owner]).status_code == 200


@pytest.fixture
def env():
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",), enabled_plugins=PLUGINS,
                                       anchor="local"), MemoryStore(), mail))
    h = {}
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    h["boss"] = login(c, mail, "boss")
    for index, user in enumerate(("ada", "bob", "cyd", "dee")):
        register(c, mail, user, f"{user}@x.ca", sn=f"10000000{index:02d}")
        h[user] = login(c, mail, user)
        activate(c, h["boss"], h[user])
    chess, go = make_club(c, h, "ada", "Chess Club"), make_club(c, h, "dee", "Go Club")
    join(c, h, "bob", chess, "ada")
    return c, h, chess, go


def ballot_body(**extra):
    return {"title": "Pick a venue", "method": "yes_no", "visibility": "open", **extra}


def test_club_boards_are_visible_only_to_that_clubs_members(env):
    c, h, chess, go = env
    assert c.post(f"/api/clubs/{chess}/boards", json={"title": "General"}, headers=h["bob"]).status_code == 403
    board = c.post(f"/api/clubs/{chess}/boards", json={"title": "General", "description": "Chat"}, headers=h["ada"])
    assert board.status_code == 201, board.text
    bid = board.json()["id"]
    assert [b["id"] for b in c.get(f"/api/clubs/{chess}/boards", headers=h["bob"]).json()["items"]] == [bid]
    assert c.get(f"/api/clubs/{chess}/boards", headers=h["cyd"]).status_code == 403
    assert c.get(f"/api/clubs/{chess}/boards", headers=h["dee"]).status_code == 403  # owns another club only
    assert [b["id"] for b in c.get("/api/discussion/boards", headers=h["bob"]).json()["scoped_boards"]] == [bid]
    assert c.get("/api/discussion/boards", headers=h["cyd"]).json()["scoped_boards"] == []
    assert c.get(f"/api/discussion/boards/{bid}/topics", headers=h["bob"]).status_code == 200
    assert c.get(f"/api/discussion/boards/{bid}/topics", headers=h["cyd"]).status_code == 404
    assert c.get(f"/api/discussion/boards/{bid}/topics", headers=h["dee"]).status_code == 404


def test_only_that_clubs_members_vote_in_its_ballots_and_only_its_officers_run_them(env):
    c, h, chess, go = env
    created = c.post(f"/api/clubs/{chess}/ballots", json=ballot_body(), headers=h["ada"])
    assert created.status_code == 201, created.text
    bid = created.json()["id"]
    assert c.post(f"/api/clubs/{chess}/ballots", json=ballot_body(), headers=h["bob"]).status_code == 403
    assert c.post(f"/api/clubs/{chess}/ballots/{bid}/open", headers=h["dee"]).status_code == 403  # officer of another club
    assert c.post(f"/api/clubs/{go}/ballots/{bid}/open", headers=h["dee"]).status_code == 404  # not that club's ballot
    assert c.post(f"/api/clubs/{chess}/ballots/{bid}/open", headers=h["ada"]).json()["status"] == "open"
    vote = lambda user: c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["yes"]}, headers=h[user])
    assert vote("cyd").status_code == 403 and vote("dee").status_code == 403 and vote("boss").status_code == 403
    assert vote("bob").status_code == 200 and vote("bob").status_code == 409
    assert vote("ada").status_code == 200  # an officer is a member too
    assert [row["id"] for row in c.get(f"/api/clubs/{chess}/ballots", headers=h["bob"]).json()["items"]] == [bid]
    assert c.get(f"/api/clubs/{chess}/ballots", headers=h["cyd"]).status_code == 403
    assert c.post(f"/api/clubs/{chess}/ballots/{bid}/close", headers=h["ada"]).json()["status"] == "closed"


def test_a_club_can_require_utsu_membership_for_its_ballots(env):
    c, h, chess, _ = env
    ctx = c.app.state.ctx
    bid = c.post(f"/api/clubs/{chess}/ballots", json=ballot_body(), headers=h["ada"]).json()["id"]
    c.post(f"/api/clubs/{chess}/ballots/{bid}/open", headers=h["ada"])
    assert c.get(f"/api/clubs/{chess}/governance", headers=h["bob"]).json() == {"utsu_only": False}
    assert c.put(f"/api/clubs/{chess}/governance", json={"utsu_only": True}, headers=h["bob"]).status_code == 403
    assert c.put(f"/api/clubs/{chess}/governance", json={"utsu_only": True}, headers=h["ada"]).json() == {"utsu_only": True}
    vote = lambda: c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["yes"]}, headers=h["bob"])
    assert vote().status_code == 403  # a club member, but not a UTSU member
    uid = ctx.uid("bob")
    ctx.store.put("roster_checks", uid, {"uuid": uid, "rostered": True})
    assert vote().status_code == 200


def test_ballots_need_an_existing_club_scope(env):
    c, h, chess, _ = env
    body = {"title": "Pick a venue", "method": "yes_no", "visibility": "open", "electorate": "club_governance:club/nope"}
    assert c.post("/api/voting/ballots", json=body, headers=h["boss"]).status_code == 422
    assert c.post("/api/voting/ballots", json={**body, "electorate": "club_governance:club"}, headers=h["boss"]).status_code == 422
    assert c.post(f"/api/clubs/{chess}/ballots", json=ballot_body(board="general"), headers=h["ada"]).status_code == 422
