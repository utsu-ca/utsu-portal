"""
Central roster check of portal accounts. Programs (AGM, ...) only read the stored result; they never match names.

`check_accounts` runs over every account whenever a roster is uploaded, so the result is a fact in `roster_checks`
(keyed by account uuid). An exact name match (narrowed by the roster-masked digits) counts; fuzzy name
matches do not, since the account already carries its legal name.
"""
from __future__ import annotations

from .roster import METHOD_FUZZY_NAME, StoreRoster, match_person
from .text import normalize_case

COL = "roster_checks"


def check_account(roster, user: dict, roster_digits: str | None = None) -> dict:
    if not roster_digits:
        return {"rostered": False, "roster_uuid": None, "reason": "student number must be confirmed for roster matching"}
    m = match_person({"full_name": f"{user.get('first_name', '')} {user.get('last_name', '')}",
                      "id": roster_digits}, roster)
    ok = m.verified and m.method != METHOD_FUZZY_NAME
    matches = [s for s in roster.by_name(normalize_case(f"{user.get('first_name', '')} {user.get('last_name', '')}"))
               if s.uuid == m.uoft_uuid] if ok else []
    return {"rostered": ok, "roster_uuid": m.uoft_uuid if ok else None,
            "division": matches[0].division if len(matches) == 1 else None,
            "reason": "" if ok else (m.reason or "not on the roster")}


def check_accounts(store, now: int = 0) -> dict:
    roster, total, ok = StoreRoster(store), 0, 0
    for user in store.list("users"):
        uid = user["uuid"]
        digits = (store.get("roster_identifiers", uid) or {}).get("digits")
        res = {"uuid": uid, "checked_at": now, **check_account(roster, user, digits)}
        store.put(COL, uid, res)
        total, ok = total + 1, ok + res["rostered"]
    return {"checked": total, "rostered": ok}


def is_rostered(store, uid: str) -> bool:
    r = store.get(COL, uid)
    return bool(r and r["rostered"])


def check_one(store, uid: str, now: int = 0) -> None:
    """Check a single account (after activation or a name change) against the current roster, if one is loaded."""
    user = store.get("users", uid)
    if user and store.get("roster_meta", "current"):
        digits = (store.get("roster_identifiers", uid) or {}).get("digits")
        store.put(COL, uid, {"uuid": uid, "checked_at": now, **check_account(StoreRoster(store), user, digits)})
