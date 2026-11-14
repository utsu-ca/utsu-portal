import base64
import csv
import hashlib
import io
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_portal import IMG, activate, login, register, scan  # noqa: E402
from utsu_core import create_app  # noqa: E402
from utsu_core.mailer import OutboxMailer  # noqa: E402
from utsu_core.settings import Settings  # noqa: E402

REDIR = "https://zoom.example/sso/cb"


@pytest.fixture
def env():
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",), zoom_client_secret="zsecret", zoom_redirect_uris=(REDIR,),
                                       zoom_url="https://zoom.example/j/1", zoom_sso_url="https://zoom.example/sso"), mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    h = {"boss": login(c, mail, "boss")}
    for u, sn in (("ada", "1234567890"), ("bob", "2234567890")):
        register(c, mail, u, f"{u}@x.ca", sn=sn, first=u.title(), last="Test")
        h[u] = login(c, mail, u)
        r = c.post("/api/verification", json={**scan(c, h[u]), "first_name": u.title(), "last_name": "Test"}, headers=h[u])
        c.post(f"/api/admin/verifications/{r.json()['id']}/decision", json={"approve": True}, headers=h["boss"])
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Test,xxxxx6789x,ARTSC,UC\nBob,Test,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=h["boss"]).json()["rostered"] == 2
    return c, h


def test_unrostered_account_cannot_reach_the_agm(env):
    c, h = env
    c.post("/api/admin/roster", json={"csv": "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Test,xxxxx6789x,ARTSC,UC\n"}, headers=h["boss"])
    assert c.get("/api/agm/invitation", headers=h["ada"]).status_code == 200
    assert c.get("/api/agm/invitation", headers=h["bob"]).status_code == 403


def test_roster_removal_blocks_checkin_and_zoom_sso(env):
    c, h = env
    rsvp(c, h, "bob", "Online")
    bob = uid(c, h, "bob")
    c.app.state.ctx.store.put("agm_grants", bob, {"uuid": bob, "exp": c.app.state.ctx.now() + 600})
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Test,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=h["boss"]).status_code == 200
    alerts = c.get("/api/admin/agm/alerts", headers=h["boss"]).json()["items"]
    assert len(alerts) == 1 and alerts[0]["kind"] == "ineligible"
    assert c.post("/api/admin/agm/checkin", json={"uuid": bob}, headers=h["boss"]).status_code == 403
    assert c.app.state.ctx.store.get("agm_attendance", bob) is None
    body = {"client_id": "zoom", "redirect_uri": REDIR}
    assert c.post("/api/sso/authorize", json=body, headers=h["bob"]).status_code == 403


def test_admin_registration_report_and_standalone_verification(env):
    c, h = env
    rsvp(c, h, "ada", "In person", accessibility="=Needs a quiet space", proxy=True, proxy_name="Proxy Person")
    rsvp(c, h, "bob", "Online")
    assert c.get("/api/admin/agm/rsvps", headers=h["ada"]).status_code == 403
    assert c.get("/api/admin/agm/rsvps.csv", headers=h["ada"]).status_code == 403
    export = c.get("/api/admin/agm/rsvps.csv", headers=h["boss"])
    rows = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
    assert {row["name"] for row in rows} == {"Ada Test", "Bob Test"}
    ada = next(row for row in rows if row["name"] == "Ada Test")
    assert ada["accessibility"] == "'=Needs a quiet space" and ada["proxy_name"] == "Proxy Person"
    assert export.headers["cache-control"] == "no-store"
    assert "agm.rsvps_exported" in {e["action"] for e in c.get("/api/admin/audit?limit=200", headers=h["boss"]).json()["entries"]}
    assert c.get("/api/admin/agm/alerts", headers=h["ada"]).status_code == 403
    alerts = c.get("/api/admin/agm/alerts", headers=h["boss"]).json()
    assert [(a["name"], a["kind"]) for a in alerts["items"]] == [("Bob Test", "online_identity_required")]
    bob = uid(c, h, "bob")
    c.app.state.ctx.store.put("agm_grants", bob, {"uuid": bob, "exp": c.app.state.ctx.now() + 600})
    assert c.get("/api/admin/agm/alerts", headers=h["boss"]).json()["items"] == []
    first = c.get("/api/admin/agm/rsvps?limit=1", headers=h["boss"]).json()
    assert len(first["items"]) == 1 and first["items"][0]["eligible"]
    second = c.get("/api/admin/agm/rsvps", params={"cursor": first["next_cursor"], "limit": 1},
                   headers=h["boss"]).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert first["items"][0]["uuid"] != second["items"][0]["uuid"]
    ada_entry = next(item for item in first["items"] + second["items"] if item["name"] == "Ada Test")
    assert ada_entry["accessibility"] == "=Needs a quiet space" and ada_entry["proxy_name"] == "Proxy Person"
    assert c.get("/api/admin/agm/rsvps?limit=0", headers=h["boss"]).status_code == 422
    match = c.post("/api/admin/agm/verify", json={"full_name": "Ada Test", "student_number": "6789"},
                   headers=h["boss"])
    assert match.status_code == 200 and match.json()["verified"]
    assert c.post("/api/admin/agm/verify", json={"full_name": "Unknown Person"}, headers=h["ada"]).status_code == 403


def rsvp(c, h, who, mode, **extra):
    f = c.get("/api/forms/agm_rsvp/static").json()
    data = {"attendance": mode, "division": "UTSG", "eligible": True, **extra}
    r = c.put("/api/forms/agm_rsvp/submission", json={"version": f["version"], "hash": f["hash"], "data": data}, headers=h[who])
    assert r.status_code == 200, r.text


def uid(c, h, who):
    return c.get("/api/me", headers=h[who]).json()["user"]["uuid"]


def test_in_person_qr_and_checkin(env):
    c, h = env
    assert c.get("/api/agm/invitation", headers=h["ada"]).json() == {"registered": False, "form": "agm_rsvp"}
    rsvp(c, h, "ada", "In person", pronouns="she/her")
    inv = c.get("/api/agm/invitation", headers=h["ada"]).json()
    assert inv["mode"] == "in_person" and not inv["checked_in"]
    q = c.get("/api/agm/qr", headers=h["ada"]).json()
    assert q["payload"] == uid(c, h, "ada") and q["svg"].startswith("<svg")
    assert c.post("/api/admin/agm/checkin", json={"uuid": q["payload"]}, headers=h["ada"]).status_code == 403
    r = c.post("/api/admin/agm/checkin", json={"uuid": q["payload"]}, headers=h["boss"]).json()
    assert (r["name"], r["pronouns"], r["already_checked_in"]) == ("Ada Test", "she/her", False) and r["message"].startswith("Checked in")
    assert c.post("/api/admin/agm/checkin", json={"uuid": q["payload"].upper()}, headers=h["boss"]).json()["already_checked_in"]
    assert c.get("/api/agm/invitation", headers=h["ada"]).json()["checked_in"]
    assert c.post("/api/admin/agm/checkin", json={"uuid": "nope"}, headers=h["boss"]).status_code == 404
    assert c.post("/api/admin/agm/checkin", json={"uuid": uid(c, h, "bob")}, headers=h["boss"]).status_code == 409  # not registered


def pkce():
    v = "v" * 50
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def test_online_verifier_gates_zoom_sso(env):
    c, h = env
    rsvp(c, h, "ada", "Online")
    assert c.post("/api/agm/zoom/join", headers=h["ada"]).status_code == 403
    v, ch = pkce()
    body = {"client_id": "zoom", "redirect_uri": REDIR, "state": "st", "nonce": "n1", "code_challenge": ch}
    assert c.post("/api/sso/authorize", json=body, headers=h["ada"]).status_code == 403  # no ID check yet
    chal = c.get("/api/agm/verify/challenge", headers=h["ada"]).json()["challenge"]
    r = c.post("/api/agm/verify", json={"face": IMG, "id_card": IMG, "challenge": chal}, headers=h["ada"])
    assert r.json()["status"] == "pending"
    assert c.get("/api/agm/verify", headers=h["ada"]).json()["status"] == "pending"
    assert c.post("/api/admin/agm/verifications/%s/decision" % r.json()["id"], json={"approve": True}, headers=h["boss"]).status_code == 200
    assert c.get(f"/api/admin/agm/verifications/{r.json()['id']}/media", headers=h["boss"]).status_code == 404  # purged
    inv = c.get("/api/agm/invitation", headers=h["ada"]).json()
    assert inv["id_verified"] and inv["zoom_sso_url"] == "https://zoom.example/sso"
    assert c.post("/api/agm/zoom/join", headers=h["ada"]).json()["zoom_url"] == "https://zoom.example/j/1"
    # wrong redirect uri refused
    assert c.post("/api/sso/authorize", json={**body, "redirect_uri": "https://evil.example/"}, headers=h["ada"]).status_code == 400
    url = c.post("/api/sso/authorize", json=body, headers=h["ada"]).json()["redirect"]
    q = parse_qs(urlsplit(url).query)
    assert q["state"] == ["st"] and url.startswith(REDIR)
    form = {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIR, "client_id": "zoom", "client_secret": "zsecret"}
    assert c.post("/sso/token", data={**form, "code_verifier": "x" * 50}).status_code == 400  # bad PKCE; code is now burned
    assert c.post("/sso/token", data={**form, "code_verifier": v}).status_code == 400
    q = parse_qs(urlsplit(c.post("/api/sso/authorize", json=body, headers=h["ada"]).json()["redirect"]).query)
    form["code"] = q["code"][0]
    assert c.post("/sso/token", data={**form, "client_secret": "bad", "code_verifier": v}).status_code == 401
    tok = c.post("/sso/token", data={**form, "code_verifier": v})
    assert tok.status_code == 200, tok.text
    t = tok.json()
    payload = json.loads(base64.urlsafe_b64decode(t["id_token"].split(".")[1] + "=="))
    assert payload["aud"] == "zoom" and payload["nonce"] == "n1" and payload["email"] == "ada@x.ca"
    assert payload["sub"] != uid(c, h, "ada")  # pairwise
    jwks = c.get("/sso/jwks").json()["keys"][0]
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    num = lambda s: int.from_bytes(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)), "big")
    pub = rsa.RSAPublicNumbers(num(jwks["e"]), num(jwks["n"])).public_key()
    hd, bd, sg = t["id_token"].split(".")
    pub.verify(base64.urlsafe_b64decode(sg + "=" * (-len(sg) % 4)), f"{hd}.{bd}".encode(), padding.PKCS1v15(), hashes.SHA256())
    ui = c.get("/sso/userinfo", headers={"Authorization": "Bearer " + t["access_token"]}).json()
    assert ui["sub"] == payload["sub"]
    assert c.get("/sso/.well-known/openid-configuration").json()["jwks_uri"].endswith("/sso/jwks")
    assert c.get("/sso/authorize").status_code == 200


def test_in_person_registrant_cannot_use_online_flow(env):
    c, h = env
    rsvp(c, h, "bob", "In person")
    chal = c.get("/api/agm/verify/challenge", headers=h["bob"]).json()["challenge"]
    assert c.post("/api/agm/verify", json={"face": IMG, "id_card": IMG, "challenge": chal}, headers=h["bob"]).status_code == 409
    body = {"client_id": "zoom", "redirect_uri": REDIR}
    assert c.post("/api/sso/authorize", json=body, headers=h["bob"]).status_code == 403
