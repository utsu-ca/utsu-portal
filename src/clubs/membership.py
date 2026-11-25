"""
Club membership beyond the officers: ordinary members, join requests and honorary membership.

Officers (owner, treasurer, executive) stay in the club's own `members` map. Ordinary members live in `club_members`
(key `{club}:{account}`) so they never gain the officers' access to the ledger and receipts. An honorary member carries
the membership session it was granted for and counts only while that is the current session.

Every permission here is scoped: `has_permission(account, PERM, scope=club_id)` is true only through the account's own
role in that one club, never through a role held in another club.
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.contracts import PluginServices
from core.permissions import PERM_CLUB_MEMBERS_MANAGE
from core.security import clean_text
from core.services.roster import membership_session

COL_MEMBERS = "club_members"
COL_REQUESTS = "club_requests"
OFFICER_ROLES = ("owner", "treasurer", "executive")


def member_key(cid: str, uid: str) -> str:
    return f"{cid}:{uid}"


def member_record(cid: str, uid: str, source: str, by: str, now: int, session: str | None = None,
                  reason: str = "") -> dict:
    record = {"club_id": cid, "uuid": uid, "source": source, "joined_at": now, "by": by}
    if session:
        record["session"] = session
    if reason:
        record["reason"] = reason
    return record


def ordinary_member(record: dict | None, now: int) -> bool:
    """A member record counts while it has no session or the session it was granted for is the current one."""
    return bool(record) and record.get("session") in (None, membership_session(now))


def club_roles(club: dict, record: dict | None, uid: str, now: int, active: bool) -> set[str]:
    """
    The scoped roles `uid` holds in this one club. Officer roles hold whatever the club's status, so a pending or
    expired club can still be run; `club_officer` and `club_member` need the club to be approved for this year.
    """
    officer = club["members"].get(uid)
    held = {f"club_{officer}"} if officer in OFFICER_ROLES else set()
    if active and officer in OFFICER_ROLES:
        held.add("club_officer")
    if active and (officer in OFFICER_ROLES or ordinary_member(record, now)):
        held.add("club_member")
    return held


def scoped_role(role: str, club_is_active: Callable[[dict, int], bool]):
    """The factory `register_scoped_role` takes for one role."""
    def build(services: PluginServices):
        def holds(account, cid: str) -> bool:
            club = services.store.get("clubs", cid)
            if not club:
                return False
            uid = account["uuid"]
            record = services.store.get(COL_MEMBERS, member_key(cid, uid))
            now = services.now()
            return role in club_roles(club, record, uid, now, club_is_active(club, now))
        return holds
    return build


class ClubDirectory:
    """The `club_directory` provided service: lets a plug-in that builds on clubs ask whether a club exists."""

    def __init__(self, services: PluginServices) -> None:
        self._store = services.store

    def for_consumer(self, _consumer: str) -> "ClubDirectory":
        return self

    def exists(self, cid: str) -> bool:
        return isinstance(cid, str) and self._store.get("clubs", cid) is not None


class JoinIn(BaseModel):
    message: str = Field("", max_length=200)


class DecisionIn(BaseModel):
    approve: bool


class HonoraryIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


def membership_router(services: PluginServices, load_club: Callable[[str], dict],
                      club_is_active: Callable[[dict, int], bool]) -> APIRouter:
    r = APIRouter()  # mounted under the clubs router, which carries the /api prefix
    S = services.store
    verified = services.auth.require_verified()

    def manage(account: dict, cid: str) -> dict:
        club = load_club(cid)
        if not services.auth.has_permission(account, PERM_CLUB_MEMBERS_MANAGE, scope=cid):
            raise HTTPException(403, "not permitted to manage this club's members")
        return club

    def name_of(uid: str) -> str:
        person = services.identity.person(uid)
        preferred = services.identity.preferred_name(uid)
        last = (person.last_name or "")[:1] if person else ""
        first = preferred or (person.first_name if person else "") or ""
        return f"{first} {last}.".strip() if last else first

    def add_member(club: dict, uid: str, source: str, by: str, session: str | None = None, reason: str = "") -> dict:
        record = member_record(club["id"], uid, source, by, services.now(), session, reason)
        S.put(COL_MEMBERS, member_key(club["id"], uid), record)
        return record

    @r.get("/clubs/joined")
    def joined(a: dict = verified):
        now = services.now()
        items = []
        for record in S.list(COL_MEMBERS, uuid=a["uuid"]):
            club = S.get("clubs", record["club_id"])
            if club and ordinary_member(record, now):
                items.append({"id": club["id"], "name": club["name"], "category": club["category"],
                              "source": record["source"], "joined_at": record["joined_at"],
                              "active": club_is_active(club, now)})
        return {"items": sorted(items, key=lambda item: item["name"].casefold())}

    @r.post("/clubs/{cid}/join-requests", status_code=201)
    def request_join(cid: str, b: JoinIn, a: dict = verified):
        club, uid = load_club(cid), a["uuid"]
        if not club_is_active(club, services.now()):
            raise HTTPException(409, "this club is not open for new members")
        if uid in club["members"] or ordinary_member(S.get(COL_MEMBERS, member_key(cid, uid)), services.now()):
            raise HTTPException(409, "you are already a member of this club")
        request = {"club_id": cid, "uuid": uid, "message": clean_text(b.message, 200), "at": services.now()}
        if not S.create(COL_REQUESTS, member_key(cid, uid), request):
            raise HTTPException(409, "you already asked to join this club")
        services.audit.record(uid, "club.join_requested", f"club:{cid}", None, {"at": request["at"]})
        return {"status": "pending"}

    @r.delete("/clubs/{cid}/join-requests/mine")
    def withdraw_request(cid: str, a: dict = verified):
        key = member_key(cid, a["uuid"])
        if not S.get(COL_REQUESTS, key):
            raise HTTPException(404, "no request to withdraw")
        S.delete(COL_REQUESTS, key)
        services.audit.record(a["uuid"], "club.join_withdrawn", f"club:{cid}")
        return {"status": "withdrawn"}

    @r.get("/clubs/{cid}/join-requests")
    def join_requests(cid: str, a: dict = verified):
        manage(a, cid)
        items = [{"uuid": request["uuid"], "name": name_of(request["uuid"]), "message": request.get("message", ""),
                  "at": request["at"]} for request in S.list(COL_REQUESTS, club_id=cid)]
        return {"items": sorted(items, key=lambda item: item["at"])}

    @r.post("/clubs/{cid}/join-requests/{uid}/decision")
    def decide_request(cid: str, uid: str, b: DecisionIn, a: dict = verified):
        club = manage(a, cid)
        key = member_key(cid, uid)
        if not S.get(COL_REQUESTS, key):
            raise HTTPException(404, "no such request")
        if b.approve:
            if not club_is_active(club, services.now()):
                raise HTTPException(409, "this club is not open for new members")
            person = services.identity.person(uid)
            if person is None or person.status != "verified":
                raise HTTPException(409, "only verified accounts can join")
            add_member(club, uid, "request", a["uuid"])
        S.delete(COL_REQUESTS, key)
        services.audit.record(a["uuid"], "club.join_approved" if b.approve else "club.join_declined",
                              f"club:{cid}", None, None, {"member": uid})
        return {"status": "approved" if b.approve else "declined"}

    @r.get("/clubs/{cid}/members")
    def members(cid: str, a: dict = verified, limit: int = Query(500, ge=1, le=1000)):
        club = manage(a, cid)
        now = services.now()
        items = [{"uuid": uid, "name": name_of(uid), "role": role, "source": "officer"}
                 for uid, role in club["members"].items()]
        items += [{"uuid": record["uuid"], "name": name_of(record["uuid"]), "role": "member",
                   "source": record["source"], "session": record.get("session")}
                  for record in S.list(COL_MEMBERS, club_id=cid) if ordinary_member(record, now)]
        return {"items": items[:limit], "total": len(items)}

    @r.delete("/clubs/{cid}/members/{uid}")
    def remove_member(cid: str, uid: str, a: dict = verified):
        manage(a, cid)
        key = member_key(cid, uid)
        before = S.get(COL_MEMBERS, key)
        if not before:
            raise HTTPException(404, "not an ordinary member of this club")
        S.delete(COL_MEMBERS, key)
        services.audit.record(a["uuid"], "club.member_removed", f"club:{cid}", before, None, {"member": uid})
        return {"status": "removed"}

    @r.post("/clubs/{cid}/leave")
    def leave(cid: str, a: dict = verified):
        club = load_club(cid)
        if a["uuid"] in club["members"]:
            raise HTTPException(409, "officers cannot leave this way; ask an owner to change your role")
        key = member_key(cid, a["uuid"])
        before = S.get(COL_MEMBERS, key)
        if not before:
            raise HTTPException(404, "you are not a member of this club")
        S.delete(COL_MEMBERS, key)
        services.audit.record(a["uuid"], "club.member_left", f"club:{cid}", before, None)
        return {"status": "left"}

    @r.get("/clubs/{cid}/people/lookup")
    def lookup(cid: str, email: str = Query("", max_length=320), student_number: str = Query("", max_length=32),
               a: dict = verified):
        club = manage(a, cid)
        if bool(email.strip()) == bool(student_number.strip()):
            raise HTTPException(422, "give either an email or a student number")
        contact = services.identity.find_contact(email, student_number)
        if contact is None:
            raise HTTPException(404, "no account found")
        uid = contact.uuid
        record = S.get(COL_MEMBERS, member_key(cid, uid))
        if uid in club["members"]:
            relation = club["members"][uid]
        elif ordinary_member(record, services.now()):
            relation = "honorary" if record["source"] == "honorary" else "member"
        elif uid in club.get("invitations", {}):
            relation = "invited"
        elif S.get(COL_REQUESTS, member_key(cid, uid)):
            relation = "requested"
        else:
            relation = "none"
        services.audit.record(a["uuid"], "club.people_lookup", f"club:{cid}", None, None,
                              {"by": "email" if email.strip() else "student_number", "found": uid})
        return {"uuid": uid, "name": name_of(uid), "verified": contact.status == "verified", "relation": relation}

    @r.put("/clubs/{cid}/members/{uid}/honorary")
    def grant_honorary(cid: str, uid: str, b: HonoraryIn, a: dict = verified):
        club = manage(a, cid)
        person = services.identity.person(uid)
        if person is None or person.status != "verified":
            raise HTTPException(409, "honorary membership can only be granted to verified accounts")
        if uid in club["members"]:
            raise HTTPException(409, "officers are already members")
        before = S.get(COL_MEMBERS, member_key(cid, uid))
        if ordinary_member(before, services.now()) and before["source"] != "honorary":
            raise HTTPException(409, "already a member")
        record = add_member(club, uid, "honorary", a["uuid"], membership_session(services.now()), b.reason.strip())
        S.delete(COL_REQUESTS, member_key(cid, uid))
        services.audit.record(a["uuid"], "club.honorary_granted", f"club:{cid}", before, record, {"member": uid})
        return {"status": "granted", "session": record["session"]}

    return r
