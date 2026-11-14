import csv
import io

from fastapi.testclient import TestClient

from test_portal import activate, login, register
from utsu_core import create_app
from utsu_core.mailer import OutboxMailer
from utsu_core.settings import Settings
from utsu_core.programs.senate.module import PREFERENCES


def test_senate_application_views_recheck_roster_and_enforce_roles():
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    assert c.get("/api/me/senate", headers=user).json() is None

    f = c.get("/api/forms/senate_application/static").json()
    data = {"division": "UTSG", "college": "UC", "year_of_study": "2", "seat_type": "Undergraduate",
            "statement": "A" * 50, "consent": True}
    assert c.put("/api/forms/senate_application/submission",
                 json={"version": f["version"], "hash": f["hash"], "data": data}, headers=user).status_code == 200
    assert c.get("/api/admin/senate/applications", headers=user).status_code == 403
    assert not c.get("/api/me/senate", headers=user).json()["eligible"]
    assert c.get("/api/admin/senate/alerts", headers=user).status_code == 403
    before = c.get("/api/admin/senate/alerts", headers=admin).json()
    assert {item["kind"] for item in before["items"]} == {"ineligible", "missing_preferences"}
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    assert c.get("/api/me/senate", headers=user).json()["eligible"]
    assert {item["kind"] for item in c.get("/api/admin/senate/alerts", headers=admin).json()["items"]} == {"missing_preferences"}
    page = c.get("/api/admin/senate/applications?limit=1", headers=admin).json()
    assert len(page["items"]) == 1 and page["items"][0]["eligible"] and page["next_cursor"] is None
    assert c.get("/api/admin/senate/applications?limit=201", headers=admin).status_code == 422


def test_seating_requires_preferences_and_publishes_only_consented_names():
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    f = c.get("/api/forms/senate_application/static").json()
    base = {"division": "UTSG", "college": "UC", "year_of_study": "2", "seat_type": "Undergraduate",
            "statement": "A" * 50, "consent": True}
    submit = lambda data: c.put("/api/forms/senate_application/submission",
                                json={"version": f["version"], "hash": f["hash"], "data": data}, headers=user)
    assert submit(base).status_code == 200
    seats = {"seats": [{"constituency": "General Member", "seats": 1}]}
    assert c.get("/api/admin/senate/seats", headers=user).status_code == 403
    assert c.get("/api/admin/senate/seats", headers=admin).json() == {"seats": []}
    assert c.put("/api/admin/senate/seats", json=seats, headers=user).status_code == 403
    assert c.put("/api/admin/senate/seats", json=seats, headers=admin).status_code == 200
    assert c.get("/api/admin/senate/seats", headers=admin).json()["seats"] == [
        {"constituency": "General Member", "division": "", "seats": 1}]
    assert c.post("/api/admin/senate/seating", json={"seed": 42}, headers=admin).status_code == 409
    preferences = {field: "Not interested" for field in PREFERENCES}
    preferences["pref_general"] = "First-choice"
    assert submit({**base, **preferences}).status_code == 200
    assert c.get("/api/admin/senate/alerts", headers=admin).json()["items"] == []
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    store = c.app.state.ctx.store
    key = f"senate_application:{uid}"
    store.update("submissions", key, lambda s: {**s, "data": {**s["data"], "pref_general": 2}})
    assert {item["kind"] for item in c.get("/api/admin/senate/alerts", headers=admin).json()["items"]} == {"invalid_preferences"}
    assert c.post("/api/admin/senate/seating", json={"seed": 42}, headers=admin).status_code == 409
    assert submit({**base, **preferences}).status_code == 200
    preview = c.post("/api/admin/senate/seating", json={"seed": 42}, headers=admin).json()
    assert preview == c.post("/api/admin/senate/seating", json={"seed": 42}, headers=admin).json()
    assert preview["result"]["seated"][0]["full_name"] == "Ada Lovelace"
    assert c.post("/api/admin/senate/seating/publish",
                  json={"seed": 42, "inputs_hash": "0" * 64}, headers=admin).status_code == 409
    assert c.get("/api/senate/current").json() is None
    assert c.get("/api/admin/senate/results.csv", headers=admin).status_code == 404
    assert c.post("/api/admin/senate/seating/publish",
                  json={"seed": 42, "inputs_hash": preview["inputs_hash"]}, headers=admin).status_code == 200
    assert c.get("/api/senate/current").json()["seated"][0]["name"] == "seat filled"
    assert c.get("/api/admin/senate/results.csv", headers=user).status_code == 403
    exported = c.get("/api/admin/senate/results.csv", headers=admin)
    assert exported.status_code == 200 and exported.headers["cache-control"] == "no-store"
    row = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))[0]
    assert row["full_name"] == "Ada Lovelace" and row["public_listing"] == "False"
    assert "senate.results_exported" in {e["action"] for e in c.get("/api/admin/audit?limit=200", headers=admin).json()["entries"]}
    assert submit({**base, **preferences, "public_listing": True}).status_code == 200
    assert c.post("/api/admin/senate/seating/publish",
                  json={"seed": 42, "inputs_hash": preview["inputs_hash"]}, headers=admin).status_code == 409
    newer = c.post("/api/admin/senate/seating", json={"seed": 42}, headers=admin).json()
    assert c.post("/api/admin/senate/seating/publish",
                  json={"seed": 42, "inputs_hash": newer["inputs_hash"]}, headers=admin).json()["version"] == 2
    assert c.get("/api/senate/current").json()["seated"][0]["name"] == "Ada Lovelace"


def test_faculty_seating_uses_roster_division_not_form_input():
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    f = c.get("/api/forms/senate_application/static").json()
    data = {"division": "UTSG", "college": "LAW", "year_of_study": "2", "seat_type": "Undergraduate",
            "statement": "A" * 50, "consent": True, **{field: "Not interested" for field in PREFERENCES},
            "pref_faculty": "First-choice"}
    assert c.put("/api/forms/senate_application/submission",
                 json={"version": f["version"], "hash": f["hash"], "data": data}, headers=user).status_code == 200
    seats = {"seats": [{"constituency": "Faculty/College", "division": "UC", "seats": 1},
                       {"constituency": "Faculty/College", "division": "LAW", "seats": 1}]}
    assert c.put("/api/admin/senate/seats", json=seats, headers=admin).status_code == 200
    result = c.post("/api/admin/senate/seating", json={"seed": 1}, headers=admin).json()["result"]
    assert result["seated"][0]["division"] == "UC"
