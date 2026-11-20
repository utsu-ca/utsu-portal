"""Local-development role and applicant accounts.

PORTAL_DEV_ADMIN=1 seeds sign-in accounts so local work can continue without registering them. The server refuses the
setting unless the portal's own address is a loopback host. Once seeding runs the store carries a marker,
and the UI turns red and renames itself for as long as that marker is present, so a copied development database is
obvious. The sign-in code goes to the configured mail sink, or to the console when no
SMTP server is configured.
"""
from __future__ import annotations

import logging
import uuid
from urllib.parse import urlsplit

from .context import Context
from .mailer import Mailer
from .settings import Settings
from .store import Doc, Store

log = logging.getLogger(__name__)

DEV_DOMAIN = "dev-local.test"
DEV_ADMIN_EMAIL = f"devadmin@{DEV_DOMAIN}"
DEV_LOCAL_NAME = "ACTIVE DEV LOCAL, DO NOT USE"
MARKER = ("dev_environment", "admin")
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class ConsoleMailer(Mailer):
    """Prints whole messages (including sign-in codes) to the server log in local dev mode."""

    def send(self, to: str, subject: str, body: str) -> None:
        log.warning("DEV MAIL to %s: %s\n%s", to, subject, body)


def check_dev_settings(settings: Settings) -> None:
    if not settings.dev_admin:
        return
    host = urlsplit(settings.sso_issuer).hostname
    if host not in LOOPBACK:
        raise RuntimeError("PORTAL_DEV_ADMIN is only allowed when PORTAL_BASE_URL is a localhost address")
    if settings.store == "firestore" or settings.auth_url:
        raise RuntimeError("PORTAL_DEV_ADMIN is only allowed with a local store and built-in sign-in")


def _reserved_uid(store: Store, key: str) -> str:
    marker = store.get(MARKER[0], key)
    if marker is None:
        uid = str(uuid.uuid4())
        if store.create(MARKER[0], key, {"uuid": uid}):
            return uid
        marker = store.get(MARKER[0], key)
        if marker is None:
            raise RuntimeError(f"Development account reservation missing for {key}")
    return marker["uuid"]


def _seed_account(ctx: Context, email: str, name: str, roles: list[str], uid: str | None = None) -> None:
    store = ctx.store
    ekey = ctx.email_key(email)
    if uid is None:
        # A seed reservation distinguishes our UUID from a normal registration using the same address.
        uid = _reserved_uid(store, f"account:{ekey}")
    email_owner = store.get("emails", ekey)
    if email_owner is None:
        if store.create("emails", ekey, {"uuid": uid}):
            email_owner = {"uuid": uid}
        else:
            email_owner = store.get("emails", ekey)
    if (email_owner or {}).get("uuid") != uid:
        log.warning("Skipping development account %s: email is already in use", email)
        return
    if store.get("users", uid) is None:
        store.create("users", uid, {"uuid": uid, "email": email, "first_name": "Dev", "last_name": name,
                                    "preferred_name": f"Dev {name}", "student_number": "", "username": "", "username_token": None})
    if store.get("accounts", uid) is not None:
        return
    is_admin = "admin" in roles
    account: Doc = {"uuid": uid, "status": "verified", "role": "admin" if is_admin else "applicant", "roles": roles,
                    "email_verified": True, "email_key": ekey}
    if is_admin:
        account["dev_admin"] = True
    if store.create("accounts", uid, account):
        event = "account.dev_admin_seeded" if is_admin else "account.dev_seeded"
        ctx.audit.record(None, event, f"user:{uid}", None, {"roles": roles}, None)


def seed_dev_admin(ctx: Context) -> str:
    uid = _reserved_uid(ctx.store, MARKER[1])
    _seed_account(ctx, DEV_ADMIN_EMAIL, "Admin", ["admin"], uid=uid)
    for role in sorted(ctx.roles):
        email = f"{role}@{DEV_DOMAIN}"
        name = role.replace("_", " ").replace("-", " ").title()
        _seed_account(ctx, email, name, [role])
    for number in range(1, 4):
        email = f"applicant+{number}@{DEV_DOMAIN}"
        _seed_account(ctx, email, f"Applicant {number}", [])
    log.warning("Development account seeding enabled for %s", DEV_DOMAIN)
    return uid


def dev_local_present(ctx: Context) -> bool:
    return ctx.store.get(*MARKER) is not None
