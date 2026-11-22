"""Ballots and polls, each optionally discussed in a discussion topic. Visibility (sealed/open/secret) is fixed when a
ballot is created and decides who can ever link a vote to its voter. Results are tallied once after closing; the
record (sorted receipt leaves, Merkle root, result) is digested and anchored through the core anchor service."""
from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from utsu_core.contracts import MessageRateLimited, PluginManifest, PluginServices, RegistrationContext
from utsu_core.permissions import PERM_ELECTIONS_ADMIN
from utsu_core.programs.discussion.contracts import DiscussionService, TopicKind

from .contracts import MEMBERS, METHODS, THRESHOLDS, VISIBILITIES, BallotSpec, Electorate
from .record import RECORD_TAG, leaf_hash, merkle_root, record_digest
from .tally import tally

BALLOT_KIND = "voting-ballot"
GRACE = 10  # seconds after closing before counting, so votes admitted just before the close are written
MAX_OPTIONS = 50
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BALLOT_ID = re.compile(r"^[0-9a-f]{16}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_YES_NO = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}, {"id": "abstain", "label": "Abstain"}]

NOTICES = {
    "open": "Recorded vote. Elections officials and the organizers of this ballot can see how each person voted, "
            "for example to act on dietary choices.",
    "sealed": "Sealed ballot. Your choice is stored with an encrypted link to you. Nobody can see how you voted "
              "unless an elections official breaks the seal and records a reason; every opening is listed on this "
              "ballot.",
    "secret": "Secret ballot. Your choice is stored with no link to you, so nobody, including officials, can ever see "
              "how you voted. Your receipt is shown only once: save it to check that your vote was counted.",
}

MANIFEST = PluginManifest(
    id="voting",
    version="0.1.0",
    core_api_major=1,
    factory="utsu_core.programs.voting.plugin:VotingPlugin",
    interface="plugin",
)


class BallotConflict(Exception):
    """The ballot is not in a state that allows this."""


def _clean_line(value: object, limit: int, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text")
    text = " ".join(_CONTROL.sub(" ", unicodedata.normalize("NFC", value)).split())
    if not text:
        raise ValueError(f"{what} is required")
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return text


def _clean_text(value: object, limit: int, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text")
    text = _CONTROL.sub("", unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))).strip()
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return text


def _options(method: str, labels: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
    if method == "yes_no":
        return [dict(option) for option in _YES_NO]
    cleaned = [_clean_line(label, 200, "option") for label in labels]
    if not 2 <= len(cleaned) <= MAX_OPTIONS:
        raise ValueError(f"a ballot needs between 2 and {MAX_OPTIONS} options")
    if len({label.casefold() for label in cleaned}) != len(cleaned):
        raise ValueError("options must all be different")
    return [{"id": f"o{index + 1}", "label": label} for index, label in enumerate(cleaned)]


def _optional_time(value: object, what: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError(f"{what} must be a positive Unix time")
    return value


class VotingRuntime:
    """Ballots, electorates, casting, counting and break-glass; the `voting` provided service."""

    def __init__(self, services: PluginServices, discussion: DiscussionService) -> None:
        self._s = services
        self._discussion = discussion
        self._electorates: dict[str, tuple[str, Electorate]] = {}
        self._frozen = False

    def for_consumer(self, consumer: str) -> "VotingClient":
        return VotingClient(self, consumer)

    def freeze(self) -> None:
        self._frozen = True

    # Electorates

    def register_electorate(self, owner: str, identifier: str, electorate: Electorate) -> None:
        if self._frozen:
            raise RuntimeError("voting electorates are frozen")
        if not isinstance(electorate, Electorate) or not callable(electorate.is_eligible):
            raise TypeError(f"electorate {identifier} is not an Electorate")
        if not _NAME.fullmatch(identifier or ""):
            raise ValueError(f"electorate ID {identifier!r} is invalid")
        key = f"{owner}:{identifier}"
        if key in self._electorates:
            raise ValueError(f"electorate {key} is already registered")
        self._electorates[key] = (owner, electorate)

    def electorates(self) -> list[dict[str, str]]:
        registered = [{"id": key, "description": entry[1].description} for key, entry in self._electorates.items()]
        return [{"id": MEMBERS, "description": "Verified members on the membership roster"},
                *sorted(registered, key=lambda item: item["id"])]

    def _electorate_label(self, key: str) -> str | None:
        if key == MEMBERS:
            return "Members"
        entry = self._electorates.get(key)
        return entry[1].description if entry else None

    def eligible(self, ballot: Mapping[str, Any], account: Mapping[str, Any]) -> bool:
        if account.get("status") != "verified":
            return False
        key = ballot.get("electorate")
        if key == MEMBERS:
            return self._s.auth.is_rostered(account)
        entry = self._electorates.get(str(key))
        if entry is None:
            return False  # the electorate's plug-in is disabled: refuse rather than guess
        try:
            return bool(entry[1].is_eligible(account))
        except Exception:
            return False

    # Ballot lifecycle

    def create(self, owner: str, spec: BallotSpec, actor: Mapping[str, Any], electorate: str) -> dict[str, Any]:
        if actor.get("status") != "verified":
            raise PermissionError("only verified accounts can create ballots")
        if spec.method not in METHODS:
            raise ValueError(f"method must be one of {', '.join(METHODS)}")
        if spec.visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {', '.join(VISIBILITIES)}")
        if electorate != MEMBERS and electorate not in self._electorates:
            raise ValueError(f"unknown electorate {electorate}")
        title = _clean_line(spec.title, 200, "title")
        description = _clean_text(spec.description, 8000, "description")
        options = _options(spec.method, spec.options)
        seats, max_choices, threshold = 1, 0, "majority"
        if spec.method in ("approval", "stv"):
            seats = spec.seats
            if type(seats) is not int or not 1 <= seats < len(options):
                raise ValueError("seats must be at least 1 and fewer than the number of options")
        if spec.method == "approval":
            max_choices = spec.max_choices
            if type(max_choices) is not int or not 0 <= max_choices <= len(options):
                raise ValueError("max choices must be between 0 (any) and the number of options")
        if spec.method == "yes_no":
            threshold = spec.threshold
            if threshold not in THRESHOLDS:
                raise ValueError(f"threshold must be one of {', '.join(THRESHOLDS)}")
        if type(spec.quorum) is not int or spec.quorum < 0:
            raise ValueError("quorum must be zero or a positive number of ballots")
        now = self._s.now()
        opens_at = _optional_time(spec.opens_at, "opening time")
        closes_at = _optional_time(spec.closes_at, "closing time")
        if closes_at is not None and (closes_at <= now or (opens_at is not None and closes_at <= opens_at)):
            raise ValueError("the closing time must be in the future and after the opening time")
        if not isinstance(spec.ref, str) or len(spec.ref) > 128:
            raise ValueError("reference must be at most 128 characters")
        if spec.board is not None and not any(board["id"] == spec.board and not board.get("archived")
                                              for board in self._discussion.boards()):
            raise ValueError("choose an open discussion board")
        ballot_id = secrets.token_hex(8)
        ballot = {
            "id": ballot_id, "owner": owner, "ref": spec.ref, "title": title, "description": description,
            "method": spec.method, "options": options, "seats": seats, "max_choices": max_choices,
            "threshold": threshold, "quorum": spec.quorum, "electorate": electorate, "visibility": spec.visibility,
            "status": "draft", "opens_at": opens_at, "closes_at": closes_at, "board": spec.board, "topic": None,
            "created_by": actor["uuid"], "created_at": now, "unsealed": [],
        }
        if not self._s.store.create("ballots", ballot_id, ballot):
            raise RuntimeError("ballot ID collision")
        if spec.board is not None:
            try:
                topic = self._discussion.create_topic(spec.board, title, description or title, actor, BALLOT_KIND,
                                                      ballot_id)
            except BaseException:
                self._s.store.delete("ballots", ballot_id)
                raise
            self._s.store.update("ballots", ballot_id, lambda current: {**(current or ballot), "topic": topic["id"]})
        self._s.audit.record(actor["uuid"], "ballot.created", f"ballots/{ballot_id}", None,
                             {"title": title, "method": spec.method, "visibility": spec.visibility,
                              "electorate": electorate}, {"owner": owner})
        return self.public(self.advance(ballot_id))

    def _get(self, ballot_id: str, owner: str | None) -> dict[str, Any]:
        ballot = self.advance(ballot_id)
        if owner is not None and ballot.get("owner") != owner:
            raise LookupError("ballot not found")
        return ballot

    def _transition(self, ballot_id: str, owner: str | None, actor: Mapping[str, Any], action: str,
                    change: Callable[[dict[str, Any], int], dict[str, Any]],
                    extra: dict[str, Any] | None = None) -> dict[str, Any]:
        self._get(ballot_id, owner)
        now = self._s.now()

        def apply(current: dict[str, Any] | None) -> dict[str, Any]:
            if current is None:
                raise LookupError("ballot not found")
            return change(current, now)

        ballot = self._s.store.update("ballots", ballot_id, apply)
        assert ballot is not None
        self._s.audit.record(actor["uuid"], action, f"ballots/{ballot_id}", None, {"status": ballot["status"]}, extra)
        return self.public(ballot)

    def open(self, ballot_id: str, owner: str | None, actor: Mapping[str, Any]) -> dict[str, Any]:
        def change(current: dict[str, Any], now: int) -> dict[str, Any]:
            if current["status"] != "draft":
                raise BallotConflict(f"the ballot is already {current['status']}")
            if current.get("closes_at") is not None and current["closes_at"] <= now:
                raise BallotConflict("the closing time has passed")
            return {**current, "status": "open", "opened_at": now}

        return self._transition(ballot_id, owner, actor, "ballot.opened", change)

    def close(self, ballot_id: str, owner: str | None, actor: Mapping[str, Any]) -> dict[str, Any]:
        def change(current: dict[str, Any], now: int) -> dict[str, Any]:
            if current["status"] != "open":
                raise BallotConflict(f"the ballot is {current['status']}, not open")
            return {**current, "status": "closed", "closed_at": now}

        return self._transition(ballot_id, owner, actor, "ballot.closed", change)

    def cancel(self, ballot_id: str, owner: str | None, actor: Mapping[str, Any], reason: str) -> dict[str, Any]:
        reason = _clean_line(reason, 500, "reason")

        def change(current: dict[str, Any], now: int) -> dict[str, Any]:
            if current["status"] not in ("draft", "open"):
                raise BallotConflict(f"a {current['status']} ballot cannot be cancelled")
            return {**current, "status": "cancelled", "cancelled_at": now, "cancel_reason": reason}

        return self._transition(ballot_id, owner, actor, "ballot.cancelled", change, {"reason": reason})

    def advance(self, ballot_id: str) -> dict[str, Any]:
        """Apply scheduled opening/closing lazily, then count once the closing grace period has passed."""
        if not _BALLOT_ID.fullmatch(ballot_id or ""):
            raise LookupError("ballot not found")
        now = self._s.now()
        actions: list[str] = []

        def step(current: dict[str, Any] | None) -> dict[str, Any] | None:
            if current is None:
                raise LookupError("ballot not found")
            actions.clear()
            new = dict(current)
            if new["status"] == "draft" and new.get("opens_at") is not None and now >= new["opens_at"]:
                new.update(status="open", opened_at=new["opens_at"])
                actions.append("ballot.opened")
            if new["status"] == "open" and new.get("closes_at") is not None and now >= new["closes_at"]:
                new.update(status="closed", closed_at=new["closes_at"])
                actions.append("ballot.closed")
            return new if actions else None

        ballot = self._s.store.update("ballots", ballot_id, step)
        assert ballot is not None
        for action in actions:
            self._s.audit.record("system", action, f"ballots/{ballot_id}", None, {"status": ballot["status"]},
                                 {"scheduled": True})
        return self._ensure_record(ballot, now)

    def _ensure_record(self, ballot: dict[str, Any], now: int) -> dict[str, Any]:
        if ballot["status"] != "closed":
            return ballot
        if ballot.get("record"):
            return self._anchor_record(ballot) if not ballot["record"].get("anchor") else ballot
        if now < int(ballot["closed_at"]) + GRACE:
            return ballot
        ballot_id = ballot["id"]
        votes = self._s.store.list("ballot_votes", ballot=ballot_id)
        receipts = len(self._s.store.list("ballot_receipts", ballot=ballot_id))
        leaves = sorted(vote["leaf"] for vote in votes)
        option_ids = [option["id"] for option in ballot["options"]]
        result = tally(ballot["method"], option_ids, ballot["seats"], ballot["threshold"], ballot["quorum"],
                       [list(vote["selection"]) for vote in votes])
        committed = {
            "v": RECORD_TAG, "ballot": ballot_id, "title": ballot["title"], "method": ballot["method"],
            "options": ballot["options"], "seats": ballot["seats"], "threshold": ballot["threshold"],
            "quorum": ballot["quorum"], "visibility": ballot["visibility"], "closed_at": ballot["closed_at"],
            "count": len(leaves), "receipts": receipts, "root": merkle_root(leaves), "result": result,
        }
        digest = record_digest(committed)
        wrote: list[bool] = [False]

        def store_record(current: dict[str, Any] | None) -> dict[str, Any] | None:
            if current is None:
                raise LookupError("ballot not found")
            wrote[0] = not current.get("record")
            if not wrote[0]:
                return None
            return {**current, "record": {"committed": committed, "digest": digest, "leaves": leaves,
                                          "anchor": None, "tallied_at": now}}

        stored = self._s.store.update("ballots", ballot_id, store_record)
        assert stored is not None
        if not wrote[0]:
            return stored
        self._s.audit.record("system", "ballot.tallied", f"ballots/{ballot_id}", None,
                             {"digest": digest, "count": len(leaves)}, {"receipts": receipts})
        return self._anchor_record(stored)

    def _anchor_record(self, ballot: dict[str, Any]) -> dict[str, Any]:
        record = ballot["record"]
        try:
            anchor_id = self._s.anchor.submit("ballot-result", record["digest"], {"ballot": ballot["id"]})
        except Exception:
            return ballot
        anchored = self._s.store.update(
            "ballots", ballot["id"],
            lambda current: None if current is None or current["record"].get("anchor") else
            {**current, "record": {**current["record"], "anchor": anchor_id}})
        return anchored or ballot

    # Reading

    def _anchor_status(self, ballot: dict[str, Any]) -> dict[str, Any] | None:
        record = ballot.get("record")
        if not record:
            return None
        anchor_id = record.get("anchor")
        return self._s.anchor.status(anchor_id) if anchor_id else None

    def public(self, ballot: dict[str, Any]) -> dict[str, Any]:
        record = ballot.get("record")
        out = {key: ballot.get(key) for key in (
            "id", "owner", "ref", "title", "description", "method", "options", "seats", "max_choices", "threshold",
            "quorum", "electorate", "visibility", "status", "opens_at", "closes_at", "opened_at", "closed_at",
            "cancelled_at", "cancel_reason", "board", "topic", "created_at")}
        out["electorate_label"] = self._electorate_label(ballot["electorate"])
        out["notice"] = NOTICES[ballot["visibility"]]
        out["unsealed"] = [{key: entry.get(key) for key in ("at", "by_name", "reason")}
                           for entry in ballot.get("unsealed", [])]
        out["counting"] = ballot["status"] == "closed" and not record
        out["results"] = record["committed"]["result"] if record else None
        out["record"] = ({"digest": record["digest"], "root": record["committed"]["root"],
                          "count": record["committed"]["count"], "anchor": self._anchor_status(ballot)}
                         if record else None)
        return out

    def _receipt_key(self, ballot_id: str, uuid: str) -> str:
        return self._s.integrity.digest("ballot-receipt", f"{ballot_id}\0{uuid}".encode())

    def _turnout(self, ballot_id: str) -> int:
        return len(self._s.store.list("ballot_receipts", ballot=ballot_id))

    def _readable(self, ballot: dict[str, Any], account: Mapping[str, Any]) -> tuple[bool, bool]:
        """(official, eligible); raises LookupError when the account may not see the ballot at all."""
        official = self._s.auth.has_permission(account, PERM_ELECTIONS_ADMIN)
        eligible = self.eligible(ballot, account)
        topic = ballot.get("topic")
        if not (official or eligible or (topic and self._discussion.can_read(topic, account))):
            raise LookupError("ballot not found")
        return official, eligible

    def view(self, ballot_id: str, account: Mapping[str, Any]) -> dict[str, Any]:
        ballot = self.advance(ballot_id)
        official, eligible = self._readable(ballot, account)
        receipt = self._s.store.get("ballot_receipts", self._receipt_key(ballot_id, account["uuid"]))
        return {**self.public(ballot), "eligible": eligible, "voted": receipt is not None,
                "receipt": (receipt or {}).get("leaf"), "turnout": self._turnout(ballot_id),
                "manage": official and ballot["owner"] == "voting",
                "can_unseal": official and ballot["visibility"] != "secret"}

    def record(self, ballot_id: str, account: Mapping[str, Any]) -> dict[str, Any]:
        ballot = self.advance(ballot_id)
        self._readable(ballot, account)
        record = ballot.get("record")
        if not record:
            raise BallotConflict("the record is published once the ballot has closed and been counted")
        return {"committed": record["committed"], "digest": record["digest"], "leaves": record["leaves"],
                "anchor": self._anchor_status(ballot)}

    # Voting

    @staticmethod
    def _selection(ballot: Mapping[str, Any], raw: object) -> list[str]:
        ids = [option["id"] for option in ballot["options"]]
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError("selection must be a list of option IDs")
        choice = [str(item) for item in raw]
        if len(set(choice)) != len(choice) or not set(choice) <= set(ids):
            raise ValueError("choose from the listed options, each at most once")
        method = ballot["method"]
        if method in ("yes_no", "single"):
            if len(choice) != 1:
                raise ValueError("choose exactly one option")
        elif method == "approval":
            limit = ballot["max_choices"] or len(ids)
            if len(choice) > limit:
                raise ValueError(f"choose at most {limit} options")
            choice.sort(key=ids.index)
        elif not choice:
            raise ValueError("rank at least one option")
        return choice

    def cast(self, ballot_id: str, account: Mapping[str, Any], raw: object) -> dict[str, Any]:
        ballot = self.advance(ballot_id)
        if ballot["status"] != "open":
            raise BallotConflict("this ballot is not open for voting")
        if not self.eligible(ballot, account):
            raise PermissionError("you are not eligible to vote on this ballot")
        choice = self._selection(ballot, raw)
        secret = ballot["visibility"] == "secret"
        vote_id, salt = secrets.token_hex(16), secrets.token_hex(16)
        leaf = leaf_hash(ballot_id, vote_id, choice, salt)
        receipt_key = self._receipt_key(ballot_id, account["uuid"])
        receipt: dict[str, Any] = {"ballot": ballot_id}
        if not secret:
            receipt["leaf"] = leaf
        if not self._s.store.create("ballot_receipts", receipt_key, receipt):
            raise BallotConflict("you have already voted on this ballot")
        vote: dict[str, Any] = {"ballot": ballot_id, "leaf": leaf, "selection": choice, "salt": salt}
        if not secret:
            vote["voter"] = account["uuid"]
        try:
            if not self._s.store.create("ballot_votes", vote_id, vote):
                raise RuntimeError("vote ID collision")
        except BaseException:
            self._s.store.delete("ballot_receipts", receipt_key)
            raise
        current = self._s.store.get("ballots", ballot_id)
        if current is None or current.get("record") or current["status"] == "cancelled":
            self._s.store.delete("ballot_votes", vote_id)
            self._s.store.delete("ballot_receipts", receipt_key)
            raise BallotConflict("the ballot closed before your vote was recorded")
        self._s.audit.record(account["uuid"], "ballot.voted", f"ballots/{ballot_id}", None, None,
                             {"visibility": ballot["visibility"]})
        return {"receipt": leaf, "kept": not secret}

    # Break glass

    def unseal(self, ballot_id: str, owner: str | None, actor: Mapping[str, Any], reason: str) -> list[dict[str, Any]]:
        ballot = self._get(ballot_id, owner)
        visibility = ballot["visibility"]
        if visibility == "secret":
            raise BallotConflict("a secret ballot stores no link between voters and votes, so it cannot be opened")
        text = _clean_line(reason, 500, "reason") if isinstance(reason, str) and reason.strip() else ""
        if visibility == "sealed" and len(text) < 10:
            raise ValueError("give a reason of at least 10 characters to break the seal")
        labels = {option["id"]: option["label"] for option in ballot["options"]}
        names: dict[str, str | None] = {}
        roll = []
        for vote in self._s.store.list("ballot_votes", ballot=ballot_id):
            voter = str(vote.get("voter") or "")
            if voter not in names:
                names[voter] = self._s.auth.display_name(voter) if voter else None
            roll.append({"voter": voter, "name": names[voter] or "Member",
                         "choices": [labels.get(item, item) for item in vote["selection"]], "receipt": vote["leaf"]})
        roll.sort(key=lambda row: (str(row["name"]).casefold(), row["voter"]))
        target = f"ballots/{ballot_id}"
        if visibility == "sealed":
            entry = {"at": self._s.now(), "by": actor["uuid"],
                     "by_name": self._s.auth.display_name(actor["uuid"]) or "Elections official", "reason": text}
            self._s.store.update("ballots", ballot_id, lambda current: None if current is None else
                                 {**current, "unsealed": [*current.get("unsealed", []), entry]})
            self._s.audit.record(actor["uuid"], "ballot.unsealed", target, None, None,
                                 {"reason": text, "votes": len(roll), "owner": ballot["owner"]})
        else:
            self._s.audit.record(actor["uuid"], "ballot.roll_viewed", target, None, None,
                                 {"reason": text, "votes": len(roll), "owner": ballot["owner"]})
        return roll


class VotingClient:
    """The `VotingService` handed to one consumer plug-in: its own electorates and ballots only."""

    def __init__(self, runtime: VotingRuntime, consumer: str) -> None:
        self._runtime = runtime
        self._consumer = consumer

    def register_electorate(self, identifier: str, electorate: Electorate) -> None:
        self._runtime.register_electorate(self._consumer, identifier, electorate)

    def create_ballot(self, spec: BallotSpec, actor: Mapping[str, Any]) -> dict[str, Any]:
        electorate = MEMBERS if spec.electorate == MEMBERS else f"{self._consumer}:{spec.electorate}"
        return self._runtime.create(self._consumer, spec, actor, electorate)

    def open_ballot(self, ballot_id: str, actor: Mapping[str, Any]) -> dict[str, Any]:
        return self._runtime.open(ballot_id, self._consumer, actor)

    def close_ballot(self, ballot_id: str, actor: Mapping[str, Any]) -> dict[str, Any]:
        return self._runtime.close(ballot_id, self._consumer, actor)

    def cancel_ballot(self, ballot_id: str, actor: Mapping[str, Any], reason: str) -> dict[str, Any]:
        return self._runtime.cancel(ballot_id, self._consumer, actor, reason)

    def ballot(self, ballot_id: str) -> dict[str, Any] | None:
        try:
            return self._runtime.public(self._runtime._get(ballot_id, self._consumer))
        except LookupError:
            return None

    def results(self, ballot_id: str) -> dict[str, Any] | None:
        ballot = self.ballot(ballot_id)
        return ballot["results"] if ballot else None

    def unseal(self, ballot_id: str, actor: Mapping[str, Any], reason: str) -> list[dict[str, Any]]:
        return self._runtime.unseal(ballot_id, self._consumer, actor, reason)


class BallotIn(BaseModel):
    title: str = Field(max_length=400)
    description: str = Field("", max_length=16000)
    method: Literal["yes_no", "single", "approval", "ranked", "stv"]
    options: list[str] = Field(default_factory=list, max_length=MAX_OPTIONS)
    seats: int = Field(1, ge=1, le=MAX_OPTIONS)
    max_choices: int = Field(0, ge=0, le=MAX_OPTIONS)
    threshold: Literal["majority", "two_thirds"] = "majority"
    quorum: int = Field(0, ge=0)
    electorate: str = Field(MEMBERS, max_length=130)
    visibility: Literal["sealed", "open", "secret"]
    opens_at: int | None = Field(None, gt=0)
    closes_at: int | None = Field(None, gt=0)
    board: str | None = Field(None, max_length=48)


class VoteIn(BaseModel):
    selection: list[str] = Field(max_length=MAX_OPTIONS)


class ReasonIn(BaseModel):
    reason: str = Field("", max_length=1000)


def _call(fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc) or "not found") from exc
    except BallotConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except MessageRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class VotingPlugin:
    manifest = MANIFEST

    def __init__(self) -> None:
        self._runtime: VotingRuntime | None = None

    def configure(self, registration: RegistrationContext) -> None:
        discussion: DiscussionService = registration.require("discussion")
        discussion.register_topic_kind(BALLOT_KIND, TopicKind(label="Ballot", panel="ballot"))
        registration.provide("voting", self._build)

    def _build(self, services: PluginServices) -> VotingRuntime:
        self._runtime = VotingRuntime(services, services.provided["discussion"])
        return self._runtime

    def router(self, services: PluginServices) -> APIRouter:
        runtime = self._runtime
        if runtime is None:
            raise RuntimeError("voting services were not built")
        r = APIRouter(prefix="/api/voting")
        verified = services.auth.require_verified()
        official = services.auth.require_permission(PERM_ELECTIONS_ADMIN)

        @r.get("/setup")
        def setup(_a: dict = official):
            boards = [board for board in services.provided["discussion"].boards() if not board.get("archived")]
            return {"methods": list(METHODS), "thresholds": list(THRESHOLDS), "electorates": runtime.electorates(),
                    "boards": boards, "visibilities": [{"id": level, "notice": NOTICES[level]}
                                                       for level in VISIBILITIES]}

        @r.get("/ballots")
        def ballots(_a: dict = official):
            rows = [runtime.public(row) for row in services.store.list("ballots")]
            rows.sort(key=lambda row: int(row["created_at"] or 0), reverse=True)
            return {"ballots": rows}

        @r.post("/ballots")
        def create(b: BallotIn, a: dict = official):
            spec = BallotSpec(title=b.title, method=b.method, options=tuple(b.options), description=b.description,
                              seats=b.seats, max_choices=b.max_choices, threshold=b.threshold, quorum=b.quorum,
                              electorate=b.electorate, visibility=b.visibility, opens_at=b.opens_at,
                              closes_at=b.closes_at, board=b.board or None)
            return _call(runtime.create, "voting", spec, a, b.electorate)

        @r.get("/ballots/{ballot_id}")
        def view(ballot_id: str, a: dict = verified):
            return _call(runtime.view, ballot_id, a)

        @r.post("/ballots/{ballot_id}/vote")
        def vote(ballot_id: str, b: VoteIn, a: dict = verified):
            return _call(runtime.cast, ballot_id, a, b.selection)

        @r.get("/ballots/{ballot_id}/record")
        def record(ballot_id: str, a: dict = verified):
            return _call(runtime.record, ballot_id, a)

        @r.post("/ballots/{ballot_id}/open")
        def open_ballot(ballot_id: str, a: dict = official):
            return _call(runtime.open, ballot_id, "voting", a)

        @r.post("/ballots/{ballot_id}/close")
        def close_ballot(ballot_id: str, a: dict = official):
            return _call(runtime.close, ballot_id, "voting", a)

        @r.post("/ballots/{ballot_id}/cancel")
        def cancel_ballot(ballot_id: str, b: ReasonIn, a: dict = official):
            return _call(runtime.cancel, ballot_id, "voting", a, b.reason)

        @r.post("/ballots/{ballot_id}/unseal")
        def unseal(ballot_id: str, b: ReasonIn, a: dict = official):
            return {"roll": _call(runtime.unseal, ballot_id, None, a, b.reason)}

        return r
