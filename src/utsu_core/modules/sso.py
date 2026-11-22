"""Generic OpenID Connect identity provider (authorization-code flow, RS256 ID tokens).

Other modules register relying parties with `ctx.services["sso"].register_client(...)` and supply an `eligibility`
callback that decides, per sign-on, whether the account may use that client. Subjects are pairwise per client, so a
relying party (e.g. Zoom) never sees the portal's internal uuid. Only name and email claims are released.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import security
from ..context import Context
from ..store import Conflict

name = "sso"
CODE_TTL, TOKEN_TTL = 60, 3600
OIDC_SCOPES = ("openid", "profile", "email")
DEFAULT_SCOPES = OIDC_SCOPES
_PKCE_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_PKCE_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
Eligibility = Callable[[dict, dict], "str | None"]  # (account, user) -> error message, or None when allowed


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _int_b64(n: int) -> str:
    return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@dataclass
class Client:
    id: str
    secret_hash: str
    redirect_uris: tuple[str, ...]
    scopes: tuple[str, ...]
    eligibility: Eligibility = field(default=lambda a, u: None)


class SSOError(Exception):
    def __init__(self, error: str, desc: str = "", status: int = 400):
        self.error, self.desc, self.status = error, desc, status


class SSOProvider:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        pem = ctx.settings.sso_key_pem
        self.key = (serialization.load_pem_private_key(pem.encode(), None) if pem
                    else rsa.generate_private_key(public_exponent=65537, key_size=2048))
        pub = self.key.public_key().public_numbers()
        self.jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "n": _int_b64(pub.n), "e": _int_b64(pub.e)}
        self.jwk["kid"] = _b64(hashlib.sha256(json.dumps(self.jwk, sort_keys=True).encode()).digest())[:16]
        self.clients: dict[str, Client] = {}

    @property
    def issuer(self) -> str:
        return self.ctx.plugin_setting("sso", "sso_issuer")

    def register_client(
        self,
        client_id: str,
        secret: str,
        redirect_uris: tuple[str, ...],
        eligibility: Eligibility | None = None,
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
    ) -> None:
        allowed_scopes = tuple(dict.fromkeys(scopes))
        if "openid" not in allowed_scopes or set(allowed_scopes) - set(OIDC_SCOPES):
            raise ValueError("SSO clients may only register supported OpenID Connect scopes")
        self.clients[client_id] = Client(
            client_id, _sha(secret), tuple(redirect_uris), allowed_scopes, eligibility or (lambda a, u: None)
        )

    def subject(self, client_id: str, uid: str) -> str:
        return security._hid(self.ctx.settings.secret, f"sso:{client_id}", uid)

    def claims(self, client_id: str, user: dict, scopes: tuple[str, ...]) -> dict:
        claims = {"sub": self.subject(client_id, user["uuid"])}
        if "email" in scopes:
            claims.update(email=user["email"], email_verified=True)
        if "profile" in scopes:
            claims.update(
                given_name=user["first_name"],
                family_name=user["last_name"],
                name=f"{user['preferred_name']} {user['last_name']}".strip(),
                preferred_username=user["email"],
            )
        return claims

    @staticmethod
    def _parse_scopes(value: str | None) -> tuple[str, ...]:
        if value is None:
            return DEFAULT_SCOPES
        scopes = value.split(" ")
        if (not value or any(not scope or not scope.isascii() or not scope.isalnum()
                             for scope in scopes) or "openid" not in scopes):
            raise ValueError("invalid scope")
        return tuple(dict.fromkeys(scopes))

    def _sign(self, payload: dict) -> str:
        head = _b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": self.jwk["kid"]}).encode())
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        sig = self.key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{_b64(sig)}"

    # -- authorization endpoint (called with the user's portal session) ------------------------------
    def authorize(self, acct: dict, user: dict, client_id: str, redirect_uri: str, state: str = "", nonce: str = "",
                  challenge: str = "", method: str = "S256", scope: str | None = None) -> str:
        c = self.clients.get(client_id)
        if not c or redirect_uri not in c.redirect_uris:  # never redirect to an unregistered URI
            raise HTTPException(400, "unknown client or redirect_uri")
        try:
            scopes = (tuple(value for value in DEFAULT_SCOPES if value in c.scopes)
                      if scope is None else self._parse_scopes(scope))
        except ValueError as exc:
            raise HTTPException(400, "invalid scope") from exc
        if not set(scopes) <= set(c.scopes):
            raise HTTPException(400, "unsupported scope")
        if acct["status"] != "verified":
            raise HTTPException(403, "account not verified")
        if challenge and method != "S256":
            raise HTTPException(400, "only S256 PKCE is supported")
        if challenge and not _PKCE_CHALLENGE.fullmatch(challenge):
            raise HTTPException(400, "invalid PKCE challenge")
        why = c.eligibility(acct, user)
        if why:
            raise HTTPException(403, why)
        code = secrets.token_urlsafe(32)
        self.ctx.store.put("sso_codes", _sha(code), {"uuid": acct["uuid"], "client": client_id, "redirect_uri": redirect_uri,
                                                      "nonce": nonce[:200], "challenge": challenge, "scopes": list(scopes),
                                                      "exp": self.ctx.now() + CODE_TTL})
        parts = urlsplit(redirect_uri)
        q = urlencode([("code", code)] + ([("state", state)] if state else []))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, f"{parts.query}&{q}" if parts.query else q, ""))

    # -- token endpoint -------------------------------------------------------------------------------
    def exchange(self, form: dict[str, str], basic: tuple[str, str] | None) -> dict:
        cid, secret = (basic or (form.get("client_id", ""), form.get("client_secret", "")))
        c = self.clients.get(cid)
        if not c or not hmac.compare_digest(c.secret_hash, _sha(secret)):
            raise SSOError("invalid_client", status=401)
        if form.get("grant_type") != "authorization_code":
            raise SSOError("unsupported_grant_type")
        key = _sha(form.get("code", ""))
        def redeem(r):  # optimistic lock: of two concurrent redemptions of one code, exactly one succeeds
            if not r or r.get("used"):
                raise SSOError("invalid_grant")
            return {**r, "used": True}

        try:
            rec = self.ctx.store.update("sso_codes", key, redeem)
        except Conflict:
            raise SSOError("invalid_grant")
        finally:
            self.ctx.store.delete("sso_codes", key)  # single use, even on failure
        if not rec or rec["exp"] < self.ctx.now() or rec["client"] != cid or rec["redirect_uri"] != form.get("redirect_uri"):
            raise SSOError("invalid_grant")
        scopes = tuple(rec.get("scopes", DEFAULT_SCOPES))
        if "scope" in form:
            try:
                requested_scopes = self._parse_scopes(form["scope"])
            except ValueError as exc:
                raise SSOError("invalid_scope") from exc
            if not set(requested_scopes) <= set(scopes):
                raise SSOError("invalid_scope")
            scopes = requested_scopes
        if rec["challenge"]:
            verifier = form.get("code_verifier", "")
            if not _PKCE_VERIFIER.fullmatch(verifier):
                raise SSOError("invalid_grant", "PKCE verification failed")
            v = _b64(hashlib.sha256(verifier.encode()).digest())
            if not hmac.compare_digest(v, rec["challenge"]):
                raise SSOError("invalid_grant", "PKCE verification failed")
        user, acct = self.ctx.identity.user(rec["uuid"]), self.ctx.identity.account(rec["uuid"])
        if not user or not acct or acct["status"] != "verified" or c.eligibility(acct, user):
            raise SSOError("invalid_grant", "no longer eligible")  # re-checked: access may have been revoked since authorize
        now = self.ctx.now()
        cl = self.claims(cid, user, scopes)
        idt = {"iss": self.issuer, "aud": cid, "iat": now, "exp": now + TOKEN_TTL, **cl, **({"nonce": rec["nonce"]} if rec["nonce"] else {})}
        at = secrets.token_urlsafe(32)
        self.ctx.store.put("sso_tokens", _sha(at), {"uuid": rec["uuid"], "client": cid, "scopes": list(scopes),
                                                     "exp": now + TOKEN_TTL})
        return {"access_token": at, "token_type": "Bearer", "expires_in": TOKEN_TTL, "id_token": self._sign(idt),
                "scope": " ".join(scopes)}

    def userinfo(self, access_token: str) -> dict:
        t = self.ctx.store.get("sso_tokens", _sha(access_token))
        user = t and t["exp"] > self.ctx.now() and self.ctx.identity.user(t["uuid"])
        if not user:
            raise HTTPException(401, "invalid token")
        return self.claims(t["client"], user, tuple(t.get("scopes", DEFAULT_SCOPES)))


class AuthorizeIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    response_type: str = Field(default="code", max_length=16)
    scope: str | None = Field(default=None, max_length=512)
    state: str = Field(default="", max_length=1024)
    nonce: str = Field(default="", max_length=200)
    code_challenge: str = Field(default="", max_length=128)
    code_challenge_method: str = Field(default="S256", max_length=16)


class SSOModule:
    name = name

    def configure(self, ctx: Context) -> None:
        if "sso" not in ctx.services:
            ctx.services["sso"] = SSOProvider(ctx)

    def router(self, ctx: Context) -> APIRouter:
        provider = ctx.services["sso"]
        r = APIRouter()
        verified = ctx.require_verified()

        @r.get("/sso/.well-known/openid-configuration")
        def discovery():
            i = provider.issuer
            return {"issuer": i, "authorization_endpoint": f"{i}/sso/authorize", "token_endpoint": f"{i}/sso/token",
                    "userinfo_endpoint": f"{i}/sso/userinfo", "jwks_uri": f"{i}/sso/jwks", "response_types_supported": ["code"],
                    "subject_types_supported": ["pairwise"], "id_token_signing_alg_values_supported": ["RS256"],
                    "scopes_supported": ["openid", "profile", "email"], "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"]}

        @r.get("/sso/jwks")
        def jwks():
            return {"keys": [provider.jwk]}

        @r.get("/sso/authorize", include_in_schema=False)
        def authorize_page():
            """Browser entry point used by relying parties; the page completes the flow with the portal session."""
            return FileResponse(__file__.rsplit("modules", 1)[0] + "static/sso.html")

        @r.post("/api/sso/authorize")
        def authorize(b: AuthorizeIn, a: dict = verified):
            if b.response_type != "code":
                raise HTTPException(400, "unsupported response_type")
            url = provider.authorize(a, ctx.identity.user(a["uuid"]), b.client_id, b.redirect_uri, b.state, b.nonce,
                                     b.code_challenge, b.code_challenge_method, b.scope)
            return {"redirect": url}

        @r.post("/sso/token")
        async def token(request: Request):
            raw = await request.body()
            if len(raw) > 4096:
                return JSONResponse({"error": "invalid_request"}, status_code=400, headers={"Cache-Control": "no-store"})
            try:
                fields = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=16, strict_parsing=True)
            except (UnicodeDecodeError, ValueError):
                return JSONResponse({"error": "invalid_request"}, status_code=400, headers={"Cache-Control": "no-store"})
            if any(len(values) != 1 for values in fields.values()) or any(
                len(key) > 64 or len(values[0]) > 2048 for key, values in fields.items()
            ):
                return JSONResponse({"error": "invalid_request"}, status_code=400, headers={"Cache-Control": "no-store"})
            form = {key: values[0] for key, values in fields.items()}
            basic = None
            h = request.headers.get("authorization", "")
            if h.lower().startswith("basic "):
                try:
                    u, _, p = base64.b64decode(h[6:]).decode().partition(":")
                    basic = (u, p)
                except Exception:
                    raise HTTPException(401, "bad basic auth")
            try:
                return JSONResponse(provider.exchange(form, basic), headers={"Cache-Control": "no-store"})
            except SSOError as e:
                return JSONResponse({"error": e.error, "error_description": e.desc}, status_code=e.status, headers={"Cache-Control": "no-store"})

        @r.get("/sso/userinfo")
        def userinfo(request: Request):
            h = request.headers.get("authorization", "")
            return provider.userinfo(h[7:] if h.lower().startswith("bearer ") else "")

        return r
