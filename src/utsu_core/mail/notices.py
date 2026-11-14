"""Build recipient records for more-information and rejection emails.

Each row keeps its original columns and gains:
  application_uuid   the application's `uuid` (derived from its submission timestamp and the student's email)
  student_uuid       the internal per-student id: derived from the student's email (else student number, else
                     name), so it is the same before and after the student is verified
  unverified_fields  the details that could not be verified, one per line (more_info only)
  rejection_reason   the text given with --reason (rejection only)

Both ids are internal database keys, shown in the emails only as a reference for replies; a reply is matched back
to an application by the student's email, student number or name.

"""
import logging
import sqlite3
from pathlib import Path

from utsu_core.verification import is_uoft_email, match_students
from utsu_core.std.csv_safe import write_rows
from utsu_core.std.utils import student_uuid_for

logger = logging.getLogger("utsu_core.mail.notices")

NAME_FIELD = "- Full name (exactly as it appears on your ACORN registration)"
NUMBER_FIELD = "- Student number"
DIVISION_FIELD = "- Division (as it appears on your ACORN registration)"
EMAIL_FIELD = "- UofT email address (it must end in @mail.utoronto.ca)"


def diagnose(app: dict[str, str], cursor: sqlite3.Cursor, program: str = "") -> tuple[str, list[str]]:
    """
    Compare an application with uoft_data. Returns (student_uuid, details that could not be verified).
    Works with a name plus, optionally, a student number and a division. The name is blamed when it is not in
    uoft_data or missing, the student number when it does not belong to the name, the division when it does not
    match the student, and, when a shared name cannot be decided, the student number or the division, whichever the
    match said is still needed. For `agm`, an email that is not `@mail.utoronto.ca` is blamed first, as in
    `agm.verify_rsvps`.
    """
    number = (app.get("id") or app.get("student_number") or "").strip()
    name = app.get("full_name", "")
    division = app.get("division", "")
    student = student_uuid_for(app.get("email", ""), number, name)
    if program == "agm" and not is_uoft_email(app.get("email", "")):
        return student, [EMAIL_FIELD]
    match = match_students([{"full_name": name, "id": number, "division": division}], cursor)[0]
    if match.verified:
        return student, []

    reason = match.reason
    if reason == "division does not match":
        fields = [DIVISION_FIELD]
    elif reason == "name not found" or reason == "no name given" or reason.startswith("name has no plain letters"):
        fields = [NAME_FIELD]
    elif "shared by" in reason:  # the name is shared: ask for what the match message says it needs
        fields = [DIVISION_FIELD] if "a division is needed" in reason else \
            [NUMBER_FIELD] if "a student number is needed" in reason else [NUMBER_FIELD, DIVISION_FIELD]
    else:  # a student number that does not match
        fields = [NUMBER_FIELD]
    return student, fields


def build_notices(rows: list[dict[str, str]], cursor: sqlite3.Cursor, kind: str,
                  reason: str = "", program: str = "") -> list[dict[str, str]]:
    if kind not in {"more_info", "rejection"}:
        raise ValueError(f"Unknown notice kind: {kind}")
    if kind == "rejection" and not reason and not any(r.get("rejection_reason") for r in rows):
        raise ValueError("A rejection needs a reason")
    notices = []
    for row in rows:
        student_uuid, fields = diagnose(row, cursor, program)
        notice = dict(row, application_uuid=row.get("uuid", ""), student_uuid=student_uuid)
        if kind == "more_info":
            if not fields:
                logger.warning(f"Application {row.get('uuid')} matches uoft_data; nothing to ask for, skipped")
                continue
            notice["unverified_fields"] = "\n".join(fields)
        elif reason:
            notice["rejection_reason"] = reason
        notices.append(notice)
    return notices


def write_notices(path: Path, notices: list[dict[str, str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_rows(path, list(dict.fromkeys(k for n in notices for k in n)), notices, extrasaction="raise")
