#!/usr/bin/env python3
"""Seed a running development portal with sample applicants, form submissions and bursary applications.

This is an external client. It makes the same HTTP calls a browser does, signs in as the development admin and
reads sign-in codes from the dev mail inbox. It never imports the portal or touches its database, so it works on
any running instance (the Docker dev stack, `uvicorn`, or a test server) and exercises the real validation.

    python testing/seed_portal.py                                   # AGM, Senate and SABP, 30 submissions each
    python testing/seed_portal.py --only senate sabp --per-program 10
    python testing/seed_portal.py --url http://127.0.0.1:8000 --mail-url http://127.0.0.1:8025

What it does, in order:
  1. generates a fake roster and fake form exports (testing/generate_fake_*.py) in a temporary folder;
  2. imports the roster (only when the instance has none);
  3. for each program that is enabled on the instance, picks applicants whose roster entry verifies, registers and
     verifies a student account for each, and files their application (SABP applications also get banking details).

Running it again is safe: existing accounts are signed in instead of re-registered and submissions are overwritten
with the same content. Auth endpoints are rate limited per client address (30 a minute by default); the client
waits as the server asks. For a faster run start the dev stack with PORTAL_RATE_LIMIT_AUTH=0.

Requirements: a local instance with PORTAL_DEV_ADMIN=1 (it signs in as admin@dev-local.test) and its mail going to
the dev mail sink. Refuses a non-local address unless --allow-remote is given.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from pathlib import Path
from random import Random
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from generate_fake_agm import MEETING
from generate_fake_data import DEFAULT_ENCODING, DEFAULT_SEED, load_students
from generate_fake_sabp import BURSARY_LC
from generate_fake_senate import DELEGATE, TIERS

ROOT = Path(__file__).resolve().parents[1]
DEV_DOMAIN = "dev-local.test"
SCAN = "data:image/png;base64,iVBORyBmYWtl"  # a stand-in image; the portal checks the type, not the picture
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

# program -> (form id, plug-in id that must be enabled, generated export)
PROGRAMS = {
    "agm": ("agm_rsvp", "agm", "fake_agm.csv"),
    "senate": ("senate_application", "senate", "fake_senator.csv"),
    "sabp": ("student_aid_bursary", "sap", "fake_sabp.csv"),
}
WFNI_FIELDS = {
    "disability_mobility": "Disability Status - Mobility", "disability_invisible": "Disability Status - Invisible",
    "living_situation": "Living Situation", "disposable_income": "Access to Disposable Income",
    "liquidity": "Liquidity", "dependents": "Number of Dependents and/or Remittance members",
    "external_supports": "Access to External Supports", "circumstances": "Extenuating Circumstances",
}


class SeedError(RuntimeError):
    """The instance cannot be seeded, or answered unexpectedly."""


class PortalError(SeedError):
    def __init__(self, method: str, path: str, status: int | None, detail: str) -> None:
        super().__init__(f"{method} /api/{path}: {status if status is not None else 'no response'} {detail}".strip())
        self.status, self.detail = status, detail


def say(message: str) -> None:
    print(message, flush=True)


class Api:
    """The portal's JSON API. Waits and retries when a rate limit is reached."""

    def __init__(self, base: str, timeout: float = 30, max_wait: float = 120) -> None:
        self.base, self.timeout, self.max_wait = base.rstrip("/"), timeout, max_wait

    def call(self, method: str, path: str, data: object | None = None, token: str | None = None) -> dict:
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(data).encode() if data is not None else None
        for _ in range(20):
            request = Request(f"{self.base}/api/{path}", data=body, method=method, headers=headers)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else {}
            except HTTPError as exc:
                text = exc.read().decode(errors="replace")
                if exc.code == 429:
                    wait = min(float(exc.headers.get("Retry-After") or 60), self.max_wait)
                    say(f"  rate limited; waiting {wait:.0f}s (start the stack with PORTAL_RATE_LIMIT_AUTH=0 to avoid this)")
                    time.sleep(wait)
                    continue
                raise PortalError(method, path, exc.code, text[:300]) from None
            except URLError as exc:
                raise PortalError(method, path, None, str(exc.reason)) from None
        raise PortalError(method, path, 429, "still rate limited after repeated waits")


class Inbox:
    """The dev mail sink's HTTP inbox, where the portal's sign-in codes arrive."""

    def __init__(self, base: str, timeout: float = 20) -> None:
        self.base, self.timeout = base.rstrip("/"), timeout

    def _messages(self, email: str) -> list[dict]:
        try:
            with urlopen(f"{self.base}/messages?to={quote(email)}", timeout=10) as response:
                return json.loads(response.read())
        except (HTTPError, URLError) as exc:
            raise SeedError(f"cannot read the mail inbox at {self.base} ({exc}); pass --mail-url") from None

    def newest_id(self, email: str) -> int:
        return max((message["id"] for message in self._messages(email)), default=0)

    def code_after(self, email: str, newest: int) -> str:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            for message in sorted(self._messages(email), key=lambda item: item["id"], reverse=True):
                found = re.search(r"code is (\d{6})", message.get("text", ""))
                if message["id"] > newest and found:
                    return found.group(1)
            time.sleep(0.25)
        raise SeedError(f"no sign-in code arrived for {email}; is the portal mailing the dev mail sink at {self.base}?")


def fold(text: str) -> str:
    """Comparable form of a name: ASCII punctuation, single spaces, no case."""
    text = unicodedata.normalize("NFKC", text)
    for dash in "\u2010\u2011\u2012\u2013\u2014\u2212":
        text = text.replace(dash, "-")
    for quote_mark in "\u2018\u2019\u02bc":
        text = text.replace(quote_mark, "'")
    return " ".join(text.split()).casefold()


def student_digits(number: str) -> int | None:
    """The four digits of a student number the roster keeps (matches the portal's roster rules)."""
    number = "".join((number or "").split())
    if len(number) == 4:
        digits = number
    elif number.endswith("x"):
        digits = number.strip("x")
    elif len(number) > 8:
        digits = number[-5:-1]
    else:
        return None
    return int(digits) if len(digits) == 4 and digits.isdigit() else None


class Seeder:
    def __init__(self, api: Api, inbox: Inbox, domain: str = DEV_DOMAIN) -> None:
        self.api, self.inbox, self.domain = api, inbox, domain
        self.admin = ""

    def sign_in(self, email: str) -> str:
        newest = self.inbox.newest_id(email)
        self.api.call("POST", "auth/login-email", {"email": email})
        code = self.inbox.code_after(email, newest)
        return self.api.call("POST", "auth/token-email", {"email": email, "code": code})["token"]

    def sign_in_admin(self) -> None:
        email = f"admin@{self.domain}"
        try:
            self.admin = self.sign_in(email)
        except SeedError as exc:
            # The portal never says whether an account exists, so a missing code and a refused request look alike.
            raise SeedError(
                f"cannot sign in as {email}: {exc}\n"
                "The instance must run with PORTAL_DEV_ADMIN=1 (on a localhost PORTAL_BASE_URL) so that account "
                f"exists, and its mail must reach the dev mail sink read at {self.inbox.base}."
            ) from None

    def enabled_plugins(self) -> set[str]:
        return set(self.api.call("GET", "plugins")["enabled"])

    def import_roster(self, roster: Path, *, mode: str) -> int:
        existing = self.api.call("GET", "admin/roster?limit=1", token=self.admin)["total"]
        if existing and mode == "refuse":
            raise SeedError(
                f"the instance already has a roster of {existing} students. Use --skip-roster if an earlier seed "
                "loaded it (the generated data is deterministic), or --replace-roster to overwrite it."
            )
        if existing and mode == "skip":
            say(f"Keeping the existing roster ({existing} students).")
            return existing
        count = self.api.call("POST", "admin/roster", {"csv": roster.read_text(encoding=DEFAULT_ENCODING)},
                              self.admin)["count"]
        say(f"Imported {count} roster entries.")
        return count

    def ensure_student(self, student: dict) -> tuple[str, bool]:
        """A verified student account: its token, and whether this call registered it. Does only what is missing."""
        digits = student["trunc"]
        email = f"student+{digits}@{self.domain}"
        newest = self.inbox.newest_id(email)
        created = True
        try:
            self.api.call("POST", "auth/register", {
                "email": email, "first_name": student["first"], "last_name": student["last"],
                "student_number": f"12345{digits}0",
            })
        except PortalError as exc:
            if exc.status != 409 or "already registered" not in exc.detail:
                raise
            created = False
        else:
            code = self.inbox.code_after(email, newest)
            self.api.call("POST", "auth/verify-email", {"email": email, "code": code})
        token = self.sign_in(email)
        if self.api.call("GET", "me", token=token).get("status") == "verified":
            return token, created
        challenge = self.api.call("GET", "verification/challenge", token=token)["challenge"]
        verification = self.api.call("POST", "verification", {
            "first_name": student["first"], "last_name": student["last"], "division_codes": ["UTSG"],
            "face": SCAN, "id_card": SCAN, "challenge": challenge,
        }, token)
        self.api.call("POST", f"admin/verifications/{verification['id']}/decision", {"approve": True}, self.admin)
        return token, created


def preference_fields(definition: dict) -> dict[str, str]:
    """Senate seating preference field -> constituency, read from the live form definition."""
    fields = definition["schema"]["fields"]
    return {field["id"]: field["label"].removesuffix(" preference")
            for field in fields if field["id"].startswith("pref_")}


def form_data(form: str, row: dict[str, str], student: dict, email: str, preferences: dict[str, str]) -> dict:
    if form == "agm_rsvp":
        return {"attendance": "In person" if row["Meeting Preference"] == MEETING[0] else "Online",
                "division": "UTSG", "eligible": True, "pronouns": row["Preferred Pronouns"],
                "accessibility": row["Accessibility Requirements"]}
    if form == "senate_application":
        chosen = {field: value if (value := row[f"Constituency Choice [{constituency}]"]) in TIERS else "Not interested"
                  for field, constituency in preferences.items()}
        statement = row["General Statement"].strip()
        return {"division": "UTSG", "college": student["division"] or student["faculty"], "year_of_study": "2",
                "seat_type": "Undergraduate", "delegated": row["Are you an delegated applicant?"] == DELEGATE,
                "statement": statement if len(statement) >= 50 else
                f"{statement} Synthetic statement for local testing only. This fixture is not a real Senate application.".strip(),
                "consent": True, **chosen}
    lc = row["Bursary Type"] == BURSARY_LC
    prefix = "LC" if lc else "AP"
    statement = row[f"{prefix} General Statement"].strip()
    extra = {key: row[column] for key, column in WFNI_FIELDS.items() if row[column]} if lc else {}
    if extra.get("disposable_income", "").startswith("A negative amount"):
        extra["disposable_income"] = "A negative amount"
    return {"terms": row["Application Semester"].split(";") if row["Application Semester"] else ["Fall 2026"],
            "enrolment": "Full-time", "monthly_income": 500, "monthly_expenses": 1500,
            "amount_requested": float(row["LC Requested Amount" if lc else "AP Request Amount"]),
            "hardship": statement if len(statement) >= 50 else
            f"{statement} Synthetic financial hardship for local testing only.".strip(),
            "contact_email": email, "consent": True,
            "bursary_type": "Living-costs Bursary" if lc else "Academic Pursuits Bursary",
            f"{prefix.lower()}_subsidies": [item for item in row[f"{prefix} Subsidies"].split(";") if item], **extra}


def applicant_rows(program: str, csv_dir: Path, by_name: dict[str, list[dict]]) -> dict[str, tuple[dict, dict[str, str]]]:
    """Applicants of one export whose name and student number match exactly one roster student, by student digits."""
    form, _, filename = PROGRAMS[program]
    matched: dict[str, tuple[dict, dict[str, str]]] = {}
    with (csv_dir / filename).open(encoding=DEFAULT_ENCODING, newline="") as stream:
        for row in csv.DictReader(stream):
            if form == "senate_application":
                name = row["Full ACORN-registered Name"]
            else:
                last_header = "ACORN-registered Last Name " if form == "agm_rsvp" else "ACORN-registered Last Name"
                name = f"{row['ACORN-registered First Name']} {row[last_header]}"
            number = row.get("Student Number", "")
            wanted = student_digits(number)
            candidates = [s for s in by_name.get(fold(name), []) if not number or wanted == int(s["trunc"])]
            if len(candidates) == 1:
                matched[candidates[0]["trunc"]] = candidates[0], row
    return matched


def pick_applicants(seeder: Seeder, program: str, matched: dict[str, tuple[dict, dict[str, str]]], count: int,
                    *, verify: bool) -> list[str]:
    """A deterministic sample of applicants. When the instance can check them, only ones the portal verifies count."""
    form = PROGRAMS[program][0]
    order = Random(f"{DEFAULT_SEED}:{form}").sample(sorted(matched), len(matched))
    chosen: list[str] = []
    for digits in order:
        if len(chosen) == count:
            break
        student, row = matched[digits]
        if verify:
            number = row.get("Student Number", "")
            name = f"{student['first']} {student['last']}"
            if not seeder.api.call("POST", "admin/agm/verify", {"full_name": name, "student_number": number},
                                   seeder.admin).get("verified"):
                continue
        chosen.append(digits)
    return chosen


def generate_fixtures(directory: Path, students: int) -> None:
    subprocess.run([sys.executable, str(Path(__file__).with_name("generate_fake_data.py")),
                    "--students", str(students)],
                   check=True, stdout=subprocess.DEVNULL, env={**os.environ, "UTSU_FAKE_OUTPUT_DIR": str(directory)})


def seed(seeder: Seeder, programs: list[str], *, per_program: int, students: int, roster: str) -> dict[str, Counter]:
    enabled = seeder.enabled_plugins()
    skipped = [name for name in programs if PROGRAMS[name][1] not in enabled]
    programs = [name for name in programs if name not in skipped]
    for name in skipped:
        say(f"Skipping {name}: the {PROGRAMS[name][1]} plug-in is not enabled on this instance.")
    if not programs:
        raise SeedError("none of the requested programs is enabled on this instance")
    with tempfile.TemporaryDirectory(prefix="seed-portal-") as tmp:
        csv_dir = Path(tmp)
        say("Generating sample data...")
        generate_fixtures(csv_dir, students)
        roster_file = csv_dir / "fake_uoft_data.csv"
        roster_students = load_students(roster_file)
        if len({s["trunc"] for s in roster_students}) != len(roster_students):
            raise SeedError("the generated roster has duplicate student identifiers")
        by_name: dict[str, list[dict]] = {}
        for student in roster_students:
            by_name.setdefault(fold(f"{student['first']} {student['last']}"), []).append(student)
        seeder.import_roster(roster_file, mode=roster)

        if "sabp" in programs:
            try:
                seeder.api.call("GET", "admin/sap/weights", token=seeder.admin)
            except PortalError as exc:
                if exc.status != 404:
                    raise
                weights = (ROOT / "data/wfni_weights.csv").read_text()
                seeder.api.call("PUT", "admin/sap/weights", {"csv": weights}, seeder.admin)
                say("Saved the default SABP weights.")

        summary: dict[str, Counter] = {}
        accounts: dict[str, tuple[str, str]] = {}
        for program in programs:
            form = PROGRAMS[program][0]
            definition = seeder.api.call("GET", f"forms/{form}/static")
            preferences = preference_fields(definition) if form == "senate_application" else {}
            matched = applicant_rows(program, csv_dir, by_name)
            stats: Counter = Counter(eligible=len(matched))
            if not matched:
                raise SeedError(f"{program}: no generated applicant matched the roster; was a different roster loaded?")
            selected = pick_applicants(seeder, program, matched, per_program, verify="agm" in enabled)
            if not selected:
                raise SeedError(f"{program}: the portal verified none of the generated applicants; was a different "
                                "roster loaded? (use --replace-roster)")
            if len(selected) < per_program:
                say(f"{program}: only {len(selected)} eligible applicants (asked for {per_program}).")
            for index, digits in enumerate(selected, 1):
                student, row = matched[digits]
                if digits not in accounts:
                    token, created = seeder.ensure_student(student)
                    accounts[digits] = f"student+{digits}@{seeder.domain}", token
                    stats["accounts" if created else "existing"] += 1
                email, token = accounts[digits]
                seeder.api.call("PUT", f"forms/{form}/submission",
                                {"version": definition["version"], "hash": definition["hash"],
                                 "data": form_data(form, row, student, email, preferences)}, token)
                stats["submitted"] += 1
                if program == "sabp" and index <= len(selected) // 2:
                    try:
                        seeder.api.call("PUT", "sap/banking", {"institution_number": "000", "transit_number": "12345",
                                                                "account_number": f"12345{digits}"}, token)
                        stats["banking"] += 1
                    except PortalError as exc:
                        # Banking details are write-once, so an earlier seed already left the wanted state.
                        if exc.status != 409 or "already submitted" not in exc.detail:
                            raise
                if index % 10 == 0 or index == len(selected):
                    say(f"  {program}: {index}/{len(selected)}")
            summary[program] = stats
        return summary


def check_target(url: str, allow_remote: bool) -> None:
    host = urlsplit(url).hostname
    if host not in LOOPBACK and not allow_remote:
        raise SeedError(f"{url} is not a local address. This tool files fake applications; "
                        "pass --allow-remote only for a test environment you own.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("PORTAL_URL", "http://127.0.0.1:8000"),
                        help="the portal (default %(default)s)")
    parser.add_argument("--mail-url", default=os.environ.get("PORTAL_MAIL_URL", "http://127.0.0.1:8025"),
                        help="the dev mail sink's web inbox (default %(default)s)")
    parser.add_argument("--only", nargs="+", choices=sorted(PROGRAMS), help="programs to seed (default: all enabled)")
    parser.add_argument("--per-program", type=int, default=30, help="submissions per program (default %(default)s)")
    parser.add_argument("--students", type=int, default=1200,
                        help="size of the generated roster, at least 500 (default %(default)s)")
    parser.add_argument("--code-timeout", type=float, default=20,
                        help="seconds to wait for each sign-in email (default %(default)s)")
    parser.add_argument("--domain", default=DEV_DOMAIN, help="the dev accounts' email domain (default %(default)s)")
    roster = parser.add_mutually_exclusive_group()
    roster.add_argument("--skip-roster", action="store_true", help="keep the roster already on the instance")
    roster.add_argument("--replace-roster", action="store_true", help="overwrite an existing roster")
    parser.add_argument("--allow-remote", action="store_true", help="allow a non-local --url (test environments only)")
    args = parser.parse_args(argv)
    if args.per_program < 1 or args.students < 500:
        parser.error("--per-program must be at least 1 and --students at least 500 (the SABP export needs that many)")
    try:
        check_target(args.url, args.allow_remote)
        seeder = Seeder(Api(args.url), Inbox(args.mail_url, args.code_timeout), args.domain)
        say(f"Seeding {args.url}")
        seeder.sign_in_admin()
        mode = "replace" if args.replace_roster else "skip" if args.skip_roster else "refuse"
        summary = seed(seeder, args.only or list(PROGRAMS), per_program=args.per_program,
                       students=args.students, roster="import" if mode == "replace" else mode)
    except (SeedError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for program, stats in summary.items():
        say(f"{program}: {stats['submitted']} submitted from {stats['eligible']} eligible applicants, "
            f"{stats['accounts']} new accounts, {stats['existing']} already existed"
            + (f", {stats['banking']} with banking" if stats["banking"] else ""))
    say("Done. Sign in with any of the dev accounts, for example admin@" + args.domain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
