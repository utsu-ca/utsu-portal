"""Text cleaning, email normalisation and typed ids (ported from utsu_core.std.utils)."""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from difflib import SequenceMatcher

# Letters that do not split into a base letter and an accent are spelled out. Anything else that is not ASCII
# is dropped: accents are removed, other scripts and emoji are not kept.
_FOLD = str.maketrans({"\u00df": "ss", "\u1e9e": "SS", "\u00e6": "ae", "\u00c6": "AE", "\u0153": "oe",
                       "\u0152": "OE", "\u00f8": "o", "\u00d8": "O", "\u0142": "l", "\u0141": "L",
                       "\u0111": "d", "\u0110": "D", "\u00f0": "d", "\u00d0": "D", "\u00fe": "th",
                       "\u00de": "Th", "\u0131": "i", "\u0127": "h", "\u0126": "H"})
# typographic apostrophes and every kind of dash are written the plain way, so "O\u2019Neil" matches "O'Neil"
_PUNCTUATION = {**{ord(c): "'" for c in "\u2018\u2019\u201b\u02bc\u02b9\u00b4`\u2032"},
                **{ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63"}}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD = re.compile(r"[A-Za-z0-9_]+")


def clean_text(txt) -> str:
    """
    Text made plain ASCII and comparable: accents removed, look-alike forms replaced by the plain ones, typographic
    apostrophes and dashes written the plain way, whitespace collapsed, everything else non-ASCII dropped.

    >>> clean_text("\\ufeff Jos\\u00e9\\u200b\\u00a0Garc\\u00eda ")
    'Jose Garcia'
    >>> clean_text("D\\u2019Angelo-Smith \\u2013 Jr")
    "D'Angelo-Smith - Jr"
    >>> clean_text("\\u674e \\u660e Ann \\U0001f600\\ud800")
    'Ann'
    >>> clean_text(None)
    ''
    """
    txt = unicodedata.normalize("NFKD", str(txt if txt is not None else "").translate(_PUNCTUATION)).translate(_FOLD)
    txt = txt.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", _CONTROL.sub("", txt)).strip()


def normalize_case(txt: str) -> str:
    """
    Title Case as plain ASCII with extra spaces erased; idempotent.

    >>> normalize_case("  MIXED case SEntence  ")
    'Mixed Case Sentence'
    >>> normalize_case("mary\\u2013jane o\\u2019neil\\u200b")
    "Mary-Jane O'Neil"
    """
    return _WORD.sub(lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), clean_text(txt))


def generate_uuid(base: str, kind: str = "") -> str:
    """
    A UUID derived from `base` inside the namespace of `kind`, so the same text hashed as two kinds never collides.

    >>> generate_uuid("a@x.ca", "student:email") != generate_uuid("a@x.ca", "student:name")
    True
    """
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"utsu:{kind}") if kind else uuid.NAMESPACE_URL
    return str(uuid.uuid5(namespace, base))


UOFT_MAIL_DOMAIN = "mail.utoronto.ca"
_UOFT_OTHER_DOMAINS = {"utoronto.ca"}
EMAIL_FLAGS = {"corrected": "email_corrected", "other": "personal_email", "invalid": "invalid_email"}


def normalize_uoft_email(email: str) -> tuple[str, str]:
    """
    Normalize an address to `@mail.utoronto.ca`. Returns (address, status): `ok`, `corrected` (a misspelt domain was
    replaced), `other` (a different valid-looking domain, left as typed) or `invalid` (left as typed).

    >>> normalize_uoft_email(" Ann@Mail.UToronto.ca ")
    ('ann@mail.utoronto.ca', 'ok')
    >>> normalize_uoft_email("ann@mal.utoront.c")
    ('ann@mail.utoronto.ca', 'corrected')
    >>> [normalize_uoft_email(v)[1] for v in ("ann@utoronto.ca", "ann@gmail.com", "ann@alum.utoronto.ca")]
    ['other', 'other', 'other']
    >>> [normalize_uoft_email(v)[1] for v in ("", "ann", "a b@mail.utoronto.ca", "@mail.utoronto.ca", "a@@b.ca")]
    ['invalid', 'invalid', 'invalid', 'invalid', 'invalid']
    """
    text = clean_text(email or "")
    local, at, domain = text.rpartition("@")
    domain = domain.lower().rstrip(".")
    if not at or not local or re.search(r"[\s@]", local) or not domain or re.search(r"\s", domain):
        return text, "invalid"
    if domain == UOFT_MAIL_DOMAIN:
        return f"{local.lower()}@{domain}", "ok"
    if domain in _UOFT_OTHER_DOMAINS:
        return text, "other"
    if domain.endswith(".utoronto.ca"):
        # a real UofT subdomain (alum, cs, ...) stays; a near-miss of "mail" (mial, mal, email) is a typo
        label = domain[:-len(".utoronto.ca")]
        if "." in label or SequenceMatcher(None, label, "mail").ratio() < 0.75:
            return text, "other"
        return f"{local.lower()}@{UOFT_MAIL_DOMAIN}", "corrected"
    if SequenceMatcher(None, domain, UOFT_MAIL_DOMAIN).ratio() >= 0.8:
        return f"{local.lower()}@{UOFT_MAIL_DOMAIN}", "corrected"
    return text, "other"


def identity_key(email: str = "", student_number: str = "", full_name: str = "") -> str:
    """
    What identifies a student across submissions: the email, else the student number, else the name (case, spaces
    and non-ASCII ignored). A value with nothing left once made ASCII is hashed so different students never share a key.

    >>> identity_key(" Ann@Mail.UToronto.ca ", "1001231617", "Ann Lee")
    'ann@mail.utoronto.ca'
    >>> identity_key("", "1001231617", "Ann Lee")
    '1001231617'
    >>> identity_key(full_name="\\u674e\\u660e") != identity_key(full_name="\\u738b\\u82b3") != ""
    True
    """
    key = (clean_text(email).replace(" ", "").casefold() or clean_text(student_number).casefold()
           or clean_text(full_name).casefold())
    if key:
        return key
    given = "|".join(str(v).strip() for v in (email, student_number, full_name))
    return "raw-" + hashlib.sha256(given.encode("utf-8", "replace")).hexdigest()[:16] if given.strip("|") else ""


def identity_key_type(email: str = "", student_number: str = "", full_name: str = "") -> str:
    """
    Which field `identity_key` used: "email", "student_number", "name", "unreadable" or "none".

    >>> identity_key_type("a@x.ca", "1", "Ann"), identity_key_type("", "1", "Ann"), identity_key_type(full_name="Ann")
    ('email', 'student_number', 'name')
    >>> identity_key_type("\\u200b ", "", "")
    'unreadable'
    """
    if clean_text(email):
        return "email"
    if clean_text(student_number):
        return "student_number"
    if clean_text(full_name):
        return "name"
    return "unreadable" if any(str(v).strip() for v in (email, student_number, full_name)) else "none"


def typed_uuid(base: str, kind: str) -> str:
    """
    A self-describing id `<kind>:<uuid>`; the kind may contain colons, the uuid follows the last one.

    >>> typed_uuid("a@x.ca", "student:email").startswith("student:email:")
    True
    """
    return f"{kind}:{generate_uuid(base, kind)}"


def parse_id(text: str) -> tuple[str, str]:
    """
    Split an id into (kind, uuid); text without a kind comes back as ("", text).

    >>> parse_id("application:sap:9c1d")
    ('application:sap', '9c1d')
    >>> parse_id("plain-id")
    ('', 'plain-id')
    """
    text = (text or "").strip()
    kind, colon, value = text.rpartition(":")
    return (kind, value) if colon and kind and value else ("", text)


def id_kind(text: str) -> str:
    """The kind an id was made for ("" when it does not say)."""
    return parse_id(text)[0]


def student_uuid_for(email: str = "", student_number: str = "", full_name: str = "") -> str:
    """
    Per-student id of kind `student:<key type>`. A database key, not an identity: it changes if the identifying
    field does.

    >>> id_kind(student_uuid_for("a@x.ca")), id_kind(student_uuid_for(full_name="Ann Lee"))
    ('student:email', 'student:name')
    """
    key_type = identity_key_type(email, student_number, full_name)
    return typed_uuid(identity_key(email, student_number, full_name), f"student:{key_type}")


def application_uuid_for(program: str, submission_ts: str, email: str = "", student_number: str = "",
                        full_name: str = "") -> str:
    """Per-application id of kind `application:<program>`: the submission timestamp and the student's key."""
    return typed_uuid(f"{submission_ts.strip()}|{identity_key(email, student_number, full_name)}",
                      f"application:{program}")
