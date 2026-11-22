import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_portal import activate, login, register  # noqa: E402

from utsu_core import create_app, plugin_host
from utsu_core.contracts import PluginManifest
from utsu_core.mailer import OutboxMailer
from utsu_core.programs.voting import plugin as voting
from utsu_core.programs.voting.contracts import BallotSpec, Electorate
from utsu_core.programs.voting.record import leaf_hash, merkle_root, record_digest
from utsu_core.programs.voting.tally import tally
from utsu_core.settings import Settings
from utsu_core.store import MemoryStore


# Counting


def test_yes_no_thresholds():
    yes, no, abstain = ["yes"], ["no"], ["abstain"]
    assert tally("yes_no", [], 1, "majority", 0, [yes, yes, no, no])["outcome"] == "defeated"
    assert tally("yes_no", [], 1, "majority", 0, [yes, no, abstain, abstain, yes])["outcome"] == "carried"
    assert tally("yes_no", [], 1, "two_thirds", 0, [yes, yes, no, abstain])["outcome"] == "carried"
    assert tally("yes_no", [], 1, "two_thirds", 0, [yes, yes, yes, no, no])["outcome"] == "defeated"
    assert tally("yes_no", [], 1, "two_thirds", 0, [abstain])["outcome"] == "defeated"


def test_plurality_and_approval_report_ties_at_the_seat_boundary():
    single = tally("single", ["x", "y"], 1, "majority", 0, [["x"], ["y"]])
    assert (single["elected"], single["tied"], single["outcome"]) == ([], ["x", "y"], "tie")
    approval = tally("approval", ["a", "b", "c"], 2, "majority", 0, [["a", "b"], ["a", "c"], ["b"]])
    assert (approval["elected"], approval["outcome"]) == (["a", "b"], "decided")
    tied = tally("approval", ["a", "b", "c"], 2, "majority", 0, [["a"], ["a"], ["b"], ["c"]])
    assert (tied["elected"], tied["tied"], tied["outcome"]) == (["a"], ["b", "c"], "tie")


def test_instant_runoff_transfers_from_the_excluded_candidate():
    result = tally("ranked", ["a", "b", "c"], 1, "majority", 0, [["a"]] * 4 + [["b", "c"]] * 3 + [["c", "b"]] * 2)
    assert result["elected"] == ["b"] and result["outcome"] == "decided"
    assert result["rounds"][0]["excluded"] == "c" and result["rounds"][1]["elected"] == ["b"]


def test_stv_transfers_surplus_at_exact_fractional_value():
    ballots = [["a", "b"]] * 5 + [["a", "c"]] * 2 + [["d"]] * 3
    result = tally("stv", ["a", "b", "c", "d"], 2, "majority", 0, ballots)
    assert result["quota"] == "4"
    assert result["rounds"][1]["tally"] == {"b": "15/7", "c": "6/7", "d": "3"}
    assert result["rounds"][1]["excluded"] == "c" and result["rounds"][2]["excluded"] == "b"
    assert result["elected"] == ["a", "d"] and result["rounds"][2]["exhausted"] == "6/7"


def test_stv_breaks_ties_backwards_and_reports_unbreakable_ties():
    backwards = tally("stv", ["a", "b", "c", "d"], 2, "majority", 0, [["a", "b"]] * 8 + [["c"]] * 3 + [["d"]])
    assert backwards["rounds"][2]["excluded"] == "b" and backwards["elected"] == ["a", "c"]
    stuck = tally("stv", ["a", "b", "c", "d"], 2, "majority", 0, [["a", "b"]] * 9 + [["c"]] * 2 + [["d"]] * 2)
    assert (stuck["outcome"], stuck["tied"], stuck["elected"]) == ("tie", ["c", "d"], ["a"])


def test_quorum_and_empty_ballots():
    assert tally("single", ["x", "y"], 1, "majority", 3, [["x"], ["x"]])["outcome"] == "no_quorum"
    assert tally("single", ["x", "y"], 1, "majority", 2, [["x"], ["x"]])["outcome"] == "decided"
    empty = tally("ranked", ["x", "y"], 1, "majority", 0, [])
    assert empty["outcome"] == "no_votes" and empty["ballots"] == 0


def test_record_root_ignores_insertion_order_and_binds_every_field():
    leaves = [leaf_hash("b1", f"v{i}", ["o1", "o2"], f"salt{i}") for i in range(5)]
    assert merkle_root(leaves) == merkle_root(list(reversed(leaves))) != merkle_root(leaves[:4])
    assert leaf_hash("b1", "v0", ["o1", "o2"], "salt0") != leaf_hash("b1", "v0", ["o2", "o1"], "salt0")
    assert record_digest({"a": 1, "b": [1, 2]}) == record_digest({"b": [1, 2], "a": 1})


# Ballots end to end


def _app(store: MemoryStore, mail: OutboxMailer, plugins: tuple[str, ...] = ("discussion", "voting"), **settings):
    return create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",), enabled_plugins=plugins, anchor="local",
                               **settings), store, mail)


def _people(c: TestClient, mail: OutboxMailer, ctx) -> dict[str, dict[str, str]]:
    heads = {}
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    heads["boss"] = login(c, mail, "boss")
    for index, user in enumerate(("ada", "bob", "cyd")):
        register(c, mail, user, f"{user}@x.ca", sn=f"10000000{index:02d}")
        heads[user] = login(c, mail, user)
        activate(c, heads["boss"], heads[user])
    for user in ("ada", "bob"):  # cyd is verified but not on the membership roster
        uid = ctx.uid(user)
        ctx.store.put("roster_checks", uid, {"uuid": uid, "rostered": True})
    return heads


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(voting, "GRACE", 0)
    mail, store = OutboxMailer(), MemoryStore()
    app = _app(store, mail, audit_db_access=True)
    c = TestClient(app)
    h = _people(c, mail, app.state.ctx)
    assert c.post("/api/discussion/boards", json={"id": "general", "title": "General"}, headers=h["boss"]).status_code == 200
    return SimpleNamespace(c=c, h=h, ctx=app.state.ctx, store=store, mail=mail)


def _ballot(env, **body):
    r = env.c.post("/api/voting/ballots", json={"title": "Adopt the budget", "method": "yes_no",
                                                "visibility": "sealed", **body}, headers=env.h["boss"])
    assert r.status_code == 200, r.text
    return r.json()


def test_sealed_ballot_counts_once_anchors_and_breaks_glass_with_a_reason(env):
    c, h = env.c, env.h
    ballot = _ballot(env, board="general", description="Motion to adopt the 2026 budget.")
    bid = ballot["id"]
    assert ballot["status"] == "draft" and ballot["topic"] and "encrypted link" in ballot["notice"]
    topic = c.get(f"/api/discussion/topics/{ballot['topic']}", headers=h["ada"]).json()["topic"]
    assert (topic["kind"], topic["panel"], topic["ref"]) == ("voting-ballot", "ballot", bid)

    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["yes"]}, headers=h["ada"]).status_code == 409
    assert c.post(f"/api/voting/ballots/{bid}/open", headers=h["ada"]).status_code == 403
    assert c.post(f"/api/voting/ballots/{bid}/open", headers=h["boss"]).json()["status"] == "open"
    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["yes", "no"]}, headers=h["ada"]).status_code == 422
    first = c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["yes"]}, headers=h["ada"]).json()
    assert len(first["receipt"]) == 64 and first["kept"] is True
    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["no"]}, headers=h["ada"]).status_code == 409
    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["abstain"]}, headers=h["bob"]).status_code == 200
    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["no"]}, headers=h["cyd"]).status_code == 403
    assert c.get(f"/api/voting/ballots/{bid}", headers=h["cyd"]).status_code == 404
    mine = c.get(f"/api/voting/ballots/{bid}", headers=h["ada"]).json()
    assert (mine["voted"], mine["receipt"], mine["turnout"], mine["results"]) == (True, first["receipt"], 2, None)
    assert c.get(f"/api/voting/ballots/{bid}/record", headers=h["ada"]).status_code == 409

    assert c.post(f"/api/voting/ballots/{bid}/close", headers=h["boss"]).json()["status"] == "closed"
    counted = c.get(f"/api/voting/ballots/{bid}", headers=h["ada"]).json()
    assert counted["results"]["counts"] == {"yes": 1, "no": 0, "abstain": 1}
    assert counted["results"]["outcome"] == "carried" and counted["record"]["anchor"]["status"] == "confirmed"
    record = c.get(f"/api/voting/ballots/{bid}/record", headers=h["ada"]).json()
    assert record_digest(record["committed"]) == record["digest"] == counted["record"]["digest"]
    assert merkle_root(record["leaves"]) == record["committed"]["root"] and first["receipt"] in record["leaves"]
    assert record["committed"]["count"] == record["committed"]["receipts"] == 2
    assert [e["action"] for e in env.ctx.audit.entries()].count("ballot.tallied") == 1

    assert c.post(f"/api/voting/ballots/{bid}/unseal", json={"reason": ""}, headers=h["boss"]).status_code == 422
    assert c.post(f"/api/voting/ballots/{bid}/unseal", json={"reason": "recount"}, headers=h["ada"]).status_code == 403
    roll = c.post(f"/api/voting/ballots/{bid}/unseal", json={"reason": "Recount requested by the CRO"},
                  headers=h["boss"]).json()["roll"]
    assert {row["voter"]: row["choices"] for row in roll} == {env.ctx.uid("ada"): ["Yes"], env.ctx.uid("bob"): ["Abstain"]}
    opened = c.get(f"/api/voting/ballots/{bid}", headers=h["bob"]).json()["unsealed"]
    assert [entry["reason"] for entry in opened] == ["Recount requested by the CRO"] and opened[0]["by_name"]


def test_secret_ballot_keeps_no_link_and_cannot_be_unsealed(env):
    c, h, ctx = env.c, env.h, env.ctx
    base = ctx.now()
    bid = _ballot(env, method="single", options=["Ada", "Bob"], visibility="secret", opens_at=base - 5,
                  closes_at=base + 60)["id"]
    assert c.get(f"/api/voting/ballots/{bid}", headers=h["ada"]).json()["status"] == "open"  # scheduled opening
    cast = c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["o2"]}, headers=h["ada"]).json()
    assert cast["kept"] is False
    view = c.get(f"/api/voting/ballots/{bid}", headers=h["ada"]).json()
    assert view["voted"] is True and view["receipt"] is None and "only once" in view["notice"]
    assert [set(vote) - {"_rev"} for vote in env.store.list("ballot_votes")] == [{"ballot", "leaf", "selection", "salt"}]
    assert [set(receipt) - {"_rev"} for receipt in env.store.list("ballot_receipts")] == [{"ballot"}]
    access = [e["meta"]["ops"] for e in ctx.audit.entries() if e["action"] == "db.access" and e["target"].endswith("/vote")]
    assert access[-1]["write:ballot_votes"]["ids"] == []
    assert c.post(f"/api/voting/ballots/{bid}/unseal", json={"reason": "Recount requested by the CRO"},
                  headers=h["boss"]).status_code == 409

    ctx.now = lambda: base + 120  # scheduled closing, then counting, on the next read
    closed = c.get(f"/api/voting/ballots/{bid}", headers=h["bob"]).json()
    assert closed["status"] == "closed" and closed["results"]["elected"] == ["o2"]
    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["o1"]}, headers=h["bob"]).status_code == 409
    record = c.get(f"/api/voting/ballots/{bid}/record", headers=h["bob"]).json()
    assert record["leaves"] == [cast["receipt"]] and record["committed"]["visibility"] == "secret"


def test_ballot_listing_does_not_apply_scheduled_transitions(env):
    base = env.ctx.now()
    ballot = _ballot(env, opens_at=base + 10, closes_at=base + 20)
    env.ctx.now = lambda: base + 15

    listed = env.c.get("/api/voting/ballots", headers=env.h["boss"]).json()["ballots"]
    assert next(row for row in listed if row["id"] == ballot["id"])["status"] == "draft"
    assert env.store.get("ballots", ballot["id"])["status"] == "draft"
    assert env.c.get(f"/api/voting/ballots/{ballot['id']}", headers=env.h["ada"]).json()["status"] == "open"


def test_open_ballot_roll_and_cancellation(env):
    c, h = env.c, env.h
    bid = _ballot(env, title="Lunch", method="ranked", options=["Pizza", "Salad", "Wraps"], visibility="open")["id"]
    c.post(f"/api/voting/ballots/{bid}/open", headers=h["boss"])
    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["o2", "o2"]}, headers=h["ada"]).status_code == 422
    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["o2", "o1"]}, headers=h["ada"]).status_code == 200
    roll = c.post(f"/api/voting/ballots/{bid}/unseal", json={}, headers=h["boss"]).json()["roll"]
    assert [row["choices"] for row in roll] == [["Salad", "Pizza"]]
    assert c.get(f"/api/voting/ballots/{bid}", headers=h["ada"]).json()["unsealed"] == []
    assert c.post(f"/api/voting/ballots/{bid}/cancel", json={"reason": ""}, headers=h["boss"]).status_code == 422
    assert c.post(f"/api/voting/ballots/{bid}/cancel", json={"reason": "Caterer booked"},
                  headers=h["boss"]).json()["status"] == "cancelled"
    assert c.post(f"/api/voting/ballots/{bid}/vote", json={"selection": ["o1"]}, headers=h["bob"]).status_code == 409


def test_ballot_setup_is_validated(env):
    c, h = env.c, env.h
    post = lambda **body: c.post("/api/voting/ballots", json={"title": "T", "method": "single", "visibility": "open",
                                                              "options": ["A", "B"], **body}, headers=h["boss"])
    assert post(visibility=None).status_code == 422
    assert post(options=["A", "a"]).status_code == 422
    assert post(method="stv", seats=2).status_code == 422
    assert post(board="missing").status_code == 422
    assert post(closes_at=env.ctx.now() - 1).status_code == 422
    assert post(electorate="nobody:here").status_code == 422
    assert c.post("/api/voting/ballots", json={"title": "T", "method": "single", "visibility": "open",
                                               "options": ["A", "B"]}, headers=h["ada"]).status_code == 403
    setup = c.get("/api/voting/setup", headers=h["boss"]).json()
    assert [v["id"] for v in setup["visibilities"]] == ["sealed", "open", "secret"]
    assert [b["id"] for b in setup["boards"]] == ["general"]


def test_consumer_plugin_runs_its_own_ballots_with_its_own_electorate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(voting, "GRACE", 0)
    attendees: set[str] = set()
    manifest = PluginManifest("dinner", "1.0", 1, "dinner_sample:Plugin", dependencies=("voting",),
                              services=("provided", "auth"))

    class Dinner:
        def configure(self, registration):
            service = registration.require("voting")
            service.register_electorate("attendees", Electorate("Dinner attendees",
                                                                lambda account: account["uuid"] in attendees))
            service.register_electorate("broken", Electorate("Broken", lambda account: 1 / 0))

        def router(self, services):
            r = APIRouter(prefix="/api/dinner")
            service = services.provided["voting"]
            staff = services.auth.require_verified()

            @r.post("/poll")
            def poll(electorate: str = "attendees", a: dict = staff):
                ballot = service.create_ballot(BallotSpec("Meal", "single", ("Vegan", "Halal", "Any"),
                                                          electorate=electorate, visibility="open"), a)
                return service.open_ballot(ballot["id"], a)

            @r.post("/poll/{ballot_id}/close")
            def close(ballot_id: str, a: dict = staff):
                service.close_ballot(ballot_id, a)
                return {"results": service.results(ballot_id), "roll": service.unseal(ballot_id, a, "")}

            @r.get("/foreign/{ballot_id}")
            def foreign(ballot_id: str):
                return {"ballot": service.ballot(ballot_id)}

            return r

    Dinner.manifest = manifest
    builtins = plugin_host.discover_manifests()
    real_import = plugin_host.importlib.import_module
    monkeypatch.setattr(plugin_host, "discover_manifests", lambda: {**builtins, manifest.id: manifest})
    monkeypatch.setattr(plugin_host.importlib, "import_module",
                        lambda name: SimpleNamespace(Plugin=Dinner) if name == "dinner_sample" else real_import(name))
    mail, store = OutboxMailer(), MemoryStore()
    app = _app(store, mail, ("discussion", "voting", "dinner"))
    c = TestClient(app)
    h = _people(c, mail, app.state.ctx)
    attendees.add(app.state.ctx.uid("cyd"))

    ballot = c.post("/api/dinner/poll", headers=h["boss"]).json()
    assert (ballot["owner"], ballot["electorate"], ballot["status"]) == ("dinner", "dinner:attendees", "open")
    assert ballot["electorate_label"] == "Dinner attendees"
    assert c.post(f"/api/voting/ballots/{ballot['id']}/close", headers=h["boss"]).status_code == 404
    view = c.get(f"/api/voting/ballots/{ballot['id']}", headers=h["cyd"]).json()
    assert view["eligible"] is True and view["manage"] is False
    assert c.post(f"/api/voting/ballots/{ballot['id']}/vote", json={"selection": ["o1"]}, headers=h["cyd"]).status_code == 200
    assert c.post(f"/api/voting/ballots/{ballot['id']}/vote", json={"selection": ["o2"]}, headers=h["ada"]).status_code == 403
    broken = c.post("/api/dinner/poll", params={"electorate": "broken"}, headers=h["boss"]).json()
    assert c.post(f"/api/voting/ballots/{broken['id']}/vote", json={"selection": ["o1"]}, headers=h["cyd"]).status_code == 403

    closed = c.post(f"/api/dinner/poll/{ballot['id']}/close", headers=h["boss"]).json()
    assert closed["results"]["elected"] == ["o1"] and [row["choices"] for row in closed["roll"]] == [["Vegan"]]
    own = c.post("/api/voting/ballots", json={"title": "T", "method": "single", "visibility": "open",
                                              "options": ["A", "B"]}, headers=h["boss"]).json()
    assert c.get(f"/api/dinner/foreign/{own['id']}").json() == {"ballot": None}
    with pytest.raises(RuntimeError, match="frozen"):
        app.state.ctx.provided["voting"][1].register_electorate("dinner", "late", Electorate("Late", bool))
