import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_voting import _app, _people  # noqa: E402

from utsu_core.mailer import OutboxMailer
from utsu_core.contracts import ThreadAccess, ThreadType
from utsu_core.modules.messaging import thread_events
from utsu_core.services.messaging import MAX_PAGE, MessagingRuntime, ThreadTypeRegistry
from utsu_core.store import MemoryStore

THREAD = "/api/threads/discussion-topic"


@pytest.fixture
def env():
    mail, store = OutboxMailer(), MemoryStore()
    app = _app(store, mail)
    c = TestClient(app)
    h = _people(c, mail, app.state.ctx)
    for board, audience in (("general", "members"), ("council", "staff")):
        r = c.post("/api/discussion/boards", json={"id": board, "title": board.title(), "audience": audience},
                   headers=h["boss"])
        assert r.status_code == 200, r.text
    tid = c.post("/api/discussion/boards/general/topics", json={"title": "Hello", "body": "First post"},
                 headers=h["ada"]).json()["id"]
    return SimpleNamespace(c=c, h=h, ctx=app.state.ctx, store=store, mail=mail, tid=tid)


def test_boards_follow_audience_and_roster(env):
    c, h = env.c, env.h
    ada = c.get("/api/discussion/boards", headers=h["ada"]).json()
    assert [b["id"] for b in ada["boards"]] == ["general"] and (ada["moderator"], ada["member"]) == (False, True)
    assert c.get("/api/discussion/boards", headers=h["cyd"]).json()["boards"] == []
    assert [b["id"] for b in c.get("/api/discussion/boards", headers=h["boss"]).json()["boards"]] == ["council", "general"]
    assert c.get("/api/discussion/boards/council/topics", headers=h["ada"]).status_code == 404
    assert c.get("/api/discussion/boards/general/topics", headers=h["cyd"]).status_code == 404
    assert c.post("/api/discussion/boards/council/topics", json={"title": "x", "body": "y"},
                  headers=h["ada"]).status_code == 404
    assert c.post("/api/discussion/boards", json={"id": "general", "title": "Again"}, headers=h["boss"]).status_code == 409
    assert c.post("/api/discussion/boards", json={"id": "Bad Id", "title": "Bad"}, headers=h["boss"]).status_code == 422
    assert c.post("/api/discussion/boards", json={"id": "mine", "title": "Mine"}, headers=h["ada"]).status_code == 403


def test_topic_threads_locking_redaction_and_author_access(env):
    c, h, tid = env.c, env.h, env.tid
    first = c.get(f"{THREAD}/{tid}/messages", headers=h["bob"]).json()
    assert [(m["seq"], m["body"], m["mine"]) for m in first] == [(1, "First post", False)] and "author" not in first[0]
    assert c.post(f"{THREAD}/{tid}/messages", json={"body": "  "}, headers=h["bob"]).status_code == 422
    assert c.post(f"{THREAD}/{tid}/messages", json={"body": "Reply"}, headers=h["bob"]).json()["seq"] == 2
    described = c.get(f"{THREAD}/{tid}", headers=h["bob"]).json()
    assert (described["post"], described["staff"], described["last_seq"], described["read"]) == (True, False, 2, 2)
    assert c.get(f"{THREAD}/{tid}/messages", headers=h["cyd"]).status_code == 403
    assert c.get(f"{THREAD}/{tid}/stream", headers=h["cyd"]).status_code == 403
    assert c.get("/api/threads/nothing/abc/messages", headers=h["ada"]).status_code == 404

    topics = c.get("/api/discussion/boards/general/topics", headers=h["ada"]).json()["topics"]
    assert [(t["id"], t["posts"], t["kind"]) for t in topics] == [(tid, 2, "discussion")]
    moderated = c.post(f"/api/discussion/topics/{tid}/moderate", json={"locked": True, "pinned": True},
                       headers=h["boss"]).json()
    assert moderated["locked"] and moderated["pinned"]
    assert c.post(f"{THREAD}/{tid}/messages", json={"body": "Late"}, headers=h["bob"]).status_code == 403
    assert c.get(f"/api/discussion/topics/{tid}", headers=h["ada"]).json()["can_post"] is False
    assert c.post(f"{THREAD}/{tid}/messages/2/redact", json={"reason": "off topic"}, headers=h["ada"]).status_code == 403
    redacted = c.post(f"{THREAD}/{tid}/messages/2/redact", json={"reason": "off topic"}, headers=h["boss"]).json()
    assert (redacted["body"], redacted["redacted"]) == ("", True)
    assert [m["body"] for m in c.get(f"{THREAD}/{tid}/messages", headers=h["ada"]).json()] == ["First post", ""]

    c.post("/api/discussion/boards/general", json={"audience": "staff"}, headers=h["boss"])
    assert c.get(f"{THREAD}/{tid}/messages", headers=h["bob"]).status_code == 403
    assert c.get(f"{THREAD}/{tid}/messages", headers=h["ada"]).status_code == 200  # the author keeps access
    c.post("/api/discussion/boards/general", json={"audience": "members", "archived": True}, headers=h["boss"])
    assert c.post("/api/discussion/boards/general/topics", json={"title": "New", "body": "Post"},
                  headers=h["ada"]).status_code == 403


def test_author_choice_is_snapshotted_for_topics_and_posts(env):
    c, h = env.c, env.h
    result = c.post("/api/discussion/boards/general/topics",
                    json={"title": "Pseudonym", "body": "Opening", "name_choice": "username"}, headers=h["ada"])
    assert result.status_code == 200 and result.json()["author_name"] == "ada"
    tid = result.json()["id"]
    reply = c.post(f"{THREAD}/{tid}/messages", json={"body": "Reply", "name_choice": "username"}, headers=h["ada"])
    assert reply.json()["author_name"] == "ada"
    assert c.patch("/api/me/username", json={"username": "newname"}, headers=h["ada"]).status_code == 200
    topic = c.get("/api/discussion/boards/general/topics", headers=h["bob"]).json()["topics"]
    assert next(item for item in topic if item["id"] == tid)["author_name"] == "ada"
    messages = c.get(f"{THREAD}/{tid}/messages", headers=h["bob"]).json()
    assert [message["author_name"] for message in messages] == ["ada", "ada"]
    assert c.patch("/api/me/username", json={"username": ""}, headers=h["ada"]).status_code == 200
    assert c.post(f"{THREAD}/{tid}/messages", json={"body": "No alias", "name_choice": "username"},
                  headers=h["ada"]).status_code == 422
    real = c.post(f"{THREAD}/{tid}/messages", json={"body": "Real name", "name_choice": "real"}, headers=h["ada"])
    assert real.status_code == 200 and real.json()["author_name"] != "ada"


def _events(chunks: list[str]) -> list[tuple[str, object]]:
    out = []
    for chunk in chunks:
        lines = dict(line.split(": ", 1) for line in chunk.strip().splitlines() if ": " in line and not line.startswith(":"))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_stream_sends_backlog_then_hints_and_stops_when_access_is_revoked(env):
    c, h, ctx, tid = env.c, env.h, env.ctx, env.tid
    c.post(f"{THREAD}/{tid}/messages", json={"body": "Second"}, headers=h["bob"])
    bob = ctx.store.get("accounts", ctx.uid("bob"))

    class Bus:
        def __init__(self, *events):
            self.events = events

        async def listen(self, _channel, _disconnected):
            for event in self.events:
                yield event() if callable(event) else event

    async def disconnected() -> bool:
        return False

    def run(bus: Bus, after: int = 0, lifetime: float = 600) -> list[tuple[str, object]]:
        async def collect():
            return [chunk async for chunk in thread_events(ctx.messaging, bus, "discussion-topic", tid, bob, after,
                                                           disconnected, lifetime=lifetime)]
        return _events(asyncio.run(collect()))

    backlog = run(Bus())
    assert backlog[0] == ("sync", {"seqs": [1, 2]})
    assert [(event, data["seq"]) for event, data in backlog[1:3]] == [("message", 1), ("message", 2)]
    assert backlog[3] == ("ready", {"cursor": 2})

    ada, boss = (ctx.store.get("accounts", ctx.uid(user)) for user in ("ada", "boss"))

    def post_third():
        ctx.messaging.post("discussion-topic", tid, ada, "Third")
        return {"seq": 3}

    def redact_first():
        ctx.messaging.redact("discussion-topic", tid, 1, boss, "spam")
        return {"redacted": 1}

    live = run(Bus(post_third, redact_first, None), after=2)
    assert [event for event, _ in live] == ["sync", "message", "message", "ready", "sync", "message", "redact"]
    assert live[5][1]["body"] == "Third"
    assert (live[6][1]["seq"], live[6][1]["redacted"], live[6][1]["body"]) == (1, True, "")
    assert run(Bus({"seq": 3}), after=3, lifetime=0)[-1] == ("reconnect", {"cursor": 3})

    ctx.store.delete("roster_checks", bob["uuid"])
    assert run(Bus(None)) == [("denied", {})]


def test_ballot_topics_fall_back_to_plain_discussion_when_voting_is_disabled(env):
    ballot = env.c.post("/api/voting/ballots", json={"title": "Motion", "method": "yes_no", "visibility": "open",
                                                     "board": "general"}, headers=env.h["boss"]).json()
    without_voting = TestClient(_app(env.store, env.mail, ("discussion",)))
    topic = without_voting.get(f"/api/discussion/topics/{ballot['topic']}", headers=env.h["ada"]).json()["topic"]
    assert (topic["kind"], topic["panel"], topic["ref"], topic["title"]) == ("discussion", None, None, "Motion")
    assert without_voting.get(f"/api/voting/ballots/{ballot['id']}", headers=env.h["ada"]).status_code == 404


def test_stream_recovers_late_writes_and_redactions_without_bus_hints(env):
    ctx, tid = env.ctx, env.tid
    key = f"discussion-topic:{tid}"
    bob = ctx.store.get("accounts", ctx.uid("bob"))
    boss = ctx.store.get("accounts", ctx.uid("boss"))

    def insert(seq):
        ctx.store.create("messages", f"{key}:{seq:010d}", {
            "thread": key, "seq": seq, "author": bob["uuid"], "internal": False,
            "body": f"Post {seq}", "at": ctx.now(),
        })

    # Sequence 2 is allocated first but commits after the following messages.
    for seq in range(3, MAX_PAGE + 5):
        insert(seq)

    class Bus:
        async def listen(self, _channel, _disconnected):
            insert(2)
            ctx.messaging.redact("discussion-topic", tid, 1, boss, "Remove personal details")
            yield None  # The publish hint was lost; the stored state must still converge.

    async def disconnected():
        return False

    async def collect():
        return [chunk async for chunk in thread_events(ctx.messaging, Bus(), "discussion-topic", tid, bob, 1,
                                                       disconnected)]

    events = _events(asyncio.run(collect()))
    ready = next(i for i, (kind, _) in enumerate(events) if kind == "ready")
    before = [data["seq"] for kind, data in events[:ready] if kind == "message"]
    assert before == [1, *range(3, MAX_PAGE + 5)]
    assert [(kind, data["seq"]) for kind, data in events[ready + 1:] if kind != "sync"] == [("redact", 1), ("message", 2)]


def test_stream_removes_internal_notes_when_staff_access_ends(env):
    ctx = env.ctx
    staff = True
    registry = ThreadTypeRegistry()
    registry.register("sample", "sample-thread", ThreadType(
        authorize=lambda _account, _record: ThreadAccess(read=True, post=True, staff=staff), internal_notes=True,
    ))
    runtime = MessagingRuntime(ctx.store, ctx.audit, ctx.bus, ctx.limiter, registry, lambda _uid: "Member", ctx.now)
    account = ctx.store.get("accounts", ctx.uid("bob"))
    runtime.post("sample-thread", "r1", account, "Staff note", internal=True)
    runtime.post("sample-thread", "r1", account, "Public reply")

    class Bus:
        async def listen(self, _channel, _disconnected):
            nonlocal staff
            staff = False
            yield None

    async def disconnected():
        return False

    async def collect():
        return [chunk async for chunk in thread_events(runtime, Bus(), "sample-thread", "r1", account, 0, disconnected)]

    events = _events(asyncio.run(collect()))
    assert [data for kind, data in events if kind == "sync"] == [{"seqs": [1, 2]}, {"seqs": [2]}]
    assert [row["body"] for row in runtime.messages("sample-thread", "r1", account)] == ["Public reply"]
