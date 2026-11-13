"""Pure AGM RSVP logic (ported from utsu_programs.agm.program). No store, no roster, no I/O."""
from __future__ import annotations

import re

from ...services.text import (EMAIL_FLAGS, UOFT_MAIL_DOMAIN, application_uuid_for, normalize_case,
                              normalize_uoft_email, student_uuid_for)

EMAIL_REASON = f"email is not a {UOFT_MAIL_DOMAIN} address"
# answers to the dietary and accessibility questions that say there is nothing to accommodate
NO_NEED = {"", "none", "n/a", "na", "no", "nil", "nothing", "not applicable", "no restrictions", "no restriction",
           "none at this time", "no allergies", "no dietary restrictions", "no accessibility requirements"}


def real_need(answer: str) -> str:
    """
    The answer to a dietary or accessibility question, or '' when it only says there is no need.

    >>> real_need("None"), real_need(" N/A. "), real_need("n.a."), real_need("Vegan"), real_need("No gluten")
    ('', '', '', 'Vegan', 'No gluten')
    """
    text = (answer or "").strip()
    return "" if re.sub(r"[^a-z/ ]", "", text.lower()).strip() in NO_NEED else text


def is_yes(value: str) -> bool:
    """
    >>> is_yes(" Yes, I agree"), is_yes("")
    (True, False)
    """
    return (value or "").strip().lower().startswith("yes")


def is_ambiguous(reason: str) -> bool:
    """
    Whether an unverified RSVP failed because more than one student matches it (a shared name).

    >>> is_ambiguous("name shared by 2 students; a student number is needed"), is_ambiguous("name not found")
    (True, False)
    """
    return "shared by" in (reason or "")


def is_in_person(meeting_preference: str) -> bool:
    """
    Whether a meeting preference means attending in person.

    >>> [is_in_person(v) for v in ("In-person", "in person", "In Person (Hart House)", "Online", "Virtual", "")]
    [True, True, True, False, False, False]
    >>> is_in_person("No, I will not be attending in person"), is_in_person("Yes, I'll be attending in-person.")
    (False, True)
    """
    text = meeting_preference or ""
    if not re.search(r"\bin[\s-]*person\b", text, re.I):
        return False
    return not re.search(r"\b(not|never|cannot|can't|won't|unable)\b[^.;]*\bin[\s-]*person\b", text, re.I)


def attendance(meeting_preference: str) -> str:
    """
    'In-person' when the answer says so, otherwise 'Virtual' (blank or unclear defaults to Virtual).

    >>> attendance("Yes, I'll be attending in-person."), attendance("Online (Zoom)"), attendance(None)
    ('In-person', 'Virtual', 'Virtual')
    """
    return "In-person" if is_in_person(meeting_preference) else "Virtual"


def agm_uuid(rsvp: dict) -> str:
    """Internal id for an RSVP: the same submission always yields the same uuid."""
    return application_uuid_for("agm", str(rsvp.get("submitted_at", "")), rsvp.get("email", ""),
                                rsvp.get("student_number", ""), rsvp.get("full_name", ""))


def review_flags(rsvp: dict, match_method: str = "", reason: str = "", fuzzy_flag: str = "") -> str:
    """Flags an admin should look at. `match_method` of name/fuzzy_name means the match used the name alone."""
    flags = []
    if match_method in ("name", "fuzzy_name"):
        flags.append("name_only_match")
    if fuzzy_flag:
        flags.append(fuzzy_flag)
    if is_ambiguous(reason):
        flags.append("ambiguous_name")
    if not is_yes(rsvp.get("data_confirmation", "")):
        flags.append("no_data_confirmation")
    if not rsvp.get("email"):
        flags.append("no_uoft_email")
    if rsvp.get("email_status") in EMAIL_FLAGS:
        flags.append(EMAIL_FLAGS[rsvp["email_status"]])
    return ";".join(flags)


def build_rsvp(row: dict) -> dict | None:
    """
    An RSVP record from a form submission's answers (plus `submitted_at`); None when there is neither a name nor a
    student number. Eligibility is not decided here: that is computed against the roster when needed.

    >>> r = build_rsvp({"first_name": "ann", "last_name": "LEE", "email": " Ann@Mail.UToronto.ca ",
    ...                 "meeting_preference": "In person", "submitted_at": "2026-09-01T10:00:00"})
    >>> r["full_name"], r["email"], r["attendance"], r["uuid"].startswith("application:agm:")
    ('Ann Lee', 'ann@mail.utoronto.ca', 'In-person', True)
    >>> build_rsvp({"email": "x@y.ca"}) is None
    True
    """
    rsvp = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items() if isinstance(k, str)}
    rsvp["full_name"] = normalize_case(f"{rsvp.get('first_name', '')} {rsvp.get('last_name', '')}")
    rsvp["student_number"] = rsvp.get("student_number") or ""
    rsvp["email"], rsvp["email_status"] = normalize_uoft_email(rsvp.get("email", ""))
    rsvp["attendance"] = attendance(rsvp.get("meeting_preference", ""))
    rsvp["uuid"] = agm_uuid(rsvp)
    rsvp["student_uuid"] = student_uuid_for(rsvp["email"], rsvp["student_number"], rsvp["full_name"])
    rsvp["flags"] = review_flags(rsvp)
    return rsvp if rsvp["full_name"] or rsvp["student_number"] else None
