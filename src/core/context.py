"""Shared application context handed to every module."""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from fastapi import Depends, Header, HTTPException
from fastapi.params import Depends as AuthDependency

from . import security
from .events import EventBus
from .mailer import Mailer
from .permissions import CORE_PERMISSIONS, CORE_ROLE_PERMISSIONS
from .role_catalogue import MEMBER_ROLES, ROLES
from .services.anchors import AnchorRuntime
from .services.email import EmailDeliveryService, EmailDeliveryUnknown, EmailNotConfigured
from .services.files import DocumentService
from .services.upload_quota import QuotaStore
from .services.forms import FormRegistry, FormsRuntime
from .services.messaging import MessagingRuntime, ThreadTypeRegistry
from .services.notifications import NotificationTypeRegistry
from .settings import Settings
from .store import Conflict, Store


def roles_of(acct: Mapping[str, Any], catalogue: Mapping[str, str] | None = None) -> set[str]:
    """Effective roles; the legacy `role: admin` field remains effective."""
    active_roles = catalogue if catalogue is not None else ROLES
    r = set(acct.get("roles") or []) & set(active_roles)
    if acct.get("role") == "admin":
        r.add("admin")
    return r


@dataclass
class Context:
    store: Store
    settings: Settings
    mailer: Mailer
    bus: EventBus = field(default_factory=EventBus)
    identity: Any = None  # Identity (local store or remote auth service)
    anchor: Any = None  # AnchorProvider
    audit: Any = None  # AuditLog, set by create_app
    limiter: Any = None
    services: dict = field(default_factory=dict)  # shared objects between modules (e.g. the SSO provider)
    roles: Mapping[str, str] = field(default_factory=lambda: ROLES)
    permissions: Mapping[str, str] = field(default_factory=lambda: CORE_PERMISSIONS)
    role_permissions: Mapping[str, frozenset[str]] = field(default_factory=lambda: CORE_ROLE_PERMISSIONS)
    plugin_settings: Mapping[str, Mapping[str, Any]] | None = None
    files: DocumentService = field(init=False)
    upload_quotas: QuotaStore = field(init=False)
    form_registry: FormRegistry = field(init=False)
    forms: FormsRuntime = field(init=False)
    notification_types: NotificationTypeRegistry = field(init=False)
    email: EmailDeliveryService = field(init=False)
    thread_types: ThreadTypeRegistry = field(init=False)
    messaging: MessagingRuntime = field(init=False)
    anchors: AnchorRuntime = field(init=False)
    provided: dict[str, tuple[str, Any]] = field(default_factory=dict)  # name -> (provider plug-in, service)
    derived_roles: dict[str, Callable[[Mapping[str, Any]], bool]] = field(default_factory=dict)  # plug-in computed roles
    scoped_roles: dict[str, Callable[[Mapping[str, Any], str], bool]] = field(default_factory=dict)  # held per scope
    # --- helpers -----------------------------------------------------------
    def now(self) -> int:
        return int(time.time())

    def uid(self, username: str) -> str:
        value = username.strip()
        if "@" in value:
            match = self.store.get("emails", self.email_key(value))
            if match:
                user = self.store.get("users", match["uuid"])
                if user and user.get("email", "").lower() == value.lower():
                    return match["uuid"]
        if value.startswith("google:"):
            match = self.store.get("google_subjects", value)
            if match:
                return match["uuid"]
        key = security.username_uuid(self.settings.secret, value)
        match = self.store.get("usernames", key)
        if match and match.get("uuid"):
            user = self.store.get("users", match["uuid"])
            if user and user.get("username_token") == match.get("token"):
                return match["uuid"]
        return key

    def email_key(self, email: str) -> str:
        return hashlib.sha256(email.lower().encode()).hexdigest()

    def plugin_setting(self, owner: str, name: str) -> Any:
        if self.plugin_settings is None:
            return getattr(self.settings, name)
        owner_settings = self.plugin_settings.get(owner)
        if owner_settings is None or name not in owner_settings:
            raise RuntimeError(f"setting {owner}.{name} is not registered for this composition")
        return owner_settings[name]

    def send_otp(self, purpose: str, subject: str, to: str, payload: dict | None = None) -> bool:
        """Issue a code unless the account is cooling down or over its per-window cap. Wrong-guess counts carry over to the
        new code within the window, so re-requesting codes cannot reset the guess budget. Returns False when throttled
        (callers must answer identically either way)."""
        s, now, key = self.settings, self.now(), f"{purpose}:{subject}"
        code, seen = security.new_otp(), {}

        def issue(o):
            if o and now - o.get("window", 0) < s.otp_window:
                window, sends, attempts = o["window"], o.get("sends", 0), 0 if o.get("used") else o["attempts"]
                if now - o.get("sent", 0) < s.otp_cooldown or sends >= s.otp_max_sends or attempts >= s.otp_attempts:
                    seen["ok"] = False
                    return None
            else:
                window, sends, attempts = now, 0, 0
            seen["ok"] = True
            return {"hash": security.hash_otp(s.secret, key, code), "exp": now + s.otp_ttl, "attempts": attempts,
                    "window": window, "sends": sends + 1, "sent": now, "payload": payload or {}}

        self.store.update("otps", key, issue)
        if not seen.get("ok"):
            return False
        try:
            self.email.send_template(
                to,
                "verification_code",
                {"code": code, "expires_minutes": self.settings.otp_ttl // 60},
                actor=subject,
            )
        except EmailNotConfigured as exc:
            raise HTTPException(503, "email delivery is not configured") from exc
        except EmailDeliveryUnknown as exc:
            raise HTTPException(502, "email delivery could not be confirmed; do not retry yet") from exc
        return True

    def check_otp(self, purpose: str, subject: str, code: str, consume: bool = True) -> dict | None:
        key = f"{purpose}:{subject}"
        seen: dict = {}

        def step(o):
            """Optimistic lock: the attempt is counted (and a correct code marked used) atomically, so neither parallel
            guesses nor a replayed correct code can get past the limits."""
            if not o or o.get("used") or o["exp"] < self.now() or o["attempts"] >= self.settings.otp_attempts:
                seen["ok"] = False
                return None
            ok = hmac.compare_digest(security.hash_otp(self.settings.secret, key, str(code)), o["hash"])
            seen["ok"], seen["payload"] = ok, o["payload"]
            if ok:
                return {**o, "used": True} if consume else None
            return {**o, "attempts": o["attempts"] + 1}

        try:
            self.store.update("otps", key, step)
        except Conflict:
            return None
        if not seen.get("ok"):
            return None
        if consume:
            self.store.delete("otps", key)
        return seen["payload"] or {}

    # --- auth dependencies ---------------------------------------------------
    def _account(self, authorization: str | None, token: str | None) -> dict:
        raw = token or (authorization or "").removeprefix("Bearer ").strip()
        acct = self.identity.authenticate(raw) if raw else None
        if not acct:
            raise HTTPException(401, "not authenticated")
        return acct

    def require_user(self) -> AuthDependency:
        def dep(authorization: str | None = Header(None)) -> dict[str, Any]:
            return self._account(authorization, None)
        return Depends(dep)

    def require_verified(self) -> AuthDependency:
        def dep(authorization: str | None = Header(None)) -> dict[str, Any]:
            a = self._account(authorization, None)
            if a["status"] != "verified":
                raise HTTPException(403, "account not verified")
            return a
        return Depends(dep)

    def require_rostered(self) -> AuthDependency:
        """Verified account that the central roster check found on the roster (the gate for program pages)."""
        def dep(authorization: str | None = Header(None)) -> dict[str, Any]:
            a = self._account(authorization, None)
            if a["status"] != "verified":
                raise HTTPException(403, "account not verified")
            check = self.identity.roster_check(a["uuid"])
            if not check or not check.get("rostered"):
                raise HTTPException(403, "not on the student roster")
            return a
        return Depends(dep)

    def require_role(self, *roles: str) -> AuthDependency:
        """Allow verified accounts holding `admin` (superset) or any of `roles`. No roles given = admin only."""
        want = {"admin", *roles}

        def dep(authorization: str | None = Header(None)) -> dict[str, Any]:
            a = self._account(authorization, None)
            if a["status"] != "verified" or not (roles_of(a, self.roles) & want):
                raise HTTPException(403, "insufficient role")
            return a
        return Depends(dep)

    def permissions_for_roles(self, *roles: str) -> frozenset[str]:
        """Return the permissions granted by active roles; used for compatibility integrations."""
        unknown = set(roles) - set(self.roles)
        if unknown:
            raise ValueError(f"unknown roles: {', '.join(sorted(unknown))}")
        return frozenset(scope for role in roles for scope in self.role_permissions.get(role, ()))

    def _validate_permissions(self, permissions: tuple[str, ...]) -> None:
        unknown = set(permissions) - set(self.permissions)
        if unknown:
            raise ValueError(f"unknown permissions: {', '.join(sorted(unknown))}")

    def derived_roles_of(self, account: Mapping[str, Any], only: set[str] | None = None) -> set[str]:
        """
        The membership roles (`MEMBER_ROLES`) and plug-in derived roles an account holds right now, or just those
        among `only`. They are computed from the roster check, never stored, so they follow the roster and the
        membership session as they change. Each is a keyed lookup for this one account, never a scan.
        """
        if account.get("status") != "verified":
            return set()
        from .services.account_check import is_active_member, is_live_member
        names = {*MEMBER_ROLES, *self.derived_roles} if only is None else only
        held: set[str] = set()
        if names & set(MEMBER_ROLES):
            check, now = self.identity.roster_check(account["uuid"]), self.now()
            if "live_member" in names and is_live_member(check, now):
                held.add("live_member")
            if "rostered_member" in names and is_active_member(check, now):
                held.add("rostered_member")
        return held | {role for role in names & set(self.derived_roles) if self.derived_roles[role](account)}

    def _derived_scopes(self) -> set[str]:
        names = {*MEMBER_ROLES, *self.derived_roles}
        return {scope for role in names for scope in self.role_permissions.get(role, ())}

    def _scoped_scopes(self) -> set[str]:
        return {scope for role in self.scoped_roles for scope in self.role_permissions.get(role, ())}

    def _granted(self, account: Mapping[str, Any], wanted: set[str], scope: str | None = None) -> set[str]:
        """
        The wanted permissions the account holds. `admin` covers all except those granted through a derived or scoped
        role: an administrator who is not a member (of the club) is no more a member than anyone else. Only the
        derived roles that could grant a still-missing permission are computed, and only when one is missing.
        A scoped permission is only ever granted for the one scope asked about (a club ID), so holding a role in one
        club grants nothing in another.
        """
        scoped = wanted & self._scoped_scopes()
        if scoped and not scope:
            raise ValueError(f"permissions need a scope: {', '.join(sorted(scoped))}")
        roles = roles_of(account, self.roles)
        granted = {scope_ for role in roles for scope_ in self.role_permissions.get(role, ())}
        derived = wanted & self._derived_scopes()
        if "admin" in roles:
            granted |= wanted - derived - scoped
        missing = derived - granted
        if missing:
            needed = {role for role in (*MEMBER_ROLES, *self.derived_roles)
                      if missing & set(self.role_permissions.get(role, ()))}
            granted |= {scope_ for role in self.derived_roles_of(account, needed)
                        for scope_ in self.role_permissions.get(role, ())}
        missing = scoped - granted
        if missing and scope and account.get("status") == "verified":
            for role, holds_in in self.scoped_roles.items():
                if missing & set(self.role_permissions.get(role, ())) and holds_in(account, scope):
                    granted |= set(self.role_permissions.get(role, ()))
        return granted & wanted

    def has_permission(self, account: Mapping[str, Any], *permissions: str, scope: str | None = None) -> bool:
        if not permissions:
            raise ValueError("at least one permission is required")
        self._validate_permissions(permissions)
        if account.get("status") != "verified":
            return False
        return self._granted(account, set(permissions), scope) == set(permissions)

    def has_any_permission(self, account: Mapping[str, Any], *permissions: str, scope: str | None = None) -> bool:
        if not permissions:
            raise ValueError("at least one permission is required")
        self._validate_permissions(permissions)
        if account.get("status") != "verified":
            return False
        return bool(self._granted(account, set(permissions), scope))

    def require_permission(self, *permissions: str) -> AuthDependency:
        """Require all named permissions on a verified account (OAuth scope semantics)."""
        required = frozenset(permissions)
        if not required:
            raise ValueError("at least one permission is required")
        self._validate_permissions(tuple(required))

        def dep(authorization: str | None = Header(None)) -> dict[str, Any]:
            account = self._account(authorization, None)
            if not self.has_permission(account, *required):
                scope = " ".join(sorted(required))
                raise HTTPException(
                    403,
                    "insufficient_scope",
                    headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'},
                )
            return account
        return Depends(dep)

    def require_admin(self) -> AuthDependency:
        return self.require_role()
