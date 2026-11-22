import io
import json
from urllib.error import URLError
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from core import create_app
from core.mailer import OutboxMailer
from core.settings import Settings


BODY = {"email": "member@example.ca", "first_name": "A", "last_name": "B", "student_number": "1234567890"}


def client(**settings):
    return TestClient(create_app(Settings(secret="s" * 40, **settings), mailer=OutboxMailer()))


def test_registration_fails_closed_without_configuration():
    c = client()
    assert c.get("/api/auth/registration-challenge").status_code == 503
    assert c.post("/api/auth/register", json=BODY).status_code == 503
    assert not c.app.state.ctx.store.list("accounts")


def test_explicit_local_dev_bypasses_challenge():
    c = client(dev_admin=True)
    assert c.get("/api/auth/registration-challenge").json() == {"site_key": None, "local_bypass": True}
    assert c.post("/api/auth/register", json=BODY).status_code == 201
    with pytest.raises(RuntimeError, match="PORTAL_DEV_ADMIN"):
        client(dev_admin=True, sso_issuer="https://portal.example.ca")


def test_turnstile_verifies_token_action_and_hostname(monkeypatch):
    from core.services import turnstile

    c = client(turnstile_site_key="site", turnstile_secret_key="secret", sso_issuer="https://portal.example.ca")
    assert c.get("/api/auth/registration-challenge").json() == {"site_key": "site", "local_bypass": False}
    assert c.post("/api/auth/register", json=BODY).status_code == 422
    assert not c.app.state.ctx.store.list("accounts")
    result = {"success": True, "hostname": "portal.example.ca", "action": "register"}
    calls = []

    def fake_open(request, timeout):
        calls.append(parse_qs(request.data.decode()))
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(turnstile, "urlopen", fake_open)
    token = "x" * 150
    for bad in ({"success": False}, {"hostname": "other.example.ca"}, {"action": "other"}):
        original = result.copy()
        result.update(bad)
        assert c.post("/api/auth/register", json=BODY | {"challenge_token": token}).status_code == 422
        result.update(original)
    assert not c.app.state.ctx.store.list("accounts")
    assert c.post("/api/auth/register", json=BODY | {"challenge_token": token}).status_code == 201
    assert calls[-1] == {"secret": ["secret"], "response": [token]}
    csp = c.get("/api/auth/registration-challenge").headers["content-security-policy"]
    assert "script-src 'self' https://challenges.cloudflare.com" in csp
    assert "frame-src https://challenges.cloudflare.com" in csp


def test_turnstile_network_failure_is_not_a_success(monkeypatch):
    from core.services import turnstile

    def unavailable(*_args, **_kwargs):
        raise URLError("unavailable")

    monkeypatch.setattr(turnstile, "urlopen", unavailable)
    c = client(turnstile_site_key="site", turnstile_secret_key="secret")
    assert c.post("/api/auth/register", json=BODY | {"challenge_token": "token"}).status_code == 503
    assert not c.app.state.ctx.store.list("accounts")
