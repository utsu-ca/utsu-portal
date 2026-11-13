"""Shared services and the AGM models: doctests plus the behaviour the portal relies on."""
import doctest

import pytest

from utsu_portal.programs.agm import models as agm
from utsu_portal.services import selection, text


@pytest.mark.parametrize("module", [text, selection, agm])
def test_doctests(module):
    result = doctest.testmod(module)
    assert result.attempted > 0 and result.failed == 0


def _row(uuid, status, ts, email="a@mail.utoronto.ca", **extra):
    return {"uuid": uuid, "status": status, "submitted_at": ts, "email": email, **extra}


def test_newest_verified_wins_and_unverified_is_non_binding():
    rows = [_row("old", "verified", "2026-01-01T00:00:00"), _row("new", "unverified", "2026-02-01T00:00:00")]
    current, changes = selection.select_current(rows)
    assert [r["uuid"] for r in current] == ["old"] and changes == {"new": "non_binding"}


def test_newer_verified_supersedes_and_final_wins():
    rows = [_row("a", "verified", "2026-01-01T00:00:00"), _row("b", "verified", "2026-02-01T00:00:00")]
    assert selection.select_current(rows)[1] == {"a": "superseded"}
    rows = [_row("a", "verified", "2026-01-01T00:00:00", final=True), _row("b", "verified", "2026-02-01T00:00:00")]
    assert selection.select_current(rows)[1] == {"b": "not_considered"}


def test_one_person_per_email_other_people_unaffected():
    rows = [_row("a", "verified", "2026-01-01T00:00:00"), _row("b", "verified", "2026-02-01T00:00:00", email="b@x.ca")]
    assert selection.select_current(rows)[1] == {}


def test_rsvp_flags_and_determinism():
    row = {"first_name": "Ann", "last_name": "Lee", "email": "ann@gmail.com", "submitted_at": "2026-09-01T10:00:00"}
    a, b = agm.build_rsvp(dict(row)), agm.build_rsvp(dict(row))
    assert a["uuid"] == b["uuid"]
    assert set(a["flags"].split(";")) == {"no_data_confirmation", "personal_email"}


def _roster_csv(*rows):
    return "First Name,Last Name,Truncated Student Number,Faculty,Division\n" + "\n".join(rows) + "\n"


def test_roster_import_and_match_over_store():
    from utsu_portal.services import roster
    from utsu_portal.store import MemoryStore
    store = MemoryStore()
    text = _roster_csv("Ann Marie,Lee,xxxxx3161x,ARTSC,UC", "Sam,Lee,xxxxx0001x,ARTSC,TRIN")
    assert roster.import_roster(store, text) == {"imported": True, "count": 2, "checked": 0, "rostered": 0}
    assert roster.import_roster(store, "bad") == {"imported": False, "count": 2}  # kept unless replacing
    r = roster.StoreRoster(store)
    exact = roster.match_person({"full_name": "sam lee", "id": "1000000019"}, r)
    assert exact.verified and exact.level == 2
    fuzzy = roster.match_person({"full_name": "Ann Lee"}, r)  # middle name left out
    assert fuzzy.verified and fuzzy.method == roster.METHOD_FUZZY_NAME and fuzzy.fuzzy_flag
    assert roster.match_person({"full_name": "Zed Nobody"}, r).reason == "name not found"


def test_bad_roster_leaves_previous_one():
    from utsu_portal.services import roster
    from utsu_portal.store import MemoryStore
    store = MemoryStore()
    roster.import_roster(store, _roster_csv("Ann,Lee,xxxxx3161x,ARTSC,UC"))
    with pytest.raises(ValueError):
        roster.import_roster(store, "First Name\nAnn\n", replace_existing=True)
    assert roster.StoreRoster(store).by_name("Ann Lee")


def test_roster_replace_removes_old_students():
    from utsu_portal.services import roster
    from utsu_portal.store import MemoryStore
    store = MemoryStore()
    roster.import_roster(store, _roster_csv("Ann,Lee,xxxxx3161x,ARTSC,UC"))
    roster.import_roster(store, _roster_csv("Bo,Chan,xxxxx1111x,ARTSC,UC"), replace_existing=True)
    r = roster.StoreRoster(store)
    assert not r.by_name("Ann Lee") and r.by_name("Bo Chan")


def test_roster_doctests():
    from utsu_portal.services import roster
    assert doctest.testmod(roster).failed == 0
