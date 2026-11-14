"""
Choosing the binding application per person. Pure: rows in, rows and status changes out, nothing stored.

Per person (see `person_key`) the newest *verified* application is binding and older ones are `superseded`; a newer
unverified one never replaces a verified one (`non_binding`); a `final` (completed) application always wins and every
other one of that person becomes `not_considered`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from .text import identity_key

logger = logging.getLogger(__name__)

LIVE = ("verified", "unverified")


def add_flag(app: dict, flag: str) -> None:
    flags = [f for f in (app.get("flags") or "").split(";") if f]
    if flag not in flags:
        flags.append(flag)
    app["flags"] = ";".join(flags)


def submission_rank(row: dict) -> object:
    """When a row was submitted (a datetime or ISO string in `submitted_at`); unreadable or missing ranks oldest."""
    value = row.get("submitted_at")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    try:
        return datetime.fromisoformat(value) if value else datetime.min
    except (TypeError, ValueError):
        return datetime.min


def person_key(row: dict) -> str:
    """
    Whose submission a row is: their email, else student number, else name.

    >>> person_key({"email": "A@x.ca", "id": "1"}) == person_key({"email": "a@x.ca", "student_number": "2"})
    True
    """
    return identity_key(row.get("email", ""), row.get("id") or row.get("student_number") or "",
                        row.get("full_name", ""))


def select_current(rows: list[dict], key_fn: Callable[[dict], str] = person_key,
                   rank_fn: Callable[[dict], object] = submission_rank) -> tuple[list[dict], dict[str, str]]:
    """
    Returns (binding rows in their original order, {uuid: new status} for the rows that lost). Statuses are also
    set on the rows. Rows whose status is not verified/unverified are ignored.
    """
    groups: dict[str, list[tuple]] = {}
    for position, row in enumerate(rows):
        if row.get("status") in LIVE:
            groups.setdefault(key_fn(row), []).append((rank_fn(row), position, row))

    winners, changes = set(), {}
    for members in groups.values():
        members.sort(key=lambda m: (m[0], m[1]))
        final = [m for m in members if m[2].get("final")]
        verified = [m for m in members if m[2]["status"] == "verified"]
        winner = final[-1] if final else (verified or members)[-1]
        winners.add(winner[2]["uuid"])
        for member in members:
            if member is winner:
                continue
            row = member[2]
            if final:
                changes[row["uuid"]] = "not_considered"
            elif (member[0], member[1]) < (winner[0], winner[1]):
                changes[row["uuid"]] = "superseded"
            else:
                changes[row["uuid"]] = "non_binding"
                logger.warning("Unverified submission %s does not replace verified application %s; it is "
                               "non-binding.", row["uuid"], winner[2]["uuid"])
    for row in rows:
        if row.get("uuid") in changes:
            row["status"] = changes[row["uuid"]]
    return [row for row in rows if row.get("uuid") in winners], changes
