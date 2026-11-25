"""Opt-out fee windows, session keys and levy projections; pure helpers with no store access."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")
WINDOW_IDS = ("fall", "summer")
SESSION_KEY = re.compile(r"(fall|summer):([0-9]{4})\Z")
# Fall opt-outs cover the Fall-Winter session; Summer opt-outs cover the Summer session.
DEFAULT_WINDOWS = {
    "fall": {"start_month": 9, "length_months": 1},
    "summer": {"start_month": 5, "length_months": 1},
}

OPTED_OUT, REFUND_REQUESTED, REFUNDED, DENIED, WITHDRAWN = (
    "opted_out", "refund_requested", "refunded", "denied", "withdrawn",
)


def window_months(config: Mapping[str, Any]) -> range:
    return range(config["start_month"], config["start_month"] + config["length_months"])


def valid_window_config(start_month: int, length_months: int) -> bool:
    return 1 <= start_month <= 12 and 1 <= length_months <= 12 and start_month + length_months - 1 <= 12


def session_label(session: str) -> str:
    """
    >>> session_label("fall:2026"), session_label("summer:2027")
    ('Fall-Winter Session 2026-27', 'Summer Session 2027')
    """
    match = SESSION_KEY.fullmatch(session)
    if not match:
        raise ValueError(f"invalid session key: {session}")
    window, year = match.group(1), int(match.group(2))
    return f"Fall-Winter Session {year}-{(year + 1) % 100:02d}" if window == "fall" else f"Summer Session {year}"


def session_order(session: str) -> tuple[int, int]:
    """Chronological sort key: summer (May) falls before fall (September) of the same year."""
    match = SESSION_KEY.fullmatch(session)
    if not match:
        raise ValueError(f"invalid session key: {session}")
    return int(match.group(2)), 1 if match.group(1) == "fall" else 0


def window_state(window_id: str, config: Mapping[str, Any], now: int) -> dict[str, Any]:
    """
    Whether a window is open at `now` (Toronto time), and the occurrence it refers to: the running one while open,
    otherwise the next one. A window opens on the first of `start_month` and closes at the end of its last month.

    >>> cfg = {"start_month": 9, "length_months": 1}
    >>> at = lambda y, m, d: int(datetime(y, m, d, 12, tzinfo=TORONTO).timestamp())
    >>> [(s["open"], s["session"]) for s in (window_state("fall", cfg, at(2026, d[0], d[1]))
    ...  for d in ((8, 31), (9, 15), (9, 30), (10, 1)))]
    [(False, 'fall:2026'), (True, 'fall:2026'), (True, 'fall:2026'), (False, 'fall:2027')]
    """
    start_month, length = config["start_month"], config["length_months"]

    def bounds(year: int) -> tuple[int, int]:
        last = start_month + length  # first month after the window; 13 means January of the next year
        end = datetime(year + (last - 1) // 12, (last - 1) % 12 + 1, 1, tzinfo=TORONTO)
        return int(datetime(year, start_month, 1, tzinfo=TORONTO).timestamp()), int(end.timestamp())

    year = datetime.fromtimestamp(now, TORONTO).year
    opens_at, closes_at = bounds(year)
    if now >= closes_at:
        year += 1
        opens_at, closes_at = bounds(year)
    session = f"{window_id}:{year}"
    return {
        "id": window_id, "start_month": start_month, "length_months": length,
        "open": opens_at <= now < closes_at, "opens_at": opens_at, "closes_at": closes_at,
        "session": session, "session_label": session_label(session),
    }


def levy_summary(fee: Mapping[str, Any], window_id: str, records: Iterable[Mapping[str, Any]],
                 enrolment: int | None) -> dict[str, Any]:
    """
    One fee's levy picture for a session. Opt-outs before payment and granted refunds reduce the levy; requested refunds
    are assumed granted in the prediction but not in the confirmed figure; denied refunds leave the levy untouched.

    >>> fee = {"id": "f", "name": "Fee", "amounts_cents": {"fall": 1000}}
    >>> rows = [{"status": "opted_out", "amount_cents": 1000}, {"status": "refund_requested", "amount_cents": 1000},
    ...         {"status": "refunded", "amount_cents": 900}, {"status": "withdrawn", "amount_cents": 1000}]
    >>> s = levy_summary(fee, "fall", rows, 100)
    >>> (s["gross_cents"], s["confirmed_net_cents"], s["predicted_net_cents"], s["opt_out_count"])
    (100000, 98100, 97100, 3)
    """
    amount = (fee.get("amounts_cents") or {}).get(window_id)
    totals = {OPTED_OUT: [0, 0], REFUND_REQUESTED: [0, 0], REFUNDED: [0, 0], DENIED: [0, 0]}
    for record in records:
        bucket = totals.get(record.get("status"))
        if bucket is not None:
            bucket[0] += 1
            bucket[1] += record["amount_cents"]
    confirmed = totals[OPTED_OUT][1] + totals[REFUNDED][1]
    pending = totals[REFUND_REQUESTED][1]
    opt_outs = totals[OPTED_OUT][0] + totals[REFUND_REQUESTED][0] + totals[REFUNDED][0]
    gross = None if enrolment is None or amount is None else enrolment * amount
    return {
        "fee_id": fee["id"], "name": fee["name"], "amount_cents": amount, "enrolment": enrolment,
        "gross_cents": gross,
        "opted_out": totals[OPTED_OUT][0], "refund_requested": totals[REFUND_REQUESTED][0],
        "refunded": totals[REFUNDED][0], "denied": totals[DENIED][0], "opt_out_count": opt_outs,
        "opt_out_rate": None if not enrolment else round(opt_outs / enrolment, 4),
        "confirmed_reduction_cents": confirmed, "pending_reduction_cents": pending,
        "confirmed_net_cents": None if gross is None else gross - confirmed,
        "predicted_net_cents": None if gross is None else gross - confirmed - pending,
    }
