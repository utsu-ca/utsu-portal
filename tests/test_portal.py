import base64
import pytest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from fastapi import HTTPException

from utsu_core import create_app, formdef, security
from utsu_core.contracts import SubmissionPolicy
from utsu_core.mailer import OutboxMailer
from utsu_core.services.submissions import (
    get_submission_policy,
    validate_submission_eligibility,
)
from utsu_core.settings import Settings

IMG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr("utsu_core.services.turnstile.verify_registration", lambda _settings, _token: None)
    mail = OutboxMailer()
    app = create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), mailer=mail)
    return TestClient(app), mail


def code(mail):
    return mail.outbox[-1][2].split("code is ")[1][:6]


def register(c, mail, user, email, sn="1234567890", first="Ada", last="Lovelace"):
    with patch("utsu_core.services.turnstile.verify_registration"):
        r = c.post("/api/auth/register", json={"username": user, "email": email, "first_name": first, "last_name": last, "student_number": sn})
    assert r.status_code == 201, r.text
    assert c.post("/api/auth/verify-email", json={"username": user, "code": code(mail)}).status_code == 200


def login(c, mail, user):
    ctx = c.app.state.ctx
    email = ctx.store.get("users", ctx.uid(user))["email"]
    c.post("/api/auth/login-email", json={"email": email})
    r = c.post("/api/auth/token-email", json={"email": email, "code": code(mail)})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def scan(c, h, **extra):
    ch = c.get("/api/verification/challenge").json()["challenge"]
    return {"first_name": "Ada", "last_name": "Lovelace", "division_codes": ["UTSG"], "face": IMG, "id_card": IMG, "challenge": ch, **extra}


def setup_admin_and_user(c, mail):
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    ah = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    uh = login(c, mail, "ada")
    return ah, uh


def activate(c, ah, uh):
    r = c.post("/api/verification", json=scan(c, uh), headers=uh)
    assert r.status_code == 201, r.text
    assert c.post(f"/api/admin/verifications/{r.json()['id']}/decision", json={"approve": True}, headers=ah).status_code == 200


def test_admin_roster_listing_masks_and_paginates(env):
    c, mail = env
    ah, uh = setup_admin_and_user(c, mail)
    register(c, mail, "dani", "dani@x.ca", sn="1234519200", first="Dani", last="Chang")
    roster = ("First Name,Last Name,Truncated Student Number,Faculty,Division\n"
              "Ada,Lovelace,xxxxx6789x,ARTSC,UC\n"
              "Dani,Chang,xxxxx1920x,ARTSC,UC\n"
              "No,Account,xxxxx0001x,ARTSC,UC\n")
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=ah).status_code == 200
    assert c.get("/api/admin/roster").status_code == 401
    assert c.get("/api/admin/roster", headers=uh).status_code == 403
    assert c.get("/api/admin/roster?limit=101", headers=ah).status_code == 422
    assert c.get("/api/admin/roster?offset=-1", headers=ah).status_code == 422

    first = c.get("/api/admin/roster?offset=0&limit=1", headers=ah).json()
    ctx = c.app.state.ctx
    ada = ctx.store.get("users", ctx.uid("ada"))
    login_event = next(e for e in ctx.audit.entries() if e["actor"] == ada["uuid"] and e["action"] == "auth.login")
    assert first == {"items": [{"name": "Ada L.", "student_number": "xxxx6789",
                                "email": "ada@x.ca", "last_login": login_event["ts"]}],
                     "total": 3, "offset": 0, "limit": 1}
    rest = c.get("/api/admin/roster?offset=1&limit=2", headers=ah).json()
    assert rest["items"] == [
        {"name": "Dani C.", "student_number": "xxxx1920", "email": "dani@x.ca", "last_login": None},
        {"name": "No A.", "student_number": "xxxx0001", "email": None, "last_login": None},
    ]
    assert c.get("/api/admin/roster?offset=3", headers=ah).json()["items"] == []

    # A second account can match the same name and truncated digits; neither email may be attributed.
    register(c, mail, "ada2", "ada2@x.ca", sn="2222267890")
    duplicate = c.get("/api/admin/roster?limit=1", headers=ah).json()["items"][0]
    assert duplicate["email"] is None and duplicate["last_login"] is None


def test_admin_roster_listing_does_not_expose_fuzzy_matches(env):
    c, mail = env
    ah, _ = setup_admin_and_user(c, mail)
    roster = ("First Name,Last Name,Truncated Student Number,Faculty,Division\n"
              "Adaa,Lovelace,xxxxx6789x,ARTSC,UC\n")
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=ah).status_code == 200
    row = c.get("/api/admin/roster", headers=ah).json()["items"][0]
    assert row == {"name": "Adaa L.", "student_number": "xxxx6789", "email": None, "last_login": None}


def test_admin_roster_listing_does_not_guess_between_duplicate_roster_entries(env):
    c, mail = env
    ah, _ = setup_admin_and_user(c, mail)
    roster = ("First Name,Last Name,Truncated Student Number,Faculty,Division\n"
              "Ada,Lovelace,xxxxx6789x,ARTSC,UC\n"
              "Ada,Lovelace,xxxxx6789x,ARTSC,TRIN\n")
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=ah).status_code == 200
    first = c.get("/api/admin/roster?limit=1", headers=ah).json()["items"][0]
    second = c.get("/api/admin/roster?offset=1&limit=1", headers=ah).json()["items"][0]
    assert first["email"] is None and second["email"] is None


def test_admin_roster_listing_reads_archived_backup_logins(env):
    import gzip
    import hashlib
    import json

    from utsu_core import audit

    c, mail = env
    ah, _ = setup_admin_and_user(c, mail)
    ctx = c.app.state.ctx
    uid = ctx.uid("ada")
    with patch.object(ctx, "now", return_value=946684800):
        event = ctx.audit.record(uid, "auth.backup_login", f"user:{uid}")
    month = audit.month_of(event["ts"])
    raw = gzip.compress(json.dumps(event).encode())
    ctx.store.put("audit_archive", month, {"id": month, "month": month, "blob": base64.b64encode(raw).decode(),
                                           "sha256": hashlib.sha256(raw).hexdigest()})
    ctx.store.delete(audit.coll(month), event["id"])
    ctx.store.update("audit_months", month, lambda row: {**row, "state": "dropped"})
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=ah).status_code == 200
    assert c.get("/api/admin/roster", headers=ah).json()["items"][0]["last_login"] == event["ts"]


def test_email_code_signin(env):
    c, mail = env
    register(c, mail, "ada", "ada@x.ca")
    response = c.post("/api/auth/login-email", json={"email": " ADA@X.CA "})
    assert response.status_code == 200
    otp = code(mail)
    result = c.post("/api/auth/token-email", json={"email": "ada@x.ca", "code": otp})
    assert result.status_code == 200
    headers = {"Authorization": f"Bearer {result.json()['token']}"}
    assert c.get("/api/me", headers=headers).json()["user"]["email"] == "ada@x.ca"
    assert c.post("/api/auth/token-email", json={"email": "ada@x.ca", "code": otp}).status_code == 400
    count = len(mail.outbox)
    unknown = c.post("/api/auth/login-email", json={"email": "unknown@example.ca"})
    assert unknown.json() == response.json()
    assert len(mail.outbox) == count
    assert c.post("/api/auth/token-email", json={"email": "unknown@example.ca", "code": otp}).status_code == 400
    assert c.post("/api/auth/login", json={"username": "ada"}).status_code == 404
    assert c.post("/api/auth/token", json={"username": "ada", "code": otp}).status_code == 404


def test_roster_requirement_is_a_neutral_submission_policy():
    ctx = create_app(Settings(secret="s" * 32)).state.ctx
    ctx.identity = SimpleNamespace(roster_check=lambda _: None)
    policy = get_submission_policy(ctx, "agm_rsvp")

    with pytest.raises(HTTPException) as exc:
        validate_submission_eligibility(ctx, policy, {"uuid": "student"})
    assert exc.value.status_code == 403
    assert "no roster record" in exc.value.detail


def test_user_record_and_lifecycle(env):
    c, mail = env
    ah, uh = setup_admin_and_user(c, mail)
    me = c.get("/api/me", headers=uh).json()
    assert set(me["user"]) == {"uuid", "email", "preferred_name", "first_name", "last_name", "student_number", "username"}
    assert me["user"]["student_number"] == "7890" and me["status"] == "pending_review"
    # unverified cannot submit
    f = c.get("/api/forms/agm_rsvp/static").json()
    body = {"version": f["version"], "hash": f["hash"], "data": {}}
    assert c.put("/api/forms/agm_rsvp/submission", json=body, headers=uh).status_code == 403
    activate(c, ah, uh)
    assert c.get("/api/me", headers=uh).json()["status"] == "verified"
    assert c.get("/api/admin/verifications", headers=uh).status_code == 403
    r = c.patch("/api/me", json={"preferred_name": "Countess"}, headers=uh)
    assert r.json()["user"]["preferred_name"] == "Countess" and r.json()["user"]["first_name"] == "Ada"


def test_optional_username_and_random_account_id(env):
    import uuid

    c, mail = env
    response = c.post("/api/auth/register", json={
        "email": "new@x.ca", "first_name": "New", "last_name": "Member", "student_number": "1234567890",
    })
    assert response.status_code == 201
    uid = response.json()["uuid"]
    assert uuid.UUID(uid).version == 4
    assert c.post("/api/auth/verify-email", json={"email": "new@x.ca", "code": code(mail)}).status_code == 200
    assert c.post("/api/auth/login-email", json={"email": "new@x.ca"}).status_code == 200
    result = c.post("/api/auth/token-email", json={"email": "new@x.ca", "code": code(mail)})
    headers = {"Authorization": f"Bearer {result.json()['token']}"}
    ctx = c.app.state.ctx

    result = c.patch("/api/me/username", headers=headers, json={"username": "Alias"})
    assert result.status_code == 200 and result.json()["user"]["username"] == "Alias"
    assert ctx.uid("alias") == uid and ctx.uid("NEW@X.CA") == uid
    assert c.patch("/api/me/username", headers=headers, json={"username": "Other"}).status_code == 200
    assert ctx.uid("other") == uid and ctx.uid("alias") != uid
    assert c.patch("/api/me/username", headers=headers, json={"username": ""}).status_code == 200
    assert ctx.uid("other") != uid
    assert c.get("/api/me", headers=headers).json()["user"]["uuid"] == uid


def test_username_reservations_follow_changes(env):
    c, mail = env
    c.app.state.ctx.settings.otp_cooldown = 0
    register(c, mail, "ada", "ada@x.ca")
    headers = login(c, mail, "ada")
    register(c, mail, "bob", "bob@x.ca", sn="2222222222")
    other = login(c, mail, "bob")
    assert c.patch("/api/me/username", headers=other, json={"username": "ADA"}).status_code == 409
    assert c.patch("/api/me/username", headers=headers, json={"username": "renamed"}).status_code == 200
    assert c.patch("/api/me/username", headers=other, json={"username": "ADA"}).status_code == 200
    ctx = c.app.state.ctx
    assert ctx.uid("ada") == c.get("/api/me", headers=other).json()["user"]["uuid"]
    assert ctx.uid("renamed") == c.get("/api/me", headers=headers).json()["user"]["uuid"]


def test_concurrent_username_changes_leave_only_the_current_name_reserved(env):
    c, mail = env
    register(c, mail, "ada", "ada@x.ca")
    headers = login(c, mail, "ada")
    uid = c.get("/api/me", headers=headers).json()["user"]["uuid"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda name: c.patch("/api/me/username", headers=headers, json={"username": name}),
            ("firstalias", "secondalias"),
        ))
    assert all(result.status_code in (200, 409) for result in results)
    assert any(result.status_code == 200 for result in results)
    current = c.get("/api/me", headers=headers).json()["user"]["username"]
    ctx = c.app.state.ctx
    assert ctx.uid(current) == uid
    for name in ("ada", "firstalias", "secondalias"):
        if name != current:
            assert ctx.uid(name) != uid


def test_concurrent_registration_reserves_username_and_indexes(env):
    c, _ = env
    registrations = [
        {"username": "racer", "email": "one@x.ca", "first_name": "One", "last_name": "User", "student_number": "1111111111"},
        {"username": "racer", "email": "two@x.ca", "first_name": "Two", "last_name": "User", "student_number": "2222222222"},
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda data: c.post("/api/auth/register", json=data), registrations))

    assert sorted(result.status_code for result in results) == [201, 409]
    ctx = c.app.state.ctx
    uid = ctx.uid("racer")
    winner = next(data for data, result in zip(registrations, results) if result.status_code == 201)
    loser = next(data for data, result in zip(registrations, results) if result.status_code == 409)
    account = ctx.store.get("accounts", uid)
    assert ctx.store.get("users", uid)["email"] == winner["email"]
    assert ctx.store.get("emails", ctx.email_key(winner["email"])) == {"uuid": uid}
    assert ctx.store.get("student_ids", account["sid_uuid"]) == {"uuid": uid}
    assert ctx.store.get("emails", ctx.email_key(loser["email"])) is None
    assert ctx.store.get("student_ids", security.student_id_uuid(ctx.settings.secret, loser["student_number"])) is None


def test_failed_registration_releases_reserved_account_and_student_id(env):
    c, mail = env
    register(c, mail, "existing", "existing@x.ca", sn="1234567890")
    response = c.post("/api/auth/register", json={
        "username": "retry", "email": "existing@x.ca", "first_name": "Retry", "last_name": "User",
        "student_number": "2222222222",
    })
    assert response.status_code == 409
    ctx = c.app.state.ctx
    assert ctx.store.get("accounts", ctx.uid("retry")) is None
    assert ctx.store.get("student_ids", ctx.store.get("accounts", ctx.uid("existing"))["sid_uuid"]) is not None
    assert ctx.store.get("student_ids", security.student_id_uuid(ctx.settings.secret, "2222222222")) is None


def test_form_event_stream_rejects_query_string_tokens(env):
    c, mail = env
    ah, _ = setup_admin_and_user(c, mail)
    token = ah["Authorization"].removeprefix("Bearer ")
    response = c.get("/api/admin/forms/agm_rsvp/events", params={"token": token})
    assert response.status_code == 401


def test_submission_validation_and_status(env):
    c, mail = env
    ah, uh = setup_admin_and_user(c, mail)
    activate(c, ah, uh)
    f = c.get("/api/forms/agm_rsvp/static").json()
    base = {"version": f["version"], "hash": f["hash"]}
    unrostered = c.put("/api/forms/agm_rsvp/submission", json={**base, "data": {"attendance": "Online", "division": "UTSG", "eligible": True}}, headers=uh)
    assert unrostered.status_code == 403 and "could not verify your student status" in unrostered.json()["detail"]
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=ah).status_code == 200
    bad = c.put("/api/forms/agm_rsvp/submission", json={**base, "data": {"attendance": "Online", "division": "UTSG", "eligible": True, "proxy": True}}, headers=uh)
    assert bad.status_code == 422  # proxy_name required when proxy
    bad = c.put("/api/forms/agm_rsvp/submission", json={**base, "data": {"attendance": "Online", "division": "UTSG", "eligible": True, "x": 1}}, headers=uh)
    assert bad.status_code == 422
    ok = c.put("/api/forms/agm_rsvp/submission", json={**base, "data": {"attendance": "Online", "division": "UTSG", "eligible": True, "accessibility": "a\x00b\u202e  c"}}, headers=uh)
    assert ok.status_code == 200
    mine = c.get("/api/me/submissions", headers=uh).json()[0]
    assert mine["data"]["accessibility"] == "ab  c" and mine["status"] == "submitted"
    uid = c.get("/api/me", headers=uh).json()["user"]["uuid"]
    key = f"agm_rsvp:{uid}"
    c.app.state.ctx.store.update("submissions", key, lambda current: {
        **current, "sap_review": {"extension": "opaque"}, "sap_previous": [{"extension": "opaque"}]})
    assert c.post(f"/api/admin/submissions/agm_rsvp/{uid}/status", json={"status": "accepted", "note": "ok"}, headers=ah).status_code == 200
    assert c.get("/api/me/submissions", headers=uh).json()[0]["status"] == "accepted"
    ctx = c.app.state.ctx
    other_uid = "00000000-0000-0000-0000-000000000001"
    submission = ctx.store.get("submissions", key)
    ctx.store.put("submissions", f"agm_rsvp:{other_uid}",
                  {**submission, "id": f"agm_rsvp:{other_uid}", "uuid": other_uid})
    with patch.object(ctx.identity, "user", wraps=ctx.identity.user) as user_lookup:
        first_page = c.get("/api/admin/forms/agm_rsvp/submissions?limit=1", headers=ah).json()
    assert [call.args[0] for call in user_lookup.call_args_list] == [other_uid]
    assert [item["uuid"] for item in first_page["items"]] == [other_uid]
    assert first_page["next_cursor"]
    with patch.object(ctx.identity, "user", wraps=ctx.identity.user) as user_lookup:
        second_page = c.get("/api/admin/forms/agm_rsvp/submissions",
                            params={"limit": 1, "cursor": first_page["next_cursor"]}, headers=ah).json()
    assert [call.args[0] for call in user_lookup.call_args_list] == [uid]
    assert [item["uuid"] for item in second_page["items"]] == [uid]
    assert second_page["next_cursor"] is None
    assert c.get("/api/admin/forms/agm_rsvp/submissions?limit=201", headers=ah).status_code == 422
    assert c.get("/api/admin/forms/agm_rsvp/submissions?cursor=%21", headers=ah).status_code == 422


def test_form_versioning_and_publish(env):
    c, mail = env
    ah, _ = setup_admin_and_user(c, mail)
    y = c.get("/api/admin/forms/agm_rsvp", headers=ah).json()["draft_yaml"]
    # same version, changed definition -> error
    r = c.put("/api/admin/forms/agm_rsvp/draft", json={"yaml": y.replace("Accessibility needs", "Access needs")}, headers=ah)
    assert r.status_code == 409 and "bump" in r.text
    # reformatting only is fine (same hash)
    assert c.put("/api/admin/forms/agm_rsvp/draft", json={"yaml": y}, headers=ah).status_code == 200
    y2 = y.replace("version: 2", "version: 3").replace("Accessibility needs", "Access needs")
    r = c.put("/api/admin/forms/agm_rsvp/draft", json={"yaml": y2}, headers=ah)
    assert r.status_code == 200
    old = c.get("/api/forms/agm_rsvp/static").json()
    assert old["version"] == 2  # draft is not live
    assert c.post("/api/admin/forms/agm_rsvp/publish", headers=ah).status_code == 200
    new = c.get("/api/forms/agm_rsvp/static").json()
    assert new["version"] == 3 and new["hash"] != old["hash"]
    # stale clients are told to reload
    _, uh = None, None
    assert c.post("/api/admin/forms/agm_rsvp/unpublish", headers=ah).status_code == 200
    assert c.get("/api/forms/agm_rsvp/static").status_code == 404
    # optimistic concurrency
    assert c.put("/api/admin/forms/agm_rsvp/draft", json={"yaml": y2, "base_rev": 0}, headers=ah).status_code == 409


def test_stale_hash_rejected(env):
    c, mail = env
    ah, uh = setup_admin_and_user(c, mail)
    activate(c, ah, uh)
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=ah).status_code == 200
    r = c.put("/api/forms/agm_rsvp/submission", json={"version": 2, "hash": "0" * 64, "data": {}}, headers=uh)
    assert r.status_code == 409


def test_recovery_flow(env):
    c, mail = env
    ah, uh = setup_admin_and_user(c, mail)
    activate(c, ah, uh)
    assert c.post("/api/recovery/start", json={"username": "ada", "student_number_trunc": "0000"}).status_code == 200
    n = len(mail.outbox)
    c.post("/api/recovery/start", json={"username": "ada", "student_number_trunc": "7890"})
    assert len(mail.outbox) == n + 1
    c.post("/api/recovery/email-otp", json={"username": "ada", "student_number_trunc": "7890", "new_email": "new@x.ca"})
    assert mail.outbox[-1][0] == "new@x.ca"
    body = scan(c, uh, username="ada", student_number_trunc="7890", new_email="new@x.ca", otp=code(mail), last_name="Byron")
    r = c.post("/api/recovery/request", json=body)
    assert r.status_code == 201, r.text
    assert c.get("/api/me", headers=uh).json()["user"]["email"] == "ada@x.ca"  # unchanged until approved
    media = c.get(f"/api/admin/verifications/{r.json()['id']}/media", headers=ah)
    assert media.status_code == 200
    c.post(f"/api/admin/verifications/{r.json()['id']}/decision", json={"approve": True}, headers=ah)
    u = c.get("/api/me", headers=uh).json()["user"]
    assert (u["email"], u["last_name"]) == ("new@x.ca", "Byron")
    assert c.get(f"/api/admin/verifications/{r.json()['id']}/media", headers=ah).status_code == 404  # images purged


def test_backup_email_signin_and_primary_change(env):
    c, mail = env
    c.app.state.ctx.settings.otp_cooldown = 0
    ah, uh = setup_admin_and_user(c, mail)
    activate(c, ah, uh)
    initial = c.get("/api/me", headers=uh).json()
    assert initial["backup_email"] is None
    enroll = "/api/auth/backup/enroll"
    confirm = "/api/auth/backup/confirm"
    assert c.post(enroll, headers=ah, json={"email": "other@outside.ca"}).status_code == 403
    assert c.post(enroll, headers=uh, json={"email": "ADA@X.CA"}).status_code == 422

    def establish(address):
        assert c.post(enroll, headers=uh, json={"email": address}).status_code == 200
        assert [msg[0] for msg in mail.outbox[-2:]] == ["ada@x.ca", address]
        proof = {"email": address, "primary_code": mail.outbox[-2][2].split("code is ")[1][:6],
                 "backup_code": code(mail)}
        wrong = "000000" if proof["primary_code"] != "000000" else "111111"
        assert c.post(confirm, headers=uh, json=proof | {"primary_code": wrong}).status_code == 400
        assert c.post(confirm, headers=uh, json=proof | {"email": "unverified@outside.ca"}).status_code == 400
        assert c.post(confirm, headers=uh, json=proof).json() == {"backup_email": address}
        assert c.post(confirm, headers=uh, json=proof).status_code == 400

    establish("one@outside.ca")
    start = "/api/recovery/backup/start"
    token = "/api/recovery/backup/token"
    count = len(mail.outbox)
    nonmatch = c.post(start, json={"username": "ada", "email": "wrong@outside.ca"})
    assert len(mail.outbox) == count
    assert c.post(start, json={"username": "ada", "email": "ONE@outside.ca"}).json() == nonmatch.json()
    credentials = {"username": "ada", "code": code(mail)}
    backup_session = c.post(token, json=credentials)
    assert backup_session.status_code == 200
    assert c.post(token, json=credentials).status_code == 400
    bh = {"Authorization": f"Bearer {backup_session.json()['token']}"}
    me = c.get("/api/me", headers=bh).json()
    assert me["backup_session"] is True and me["user"] == initial["user"]
    assert c.post(enroll, headers=bh, json={"email": "other@outside.ca"}).status_code == 403

    establish("two@outside.ca")
    assert c.get("/api/me", headers=bh).status_code == 401
    c.post(start, json={"username": "ada", "email": "two@outside.ca"})
    bh = {"Authorization": "Bearer " + c.post(token, json={"username": "ada", "code": code(mail)}).json()["token"]}
    change = {"username": "ada", "student_number_trunc": "7890", "new_email": "new@x.ca"}
    assert c.post("/api/recovery/email-otp", headers=bh, json=change).status_code == 200
    request = c.post("/api/recovery/request", headers=bh, json=scan(c, bh, otp=code(mail), **change))
    assert request.status_code == 201
    assert c.post(f"/api/admin/verifications/{request.json()['id']}/decision", headers=ah, json={"approve": True}).status_code == 200
    me = c.get("/api/me", headers=bh).json()
    assert me["user"]["uuid"] == initial["user"]["uuid"] and me["user"]["email"] == "new@x.ca"
    assert me["backup_email"] == "two@outside.ca"
    assert c.put("/api/admin/roles", headers=ah, json={"username": "ada", "roles": ["admin"]}).status_code == 200
    assert c.get("/api/me", headers=bh).status_code == 401


def test_configured_applicant_primary_email_domains():
    mail = OutboxMailer()
    settings = Settings(secret="s" * 32, applicant_email_domains=("school.example.ca",))
    c = TestClient(create_app(settings, mailer=mail))
    body = {"username": "ada", "first_name": "Ada", "last_name": "Lovelace", "student_number": "1234567890"}
    for rejected in ("ada@sub.school.example.ca", "ada@example.ca"):
        assert c.post("/api/auth/register", json=body | {"email": rejected}).status_code == 422
    assert not mail.outbox
    register(c, mail, "ada", "ada@school.example.ca")
    change = {"username": "ada", "student_number_trunc": "7890", "new_email": "ada@example.ca"}
    assert c.post("/api/recovery/email-otp", json=change).status_code == 422
    assert c.post("/api/recovery/request", json=scan(c, {}, **change)).status_code == 422


def test_duplicates_and_otp_lockout(env):
    c, mail = env
    setup_admin_and_user(c, mail)
    r = c.post("/api/auth/register", json={"username": "ADA", "email": "o@x.ca", "first_name": "A", "last_name": "B", "student_number": "1234567890"})
    assert r.status_code == 409
    c.post("/api/auth/login-email", json={"email": "ada@x.ca"})
    good = code(mail)
    for _ in range(5):
        assert c.post("/api/auth/token-email", json={"email": "ada@x.ca", "code": "000000" if good != "000000" else "111111"}).status_code == 400
    assert c.post("/api/auth/token-email", json={"email": "ada@x.ca", "code": good}).status_code == 400


def test_schema_validation():
    with pytest.raises(formdef.FormError):
        formdef.parse_yaml("id: a\nversion: 1\ntitle: t\nfields:\n  - {id: x, type: select, label: X}\n")
    with pytest.raises(formdef.FormError):
        formdef.parse_yaml("id: a\nversion: 1\ntitle: t\nfields:\n  - {id: x, type: text, label: X, show_if: {field: y, equals: 1}}\n")
    a = formdef.parse_yaml("id: a\nversion: 1\ntitle: t\nfields:\n  - {id: x, type: text, label: X}\n")
    b = formdef.parse_yaml("fields: [{label: X, type: text, id: x}]\ntitle: t\nversion: 1\nid: a\n")
    assert formdef.schema_hash(a) == formdef.schema_hash(b)
