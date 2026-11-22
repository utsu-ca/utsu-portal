import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from test_portal import login, register
from core import create_app
from core.authsvc import create_auth_app
from core.mailer import OutboxMailer
from core.services import google_staff
from core.settings import Settings
from core.store import MemoryStore

BASE = "/api/auth/google"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(params=[create_app, create_auth_app], ids=["local", "standalone-auth"])
def workspace(request, monkeypatch, signing_key):
    settings = Settings(secret="a" * 40, service_token="s" * 40,
                        admin_emails=("boss@example.ca", "staff@example.ca"),
                        sso_issuer="https://portal.example.ca", google_staff_domains=("example.ca",),
                        google_client_id="portal-client", google_client_secret="client-secret")
    mail, store = OutboxMailer(), MemoryStore()
    client = TestClient(request.param(settings, store, mail))
    tokens = {}

    def exchange(fields):
        assert fields["redirect_uri"] == "https://portal.example.ca/"
        assert fields["client_secret"] == settings.google_client_secret
        claim = tokens.pop(fields["code"], None)
        if claim is None:
            raise HTTPException(400, "Google rejected the used authorization code")
        verifier, token = claim
        assert fields["code_verifier"] == verifier
        return token

    monkeypatch.setattr(google_staff, "_exchange", exchange)
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt",
                        lambda self, token: SimpleNamespace(key=signing_key.public_key()))

    def attempt(overrides=None, signing_algorithm="RS256"):
        start = client.post(BASE + "/start").json()
        url = urlsplit(start["url"])
        params = parse_qs(url.query)
        assert url.hostname == "accounts.google.com" and params["code_challenge_method"] == ["S256"]
        assert params["state"] == [start["state"]] and "client_secret" not in params
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": settings.google_client_id,
                  "iat": now, "exp": now + 300, "sub": "google-user-001", "nonce": params["nonce"][0],
                  "email": "staff@example.ca", "email_verified": True, "hd": "example.ca",
                  "given_name": "Staff", "family_name": "Member"} | (overrides or {})
        code = f"code-{start['verifier']}"
        key = signing_key if signing_algorithm == "RS256" else "test-secret" * 4
        tokens[code] = (start["verifier"], jwt.encode(claims, key, algorithm=signing_algorithm))
        return {"code": code, "state": start["state"], "verifier": start["verifier"]}

    return client, store, mail, attempt


def test_google_staff_is_opt_in():
    client = TestClient(create_app(Settings(secret="a" * 40), MemoryStore(), OutboxMailer()))
    assert client.get(BASE).json() == {"enabled": False}
    assert client.post(BASE + "/start").status_code == 404


def test_google_staff_sign_in_has_a_tighter_ip_limit(workspace):
    client, *_ = workspace
    assert all(client.post(BASE + "/start").status_code == 200 for _ in range(20))
    limited = client.post(BASE + "/start")
    assert limited.status_code == 429
    assert limited.headers["retry-after"]


def test_staff_provisioning_promotion_and_replay(workspace):
    client, store, mail, attempt = workspace
    body = attempt()
    assert client.post(BASE + "/complete", json=body | {"verifier": "a" * 43}).status_code == 400
    result = client.post(BASE + "/complete", json=body)
    assert result.status_code == 200, result.text
    assert client.post(BASE + "/complete", json=body).status_code == 400
    headers = {"Authorization": f"Bearer {result.json()['token']}"}
    me = client.get("/api/me", headers=headers).json()
    assert uuid.UUID(me["user"]["uuid"]).version == 4
    assert me["roles"] == ["staff"] and me["staff_sso"] is True
    assert not me["user"]["student_number"]
    assert not store.list("roster_identifiers") and not store.list("student_ids")
    assert client.post("/api/me/roster-number", json={"student_number": "1234567890"}, headers=headers).status_code == 403
    assert client.get("/api/admin/roles", headers=headers).status_code == 403

    register(client, mail, "boss", "boss@example.ca")
    admin = login(client, mail, "boss")
    holders = client.get("/api/admin/roles/holders", headers=admin).json()["items"]
    holder = next(h for h in holders if h.get("email") == "staff@example.ca")
    username = holder["username"]
    change = {"username": username, "roles": ["staff", "admin"]}
    assert client.put("/api/admin/roles", headers=headers, json=change).status_code == 403
    assert client.put("/api/admin/roles", headers=admin, json=change).status_code == 200
    again = client.post(BASE + "/complete", json=attempt()).json()
    signed_in = {"Authorization": f"Bearer {again['token']}"}
    assert client.get("/api/admin/roles", headers=signed_in).status_code == 200
    assert len(store.list("accounts")) == 2
    assert client.put("/api/admin/roles", headers=admin, json=change | {"roles": []}).status_code == 200
    assert client.get("/api/admin/roles", headers=signed_in).status_code == 403
    again = client.post(BASE + "/complete", json=attempt()).json()
    assert client.get("/api/me", headers={"Authorization": f"Bearer {again['token']}"}).json()["roles"] == []

    mail_count = len(mail.outbox)
    assert client.post("/api/auth/login-email", json={"email": "staff@example.ca"}).status_code == 200
    assert client.post("/api/auth/token-email", json={"email": "staff@example.ca", "code": "000000"}).status_code == 400
    client.post("/api/recovery/start", json={"username": username, "student_number_trunc": "0000"})
    assert len(mail.outbox) == mail_count


def test_workspace_requires_signed_approved_identity_and_never_links_email(workspace):
    client, store, mail, attempt = workspace
    for fields in ({"hd": None}, {"hd": "sub.example.ca", "email": "staff@sub.example.ca"},
                   {"email_verified": False}, {"nonce": "wrong"}, {"aud": "wrong"},
                   {"iss": "https://wrong.example.ca"}, {"exp": 1}):
        assert client.post(BASE + "/complete", json=attempt(fields)).status_code == 403
    assert client.post(BASE + "/complete", json=attempt(signing_algorithm="HS256")).status_code == 403
    assert not store.list("accounts")

    register(client, mail, "legacy", "staff@example.ca")
    result = client.post(BASE + "/complete", json=attempt())
    assert result.status_code == 409
    accounts = store.list("accounts")
    assert len(accounts) == 1 and not accounts[0].get("google_staff_username")


def test_google_bootstrap_is_exact_and_only_used_once(workspace):
    client, store, mail, attempt = workspace
    settings = client.app.state.ctx.settings
    settings.google_bootstrap_admin_email = "first@example.ca"
    ordinary = client.post(BASE + "/complete", json=attempt())
    assert ordinary.status_code == 200 and not store.list("auth_bootstrap")
    first = {"email": "first@example.ca", "sub": "first-sub"}
    result = client.post(BASE + "/complete", json=attempt(first))
    assert result.status_code == 200, result.text
    auth = {"Authorization": f"Bearer {result.json()['token']}"}
    assert client.get("/api/me", headers=auth).json()["roles"] == ["admin", "staff"]
    assert client.get("/api/admin/roles", headers=auth).status_code == 200
    assert store.get("auth_bootstrap", "google-admin")["completed"] is True
    settings.google_bootstrap_admin_email = "second@example.ca"
    result = client.post(BASE + "/complete", json=attempt({"email": "second@example.ca", "sub": "second-sub"}))
    auth = {"Authorization": f"Bearer {result.json()['token']}"}
    assert client.get("/api/me", headers=auth).json()["roles"] == ["staff"]


def test_initial_admin_configuration_disallows_domains_and_unapproved_addresses():
    settings = Settings(secret="a" * 40, google_staff_domains=("example.ca",),
                        google_client_id="portal-client", google_client_secret="client-secret")
    for invalid in ("example.ca", "admin@other.example.ca"):
        settings.google_bootstrap_admin_email = invalid
        with pytest.raises(RuntimeError, match="one exact email"):
            create_app(settings, MemoryStore(), OutboxMailer())
