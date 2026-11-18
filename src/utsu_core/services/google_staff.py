"""Google Workspace sign-in for staff; independent of the portal's outbound SSO issuer."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import security
from ..context import Context

AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
JWKS = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPE = "google-staff"
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class Completion(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=512)
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


def _challenge(value: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()).decode().rstrip("=")


def _nonce(verifier: str) -> str:
    return _challenge(f"{SCOPE}:{verifier}")


def _exchange(fields: dict[str, str]) -> str:
    request = Request(TOKEN, data=urlencode(fields).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=8) as response:
            token = json.loads(response.read(65536))["id_token"]
        if not isinstance(token, str):
            raise TypeError("expected an ID token")
        return token
    except HTTPError as exc:
        if exc.code in (400, 401):
            raise HTTPException(400, "Google could not complete sign-in. Start again or contact your administrator.") from exc
        raise HTTPException(502, "Google sign-in is unavailable. Try again shortly.") from exc
    except (URLError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "Google sign-in is unavailable. Try again shortly.") from exc


def _provision(ctx: Context, claims: dict, subject: str, email: str) -> str:
    # A colon cannot occur in a self-registered username. Scope the handle to Google's stable subject, not email.
    username = "google:" + hmac.new(ctx.settings.secret.encode(), subject.encode(), hashlib.sha256).hexdigest()[:32]
    uid = ctx.uid(username)
    ekey = ctx.email_key(email)
    store = ctx.store
    account = store.get("accounts", uid)
    if account is not None:
        if (account.get("google_staff_username") != username or account.get("status") != "verified"
                or account.get("email_key") != ekey or (store.get("emails", ekey) or {}).get("uuid") != uid):
            raise HTTPException(403, "This staff account cannot sign in. Contact your administrator.")
        return uid

    if not store.create("emails", ekey, {"uuid": uid}) and (store.get("emails", ekey) or {}).get("uuid") != uid:
        raise HTTPException(409, "That email already belongs to a portal account. Use its existing sign-in or contact your administrator.")
    first = security.clean_text(claims.get("given_name") or "Staff", 100)
    last = security.clean_text(claims.get("family_name") or "", 100)
    store.create("users", uid, {"uuid": uid, "email": email, "first_name": first,
                                "last_name": last, "preferred_name": first, "student_number": ""})
    if (store.get("users", uid) or {}).get("email") != email:
        raise HTTPException(403, "This staff account cannot sign in. Contact your administrator.")
    # A concurrent first login can finish provisioning but cannot replace roles on an existing account.
    if store.create("accounts", uid, {"uuid": uid, "status": "verified", "role": "applicant", "roles": ["staff"],
                                      "email_verified": True, "email_key": ekey, "google_staff_username": username}):
        ctx.audit.record(uid, "account.staff_provisioned", f"user:{uid}", None, {"roles": ["staff"]}, {"provider": "google"})
    else:
        saved = store.get("accounts", uid) or {}
        if (saved.get("email_key") != ekey or saved.get("status") != "verified"
                or saved.get("google_staff_username") != username):
            raise HTTPException(403, "This staff account cannot sign in. Contact your administrator.")
    return uid


def register_routes(router: APIRouter, ctx: Context) -> None:
    cfg = ctx.settings
    domains = {domain.strip().lower() for domain in cfg.google_staff_domains}
    client_id, secret = cfg.google_client_id, cfg.google_client_secret
    enabled = bool(domains and client_id and secret)
    redirect_uri = cfg.sso_issuer.rstrip("/") + "/"
    if domains or client_id or secret:
        if not enabled or any(not DOMAIN.fullmatch(domain) for domain in domains):
            raise RuntimeError("Google staff sign-in requires a client ID, secret and exact Workspace domains")
        url = urlsplit(redirect_uri)
        if (not url.hostname or url.username or url.password or url.query or url.fragment or url.path != "/"
                or not (url.scheme == "https" or (url.scheme == "http" and url.hostname == "localhost"))):
            raise RuntimeError("Google staff sign-in requires PORTAL_BASE_URL to be the public HTTPS portal origin (or HTTP localhost)")
    keys = jwt.PyJWKClient(JWKS, timeout=8)

    @router.get("/auth/google")
    def available():
        return {"enabled": enabled}

    @router.post("/auth/google/start")
    def start():
        if not enabled:
            raise HTTPException(404, "Google staff sign-in is not enabled")
        verifier = secrets.token_urlsafe(32)
        challenge = _challenge(verifier)
        state = "google." + security.sign_token(cfg.secret, challenge, 600, SCOPE)
        params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
                  "scope": "openid email profile", "state": state, "nonce": _nonce(verifier),
                  "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
        if len(domains) == 1:
            params["hd"] = next(iter(domains))  # UI hint only; the signed claim is checked below.
        return {"url": f"{AUTHORIZE}?{urlencode(params)}", "state": state, "verifier": verifier}

    @router.post("/auth/google/complete")
    def complete(body: Completion):
        if not enabled or client_id is None or secret is None:
            raise HTTPException(404, "Google staff sign-in is not enabled")
        challenge = (security.read_token(cfg.secret, body.state[7:], SCOPE)
                     if body.state.startswith("google.") else None)
        if challenge is None or not hmac.compare_digest(challenge, _challenge(body.verifier)):
            raise HTTPException(400, "This Google sign-in attempt is invalid or expired. Start again.")
        # Google consumes the authorization code at exchange; the portal never issues a reusable completion code.
        token = _exchange({"code": body.code, "client_id": client_id, "client_secret": secret,
                           "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": body.verifier})
        try:
            key = keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id, issuer=list(ISSUERS),
                                options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"], "strict_aud": True})
        except jwt.PyJWKClientConnectionError as exc:
            raise HTTPException(502, "Google's sign-in keys are unavailable. Try again shortly.") from exc
        except jwt.PyJWTError as exc:
            raise HTTPException(403, "Google could not verify this sign-in. Start again.") from exc
        subject, email, hosted = claims.get("sub"), claims.get("email"), claims.get("hd")
        if (not isinstance(subject, str) or not subject or claims["nonce"] != _nonce(body.verifier)
                or claims.get("azp", client_id) != client_id):
            raise HTTPException(403, "Google could not verify this sign-in. Start again.")
        if (not isinstance(email, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+", email)
                or claims.get("email_verified") is not True or not isinstance(hosted, str)
                or hosted.lower() not in domains or email.lower().rsplit("@", 1)[1] != hosted.lower()):
            raise HTTPException(403, "Use a verified Google Workspace account in an approved staff domain.")
        uid = _provision(ctx, claims, subject, email.lower())
        ctx.audit.record(uid, "auth.login", f"user:{uid}", None, None, {"provider": "google"})
        return {"token": ctx.identity.issue(uid), "status": "verified"}
