"""
Club governance: ballots and discussion boards that belong to one club.

An opt-in plug-in that joins Clubs with Voting and Discussion. Each club gets its own electorate (`club/<club id>`)
and board audience, both decided live by the scoped permissions `utsu:clubs:vote` and `utsu:clubs:discuss` in that one
club, so a member of one club has no say in another. A club may also require UTSU membership for its ballots.
"""
from __future__ import annotations

import secrets
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from chat.contracts import Audience, DiscussionService
from core.contracts import PluginManifest, PluginServices, RegistrationContext
from core.permissions import PERM_CLUB_BALLOTS_MANAGE, PERM_CLUB_DISCUSS, PERM_CLUB_VOTE, PERM_VOTING_VOTE
from voting.contracts import BallotSpec, Electorate, VotingService

COL_BALLOTS = "club_ballots"
COL_SETTINGS = "club_settings"
AUDIENCE = "club"


class SettingsIn(BaseModel):
    utsu_only: bool


class ClubBallotIn(BaseModel):
    title: str = Field(max_length=400)
    description: str = Field("", max_length=16000)
    method: Literal["yes_no", "single", "approval", "ranked", "stv"]
    options: list[str] = Field(default_factory=list, max_length=50)
    seats: int = Field(1, ge=1, le=50)
    max_choices: int = Field(0, ge=0, le=50)
    threshold: Literal["majority", "two_thirds"] = "majority"
    quorum: int = Field(0, ge=0)
    visibility: Literal["sealed", "open", "secret"] = "secret"
    opens_at: int | None = None
    closes_at: int | None = None
    board: str | None = Field(None, max_length=48)


class ClubBoardIn(BaseModel):
    title: str = Field(max_length=400)
    description: str = Field("", max_length=4000)


class ClubGovernancePlugin:
    manifest = PluginManifest(
        "club_governance",
        "0.1.0",
        1,
        "clubs.governance:ClubGovernancePlugin",
        interface="plugin",
        default_enabled=False,
        dependencies=("clubs", "discussion", "voting"),
        order=150,
        services=("audit", "auth", "now", "provided", "store"),
        collections=(COL_BALLOTS, COL_SETTINGS),
    )

    def __init__(self) -> None:
        self._s: PluginServices | None = None

    # The electorate and audience are registered while the plug-in is configured, before its services exist; they are
    # only called once requests arrive.

    def _services(self) -> PluginServices:
        if self._s is None:
            raise RuntimeError("club governance is not ready")
        return self._s

    def _club_exists(self, cid: str) -> bool:
        return bool(self._services().provided["club_directory"].exists(cid))

    def _can_vote(self, account: Any, cid: str) -> bool:
        s = self._services()
        if not s.auth.has_permission(account, PERM_CLUB_VOTE, scope=cid):
            return False
        settings = s.store.get(COL_SETTINGS, cid)
        return not (settings and settings.get("utsu_only")) or s.auth.has_permission(account, PERM_VOTING_VOTE)

    def _can_view(self, account: Any, cid: str) -> bool:
        return self._services().auth.has_permission(account, PERM_CLUB_DISCUSS, scope=cid)

    def configure(self, registration: RegistrationContext) -> None:
        voting: VotingService = registration.require("voting")
        voting.register_electorate(AUDIENCE, Electorate("Members of one club", self._can_vote, scoped=True,
                                                        scope_exists=self._club_exists))
        discussion: DiscussionService = registration.require("discussion")
        discussion.register_audience(AUDIENCE, Audience("Members of one club", self._can_view, self._club_exists))

    def router(self, services: PluginServices) -> APIRouter:
        self._s = services
        r = APIRouter(prefix="/api/clubs/{cid}")
        S = services.store
        verified = services.auth.require_verified()
        voting: VotingService = services.provided["voting"]
        discussion: DiscussionService = services.provided["discussion"]

        def club(cid: str) -> None:
            if not self._club_exists(cid):
                raise HTTPException(404, "unknown club")

        def officer(cid: str, account: dict) -> None:
            club(cid)
            if not services.auth.has_permission(account, PERM_CLUB_BALLOTS_MANAGE, scope=cid):
                raise HTTPException(403, "only officers of an approved club can do this")

        def member(cid: str, account: dict) -> None:
            club(cid)
            if not self._can_view(account, cid):
                raise HTTPException(403, "only members of an approved club can see this")

        def audience_of(cid: str) -> str:
            return f"club_governance:{AUDIENCE}/{cid}"

        def own_ballot(cid: str, ballot_id: str) -> None:
            record = S.get(COL_BALLOTS, ballot_id)
            if not record or record["club_id"] != cid:
                raise HTTPException(404, "no such club ballot")

        def failed(exc: Exception) -> HTTPException:
            if isinstance(exc, PermissionError):
                return HTTPException(403, str(exc))
            if isinstance(exc, LookupError):
                return HTTPException(404, str(exc) or "not found")
            return HTTPException(422 if isinstance(exc, ValueError) else 409, str(exc))

        @r.get("/governance")
        def get_settings(cid: str, a: dict = verified):
            member(cid, a)
            return {"utsu_only": bool((S.get(COL_SETTINGS, cid) or {}).get("utsu_only"))}

        @r.put("/governance")
        def set_settings(cid: str, b: SettingsIn, a: dict = verified):
            officer(cid, a)
            before = S.get(COL_SETTINGS, cid)
            S.put(COL_SETTINGS, cid, {"club_id": cid, "utsu_only": b.utsu_only})
            services.audit.record(a["uuid"], "club.governance_set", f"club:{cid}", before, {"utsu_only": b.utsu_only})
            return {"utsu_only": b.utsu_only}

        @r.get("/ballots")
        def list_ballots(cid: str, a: dict = verified):
            member(cid, a)
            rows = []
            for record in S.list(COL_BALLOTS, club_id=cid):
                ballot = voting.ballot(record["ballot_id"])
                if ballot and ballot.get("status") != "draft":
                    rows.append({"id": ballot["id"], "title": ballot["title"], "method": ballot["method"],
                                 "status": ballot["status"], "closes_at": ballot.get("closes_at"),
                                 "eligible": self._can_vote(a, cid)})
            return {"items": sorted(rows, key=lambda row: row["title"].casefold())}

        @r.get("/ballots/manage")
        def manage_ballots(cid: str, a: dict = verified):
            officer(cid, a)
            rows = [voting.ballot(record["ballot_id"]) for record in S.list(COL_BALLOTS, club_id=cid)]
            return {"items": [row for row in rows if row]}

        @r.post("/ballots", status_code=201)
        def create_ballot(cid: str, b: ClubBallotIn, a: dict = verified):
            officer(cid, a)
            if b.board and not any(row["id"] == b.board and row.get("audience") == audience_of(cid)
                                   for row in discussion.boards()):
                raise HTTPException(422, "choose one of this club's boards")
            spec = BallotSpec(title=b.title, method=b.method, options=tuple(b.options), description=b.description,
                              seats=b.seats, max_choices=b.max_choices, threshold=b.threshold, quorum=b.quorum,
                              electorate=f"{AUDIENCE}/{cid}", visibility=b.visibility, opens_at=b.opens_at,
                              closes_at=b.closes_at, board=b.board or None, ref=cid)
            try:
                ballot = voting.create_ballot(spec, a)
            except (ValueError, PermissionError, LookupError) as exc:
                raise failed(exc) from exc
            S.put(COL_BALLOTS, ballot["id"], {"ballot_id": ballot["id"], "club_id": cid, "created_by": a["uuid"],
                                              "at": services.now()})
            services.audit.record(a["uuid"], "club.ballot_created", f"club:{cid}", None, {"ballot": ballot["id"]})
            return ballot

        @r.post("/ballots/{ballot_id}/open")
        def open_ballot(cid: str, ballot_id: str, a: dict = verified):
            officer(cid, a)
            own_ballot(cid, ballot_id)
            try:
                return voting.open_ballot(ballot_id, a)
            except Exception as exc:
                raise failed(exc) from exc

        @r.post("/ballots/{ballot_id}/close")
        def close_ballot(cid: str, ballot_id: str, a: dict = verified):
            officer(cid, a)
            own_ballot(cid, ballot_id)
            try:
                return voting.close_ballot(ballot_id, a)
            except Exception as exc:
                raise failed(exc) from exc

        @r.get("/boards")
        def boards(cid: str, a: dict = verified):
            member(cid, a)
            return {"items": [row for row in discussion.boards() if row.get("audience") == audience_of(cid)]}

        @r.post("/boards", status_code=201)
        def create_board(cid: str, b: ClubBoardIn, a: dict = verified):
            officer(cid, a)
            board_id = f"club-{cid}-{secrets.token_hex(3)}"
            try:
                board = discussion.ensure_board(board_id, b.title, b.description, f"{AUDIENCE}/{cid}")
            except ValueError as exc:
                raise failed(exc) from exc
            services.audit.record(a["uuid"], "club.board_created", f"club:{cid}", None, {"board": board["id"]})
            return board

        return r
