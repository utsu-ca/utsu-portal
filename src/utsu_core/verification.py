"""Historical SQLite roster adapter; canonical normalization and fuzzy-name helpers live in services."""
import csv
import logging
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from utsu_core.std.csv_safe import open_csv
from utsu_core.services.roster import (
    FUZZY_FLAG, FUZZY_THRESHOLD, MAX_CANDIDATES, METHOD_FUZZY_NAME, METHOD_NAME,
    division_of, first_name_similarity, is_uoft_email,
)
from utsu_core.std.parsing_utils import get_trunc_id
from utsu_core.std.utils import clean_text, normalize_case

logger = logging.getLogger(__name__)


_CHUNK = 300
_SQL_VARIABLES = 500


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
    """
    The outcome of checking one person against uoft_data. `similarity` is set only when the first name was matched
    by `first_name_similarity` rather than exactly; `candidates` are then the closest roster students (best first),
    which include the one matched, and are also given when the fuzzy match could not be decided.
    """
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
        """The review flag for a fuzzy verification, `fuzzy_name_match=0.86`; empty for any other outcome."""
        return f"{FUZZY_FLAG}={self.similarity:.2f}" if self.verified and self.similarity is not None else ""


def decide(name: str, number: str, division: str, by_name: list[tuple[str, int, str]],
           similarity: dict[str, float] | None = None) -> Match:
    """
    Decide one person from the roster rows sharing their name (each a (uoft_data.uuid, trunc_id, division code)
    tuple). A name is always needed: the roster only has the last four digits of a student number, which many
    students share, so a number or a division never identifies anyone alone. They only narrow down the people
    with that name. Every value given has to match the same student, and a wrong value never falls back to the
    others:
      name only                          the name belongs to exactly one student            level 1
      name + student number              both belong to the same student                    level 2
      name + division                    exactly one student of that name is in the division level 2
      name + student number + division   all three belong to the same student              level 3
    A roster student's division is their college or division, or their faculty when they have none (see
    `match_students`); a student with neither never matches a given division.

    `similarity` is given when the rows are not an exact name match but students whose last name agrees and whose
    first name is close (see `first_name_similarity`; it maps each row's uuid to its score). The factors are the
    same, but the name factor is `fuzzy name` and the match carries the similarity of the student it settled on.

    >>> decide("Ann Lee", "1001231617", "", [("u1", 3161, "UC")])
    Match(uoft_uuid='u1', level=2, method='name + student number', reason='', similarity=None, candidates=())
    >>> decide("Ann Lee", "1001231617", "UC", [("u1", 3161, "UC")]).level
    3
    >>> decide("Ann Lee", "", "", [("u1", 3161, "UC")])
    Match(uoft_uuid='u1', level=1, method='name only', reason='', similarity=None, candidates=())
    >>> decide("Sam Lee", "", "TRIN", [("u1", 1, "UC"), ("u2", 2, "TRIN")])
    Match(uoft_uuid='u2', level=2, method='name + division', reason='', similarity=None, candidates=())
    >>> decide("Ann Lee", "1001231617", "", [("u1", 3161, "UC")], {"u1": 0.92})
    Match(uoft_uuid='u1', level=2, method='fuzzy name + student number', reason='', similarity=0.92, candidates=())
    >>> decide("Ann Lee", "", "TRIN", [("u1", 3161, "UC")]).reason
    'division does not match'
    >>> decide("Ann Lee", "1001231617", "TRIN", [("u1", 3161, "UC")]).reason
    'division does not match'
    >>> decide("Ann Lee", "1009999999", "", [("u1", 3161, "UC")]).reason
    'student number does not match'
    >>> decide("", "1001231617", "UC", []).reason
    'no name given'
    """
    if not name:
        return Match(None, None, "", "no name given")
    if not by_name:
        return Match(None, None, "", "name not found")

    # `candidates` starts as the roster students with this name.
    candidates = by_name
    if number:
        trunc = get_trunc_id(number)
        candidates = [student for student in candidates if student[1] == trunc]
        if not candidates:
            return Match(None, None, "", "student number does not match")
    if division:
        candidates = [student for student in candidates if student[2] == division]
        if not candidates:
            return Match(None, None, "", "division does not match")

    # more than one student is still possible: it cannot be decided, so a person has to
    if len(candidates) > 1:
        what = "name and student number" if number else "name"
        within = " in that division" if division else ""
        need = "a student number" if not number else "a division" if not division else "a person to decide"
        return Match(None, None, "", f"{what} shared by {len(candidates)} students{within}; {need} is needed")

    # exactly one student is left; the more things that agreed, the higher the level
    fuzzy = similarity is not None
    factors = ["fuzzy name" if fuzzy else "name"] + ["student number"] * bool(number) + ["division"] * bool(division)
    method = " + ".join(factors) if len(factors) > 1 else METHOD_FUZZY_NAME if fuzzy else METHOD_NAME
    return Match(candidates[0][0], len(factors), method, "", similarity[candidates[0][0]] if fuzzy else None)


def _name_splits(name: str) -> list[tuple[str, str]]:
    """Every way to read a full name as (first names, last name): the last name is one or more trailing words."""
    words = name.split()
    return [(" ".join(words[:k]), " ".join(words[k:])) for k in range(1, len(words))]


def match_students(people: list[dict], cursor: sqlite3.Cursor, name_key: str = "full_name",
                   id_key: str = "id", division_key: str = "division") -> list[Match]:
    """
    Check each person against uoft_data and return one Match per person, in the same order. A person must give a
    name; a student number and a division are optional (see `decide`). Database errors are raised, never reported
    as "not found".

    A name that no roster student has exactly gets a fuzzy second pass, because students often leave out their
    middle names: the last name has to agree exactly (it is taken to be the trailing words of the name, as
    roster last names can have several), and the first names have to be alike (see `first_name_similarity`).
    The students that pass are decided like exact matches (see `decide`); the best `MAX_CANDIDATES` of them are
    kept on the Match, whatever the outcome.
    """
    results: list[Match] = []
    for start in range(0, len(people), _CHUNK):
        chunk = people[start:start + _CHUNK]
        names = [normalize_case(p.get(name_key) or "") for p in chunk]
        numbers = [(p.get(id_key) or "").strip() for p in chunk]
        divisions = [division_of(p.get(division_key) or "") for p in chunk]
        wanted_names = sorted({n for n in names if n})

        def roster_division(division, faculty):
            # a student's division is their college or division; a student with none belongs to their faculty
            # (the Senate form's division codes are colleges and faculties, as in the seats table)
            return division_of(division or "") or division_of(faculty or "")

        by_name: dict[str, list[tuple[str, int, str]]] = {}
        if wanted_names:
            cursor.execute(f"SELECT uuid, trunc_id, full_name, division, faculty FROM uoft_data "
                           f"WHERE full_name IN ({','.join('?' * len(wanted_names))})", wanted_names)
            for uoft_uuid, trunc_id, full_name, division, faculty in cursor.fetchall():
                by_name.setdefault(full_name, []).append((uoft_uuid, trunc_id, roster_division(division, faculty)))

        # students whose last name could be the end of a name that was not found, for the fuzzy pass
        last_names = sorted({last for n in wanted_names if n not in by_name for _, last in _name_splits(n)})
        by_last: dict[str, list[tuple]] = {}
        for i in range(0, len(last_names), _SQL_VARIABLES):
            part = last_names[i:i + _SQL_VARIABLES]
            cursor.execute(f"SELECT uuid, trunc_id, first_name, last_name, division, faculty FROM uoft_data "
                           f"WHERE last_name IN ({','.join('?' * len(part))})", part)
            for uoft_uuid, trunc_id, first_name, last_name, division, faculty in cursor.fetchall():
                by_last.setdefault(last_name, []).append(
                    (uoft_uuid, trunc_id, first_name, last_name, roster_division(division, faculty)))

        for raw, name, number, division in zip(chunk, names, numbers, divisions):
            if not name and str(raw.get(name_key) or "").strip():
                # a name was given, and nothing of it is plain ASCII (e.g. another script)
                results.append(Match(None, None, "", "name has no plain letters (only A-Z letters are supported)"))
                continue
            if name in by_name or not name:
                results.append(decide(name, number, division, by_name.get(name, [])))
                continue
            scored = []
            for given, last in _name_splits(name):
                for uoft_uuid, trunc_id, first_name, last_name, roster_div in by_last.get(last, []):
                    score = first_name_similarity(given, first_name)
                    if score:
                        scored.append((score, uoft_uuid, trunc_id, first_name, last_name, roster_div))
            if not scored:
                results.append(decide(name, number, division, []))
                continue
            scored.sort(key=lambda s: (-s[0], s[3], s[1]))
            match = decide(name, number, division, [(u, t, d) for _, u, t, _, _, d in scored],
                           {u: score for score, u, *_ in scored})
            candidates = tuple(Candidate(u, f"{first} {last}", f"xxxxx{t:04d}x", d, score)
                               for score, u, t, first, last, d in scored[:MAX_CANDIDATES])
            results.append(replace(match, candidates=candidates))
    return results


def truncated_numbers(cursor: sqlite3.Cursor, uoft_uuids) -> dict[str, str]:
    """
    The roster's truncated student number (`xxxxx3971x`) for each uoft_data uuid, read from the database. A uuid
    that is not there, or has no number, is left out.
    """
    wanted = sorted({u for u in uoft_uuids if u})
    found: dict[str, str] = {}
    for start in range(0, len(wanted), _SQL_VARIABLES):
        chunk = wanted[start:start + _SQL_VARIABLES]
        cursor.execute(f"SELECT uuid, trunc_id FROM uoft_data WHERE uuid IN ({','.join('?' * len(chunk))})", chunk)
        found.update({u: f"xxxxx{t:04d}x" for u, t in cursor.fetchall() if t is not None})
    return found


def construct_lookup_tables(mapping_file: Path) -> list[dict[str, str]]:
    # ensure the file exists
    if not mapping_file.exists():
        raise FileNotFoundError(f"Mapping file not found: {mapping_file}")

    name_table_mapping = {}
    table_name_mapping = {}
    # the csv module handles quoted commas; skipinitialspace allows `"a", "b"` with a space after the comma
    with open(mapping_file, 'r', newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f, skipinitialspace=True)
        header = next(reader, [])  # skip the header row
        logger.info(f"Processing lookup table from {mapping_file}; header: {header}")
        for row in reader:
            row = [_.strip() for _ in row]
            if len(row) >= 2 and row[0]:
                name_table_mapping[row[0]] = row[1]
                table_name_mapping[row[1]] = row[0]
    if not name_table_mapping:
        raise ValueError(f"Lookup table {mapping_file} has no 'form column, replacement name' rows")

    return [name_table_mapping, table_name_mapping]


def setup_reader(csv_file: Path, mapping_file: Path, optional=(), identity=()):
    """
    Read a form export with its columns renamed by the lookup table.

    `optional` are replacement names whose form column may be missing from the export (the program then works
    without them). `identity` are the replacement names that identify a person: at least one must be present.
    """
    name_table_mapping, table_name_mapping = construct_lookup_tables(mapping_file)

    # ensure the CSV file exists
    if not csv_file.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_file}")

    required_col = {form for form, name in name_table_mapping.items() if name not in set(optional)}
    f_in = open_csv(csv_file)
    reader = csv.reader(f_in)
    headers = next(reader, None)
    if not headers or not any(h.strip() for h in headers):
        f_in.close()
        raise ValueError(f"{csv_file} is empty: it has no header row. Export the full form responses again.")

    # check if required columns are present in the headers; padding such as the form's "Faculty " is ignored
    headers = [clean_text(h) for h in headers]
    if not required_col.issubset(set(headers)):
        missing_cols = required_col - set(headers)
        f_in.close()
        raise ValueError(f"Missing required names in columns: {missing_cols}")

    # replace current headers with new names; columns not in the mapping keep their original header so
    # the remaining columns stay aligned
    new_headers = []
    for key in headers:
        if name_table_mapping.get(key):
            new_headers.append(name_table_mapping[key])
        else:
            new_headers.append(key)
            logger.debug(f" {key} not in mapping; keeping its original name")

    if identity and not set(identity) & set(new_headers):
        f_in.close()
        raise ValueError(f"The export needs at least one of these columns to identify people: "
                         f"{', '.join(name_table_mapping_forms(name_table_mapping, identity))}")

    # replace reader
    return csv.DictReader(f_in, fieldnames=new_headers)


def mapped_names(mapping_file: Path) -> list[str]:
    """The replacement names a lookup table gives the form's columns: the fields the program uses from a form."""
    return sorted(construct_lookup_tables(mapping_file)[1])


def name_table_mapping_forms(name_table_mapping: dict[str, str], names) -> list[str]:
    """The form column headers behind some replacement names (a name with no lookup row is its own header)."""
    by_name = {name: form for form, name in name_table_mapping.items()}
    return [by_name.get(n, n) for n in names]
