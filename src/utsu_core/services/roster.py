"""
The student roster (ported from utsu_core.verification and importing_data) and matching a person against it.

The roster is a fact stored in two lookup collections written at import (`roster_name`, keyed by full name, and
`roster_last`, keyed by last name), so matching one person is two reads and never scans. `match_people` is pure over a
`Roster`, so it runs the same over the store or an in-memory list.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Iterable, Protocol

from .text import UOFT_MAIL_DOMAIN, clean_text, normalize_case, typed_uuid

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("First Name", "Last Name", "Truncated Student Number", "Faculty", "Division")
METHOD_NAME = "name only"
METHOD_FUZZY_NAME = "fuzzy name"
FUZZY_FLAG = "fuzzy_name_match"
MAX_CANDIDATES = 3
# how alike two first names have to be (0 to 1, `difflib` ratio) to be taken as the same name
FUZZY_THRESHOLD = 0.85
COL_NAME, COL_LAST, COL_META = "roster_name", "roster_last", "roster_meta"


def is_uoft_email(email: str) -> bool:
    """
    Only `@mail.utoronto.ca` counts (case and surrounding spaces ignored).

    >>> [is_uoft_email(v) for v in ("ann.lee@mail.utoronto.ca", " Ann@MAIL.UTORONTO.CA ", "ann@utoronto.ca", "", None)]
    [True, True, False, False, False]
    """
    local, _, domain = (email or "").strip().lower().rpartition("@")
    return bool(local) and domain == UOFT_MAIL_DOMAIN and not re.search(r"[\s@]", local)


def division_code(division: str) -> str:
    """
    >>> division_code("TRIN - Trinity College"), division_code(" uc ")
    ('TRIN', 'UC')
    """
    return division.split(" - ")[0].strip().upper()


def division_of(text: str) -> str:
    """
    >>> division_of(" uc - University College "), division_of(None)
    ('UC', '')
    """
    return division_code(clean_text(text))


def get_trunc_id(id_txt: str) -> int | None:
    """
    The 4 digits of a student number the roster keeps, or None when blank or unreadable.

    >>> get_trunc_id("1006639715"), get_trunc_id("xxxxx3971x"), get_trunc_id("3971"), get_trunc_id("12ab")
    (3971, 3971, 3971, None)
    """
    id_txt = clean_text(id_txt or "").replace(" ", "")
    if len(id_txt) == 4:
        trunc = id_txt
    elif id_txt.endswith("x"):
        trunc = id_txt.strip("x")
    elif len(id_txt) > 8:
        trunc = id_txt[-5:-1]
    else:
        return None
    return int(trunc) if len(trunc) == 4 and trunc.isdigit() else None


@dataclass(frozen=True)
class Student:
    """One roster student. `division` is their college/division, or their faculty when they have none."""
    uuid: str
    trunc_id: int | None
    first_name: str
    last_name: str
    division: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def to_list(self) -> list:
        return [self.uuid, self.trunc_id, self.first_name, self.last_name, self.division]


class Roster(Protocol):
    def by_name(self, full_name: str) -> list[Student]: ...
    def by_last(self, last_name: str) -> list[Student]: ...


class MemoryRoster:
    def __init__(self, students: Iterable[Student]) -> None:
        self.students = list(students)

    def by_name(self, full_name):
        return [s for s in self.students if s.full_name == full_name]

    def by_last(self, last_name):
        return [s for s in self.students if s.last_name == last_name]


class StoreRoster:
    """The roster as imported into a `Store` (two reads per person)."""

    def __init__(self, store) -> None:
        self.store = store

    def _bucket(self, col: str, key: str) -> list[Student]:
        doc = self.store.get(col, key)
        return [Student(u, t, f, l, d) for u, t, f, l, d in (doc or {}).get("students", [])]

    def by_name(self, full_name):
        return self._bucket(COL_NAME, full_name)

    def by_last(self, last_name):
        return self._bucket(COL_LAST, last_name)


# --- import -----------------------------------------------------------------------------------------------------------

def _digits(text: str) -> str:
    return "".join(c for c in clean_text(text) if c.isdigit())


def read_roster_rows(text: str) -> list[Student]:
    """
    Students in a roster CSV (text), cleaned: plain ASCII, Title Case names, digits only. Blank lines are skipped, a
    missing column or short row is an error and exact duplicates are kept once.
    """
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    headers = [clean_text(h) for h in next(reader, [])]
    missing = set(REQUIRED_COLUMNS) - set(headers)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    ti, li, fi, fa, di = (headers.index(c) for c in ("Truncated Student Number", "Last Name", "First Name",
                                                    "Faculty", "Division"))
    needed = max(ti, li, fi, fa, di) + 1
    students: dict[str, Student] = {}
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < needed:
            raise ValueError(f"Line {reader.line_num} has {len(row)} column(s); expected at least {needed}")
        first, last = normalize_case(row[fi]), normalize_case(row[li])
        faculty, division = clean_text(row[fa]).upper(), clean_text(row[di]).upper()
        digits = _digits(row[ti])
        uid = typed_uuid(f"{digits}{last}{first}{faculty}{division}", "roster")
        students[uid] = Student(uid, int(digits) if digits.isdigit() else None, first, last,
                                division_of(division) or division_of(faculty))
    return list(students.values())


def import_roster(store, text: str, replace_existing: bool = False, now: int = 0) -> dict:
    """
    Import a roster CSV. The file is checked before anything is written, so a bad file leaves the old roster. Without
    `replace_existing` an existing roster is kept (`imported` is False). Returns {"imported", "count"}.
    """
    meta = store.get(COL_META, "current")
    if meta and not replace_existing:
        return {"imported": False, "count": meta["count"]}
    students = read_roster_rows(text)
    if not students:
        raise ValueError("The roster file has no students")
    by_name: dict[str, list] = {}
    by_last: dict[str, list] = {}
    for s in students:
        by_name.setdefault(s.full_name, []).append(s.to_list())
        by_last.setdefault(s.last_name, []).append(s.to_list())
    for col, buckets in ((COL_NAME, by_name), (COL_LAST, by_last)):
        for old in store.list(col):
            if old["key"] not in buckets:
                store.delete(col, old["key"])
        for key, rows in buckets.items():
            store.put(col, key, {"key": key, "students": rows})
    store.put(COL_META, "current", {"count": len(students), "imported_at": now})
    from .account_check import check_accounts  # the whole-DB account check runs on every new roster
    return {"imported": True, "count": len(students), **check_accounts(store, now)}


# --- matching ---------------------------------------------------------------------------------------------------------

def _name_parts(first_name: str) -> list[str]:
    return [part for part in re.split(r"[\s-]+", first_name.lower()) if part]


def _part_similarity(a: str, b: str) -> float:
    if len(a) == 1 or len(b) == 1:  # an initial stands for the whole name
        return 1.0 if a[0] == b[0] else 0.0
    return SequenceMatcher(None, a, b).ratio()


def first_name_similarity(given: str, roster: str) -> float:
    """
    How alike two first names are, 0 to 1; 0 below `FUZZY_THRESHOLD`. Students leave out middle names, so the first
    parts must agree (allowing a typo) and each part of the shorter name must be found in the longer one.

    >>> [first_name_similarity(*n) for n in (("Ann", "Ann Marie"), ("Ann M", "Ann Marie"), ("Ann Lynn", "Ann Marie"))]
    [1.0, 1.0, 0.0]
    >>> round(first_name_similarity("Jon", "John"), 2), first_name_similarity("Mary-Jane", "Mary Jane")
    (0.86, 1.0)
    """
    a, b = _name_parts(given), _name_parts(roster)
    if not a or not b:
        return 0.0
    shorter, longer = sorted((a[1:], b[1:]), key=len)
    scores = [_part_similarity(a[0], b[0])]
    for part in shorter:
        scores.append(max(_part_similarity(part, other) for other in longer) if longer else 0.0)
    score = min(scores)
    return round(score, 2) if score >= FUZZY_THRESHOLD else 0.0


@dataclass(frozen=True)
class Candidate:
    """A roster student whose name is close to a name that was not found exactly."""
    uoft_uuid: str
    full_name: str
    student_number: str  # truncated, as on the roster: xxxxx3971x
    division: str
    similarity: float


@dataclass(frozen=True)
class Match:
    """The outcome of checking one person against the roster."""
    uoft_uuid: str | None
    level: int | None
    method: str
    reason: str
    similarity: float | None = None
    candidates: tuple[Candidate, ...] = ()

    @property
    def verified(self) -> bool:
        return self.uoft_uuid is not None

    @property
    def fuzzy_flag(self) -> str:
        return f"{FUZZY_FLAG}={self.similarity:.2f}" if self.verified and self.similarity is not None else ""


def decide(name: str, number: str, division: str, by_name: list[tuple[str, int | None, str]],
           similarity: dict[str, float] | None = None) -> Match:
    """
    Decide one person from the roster rows sharing their name, each (uuid, trunc_id, division). A name is always
    needed; a number or division only narrows down people with that name, and a wrong value never falls back.
    Level = how many of name, student number, division agreed.

    >>> decide("Ann Lee", "1001231617", "UC", [("u1", 3161, "UC")]).level
    3
    >>> decide("Ann Lee", "", "", [("u1", 3161, "UC")]).method
    'name only'
    >>> decide("Ann Lee", "1009999999", "", [("u1", 3161, "UC")]).reason
    'student number does not match'
    >>> decide("Ann Lee", "", "TRIN", [("u1", 3161, "UC")]).reason
    'division does not match'
    >>> decide("", "1", "", []).reason
    'no name given'
    """
    if not name:
        return Match(None, None, "", "no name given")
    if not by_name:
        return Match(None, None, "", "name not found")
    candidates = by_name
    if number:
        trunc = get_trunc_id(number)
        candidates = [s for s in candidates if s[1] == trunc]
        if not candidates:
            return Match(None, None, "", "student number does not match")
    if division:
        candidates = [s for s in candidates if s[2] == division]
        if not candidates:
            return Match(None, None, "", "division does not match")
    if len(candidates) > 1:
        what = "name and student number" if number else "name"
        within = " in that division" if division else ""
        need = "a student number" if not number else "a division" if not division else "a person to decide"
        return Match(None, None, "", f"{what} shared by {len(candidates)} students{within}; {need} is needed")
    fuzzy = similarity is not None
    factors = ["fuzzy name" if fuzzy else "name"] + ["student number"] * bool(number) + ["division"] * bool(division)
    method = " + ".join(factors) if len(factors) > 1 else METHOD_FUZZY_NAME if fuzzy else METHOD_NAME
    return Match(candidates[0][0], len(factors), method, "", similarity[candidates[0][0]] if fuzzy else None)


def _name_splits(name: str) -> list[tuple[str, str]]:
    """Every way to read a full name as (first names, last name): the last name is one or more trailing words."""
    words = name.split()
    return [(" ".join(words[:k]), " ".join(words[k:])) for k in range(1, len(words))]


def match_person(person: dict, roster: Roster, name_key: str = "full_name", id_key: str = "id",
                 division_key: str = "division") -> Match:
    """
    Check one person against the roster. A name is required; a student number and division are optional (see
    `decide`). A name no student has exactly gets a fuzzy pass: the last name must agree exactly (the trailing
    words of the name) and the first names must be alike (`first_name_similarity`). The closest `MAX_CANDIDATES`
    are kept on the Match.
    """
    raw = person.get(name_key) or ""
    name = normalize_case(raw)
    number = (person.get(id_key) or "").strip()
    division = division_of(person.get(division_key) or "")
    if not name and str(raw).strip():
        return Match(None, None, "", "name has no plain letters (only A-Z letters are supported)")
    exact = roster.by_name(name) if name else []
    if exact or not name:
        return decide(name, number, division, [(s.uuid, s.trunc_id, s.division) for s in exact])
    scored = []
    for given, last in _name_splits(name):
        for s in roster.by_last(last):
            score = first_name_similarity(given, s.first_name)
            if score:
                scored.append((score, s))
    if not scored:
        return decide(name, number, division, [])
    scored.sort(key=lambda x: (-x[0], x[1].first_name, x[1].uuid))
    match = decide(name, number, division, [(s.uuid, s.trunc_id, s.division) for _, s in scored],
                   {s.uuid: score for score, s in scored})
    candidates = tuple(Candidate(s.uuid, s.full_name, f"xxxxx{(s.trunc_id or 0):04d}x", s.division, score)
                       for score, s in scored[:MAX_CANDIDATES])
    return replace(match, candidates=candidates)


def match_people(people: list[dict], roster: Roster, **keys) -> list[Match]:
    """One Match per person, in order."""
    return [match_person(p, roster, **keys) for p in people]
