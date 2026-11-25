from __future__ import annotations

import base64
import hmac
import logging
import re
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .. import security
from ..context import Context, roles_of
from ..identity import permits_backup_signin
from ..security import clean_text
from ..services.account_check import check_one, membership_status
from ..services.google_staff import register_routes as register_google_routes
from ..services.roster import get_trunc_id, membership_session
from ..services.usernames import release, reserve, username_key
from ..services import turnstile

name = "auth"
USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
EMAIL = re.compile(r"^[^@\s<>]{1,64}@[^@\s<>]{1,255}\.[^@\s<>]{2,}$")
log = logging.getLogger(__name__)


class Name(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, v, info: ValidationInfo):
        return clean_text(v, 100) if isinstance(v, str) and info.field_name != "challenge_token" else v


class Register(Name):
    username: str = ""
    email: str
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    preferred_name: str = ""
    student_number: str = Field(pattern=r"^\d{7,10}$")
    challenge_token: str = Field(default="", max_length=2048)


class Username(BaseModel):
    username: str


class Recover(Username):
    student_number_trunc: str = Field(pattern=r"^\d{4}$")


class CodeIn(Username):
    code: str


class EmailIn(BaseModel):
    email: str = Field(max_length=320)


class EmailCodeIn(EmailIn):
    code: str


class BackupAddress(BaseModel):
    email: str = Field(max_length=320)


class BackupEnrollment(BackupAddress):
    primary_code: str = Field(pattern=r"^\d{6}$")
    backup_code: str = Field(pattern=r"^\d{6}$")


class BackupLogin(Username, BackupAddress):
    pass


class Preferred(Name):
    preferred_name: str = Field(min_length=1)


class PublicUsername(Name):
    username: str = ""

class ConfirmRosterNumber(BaseModel):
    student_number: str = Field(pattern=r"^\d{7,10}$")


class Verification(Name):
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    division_codes: list[str] = Field(min_length=1, max_length=5)
    face: str
    id_card: str
    challenge: str


class RecoveryChange(Verification):
    username: str
    student_number_trunc: str = Field(pattern=r"^\d{4}$")
    new_email: str | None = None
    otp: str | None = None


class RecoveryOtp(Recover):
    new_email: str


def _image(ctx: Context, data_url: str) -> str:
    m = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", data_url or "")
    if not m:
        raise HTTPException(422, "image must be a base64 jpeg/png/webp data URL")
    if len(base64.b64decode(m.group(2), validate=True)) > ctx.settings.max_image_bytes:
        raise HTTPException(413, "image too large")
    return data_url


def _divisions(ctx: Context, codes: list[str]) -> list[str]:
    codes = [clean_text(c, 10).upper() for c in codes]
    bad = [c for c in codes if c not in ctx.settings.division_codes]
    if bad:
        raise HTTPException(422, f"unknown division code(s): {bad}")
    return codes


def _public_user(user: dict) -> dict:
    return {key: value for key, value in user.items() if key != "username_token"}


def _submit_verification(ctx: Context, uid: str, kind: str, body: Verification, changes: dict) -> dict:
    if security.read_token(ctx.settings.secret, body.challenge, "chal") is None:
        raise HTTPException(422, "live-scan challenge missing or expired; capture again")
    face, idc = _image(ctx, body.face), _image(ctx, body.id_card)
    divs = _divisions(ctx, body.division_codes)
    for v in ctx.store.list("verifications", uuid=uid, status="pending"):
        raise HTTPException(409, "a verification request is already pending")
    vid = security.secrets.token_hex(12)
    rec = {"id": vid, "uuid": uid, "kind": kind, "status": "pending", "submitted_at": ctx.now(),
           "division_codes": divs, "first_name": body.first_name, "last_name": body.last_name, "changes": changes}
    ctx.store.put("media", vid, {"id": vid, "uuid": uid, "face": face, "id_card": idc})
    ctx.store.put("verifications", vid, rec)
    return {"id": vid, "status": "pending"}


class AuthModule:
    name = name

    def router(self, ctx: Context) -> APIRouter:
        r = APIRouter(prefix="/api")
        S = ctx.store
        user_dep = ctx.require_user()
        @r.get("/auth/storage")
        def storage_usage(account: dict = user_dep):
            return ctx.upload_quotas.usage(f"user:{account['uuid']}")

        register_google_routes(r, ctx)
        domains = {d.strip().lower() for d in ctx.settings.applicant_email_domains}

        def school_email(email: str) -> bool:
            return bool(EMAIL.fullmatch(email) and (not domains or email.lower().rsplit("@", 1)[1] in domains))

        @r.get("/auth/registration-challenge")
        def registration_challenge():
            return turnstile.configuration(ctx.settings)

        @r.post("/auth/register", status_code=201)
        def register(b: Register):
            if b.username and not USERNAME.fullmatch(b.username):
                raise HTTPException(422, "username must be 3-64 chars of letters, digits . _ -")
            if not EMAIL.match(b.email):
                raise HTTPException(422, "invalid email")
            if b.email.lower() not in ctx.settings.admin_emails and not school_email(b.email):
                raise HTTPException(422, "Use a primary email address from an approved school domain")
            if b.email.lower() == (ctx.settings.google_bootstrap_admin_email or "").strip().lower():
                raise HTTPException(409, "Use Google sign-in for this work account")
            turnstile.verify_registration(ctx.settings, b.challenge_token)
            uid = str(uuid.uuid4())
            sid = security.student_id_uuid(ctx.settings.secret, b.student_number)
            ekey = ctx.email_key(b.email)
            ukey = username_key(ctx.settings.secret, b.username) if b.username else None
            utoken = None
            sid_reserved = email_reserved = user_saved = roster_saved = registered = account_saved = False
            try:
                if ukey:
                    utoken = reserve(S, ukey, uid)
                if not S.create("accounts", uid, {
                    "uuid": uid, "status": "registering", "role": "applicant", "roles": [],
                    "email_verified": False, "sid_uuid": sid, "email_key": ekey,
                }):
                    raise HTTPException(409, "account ID collision; retry registration")
                account_saved = True
                if not S.create("student_ids", sid, {"uuid": uid}):
                    raise HTTPException(409, "student number already registered")
                sid_reserved = True
                if not S.create("emails", ekey, {"uuid": uid}):
                    raise HTTPException(409, "email already registered")
                email_reserved = True

                admin = b.email.strip().lower() in ctx.settings.admin_emails  # username is never trusted; the email OTP below proves the mailbox
                S.put("users", uid, {"uuid": uid, "email": b.email.lower(), "preferred_name": b.preferred_name or b.first_name,
                                     "first_name": b.first_name, "last_name": b.last_name,
                                     "student_number": security.truncate_student_number(b.student_number),
                                     "username": b.username, "username_token": utoken})
                user_saved = True
                digits = get_trunc_id(b.student_number)
                if digits is not None:
                    S.put("roster_identifiers", uid, {"digits": f"{digits:04d}"})
                    roster_saved = True
                def finish(acct):
                    if not acct or acct.get("status") != "registering":
                        raise HTTPException(409, "registration reservation was lost")
                    return {**acct, "status": "pending_email", "role": "admin" if admin else "applicant",
                            "roles": ["admin"] if admin else []}

                S.update("accounts", uid, finish)
                ctx.send_otp("email", uid, b.email)
                registered = True
                ctx.audit.record(uid, "account.registered", f"user:{uid}", None, S.get("users", uid), {"role": "admin" if admin else "applicant"})
                return {"uuid": uid, "status": "pending_email"}
            finally:
                if not registered:
                    if email_reserved:
                        S.delete("emails", ekey)
                    if sid_reserved:
                        S.delete("student_ids", sid)
                    if roster_saved:
                        S.delete("roster_identifiers", uid)
                    if user_saved:
                        S.delete("users", uid)
                    if account_saved:
                        S.delete("accounts", uid)
                    if ukey and utoken:
                        release(S, ukey, utoken)

        @r.post("/auth/verify-email")
        def verify_email(b: EmailCodeIn | CodeIn):
            uid = ctx.uid(b.email if isinstance(b, EmailCodeIn) else b.username)
            acct = S.get("accounts", uid)
            if acct is None or ctx.check_otp("email", uid, b.code) is None:
                raise HTTPException(400, "invalid or expired code")
            def mark(ac):
                ac = {**ac, "email_verified": True}
                if ac["status"] == "pending_email":
                    ac["status"] = "verified" if ac["role"] == "admin" else "pending_review"
                return ac

            acct = S.update("accounts", uid, mark)
            ctx.audit.record(uid, "account.email_verified", f"user:{uid}", None, {"status": acct["status"]})
            return {"status": acct["status"]}

        def email_account(email: str) -> str | None:
            """The account whose current primary email is this address; never a Google staff account."""
            email = email.strip().lower()
            if not EMAIL.fullmatch(email):
                return None
            uid = (S.get("emails", ctx.email_key(email)) or {}).get("uuid")
            user, acct = (S.get("users", uid), S.get("accounts", uid)) if uid else (None, None)
            if not user or not acct or acct.get("google_staff_username") or user.get("email", "").lower() != email:
                return None
            return uid

        @r.post("/auth/login-email")
        def login_by_email(b: EmailIn):
            uid = email_account(b.email)
            if uid:  # no account enumeration
                ctx.send_otp("login", uid, S.get("users", uid)["email"])
            return {"detail": "if an account uses that email, a code was emailed"}

        @r.post("/auth/token-email")
        def token_by_email(b: EmailCodeIn):
            return finish_login(email_account(b.email), b.code)

        def finish_login(uid: str | None, code: str) -> dict:
            acct = S.get("accounts", uid) if uid else None
            if acct is None or acct.get("google_staff_username") or ctx.check_otp("login", uid, code) is None:
                raise HTTPException(400, "invalid or expired code")
            if not acct["email_verified"]:
                def mark(ac):  # the login code proves control of the registered email
                    ac = {**ac, "email_verified": True}
                    if ac["status"] == "pending_email":
                        ac["status"] = "verified" if ac["role"] == "admin" else "pending_review"
                    return ac

                acct = S.update("accounts", uid, mark)
            ctx.audit.record(uid, "auth.login", f"user:{uid}")
            return {"token": ctx.identity.issue(uid), "status": acct["status"]}

        @r.get("/me")
        def me(acct: dict = user_dep):
            u = S.get("users", acct["uuid"])
            result = {"user": _public_user(u), "status": acct["status"], "role": acct["role"],
                      "roles": sorted(roles_of(acct, ctx.roles)),
                      "membership_session": membership_session(ctx.now()),
                      "membership": membership_status(S, acct["uuid"], ctx.now())}
            if acct.get("google_staff_username"):
                result["staff_sso"] = True
            if permits_backup_signin(acct):
                result["backup_email"] = acct.get("recovery_email")
                result["backup_session"] = bool(acct.get("backup_session"))
            return result

        def backup_enrollment(body: BackupAddress, acct: dict) -> tuple[str, str, dict]:
            if not permits_backup_signin(acct) or acct.get("backup_session"):
                raise HTTPException(403, "Sign in with your primary email to manage your backup email")
            primary = S.get("users", acct["uuid"])["email"]
            backup = body.email.strip().lower()
            if not EMAIL.fullmatch(backup) or backup == primary.lower():
                raise HTTPException(422, "Enter a valid backup email different from your primary email")
            binding = {"primary": acct["email_key"], "backup": ctx.email_key(backup),
                       "version": acct.get("recovery_version")}
            return primary, backup, binding

        @r.post("/auth/backup/enroll")
        def enroll_backup(body: BackupAddress, acct: dict = user_dep):
            primary, backup, binding = backup_enrollment(body, acct)
            uid = acct["uuid"]
            sent_primary = ctx.send_otp("backup-primary", uid, primary, binding)
            sent_backup = ctx.send_otp("backup-enroll", uid, backup, binding)
            if not sent_primary or not sent_backup:
                raise HTTPException(429, "A code was requested recently. Check both inboxes or wait before requesting new codes.")
            return {"detail": "Codes sent to your primary and backup addresses"}

        @r.post("/auth/backup/confirm")
        def confirm_backup(body: BackupEnrollment, acct: dict = user_dep):
            _, backup, binding = backup_enrollment(body, acct)
            uid = acct["uuid"]
            checks = (("backup-primary", body.primary_code), ("backup-enroll", body.backup_code))
            for consume in (False, True):
                if not all(ctx.check_otp(purpose, uid, code, consume=consume) == binding for purpose, code in checks):
                    raise HTTPException(400, "The email codes are invalid or expired")

            def save(current):
                if (not current or not permits_backup_signin(current)
                        or current.get("recovery_version") != binding["version"]
                        or current.get("email_key") != binding["primary"]):
                    raise HTTPException(409, "Your account changed; request new codes and try again")
                return {**current, "recovery_email": backup, "recovery_version": security.secrets.token_hex(16)}

            S.update("accounts", uid, save)
            ctx.audit.record(uid, "account.backup_email_verified", f"user:{uid}")
            return {"backup_email": backup}

        @r.patch("/me")
        def update_me(b: Preferred, acct: dict = user_dep):
            u = S.get("users", acct["uuid"])
            old = u["preferred_name"]
            u = S.update("users", acct["uuid"], lambda cur: {**cur, "preferred_name": b.preferred_name})  # the only user-mutable field
            ctx.audit.record(acct["uuid"], "user.preferred_name_changed", f"user:{acct['uuid']}", {"preferred_name": old}, {"preferred_name": u["preferred_name"]})
            return {"user": _public_user(u)}

        @r.patch("/me/username")
        def update_username(body: PublicUsername, acct: dict = user_dep):
            if acct.get("backup_session"):
                raise HTTPException(403, "Sign in with your primary email to change your username")
            value = body.username
            if value and not USERNAME.fullmatch(value):
                raise HTTPException(422, "username must be 3-64 chars of letters, digits . _ -")
            uid = acct["uuid"]
            user = S.get("users", uid)
            old_value, old_token = user.get("username", ""), user.get("username_token")
            if value == old_value:
                return {"user": _public_user(user)}
            old_key = username_key(ctx.settings.secret, old_value) if old_value else None
            new_key = username_key(ctx.settings.secret, value) if value else None
            # A case-only edit keeps the existing reservation.
            new_token = old_token if new_key and new_key == old_key else None
            if new_key and new_key != old_key:
                new_token = reserve(S, new_key, uid)
            saved = False
            try:
                def change(current):
                    if not current or current.get("username_token") != old_token or current.get("username", "") != old_value:
                        raise HTTPException(409, "Your username changed; reload and try again")
                    return {**current, "username": value, "username_token": new_token}

                user = S.update("users", uid, change)
                saved = True
            finally:
                if not saved and new_key and new_key != old_key and new_token:
                    release(S, new_key, new_token)
            if old_key and old_key != new_key and old_token:
                release(S, old_key, old_token)
            ctx.audit.record(uid, "user.username_changed", f"user:{uid}")
            return {"user": _public_user(user)}

        @r.post("/me/roster-number")
        def confirm_roster_number(body: ConfirmRosterNumber, acct: dict = user_dep):
            if acct.get("google_staff_username"):
                raise HTTPException(403, "Staff sign-in does not verify student status")
            uid = acct["uuid"]
            if not ctx.limiter.allow(f"roster-number:{uid}", ctx.settings.recovery_attempts, ctx.settings.recovery_window):
                raise HTTPException(429, "too many attempts; try again later")
            digits = get_trunc_id(body.student_number)
            if digits is None:
                raise HTTPException(422, "student number cannot be matched to the roster format")
            if not hmac.compare_digest(security.student_id_uuid(ctx.settings.secret, body.student_number),
                                       acct.get("sid_uuid") or ""):
                raise HTTPException(422, "student number does not match the registered account")
            S.put("roster_identifiers", uid, {"digits": f"{digits:04d}"})
            check_one(S, uid, ctx.now())
            ctx.audit.record(uid, "user.roster_number_confirmed", f"user:{uid}")
            return {"status": "confirmed"}

        @r.get("/verification/challenge")
        def challenge():
            nonce = security.secrets.token_hex(3)
            return {"challenge": security.sign_token(ctx.settings.secret, nonce, 600, "chal"), "phrase": nonce.upper()}

        @r.post("/verification", status_code=201)
        def verification(b: Verification, acct: dict = user_dep):
            if acct["status"] == "verified":
                raise HTTPException(409, "already verified")
            if acct["status"] == "rejected":
                raise HTTPException(403, "verification was rejected; contact an administrator")
            u = S.get("users", acct["uuid"])
            if (b.first_name, b.last_name) != (u["first_name"], u["last_name"]):
                raise HTTPException(422, "names must match the registered names; use account recovery to change them")
            return _submit_verification(ctx, acct["uuid"], "activation", b, {})

        @r.get("/verification")
        def verification_status(acct: dict = user_dep):
            return [{k: v[k] for k in ("id", "kind", "status", "submitted_at")} | {"note": v.get("note", "")}
                    for v in S.list("verifications", uuid=acct["uuid"])]

        # ---- account recovery -------------------------------------------------
        def backup_match(body: BackupLogin) -> tuple[str, str, str] | None:
            uid = ctx.uid(body.username)
            acct = S.get("accounts", uid)
            if acct and permits_backup_signin(acct):
                email, version = acct.get("recovery_email"), acct.get("recovery_version")
                if (email and version and ctx.email_key(email) != acct.get("email_key")
                        and hmac.compare_digest(ctx.email_key(email), ctx.email_key(body.email.strip()))):
                    return uid, email, version
            return None

        @r.post("/recovery/backup/start")
        def send_backup_login(body: BackupLogin):
            match = backup_match(body)
            if match:
                uid, email, version = match
                try:
                    ctx.send_otp("backup-login", uid, email, {"version": version})
                except HTTPException as exc:
                    if exc.status_code not in (502, 503):
                        raise
                    log.warning("Backup sign-in code delivery failed (HTTP %d)", exc.status_code)
            return {"detail": "If the details match, a code was sent to your backup email"}

        @r.post("/recovery/backup/token")
        def backup_token(body: CodeIn):
            uid = ctx.uid(body.username)
            proof = ctx.check_otp("backup-login", uid, body.code)
            acct = S.get("accounts", uid)
            if (not proof or not acct or not permits_backup_signin(acct)
                    or not acct.get("recovery_version") or proof.get("version") != acct["recovery_version"]
                    or not acct.get("recovery_email") or ctx.email_key(acct["recovery_email"]) == acct.get("email_key")):
                raise HTTPException(400, "The backup email code is invalid or expired")
            ctx.audit.record(uid, "auth.backup_login", f"user:{uid}")
            return {"token": ctx.identity.issue_backup(uid, acct["recovery_version"]), "status": acct["status"]}

        def _guard(uid: str) -> None:
            """Per-username budget (not per-IP): 10^4 student-number guesses cannot be spread across addresses."""
            st = ctx.settings
            if not ctx.limiter.allow(f"recovery:{uid}", st.recovery_attempts, st.recovery_window):
                raise HTTPException(429, "too many recovery attempts; try again later", headers={"Retry-After": str(st.recovery_window)})

        def _recoverable(b: Recover) -> tuple[dict, dict]:
            uid = ctx.uid(b.username)
            _guard(uid)
            u, a = S.get("users", uid), S.get("accounts", uid)
            if not u or not a or u["student_number"] != b.student_number_trunc:
                raise HTTPException(400, "details do not match an account")
            return u, a

        @r.post("/recovery/start")
        def recovery_start(b: Recover):
            uid = ctx.uid(b.username)
            _guard(uid)
            u = S.get("users", uid)
            if u and u["student_number"] == b.student_number_trunc:
                ctx.send_otp("login", uid, u["email"])
            return {"detail": "if the details match, a code was emailed; sign in using your primary email"}

        @r.post("/recovery/email-otp")
        def recovery_email_otp(b: RecoveryOtp):
            if not school_email(b.new_email):
                raise HTTPException(422, "Use a primary email address from an approved school domain")
            try:
                u, a = _recoverable(b)
            except HTTPException as e:
                if e.status_code != 400:
                    raise
                return {"detail": "if the details match, a code was sent to the new address"}  # uniform: no guessing oracle
            if b.new_email.lower() != a.get("recovery_email") and not S.get("emails", ctx.email_key(b.new_email)):
                ctx.send_otp("newemail", a["uuid"], b.new_email, {"email": b.new_email.lower()})
            return {"detail": "if the details match, a code was sent to the new address"}

        @r.post("/recovery/request", status_code=201)
        def recovery_request(b: RecoveryChange):
            u, a = _recoverable(b)
            changes: dict = {}
            if (b.first_name, b.last_name) != (u["first_name"], u["last_name"]):
                changes["first_name"], changes["last_name"] = b.first_name, b.last_name
            if b.new_email:
                if not school_email(b.new_email):
                    raise HTTPException(422, "Use a primary email address from an approved school domain")
                if b.new_email.lower() == a.get("recovery_email"):
                    raise HTTPException(422, "The primary and backup addresses must be different")
                p = ctx.check_otp("newemail", a["uuid"], b.otp or "")
                if not p or p["email"] != b.new_email.lower():
                    raise HTTPException(400, "new email not verified")
                changes["email"] = p["email"]
            if not changes:
                raise HTTPException(422, "no change requested; use /api/recovery/start to regain access")
            return _submit_verification(ctx, a["uuid"], "recovery", b, changes)

        return r
