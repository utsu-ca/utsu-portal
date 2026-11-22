"""Deterministic counting. Exact fractions, no randomness: a tie that earlier rounds cannot break is reported."""
from __future__ import annotations

from fractions import Fraction
from typing import Any


def _yes_no(selections: list[list[str]], threshold: str) -> dict[str, Any]:
    counts = {"yes": 0, "no": 0, "abstain": 0}
    for selection in selections:
        counts[selection[0]] += 1
    decided = counts["yes"] + counts["no"]
    if threshold == "two_thirds":
        carried = decided > 0 and 3 * counts["yes"] >= 2 * decided
    else:
        carried = counts["yes"] > counts["no"]
    return {"counts": counts, "outcome": "carried" if carried else "defeated"}


def _plurality(selections: list[list[str]], options: list[str], seats: int) -> dict[str, Any]:
    counts = {option: 0 for option in options}
    for selection in selections:
        for option in selection:
            counts[option] += 1
    ranked = sorted(options, key=lambda option: (-counts[option], options.index(option)))
    if seats >= len(ranked) or counts[ranked[seats - 1]] != counts[ranked[seats]]:
        return {"counts": counts, "elected": ranked[:seats], "tied": [], "outcome": "decided"}
    cutoff = counts[ranked[seats - 1]]
    return {"counts": counts, "elected": [option for option in ranked if counts[option] > cutoff],
            "tied": [option for option in ranked if counts[option] == cutoff], "outcome": "tie"}


def _break_backwards(tied: list[str], history: list[dict[str, Fraction]]) -> list[str]:
    for earlier in reversed(history):
        lowest = min(earlier[candidate] for candidate in tied)
        tied = [candidate for candidate in tied if earlier[candidate] == lowest]
        if len(tied) == 1:
            break
    return tied


def _stv(selections: list[list[str]], options: list[str], seats: int) -> dict[str, Any]:
    """Droop quota with simultaneous election and weighted inclusive Gregory surplus transfers. With one seat this
    is instant-runoff voting. Exhausted ballots drop out; a lowest-candidate tie is broken backwards through earlier
    rounds, and a tie that remains is reported for the officials to resolve."""
    ballots = [tuple(selection) for selection in selections if selection]
    quota = Fraction(len(ballots) // (seats + 1) + 1)
    weights = [Fraction(1)] * len(ballots)
    hopeful = list(options)
    elected: list[str] = []
    rounds: list[dict[str, Any]] = []
    history: list[dict[str, Fraction]] = []
    while len(elected) < seats:
        if len(elected) + len(hopeful) <= seats:
            rounds.append({"elected": list(hopeful), "remaining": True})
            elected.extend(hopeful)
            hopeful = []
            break
        continuing = set(hopeful)
        tally = {candidate: Fraction(0) for candidate in hopeful}
        holder: list[str | None] = [None] * len(ballots)
        exhausted = Fraction(0)
        for index, preferences in enumerate(ballots):
            if weights[index] == 0:
                continue
            top = next((choice for choice in preferences if choice in continuing), None)
            if top is None:
                exhausted += weights[index]
            else:
                tally[top] += weights[index]
                holder[index] = top
        history.append(tally)
        current: dict[str, Any] = {"tally": {candidate: str(value) for candidate, value in tally.items()},
                                   "exhausted": str(exhausted)}
        reached = sorted((candidate for candidate in hopeful if tally[candidate] >= quota),
                         key=lambda candidate: (-tally[candidate], options.index(candidate)))
        if reached:
            for candidate in reached:
                factor = (tally[candidate] - quota) / tally[candidate]
                for index in range(len(ballots)):
                    if holder[index] == candidate:
                        weights[index] *= factor
                hopeful.remove(candidate)
                elected.append(candidate)
            current["elected"] = reached
        else:
            lowest = min(tally.values())
            tied = _break_backwards([c for c in hopeful if tally[c] == lowest], history[:-1])
            if len(tied) > 1:
                current["tied"] = tied
                rounds.append(current)
                return {"quota": str(quota), "valid": len(ballots), "rounds": rounds, "elected": elected,
                        "tied": tied, "outcome": "tie"}
            hopeful.remove(tied[0])
            current["excluded"] = tied[0]
        rounds.append(current)
    return {"quota": str(quota), "valid": len(ballots), "rounds": rounds, "elected": elected, "tied": [],
            "outcome": "decided"}


def tally(method: str, options: list[str], seats: int, threshold: str, quorum: int,
          selections: list[list[str]]) -> dict[str, Any]:
    """`selections` are validated option-ID lists (ranked ones in preference order)."""
    if method == "yes_no":
        result = _yes_no(selections, threshold)
    elif method in ("single", "approval"):
        result = _plurality(selections, options, 1 if method == "single" else seats)
    elif method in ("ranked", "stv"):
        result = _stv(selections, options, 1 if method == "ranked" else seats)
    else:
        raise ValueError(f"unknown voting method {method}")
    result["ballots"] = len(selections)
    result["quorum_met"] = len(selections) >= quorum
    if not selections:
        result["outcome"] = "no_votes"
    elif not result["quorum_met"]:
        result["outcome"] = "no_quorum"
    return result
