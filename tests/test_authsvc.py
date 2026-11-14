import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_portal import activate, login, register  # noqa: E402

from utsu_core import create_app, security
from utsu_core.authsvc import create_auth_app
from utsu_core.identity import RemoteIdentity
from utsu_core.mailer import OutboxMailer
from utsu_core.programs.senate.module import PREFERENCES
from utsu_core.settings import Settings
from utsu_core.store import MemoryStore

SVC = "svc-" + "t" * 40


def make(auth_pem=None):
    mail = OutboxMailer()
    astore, pstore = MemoryStore(), MemoryStore()
    key = security.load_signing_key(auth_pem, "a" * 40)
    auth = create_auth_app(Settings(secret="a" * 40, admin_emails=("boss@x.ca",), service_token=SVC), astore, mail)
    ac = TestClient(auth)
    # the auth service signs with a key derived from ITS secret; the portal only receives the public half
    pub = security.public_pem(auth.state.ctx.identity.key)

    def fetch(path, body=None):
        r = ac.post(path, json=body, headers={"Authorization": "Bearer " + SVC}) if body is not None else ac.get(path, headers={"Authorization": "Bearer " + SVC})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    portal = create_app(Settings(secret="p" * 40), pstore, mail, identity=RemoteIdentity(pub, fetch))
    return ac, TestClient(portal), mail, astore, pstore


def test_split_deployment_roundtrip():
    ac, pc, mail, astore, pstore = make()
    register(ac, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    ah = login(ac, mail, "boss")
    register(ac, mail, "ada", "ada@x.ca")
    uh = login(ac, mail, "ada")
    # a token minted by the auth service works on the portal...
    assert pc.get("/api/me/submissions", headers=uh).status_code == 200
    assert pc.get("/api/admin/audit", headers=uh).status_code == 403
    assert pc.get("/api/admin/audit", headers=ah).status_code == 200
    # ...the portal itself has no identity surface and stores no accounts
    assert pc.post("/api/auth/register", json={}).status_code in (404, 405)
    assert pc.get("/api/admin/verifications", headers=ah).status_code == 404
    assert not pstore.list("accounts") and not pstore.list("users")
    assert astore.list("accounts") and not astore.list("form_versions")
    f = pc.get("/api/forms/agm_rsvp/static").json()
    body = {"version": f["version"], "hash": f["hash"], "data": {}}
    assert pc.put("/api/forms/agm_rsvp/submission", json=body, headers=uh).status_code == 403  # not verified yet
    activate(ac, ah, uh)
    pc.app.state.ctx.identity._cache.clear()  # status lookups are cached for RemoteIdentity.CACHE_TTL seconds
    assert pc.put("/api/forms/agm_rsvp/submission", json={"version": f["version"], "hash": f["hash"], "data": {}}, headers=uh).status_code != 403


def test_portal_cannot_mint_or_replay_tokens():
    ac, pc, mail, *_ = make()
    register(ac, mail, "ada", "ada@x.ca")
    uid = ac.app.state.ctx.uid("ada")
    forged = security.sign_user_token(security.load_signing_key(None, "p" * 40), uid, 600)  # portal's own derived key
    assert pc.get("/api/me/submissions", headers={"Authorization": "Bearer " + forged}).status_code == 401
    old = security.sign_token("p" * 40, uid, 600)  # legacy HMAC format
    assert pc.get("/api/me/submissions", headers={"Authorization": "Bearer " + old}).status_code == 401
    tok = login(ac, mail, "ada")["Authorization"][7:]
    body, signature = tok.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered = ".".join((body, replacement + signature[1:]))
    assert pc.get("/api/me/submissions", headers={"Authorization": "Bearer " + tampered}).status_code == 401


def test_internal_api_requires_service_token():
    ac, *_ = make()
    assert ac.get("/internal/accounts/x").status_code == 404
    assert ac.get("/internal/ping", headers={"Authorization": "******"}).status_code == 404
    assert ac.get("/internal/ping", headers={"Authorization": "Bearer " + SVC}).status_code == 200
    # disabled entirely when no service token is configured
    bare = TestClient(create_auth_app(Settings(secret="a" * 40), MemoryStore(), OutboxMailer()))
    assert bare.get("/internal/ping", headers={"Authorization": "Bearer " + SVC}).status_code == 404


def test_portal_fails_closed_when_auth_down():
    ac, pc, mail, *_ = make()
    register(ac, mail, "ada", "ada@x.ca")
    tok = login(ac, mail, "ada")
    def boom(*a, **k):
        raise ConnectionError
    pc.app.state.ctx.identity.fetch = boom
    pc.app.state.ctx.identity._cache.clear()
    assert pc.get("/api/me/submissions", headers=tok).status_code == 401
    assert pc.get("/readyz").status_code == 503


def test_remote_config_requires_keys():
    with pytest.raises(RuntimeError):
        create_app(Settings(secret="p" * 40, auth_url="https://auth.internal"), MemoryStore())


def test_split_roster_drives_program_eligibility_and_seating():
    ac, pc, mail, astore, pstore = make()
    register(ac, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(ac, mail, "boss")
    register(ac, mail, "ada", "ada@x.ca")
    user = login(ac, mail, "ada")
    activate(ac, admin, user)
    assert pc.get("/api/agm/invitation", headers=user).status_code == 403
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert ac.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    assert pc.get("/api/agm/invitation", headers=user).status_code == 200
    assert pc.post("/api/admin/agm/verify", json={"full_name": "Ada Lovelace", "student_number": "6789"},
                   headers=admin).json()["verified"]
    form = pc.get("/api/forms/senate_application/static").json()
    data = {"division": "UTSG", "college": "LAW", "year_of_study": "2", "seat_type": "Undergraduate",
            "statement": "A" * 50, "consent": True, **{field: "Not interested" for field in PREFERENCES},
            "pref_faculty": "First-choice"}
    assert pc.put("/api/forms/senate_application/submission",
                  json={"version": form["version"], "hash": form["hash"], "data": data}, headers=user).status_code == 200
    assert pc.get("/api/me/senate", headers=user).json()["eligible"]
    uid = ac.get("/api/me", headers=user).json()["user"]["uuid"]
    preview = pc.post("/api/admin/notices/senate/preview", json={"uid": uid}, headers=admin)
    assert preview.status_code == 200 and preview.json()["to"] == "ada@x.ca"
    assert pc.post("/api/admin/notices/senate/send",
                   json={"uid": uid, "preview_hash": preview.json()["preview_hash"], "confirm": True},
                   headers=admin).status_code == 200
    assert pc.put("/api/admin/senate/seats",
                  json={"seats": [{"constituency": "Faculty/College", "division": "UC", "seats": 1}]},
                  headers=admin).status_code == 200
    assert pc.post("/api/admin/senate/seating", json={"seed": 1}, headers=admin).json()["result"]["seated"][0]["division"] == "UC"
    assert not pstore.list("roster_checks") and not pstore.list("roster_name")
    astore.delete("roster_identifiers", uid)
    assert pc.get("/api/agm/invitation", headers=user).status_code == 403
    assert ac.post("/api/me/roster-number", json={"student_number": "1234567890"}, headers=user).status_code == 200
    assert pc.get("/api/agm/invitation", headers=user).status_code == 200
    assert ac.get("/internal/roster/accounts/x").status_code == 404
    assert ac.post("/internal/roster/match", json={"full_name": "Ada Lovelace"}).status_code == 404
    other_roster = roster.replace("Ada,Lovelace", "Other,Person")
    assert ac.post("/api/admin/roster", json={"csv": other_roster}, headers=admin).status_code == 200
    assert pc.get("/api/agm/invitation", headers=user).status_code == 403
    assert not pc.get("/api/me/senate", headers=user).json()["eligible"]

    identity = pc.app.state.ctx.identity
    original_fetch = identity.fetch
    def failed_roster(path, body=None):
        if path.startswith("/internal/roster/"):
            raise ConnectionError("roster unavailable")
        return original_fetch(path, body)
    identity.fetch = failed_roster
    assert pc.get("/api/agm/invitation", headers=user).status_code == 503
    assert pc.post("/api/admin/agm/verify", json={"full_name": "Ada Lovelace"}, headers=admin).status_code == 503
