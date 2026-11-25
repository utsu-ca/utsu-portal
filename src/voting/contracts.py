"""Implementation-free contracts for plug-ins that run ballots. A consumer lists `voting` in its manifest
`dependencies`, adds `provided` to its services, registers electorates with `registration.require("voting")` and
runs ballots through `services.provided["voting"]`. Consumers import this module, never the plug-in."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

METHODS = ("yes_no", "single", "approval", "ranked", "stv")
VISIBILITIES = ("sealed", "open", "secret")
THRESHOLDS = ("majority", "two_thirds")
MEMBERS = "members"


@dataclass(frozen=True)
class Electorate:
    """
    Who may vote. `is_eligible(account)` is called for every vote; raising or returning False refuses it.
    A `scoped` electorate belongs to one subject of its consumer (for example one club): a ballot names it as
    `{identifier}/{scope}`, `scope_exists(scope)` must accept that scope when the ballot is created, and
    `is_eligible(account, scope)` is called with it, so eligibility in one scope never carries over to another.
    """

    description: str
    is_eligible: Callable[..., bool]
    scoped: bool = False
    scope_exists: Callable[[str], bool] | None = None


@dataclass(frozen=True)
class BallotSpec:
    """`visibility` decides who can ever link a vote to its voter (fixed once the ballot is created):
    `sealed` keeps an encrypted link only an elections official can open, with a recorded reason;
    `open` is a recorded vote (for example dietary choices organizers must act on);
    `secret` stores no link at all, so nobody can ever see who voted for what."""

    title: str
    method: str
    options: tuple[str, ...] = ()
    description: str = ""
    seats: int = 1
    max_choices: int = 0  # approval: 0 allows every option
    threshold: str = "majority"  # yes_no only
    quorum: int = 0
    electorate: str = MEMBERS  # `members`, or an electorate this consumer registered
    visibility: str = "sealed"
    opens_at: int | None = None
    closes_at: int | None = None
    board: str | None = None  # discussion board for the ballot's topic
    ref: str = ""


class VotingService(Protocol):
    def register_electorate(self, identifier: str, electorate: Electorate) -> None: ...
    def create_ballot(self, spec: BallotSpec, actor: Mapping[str, Any]) -> dict[str, Any]: ...
    def open_ballot(self, ballot_id: str, actor: Mapping[str, Any]) -> dict[str, Any]: ...
    def close_ballot(self, ballot_id: str, actor: Mapping[str, Any]) -> dict[str, Any]: ...
    def cancel_ballot(self, ballot_id: str, actor: Mapping[str, Any], reason: str) -> dict[str, Any]: ...
    def ballot(self, ballot_id: str) -> dict[str, Any] | None: ...
    def results(self, ballot_id: str) -> dict[str, Any] | None: ...
    def unseal(self, ballot_id: str, actor: Mapping[str, Any], reason: str) -> list[dict[str, Any]]: ...
