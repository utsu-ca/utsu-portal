"""
Central roster check of portal accounts. Programs (AGM, ...) only read the stored result; they never match names.

`check_accounts` runs over every account whenever a roster is uploaded, so the result is a fact in `roster_checks`
(keyed by account uuid). An exact name match (narrowed by the roster-masked digits) counts; fuzzy name
matches do not, since the account already carries its legal name.

An admin can also grant honorary membership for one membership session (`COL_HONORARY`): the account then counts as a
member for that session without being on the roster. `read_check` merges that into the check as `honorary_session`.

Each result also records the `membership_session` (Fall/Winter/Summer plus year) of the roster that confirmed the
account. An account is an active member only while that is the current session (`membership_status`).
"""
from __future__ import annotations

from .roster import METHOD_FUZZY_NAME, StoreRoster, match_person, membership_session, roster_session
from .text import normalize_case

COL = "roster_checks"
COL_HONORARY = "honorary_members"  # {uuid, session, division, reason, granted_by, granted_at}: one grant per account


def check_account(roster, user: dict, roster_digits: str | None = None, session: str | None = None) -> dict:
    if not roster_digits:
        return {"rostered": False, "roster_uuid": None, "membership_session": None,
                "reason": "student number must be confirmed for roster matching"}
    m = match_person({"full_name": f"{user.get('first_name', '')} {user.get('last_name', '')}",
                      "id": roster_digits}, roster)
    ok = m.verified and m.method != METHOD_FUZZY_NAME
    matches = [s for s in roster.by_name(normalize_case(f"{user.get('first_name', '')} {user.get('last_name', '')}"))
               if s.uuid == m.uoft_uuid] if ok else []
    return {"rostered": ok, "roster_uuid": m.uoft_uuid if ok else None, "membership_session": session if ok else None,
            "division": matches[0].division if len(matches) == 1 else None,
            "reason": "" if ok else (m.reason or "not on the roster")}


def check_accounts(store, now: int = 0) -> dict:
    roster, total, ok, session = StoreRoster(store), 0, 0, roster_session(store)
    for user in store.list("users"):
        uid = user["uuid"]
        digits = (store.get("roster_identifiers", uid) or {}).get("digits")
        res = {"uuid": uid, "checked_at": now, **check_account(roster, user, digits, session)}
        store.put(COL, uid, res)
        total, ok = total + 1, ok + res["rostered"]
    return {"checked": total, "rostered": ok}


def is_rostered(store, uid: str) -> bool:
    r = store.get(COL, uid)
    return bool(r and r["rostered"])


def with_honorary(store, uid: str, check: dict | None) -> dict | None:
    """The roster check with the account's honorary grant (if any) added as `honorary_session`."""
    grant = store.get(COL_HONORARY, uid)
    if not grant:
        return check
    merged = dict(check or {"uuid": uid, "rostered": False, "roster_uuid": None, "membership_session": None,
                            "reason": "not on the roster"})
    merged["honorary_session"] = grant["session"]
    if not merged.get("division"):
        merged["division"] = grant.get("division")
    return merged


def read_check(store, uid: str) -> dict | None:
    return with_honorary(store, uid, store.get(COL, uid))


def _honorary_now(check: dict | None, now: int) -> bool:
    return bool(check and check.get("honorary_session") == membership_session(now))


def is_live_member(check: dict | None, now: int) -> bool:
    """On the roster now (whichever session it is for), or honorary for the current session."""
    return bool(check and check.get("rostered")) or _honorary_now(check, now)


def is_active_member(check: dict | None, now: int) -> bool:
    """Rostered by a roster of the current membership session (a check from before sessions counts as its check time),
    or honorary for the current session."""
    if _honorary_now(check, now):
        return True
    if not check or not check.get("rostered"):
        return False
    return (check.get("membership_session") or membership_session(check.get("checked_at", 0))) == membership_session(now)


def membership_status(store, uid: str, now: int) -> str:
    """"active" while the roster confirming the account is for the current membership session, else "inactive"."""
    return "active" if is_active_member(read_check(store, uid), now) else "inactive"


def check_one(store, uid: str, now: int = 0) -> None:
    """Check a single account (after activation or a name change) against the current roster, if one is loaded."""
    user = store.get("users", uid)
    if user and store.get("roster_meta", "current"):
        digits = (store.get("roster_identifiers", uid) or {}).get("digits")
        store.put(COL, uid, {"uuid": uid, "checked_at": now, **check_account(StoreRoster(store), user, digits, roster_session(store))})
