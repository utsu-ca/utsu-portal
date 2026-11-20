"""Exercise and persist generated API fixtures for the dev Compose stack."""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from generate_fake_agm import MEETING
from generate_fake_data import DEFAULT_ENCODING, DEFAULT_SEED, load_students
from generate_fake_sabp import BURSARY_LC
from generate_fake_senate import DELEGATE, TIERS
from utsu_core import create_app
from utsu_core.devadmin import DEV_DOMAIN, check_dev_settings
from utsu_core.mailer import OutboxMailer
from utsu_core.programs.senate.module import PREFERENCES
from utsu_core.services.roster import get_trunc_id
from utsu_core.services.text import normalize_case
from utsu_core.settings import Settings
from utsu_core.store import MemoryStore, Store, open_store

ROOT = Path(__file__).resolve().parents[1]
SAMPLED_ACCOUNTS = 1000
ROSTER_SIZE = 1200
SUBMISSIONS_PER_PROGRAM = 30
SCAN = "data:image/png;base64,iVBORyBmYWtl"
FORMS = {
    "agm_rsvp": "fake_agm.csv",
    "senate_application": "fake_senator.csv",
    "student_aid_bursary": "fake_sabp.csv",
}
WFNI_FIELDS = {
    "disability_mobility": "Disability Status - Mobility", "disability_invisible": "Disability Status - Invisible",
    "living_situation": "Living Situation", "disposable_income": "Access to Disposable Income",
    "liquidity": "Liquidity", "dependents": "Number of Dependents and/or Remittance members",
    "external_supports": "Access to External Supports", "circumstances": "Extenuating Circumstances",
}


def form_data(form: str, row: dict[str, str], student: dict, email: str) -> dict:
    if form == "agm_rsvp":
        return {"attendance": "In person" if row["Meeting Preference"] == MEETING[0] else "Online",
                "division": "UTSG", "eligible": True, "pronouns": row["Preferred Pronouns"],
                "accessibility": row["Accessibility Requirements"]}
    if form == "senate_application":
        preferences = {field: value if (value := row[f"Constituency Choice [{choice}]"]) in TIERS else "Not interested"
                       for field, choice in PREFERENCES.items()}
        statement = row["General Statement"].strip()
        return {"division": "UTSG", "college": student["division"] or student["faculty"], "year_of_study": "2",
                "seat_type": "Undergraduate", "delegated": row["Are you an delegated applicant?"] == DELEGATE,
                "statement": statement if len(statement) >= 50 else f"{statement} Synthetic statement for local testing only.".strip()
                + " This fixture is not a real Senate application.", "consent": True, **preferences}
    lc = row["Bursary Type"] == BURSARY_LC
    prefix = "LC" if lc else "AP"
    statement = row[f"{prefix} General Statement"].strip()
    extra = {key: row[column] for key, column in WFNI_FIELDS.items() if row[column]} if lc else {}
    if extra.get("disposable_income", "").startswith("A negative amount"):
        extra["disposable_income"] = "A negative amount"
    return {"terms": row["Application Semester"].split(";") if row["Application Semester"] else ["Fall 2026"],
            "enrolment": "Full-time", "monthly_income": 500, "monthly_expenses": 1500,
            "amount_requested": float(row["LC Requested Amount" if lc else "AP Request Amount"]),
            "hardship": statement if len(statement) >= 50 else f"{statement} Synthetic financial hardship for local testing only.".strip(),
            "contact_email": email, "consent": True, "bursary_type": "Living-costs Bursary" if lc else "Academic Pursuits Bursary",
            f"{prefix.lower()}_subsidies": [item for item in row[f"{prefix} Subsidies"].split(";") if item], **extra}


def exercise_api(csv_dir: Path, settings: Settings, store: Store) -> dict[str, Counter[str]]:
    mail = OutboxMailer()
    with TestClient(create_app(settings, store=store, mailer=mail)) as client:
        def request(method: str, endpoint: str, data: dict | None = None, headers: dict | None = None) -> dict:
            response = client.request(method, f"/api/{endpoint}", json=data, headers=headers)
            if not response.is_success:
                raise RuntimeError(f"{method} /api/{endpoint}: {response.status_code} {response.text}")
            return response.json()

        def code(email: str) -> str:
            recipient, _, body = mail.outbox.pop()
            if recipient != email:
                raise RuntimeError(f"Expected a sign-in code for {email}")
            return body.split("code is ", 1)[1][:6]

        def login(email: str) -> dict[str, str]:
            request("POST", "auth/login-email", {"email": email})
            result = request("POST", "auth/token-email", {"email": email, "code": code(email)})
            return {"Authorization": f"Bearer {result['token']}"}

        admin = login(f"admin@{DEV_DOMAIN}")
        roster = csv_dir / "fake_uoft_data.csv"
        students = load_students(roster)
        if len(students) < SAMPLED_ACCOUNTS:
            raise RuntimeError(f"Generated roster needs at least {SAMPLED_ACCOUNTS} students")
        if len({s["trunc"] for s in students}) != len(students):
            raise RuntimeError("Generated roster contains duplicate student identifiers")
        sample = Random(DEFAULT_SEED).sample(sorted(students, key=lambda s: s["trunc"]), SAMPLED_ACCOUNTS)
        imported = request("POST", "admin/roster", {"csv": roster.read_text(encoding=DEFAULT_ENCODING)}, admin)
        if imported["count"] != len(students):
            raise RuntimeError(f"Roster import accepted {imported['count']} of {len(students)} generated students")
        request("PUT", "admin/sap/weights", {"csv": (ROOT / "data/wfni_weights.csv").read_text()}, admin)
        by_name = {}
        for student in students:
            name = normalize_case(f"{student['first']} {student['last']}")
            by_name.setdefault(name, []).append(student)

        users: dict[str, tuple[str, dict[str, str]]] = {}

        for student in sample:
            digits = student["trunc"]
            email = f"student+{digits}@{DEV_DOMAIN}"
            first, last = student["first"], student["last"]
            request("POST", "auth/register", {
                "email": email, "first_name": first, "last_name": last, "student_number": f"12345{digits}0",
            })
            request("POST", "auth/verify-email", {"email": email, "code": code(email)})
            headers = login(email)
            challenge = request("GET", "verification/challenge")["challenge"]
            verification = request("POST", "verification", {
                "first_name": first, "last_name": last, "division_codes": ["UTSG"],
                "face": SCAN, "id_card": SCAN, "challenge": challenge,
            }, headers)
            request("POST", f"admin/verifications/{verification['id']}/decision", {"approve": True}, admin)
            users[digits] = email, headers
        print(f"Imported {imported['count']} roster entries and registered {len(users)} sampled students.", flush=True)

        summary = {}
        for form, filename in FORMS.items():
            definition = request("GET", f"forms/{form}/static")
            stats: Counter[str] = Counter()
            eligible = {}
            with (csv_dir / filename).open(encoding=DEFAULT_ENCODING, newline="") as stream:
                for row in csv.DictReader(stream):
                    stats["checked"] += 1
                    if form == "senate_application":
                        name = row["Full ACORN-registered Name"]
                    else:
                        last_header = "ACORN-registered Last Name " if form == "agm_rsvp" else "ACORN-registered Last Name"
                        name = f"{row['ACORN-registered First Name']} {row[last_header]}"
                    number = row.get("Student Number", "")
                    match = request("POST", "admin/agm/verify", {"full_name": name, "student_number": number}, admin)
                    candidates = [s for s in by_name.get(normalize_case(name), [])
                                  if not number or get_trunc_id(number) == int(s["trunc"])]
                    if not match["verified"] or len(candidates) != 1:
                        stats["unmatched"] += 1
                        continue
                    student = candidates[0]
                    digits = student["trunc"]
                    if digits not in users:
                        stats["not_sampled"] += 1
                        continue
                    eligible[digits] = student, row
            if len(eligible) < SUBMISSIONS_PER_PROGRAM:
                raise RuntimeError(f"{form}: only {len(eligible)} eligible applicants; need {SUBMISSIONS_PER_PROGRAM}")
            selected = Random(f"{DEFAULT_SEED}:{form}").sample(sorted(eligible), SUBMISSIONS_PER_PROGRAM)
            for digits in selected:
                student, row = eligible[digits]
                email, headers = users[digits]
                request("PUT", f"forms/{form}/submission",
                        {"version": definition["version"], "hash": definition["hash"],
                         "data": form_data(form, row, student, email)}, headers)
                stats["submitted"] += 1
            stats["stored"] = stats["submitted"]
            summary[form] = stats
            print(f"{form}: {stats['checked']} checked, {stats['unmatched']} unmatched, "
                  f"{stats['not_sampled']} outside the sample, "
                  f"{stats['submitted']} submitted, {stats['stored']} unique submissions", flush=True)
        return summary


SEED_MARKER = ("dev_environment", "generated-data")


def prepare_database(settings: Settings, store: Store) -> None:
    state = store.get(*SEED_MARKER)
    if state is not None and state["status"] != "complete":
        raise RuntimeError("Previous dev seed did not complete; use a fresh generated-data Compose project.")
    if state is None and (store.list("accounts") or store.get("roster_meta", "current")):
        raise RuntimeError("Refusing to seed a populated database; use a separate generated-data Compose project.")
    with TemporaryDirectory(prefix="dev-seed-") as tmp:
        seed_dir = Path(tmp)
        subprocess.run([sys.executable, str(ROOT / "testing/generate_fake_data.py"), "--students", str(ROSTER_SIZE)], check=True,
                       env={**os.environ, "UTSU_FAKE_OUTPUT_DIR": tmp})
        if state is None and not store.create(*SEED_MARKER, {"status": "in_progress"}):
            raise RuntimeError("Another process is seeding this development database.")
        seed_settings = replace(settings, redis_url=None, rate_limit_auth=0, applicant_email_domains=(),
                                admin_emails=())
        exercise_api(seed_dir, seed_settings, store if state is None else MemoryStore())
        if state is None:
            store.put(*SEED_MARKER, {"status": "complete"})
            print("Generated data saved to the dev database.", flush=True)
        else:
            print("Generated API checks passed in isolation; kept the existing dev database.", flush=True)


def main() -> None:
    flag = os.environ.get("PORTAL_DEV_SEED", "").strip().lower()
    if flag in ("", "0", "false", "no"):
        return
    if flag not in ("1", "true", "yes"):
        raise RuntimeError("PORTAL_DEV_SEED must be a boolean (for example 0 or 1).")
    settings = Settings.from_env()
    check_dev_settings(settings)
    if not settings.dev_admin or settings.store != "sql":
        raise RuntimeError("Generated fixtures require the local dev Compose SQL store.")
    prepare_database(settings, open_store(settings.store))


if __name__ == "__main__":
    main()
