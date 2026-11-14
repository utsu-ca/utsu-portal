import csv
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_portal import activate, login, register, scan  # noqa: E402

from utsu_core import create_app
from utsu_core.crypto import PREFIX
from utsu_core.mailer import Mailer, OutboxMailer
from utsu_core.settings import Settings
from utsu_core.store import MemoryStore
from utsu_core.programs.sap.wfni import QUESTIONS
from utsu_core.programs.sap.module import review_stage


def test_living_costs_subsidies_prioritize_emergency_reviews():
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",), sap_review_enabled=True),
                              store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "reviewer", "reviewer@x.ca", sn="1111111111")
    reviewer = login(c, mail, "reviewer")
    activate(c, admin, reviewer)
    assert c.put("/api/admin/roles", json={"username": "reviewer", "roles": ["sap_reviewer"]}, headers=admin).status_code == 200
    roster = ("First Name,Last Name,Truncated Student Number,Faculty,Division\n"
              "Ada,Lovelace,xxxxx6789x,ARTSC,UC\n"
              "Grace,Hopper,xxxxx6678x,ARTSC,UC\n")
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    weights = (Path(__file__).resolve().parents[1] / "data" / "wfni_weights.csv").read_text()
    assert c.put("/api/admin/sap/weights", json={"csv": weights}, headers=admin).status_code == 200
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "Financial hardship. " * 4,
            "contact_email": "student@example.ca", "consent": True, "bursary_type": "Living-costs Bursary",
            "disability_mobility": "Yes", "disability_invisible": "No", "living_situation": "Stably Housed",
            "disposable_income": "Less than $100", "liquidity": "$0 to $500", "dependents": "1",
            "external_supports": "No"}
    review_ids = []
    for username, first, last, number, subsidies in (
        ("ada", "Ada", "Lovelace", "1234567890", None),
        ("grace", "Grace", "Hopper", "1234566789", ["Emergency Bursary (expedited processing)"]),
    ):
        register(c, mail, username, f"{username}@x.ca", sn=number, first=first, last=last)
        user = login(c, mail, username)
        verification = c.post("/api/verification", json=scan(c, user, first_name=first, last_name=last), headers=user)
        assert verification.status_code == 201
        assert c.post(f"/api/admin/verifications/{verification.json()['id']}/decision",
                      json={"approve": True}, headers=admin).status_code == 200
        body = {"version": form["version"], "hash": form["hash"],
                "data": {**data, **({"lc_subsidies": subsidies} if subsidies else {})}}
        assert c.put("/api/forms/student_aid_bursary/submission", json=body, headers=user).status_code == 200
        uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
        response = c.post(f"/api/admin/sap/applications/{uid}/enqueue", headers=admin)
        assert response.status_code == 200, response.text
        review_ids.append(response.json()["review_id"])
        revision = next(item["revision"] for item in c.get("/api/admin/sap/applications", headers=admin).json()["items"]
                        if item["uuid"] == uid)
        prepared = {"text": "The applicant describes financial hardship.", "confirm_redacted": True, "revision": revision}
        assert c.put(f"/api/admin/sap/applications/{uid}/review-hardship", json=prepared, headers=admin).status_code == 200
        key = f"student_aid_bursary:{uid}"
        c.app.state.ctx.store.update("submissions", key, lambda current: {**current, "updated_at": 100 if username == "ada" else 200})
    queue = c.get("/api/sap/queue", headers=reviewer).json()
    assert [row["review_id"] for row in queue] == list(reversed(review_ids))
    assert [row["emergency"] for row in queue] == [True, None]
    assert all(row["submitted_hour"] == 0 for row in queue)
    assert "Ada" not in str(queue) and "Grace" not in str(queue) and "student@example.ca" not in str(queue)
    claim = c.post(f"/api/sap/queue/{review_ids[1]}/claim", headers=reviewer).json()
    assert claim["emergency"] is True


def test_review_stage_ignores_expired_claims():
    review = {"reviews": [{"reviewer": "r", "lease_until": 10}]}
    assert review_stage(review, 9) == "in_review"
    assert review_stage(review, 10) == "ready"
    review["reviews"][0]["level"] = 2
    assert review_stage(review, 10) == "in_review"


def test_import_historical_decisions_is_private_atomic_and_non_overlapping():
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    path = f"/api/admin/sap/applications/{uid}/history"
    record = {"source": "batch B012-01", "terms": ["Fall 2025"], "approved": True, "amount": 500,
              "decided_on": "2025-11-01", "ratings": [{"reviewer": "Private Reviewer", "level": 3,
                                                            "notes": "Private committee notes\nSecond line"}],
              "revision": 0, "confirm": True}
    assert c.post(path, json=record, headers=user).status_code == 403
    assert c.post(path, json=record, headers=admin).status_code == 404
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "Financial hardship. " * 4,
            "contact_email": "student@example.ca", "consent": True, "bursary_type": "Academic Pursuits Bursary"}
    body = {"version": form["version"], "hash": form["hash"], "data": data}
    assert c.put("/api/forms/student_aid_bursary/submission", json=body, headers=user).status_code == 200
    key = f"student_aid_bursary:{uid}"
    original = c.app.state.ctx.store.get("submissions", key)
    revision = original["_rev"]
    record["revision"] = revision
    assert c.post(path, json={**record, "confirm": False}, headers=admin).status_code == 422
    assert c.post(path, json={**record, "approved": False}, headers=admin).status_code == 422
    assert c.post(path, json={**record, "terms": [" fall  2026 "]}, headers=admin).status_code == 409
    assert c.post(path, json={**record, "terms": ["Fall 2025", "fall 2025"]}, headers=admin).status_code == 422
    assert c.post(path, json={**record, "decided_on": "2999-01-01"}, headers=admin).status_code == 422
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: c.post(path, json=record, headers=admin), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    saved = c.app.state.ctx.store.get("submissions", key)
    assert saved["data"] == original["data"]
    assert len(saved["sap_previous"]) == 1
    assert inner.get("submissions", key)["sap_previous"].startswith(PREFIX)
    assert "Private Reviewer" not in str(inner.get("submissions", key))
    admin_history = c.get(path, headers=admin).json()
    assert admin_history[0]["sap_review"]["reviews"][0]["notes"] == "Private committee notes\nSecond line"
    applicant_history = c.get("/api/me/sap/history", headers=user).json()
    assert applicant_history[0]["data"]["terms"] == ["Fall 2025"]
    assert applicant_history[0]["decision"]["amount"] == 500
    assert "Private Reviewer" not in str(applicant_history) and "batch B012-01" not in str(applicant_history)
    updated = {**record, "revision": saved["_rev"]}
    assert c.post(path, json={**updated, "terms": ["Winter 2025"]}, headers=admin).status_code == 409
    assert c.post(path, json={**updated, "source": "another", "terms": ["FALL  2025"]}, headers=admin).status_code == 409
    second = {**updated, "source": "another", "terms": ["Winter 2027"], "approved": False, "amount": 0}
    assert c.post(path, json=second, headers=admin).status_code == 200
    assert c.put("/api/forms/student_aid_bursary/submission",
                 json={**body, "data": {**data, "terms": ["Winter 2027"]}}, headers=user).status_code == 409
    assert c.put("/api/forms/student_aid_bursary/submission", json=body, headers=user).status_code == 200
    assert len(c.get(path, headers=admin).json()) == 2
    assert "sap.history_imported" in {e["action"] for e in c.get("/api/admin/audit?limit=200", headers=admin).json()["entries"]}


def test_admin_resolves_disagreement_with_revision_and_locks_ratings():
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",), sap_review_enabled=True),
                              store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    register(c, mail, "reviewer", "reviewer@x.ca", sn="1111111111")
    reviewer = login(c, mail, "reviewer")
    activate(c, admin, reviewer)
    assert c.put("/api/admin/roles", json={"username": "reviewer", "roles": ["sap_reviewer"]}, headers=admin).status_code == 200
    reviewer_uid = c.get("/api/me", headers=reviewer).json()["user"]["uuid"]
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "financial hardship " * 4,
            "contact_email": "ada@example.ca", "consent": True, "bursary_type": "Academic Pursuits Bursary"}
    assert c.put("/api/forms/student_aid_bursary/submission",
                 json={"version": form["version"], "hash": form["hash"], "data": data}, headers=user).status_code == 200
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    review_id = c.post(f"/api/admin/sap/applications/{uid}/enqueue", headers=admin).json()["review_id"]
    key = f"student_aid_bursary:{uid}"
    store = c.app.state.ctx.store
    store.update("submissions", key, lambda current: {**current, "sap_review_hardship": "Financial hardship.",
                 "sap_review": {**current["sap_review"], "reviews": [
                     {"reviewer": reviewer_uid, "lease_until": 0, "level": 1},
                     {"reviewer": "another-reviewer", "lease_until": 0, "level": 4}]}})
    revision = store.get("submissions", key)["_rev"]
    path = f"/api/admin/sap/applications/{uid}/resolve-review"
    body = {"reason": "Committee discussed the difference.", "revision": revision}
    assert c.post(path, json=body, headers=user).status_code == 403
    assert c.put("/api/admin/roles", json={"username": "reviewer", "roles": ["sap_reviewer", "sap_admin"]}, headers=admin).status_code == 200
    assert c.post(path, json=body, headers=reviewer).status_code == 403
    assert c.post(path, json={**body, "reason": "   "}, headers=admin).status_code == 422
    assert c.post(path, json={**body, "revision": revision - 1}, headers=admin).status_code == 409
    assert c.get("/api/admin/sap/review-report", headers=admin).json()[0]["stage"] == "discuss"
    resolved = c.post(path, json=body, headers=admin)
    assert resolved.status_code == 200 and resolved.json()["reason"] == body["reason"]
    stored = inner.get("submissions", key)
    assert stored["sap_review"].startswith(PREFIX) and body["reason"] not in str(stored)
    report = c.get("/api/admin/sap/review-report", headers=admin).json()[0]
    assert report["stage"] == "reconciled" and report["resolution"] == resolved.json()
    assert c.post(path, json=body, headers=admin).status_code == 409
    assert c.put(f"/api/sap/review/{review_id}", json={"level": 3}, headers=reviewer).status_code == 409
    assert c.put(f"/api/admin/sap/applications/{uid}/review-count",
                 json={"required_reviews": 3}, headers=admin).status_code == 409
    assert c.post(f"/api/admin/sap/applications/{uid}/decision",
                  json={"approved": True, "amount": 500}, headers=admin).status_code == 200


def test_sap_application_access_and_encryption():
    inner = MemoryStore()
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    assert c.get("/api/me/sap", headers=user).json() is None
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "private " * 10,
            "contact_email": "ada@example.ca", "consent": True}
    response = c.put("/api/forms/student_aid_bursary/submission",
                     json={"version": form["version"], "hash": form["hash"], "data": data}, headers=user)
    assert response.status_code == 200, response.text
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    assert c.post(f"/api/admin/sap/applications/{uid}/enqueue", headers=admin).status_code == 503
    stored = inner.get("submissions", f"student_aid_bursary:{uid}")
    assert stored["data"].startswith(PREFIX)
    assert "private" not in stored["data"] and "ada@example.ca" not in stored["data"]
    assert c.get("/api/me/sap", headers=user).json()["data"]["hardship"] == data["hardship"].strip()
    assert c.get("/api/admin/sap/applications", headers=user).status_code == 403
    assert not c.get("/api/admin/sap/applications", headers=admin).json()["items"][0]["eligible"]
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    assert c.get("/api/me/sap", headers=user).json()["eligible"]
    assert c.get("/api/admin/sap/applications?limit=0", headers=admin).status_code == 422


def test_sap_scoring_requires_weights_and_complete_questionnaire():
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "private " * 10,
            "contact_email": "ada@example.ca", "consent": True, "bursary_type": "Living-costs Bursary"}
    def submit(values):
        return c.put("/api/forms/student_aid_bursary/submission",
                     json={"version": form["version"], "hash": form["hash"], "data": values}, headers=user)
    assert submit(data).status_code == 200
    assert c.get("/api/me/sap", headers=user).json()["wfni"]["status"] == "weights_not_configured"
    weights = (Path(__file__).resolve().parents[1] / "data" / "wfni_weights.csv").read_text()
    assert c.put("/api/admin/sap/weights", json={"csv": weights}, headers=user).status_code == 403
    assert c.put("/api/admin/sap/weights", json={"csv": "bad"}, headers=admin).status_code == 422
    config = c.put("/api/admin/sap/weights", json={"csv": weights}, headers=admin).json()
    result = c.get("/api/me/sap", headers=user).json()["wfni"]
    assert result["status"] == "incomplete" and set(result["missing"]) == set(QUESTIONS)
    answers = {"disability_mobility": "Yes", "disability_invisible": "No", "living_situation": "Stably Housed",
               "disposable_income": "Less than $100", "liquidity": "$0 to $500", "dependents": "1",
               "external_supports": "No", "circumstances": "Racial Violence;Recent estrangement"}
    assert submit({**data, **answers}).status_code == 200
    result = c.get("/api/admin/sap/applications", headers=admin).json()["items"][0]["wfni"]
    assert result["score"] == 17 and result["status"] == "complete" and result["weights_hash"] == config["hash"]
    assert c.put("/api/admin/sap/weights", json={"csv": "invalid"}, headers=admin).status_code == 422
    assert c.get("/api/admin/sap/weights", headers=admin).json()["hash"] == config["hash"]


def test_plaintext_legacy_submission_is_readable_and_encrypted_when_updated():
    inner = MemoryStore()
    uid = "old"
    inner.put("submissions", uid, {"data": {"hardship": "legacy"},
                                   "sap_review": {"reviews": [], "id": "legacy"}})
    app = create_app(Settings(secret="s" * 32), store=inner)
    store = app.state.ctx.store
    assert store.get("submissions", uid)["data"] == {"hardship": "legacy"}
    assert store.get("submissions", uid)["sap_review"]["id"] == "legacy"
    store.update("submissions", uid, lambda item: {**item, "status": "submitted"})
    assert inner.get("submissions", uid)["data"].startswith(PREFIX)
    assert inner.get("submissions", uid)["sap_review"].startswith(PREFIX)
    assert store.get("submissions", uid)["data"] == {"hardship": "legacy"}


def test_legacy_account_reconfirms_roster_number_after_restart():
    inner, mail = MemoryStore(), OutboxMailer()
    settings = Settings(secret="s" * 32, admin_emails=("boss@x.ca",))
    c = TestClient(create_app(settings, store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca", sn="1234567890")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).json()["rostered"] == 1
    assert inner.get("roster_identifiers", uid)["digits"].startswith(PREFIX)
    assert c.get("/api/agm/invitation", headers=user).status_code == 200
    inner.delete("roster_identifiers", uid)
    c = TestClient(create_app(settings, store=inner, mailer=mail))
    assert c.get("/api/agm/invitation", headers=user).status_code == 403
    assert c.post("/api/me/roster-number", json={"student_number": "1234567891"}, headers=user).status_code == 422
    assert c.get("/api/agm/invitation", headers=user).status_code == 403
    assert c.post("/api/me/roster-number", json={"student_number": "1234567890"}, headers=user).status_code == 200
    assert c.get("/api/agm/invitation", headers=user).status_code == 200
    assert c.get("/api/me", headers=user).json()["user"]["student_number"] == "7890"
    assert "digits" not in str(c.get("/api/me", headers=user).json())


def test_sap_banking_is_encrypted_private_and_irreversibly_destroyed():
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    banking = {"institution_number": "001", "transit_number": "00023", "account_number": "0000456"}
    assert c.put("/api/sap/banking", json=banking, headers=user).status_code == 409
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "private " * 10,
            "contact_email": "ada@example.ca", "consent": True}
    assert c.put("/api/forms/student_aid_bursary/submission",
                 json={"version": form["version"], "hash": form["hash"], "data": data}, headers=user).status_code == 200
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    assert c.put("/api/sap/banking", json={**banking, "transit_number": "123"}, headers=user).status_code == 422
    assert c.put("/api/sap/banking", json=banking, headers=user).json() == {"status": "on_file"}
    stored = inner.get("sap_banking", uid)
    assert all(stored[field].startswith(PREFIX) for field in banking)
    assert c.get("/api/sap/banking", headers=user).json() == {"status": "on_file"}
    path = f"/api/admin/sap/applications/{uid}/banking"
    assert c.get(path, headers=user).status_code == 403
    assert c.get(path, headers=admin).json() == banking
    assert c.put("/api/sap/banking", json=banking, headers=user).status_code == 409
    assert c.request("DELETE", path, json={"confirm": False}, headers=admin).status_code == 422
    assert c.request("DELETE", path, json={"confirm": True}, headers=admin).json() == {"status": "destroyed"}
    assert not any(field in inner.get("sap_banking", uid) for field in banking)
    assert c.get(path, headers=admin).status_code == 404
    assert c.put("/api/sap/banking", json=banking, headers=user).status_code == 409
    assert all("0000456" not in str(entry) for entry in c.app.state.ctx.audit.entries())


def test_banking_reverification_is_single_use_encrypted_and_requires_acceptance(monkeypatch):
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "private " * 10,
            "contact_email": "ada@example.ca", "consent": True}
    assert c.put("/api/forms/student_aid_bursary/submission",
                 json={"version": form["version"], "hash": form["hash"], "data": data}, headers=user).status_code == 200
    old = {"institution_number": "001", "transit_number": "00023", "account_number": "0000456"}
    new = {"institution_number": "002", "transit_number": "12345", "account_number": "999888"}
    assert c.put("/api/sap/banking", json=old, headers=user).status_code == 200
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    path = f"/api/admin/sap/applications/{uid}/banking"
    assert c.post(path + "/reverify", headers=user).status_code == 403
    c.app.state.ctx.mailer = Mailer()
    assert c.post(path + "/reverify", headers=admin).status_code == 503
    assert c.get("/api/sap/banking", headers=user).json() == {"status": "on_file"}
    c.app.state.ctx.mailer = mail
    assert c.post(path + "/reverify", headers=admin).status_code == 200
    link = mail.outbox[-1][2].splitlines()[-1]
    token = parse_qs(urlsplit(link).fragment)["sap-banking"][0]
    assert token not in str(inner.get("sap_banking", uid))
    assert c.put("/api/sap/banking/reverify", json={**new, "token": "wrong"}, headers=user).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: c.put("/api/sap/banking/reverify",
                             json={**new, "token": token}, headers=user), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert c.get("/api/sap/banking", headers=user).json() == {"status": "reverify_received"}
    raw = inner.get("sap_banking", uid)
    assert raw["pending"].startswith(PREFIX) and "999888" not in str(raw)
    assert c.get(path, headers=admin).json() == old
    assert c.post(path + "/reverify", headers=admin).status_code == 409
    assert c.get(path + "/pending", headers=user).status_code == 403
    comparison = c.get(path + "/pending", headers=admin).json()
    assert comparison["active"] == old and comparison["pending"] == new
    assert c.post(path + "/accept", json={"revision": comparison["revision"], "confirm": False}, headers=admin).status_code == 422
    assert c.post(path + "/accept", json={"revision": comparison["revision"] - 1, "confirm": True}, headers=admin).status_code == 409
    assert c.post(path + "/accept", json={"revision": comparison["revision"], "confirm": True}, headers=admin).status_code == 200
    assert c.get(path, headers=admin).json() == new
    assert c.get(path + "/pending", headers=admin).status_code == 404
    assert "pending" not in inner.get("sap_banking", uid)
    assert all("999888" not in str(entry) for entry in c.app.state.ctx.audit.entries())
    assert c.post(path + "/reverify", headers=admin).status_code == 200
    token = parse_qs(urlsplit(mail.outbox[-1][2].splitlines()[-1]).fragment)["sap-banking"][0]
    expires = c.app.state.ctx.store.get("sap_banking", uid)["request_expires"]
    monkeypatch.setattr(c.app.state.ctx, "now", lambda: expires)
    assert c.put("/api/sap/banking/reverify", json={**old, "token": token}, headers=user).status_code == 409
    assert c.request("DELETE", path, json={"confirm": True}, headers=admin).status_code == 200
    assert c.put("/api/sap/banking/reverify", json={**old, "token": token}, headers=user).status_code == 409


def test_review_queue_claims_decision_and_submission_lock():
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",), sap_review_enabled=True),
                              store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    reviewers = []
    for name, number in (("reviewone", "1111111111"), ("reviewtwo", "2222222222")):
        register(c, mail, name, f"{name}@x.ca", sn=number)
        auth = login(c, mail, name)
        activate(c, admin, auth)
        assert c.put("/api/admin/roles", json={"username": name, "roles": ["sap_reviewer"]}, headers=admin).status_code == 200
        reviewers.append(auth)
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "Ada private " * 10,
            "contact_email": "ada@example.ca", "consent": True, "bursary_type": "Academic Pursuits Bursary"}
    body = {"version": form["version"], "hash": form["hash"], "data": data}
    assert c.put("/api/forms/student_aid_bursary/submission", json=body, headers=user).status_code == 200
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    assert c.patch("/api/me", json={"preferred_name": "Countess"}, headers=user).status_code == 200
    assert c.post(f"/api/admin/sap/applications/{uid}/enqueue", headers=user).status_code == 403
    review_id = c.post(f"/api/admin/sap/applications/{uid}/enqueue", headers=admin).json()["review_id"]
    assert c.put("/api/forms/student_aid_bursary/submission", json=body, headers=user).status_code == 409
    assert c.post(f"/api/admin/submissions/student_aid_bursary/{uid}/status",
                  json={"status": "accepted"}, headers=admin).status_code == 409
    hardship_path = f"/api/admin/sap/applications/{uid}/review-hardship"
    application = c.get("/api/admin/sap/applications", headers=admin).json()["items"][0]
    assert application["preferred_name"] == "Countess"
    revision = application["revision"]
    prepared = {"text": "The applicant describes financial hardship.", "confirm_redacted": True, "revision": revision}
    assert c.get("/api/sap/queue", headers=reviewers[0]).json() == []
    assert c.post(f"/api/sap/queue/{review_id}/claim", headers=reviewers[0]).status_code == 409
    assert c.put(hardship_path, json=prepared, headers=user).status_code == 403
    assert c.put(hardship_path, json={**prepared, "confirm_redacted": False}, headers=admin).status_code == 422
    assert c.put(hardship_path, json={**prepared, "revision": revision - 1}, headers=admin).status_code == 409
    for identifiable in ("Ada needs assistance", "LOVELACE needs assistance", "Countess needs assistance",
                         "Student 1234567 needs assistance",
                         "Contact ada@example.ca"):
        response = c.put(hardship_path, json={**prepared, "text": identifiable}, headers=admin)
        assert response.status_code == 422 and "Automatic checks" in response.json()["detail"]
        assert "sap_review_hardship" not in inner.get("submissions", f"student_aid_bursary:{uid}")
    assert c.put(hardship_path, json=prepared, headers=admin).status_code == 200
    assert inner.get("submissions", f"student_aid_bursary:{uid}")["sap_review_hardship"].startswith(PREFIX)
    queue = c.get("/api/sap/queue", headers=reviewers[0]).json()
    assert len(queue) == 1 and queue[0]["review_id"] == review_id
    assert uid not in str(queue) and "Ada" not in str(queue) and "example.ca" not in str(queue)
    assert "hardship" not in queue[0]
    assert c.get(f"/api/sap/review/{review_id}", headers=reviewers[0]).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: c.post(f"/api/sap/queue/{review_id}/claim", headers=reviewers[0]), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    claimed = next(response for response in responses if response.status_code == 200)
    assert claimed.status_code == 200 and claimed.json()["hardship"] == prepared["text"]
    assert c.get("/api/sap/queue", headers=reviewers[0]).json() == []
    assert len(c.get("/api/sap/queue", headers=reviewers[1]).json()) == 1
    assert c.get(f"/api/sap/review/{review_id}", headers=reviewers[0]).json()["hardship"] == prepared["text"]
    revision = c.get("/api/admin/sap/applications", headers=admin).json()["items"][0]["revision"]
    assert c.put(hardship_path, json={**prepared, "revision": revision}, headers=admin).status_code == 409
    assert c.post(f"/api/sap/queue/{review_id}/release", headers=reviewers[0]).status_code == 200
    assert c.get(f"/api/sap/review/{review_id}", headers=reviewers[0]).status_code == 409
    for auth, score in zip(reviewers, (1, 4)):
        assert c.post(f"/api/sap/queue/{review_id}/claim", headers=auth).status_code == 200
        assert c.post(f"/api/sap/queue/{review_id}/claim", headers=auth).status_code == 409
        assert c.put(f"/api/sap/review/{review_id}", json={"level": score}, headers=auth).status_code == 200
    assert c.get("/api/sap/queue", headers=reviewers[0]).json() == []
    assert c.get("/api/admin/sap/review-report", headers=user).status_code == 403
    assert c.get("/api/admin/sap/review-report", headers=admin).json()[0]["stage"] == "discuss"
    assert c.post(f"/api/admin/sap/applications/{uid}/decision",
                  json={"approved": True, "amount": 900}, headers=admin).status_code == 409
    assert c.put(f"/api/sap/review/{review_id}", json={"level": 3}, headers=reviewers[0]).status_code == 200
    assert c.get("/api/admin/sap/review-report", headers=admin).json()[0]["stage"] == "reconciled"
    assert c.put(f"/api/admin/sap/applications/{uid}/review-count",
                 json={"required_reviews": 2}, headers=admin).status_code == 422
    assert c.put(f"/api/admin/sap/applications/{uid}/review-count",
                 json={"required_reviews": 3}, headers=admin).status_code == 200
    assert c.post(f"/api/admin/sap/applications/{uid}/decision",
                  json={"approved": True, "amount": 900}, headers=admin).status_code == 409
    register(c, mail, "reviewthree", "reviewthree@x.ca", sn="3333333333")
    third = login(c, mail, "reviewthree")
    activate(c, admin, third)
    assert c.put("/api/admin/roles", json={"username": "reviewthree", "roles": ["sap_reviewer"]}, headers=admin).status_code == 200
    third_uid = c.get("/api/me", headers=third).json()["user"]["uuid"]
    assign_path = f"/api/admin/sap/review/{review_id}/assign"
    assert c.post(assign_path, json={"reviewer": uid}, headers=admin).status_code == 422
    assert c.post(assign_path, json={"reviewer": third_uid}, headers=third).status_code == 403
    assert c.post(assign_path, json={"reviewer": third_uid}, headers=admin).status_code == 200
    assert [r["review_id"] for r in c.get("/api/sap/my-reviews", headers=third).json()] == [review_id]
    assert c.get(f"/api/sap/review/{review_id}", headers=third).json()["hardship"] == prepared["text"]
    assert c.post(f"/api/sap/queue/{review_id}/claim", headers=third).status_code == 409
    assert c.put(f"/api/sap/review/{review_id}", json={"level": 3.5}, headers=third).status_code == 200
    assert c.post(f"/api/admin/sap/applications/{uid}/decision",
                  json={"approved": True, "amount": 1100}, headers=admin).status_code == 422
    decision = c.post(f"/api/admin/sap/applications/{uid}/decision",
                      json={"approved": True, "amount": 900}, headers=admin)
    assert decision.status_code == 200 and decision.json()["amount"] == 900
    export_path = "/api/admin/sap/applications.csv"
    assert c.get(export_path, headers=user).status_code == 403
    assert c.get(export_path, headers=reviewers[0]).status_code == 403
    export = c.get(export_path, headers=admin)
    assert export.status_code == 200 and export.headers["cache-control"] == "no-store"
    exported = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
    assert len(exported) == 1 and exported[0]["approved"] == "True" and exported[0]["awarded_amount"] == "900.0"
    assert data["hardship"] not in export.text and review_id not in export.text
    assert "sap.applications_exported" in {e["action"] for e in c.get("/api/admin/audit?limit=200", headers=admin).json()["entries"]}
    assert c.get("/api/me/sap", headers=user).json()["submission_status"] == "accepted"
    assert c.put(f"/api/sap/review/{review_id}", json={"level": 4}, headers=reviewers[0]).status_code == 409
    assert c.put("/api/forms/student_aid_bursary/submission", json=body, headers=user).status_code == 409
    overlap = {**body, "data": {**data, "terms": ["Fall 2026", "Winter 2027"]}}
    assert c.put("/api/forms/student_aid_bursary/submission", json=overlap, headers=user).status_code == 409
    new = {**body, "data": {**data, "terms": ["Winter 2027"]}}
    assert c.put("/api/forms/student_aid_bursary/submission", json=new, headers=user).status_code == 200
    assert c.put("/api/forms/student_aid_bursary/submission", json=overlap, headers=user).status_code == 409
    history = c.get("/api/me/sap/history", headers=user).json()
    assert len(history) == 1 and history[0]["data"]["terms"] == ["Fall 2026"]
    assert history[0]["decision"] == {"approved": True, "amount": 900, "at": decision.json()["at"]}
    assert "reviewer" not in str(history) and review_id not in str(history)
    path = f"/api/admin/sap/applications/{uid}/history"
    assert c.get(path, headers=user).status_code == 403
    assert c.get(path, headers=admin).json()[0]["sap_review"]["id"] == review_id
    assert inner.get("submissions", f"student_aid_bursary:{uid}")["sap_previous"].startswith(PREFIX)
    assert c.get("/api/me/sap", headers=user).json()["data"]["terms"] == ["Winter 2027"]
    assert c.get(f"/api/sap/review/{review_id}", headers=reviewers[0]).status_code == 404

    second_id = c.post(f"/api/admin/sap/applications/{uid}/enqueue", headers=admin).json()["review_id"]
    revision = c.get("/api/admin/sap/applications", headers=admin).json()["items"][0]["revision"]
    assert c.put(hardship_path, json={**prepared, "revision": revision}, headers=admin).status_code == 200
    for auth in reviewers:
        assert c.post(f"/api/sap/queue/{second_id}/claim", headers=auth).status_code == 200
        assert c.put(f"/api/sap/review/{second_id}", json={"level": 3}, headers=auth).status_code == 200
    assert c.post(f"/api/admin/sap/applications/{uid}/decision",
                  json={"approved": False, "amount": 0}, headers=admin).status_code == 200
    third_body = {**body, "data": {**data, "terms": ["Summer 2027"]}}
    key = f"student_aid_bursary:{uid}"
    saved = inner.get("submissions", key)
    inner.put("submissions", key, {k: v for k, v in saved.items() if k != "data"})
    assert c.put("/api/forms/student_aid_bursary/submission", json=third_body, headers=user).status_code == 409
    inner.put("submissions", key, saved)
    assert c.put("/api/forms/student_aid_bursary/submission", json=third_body, headers=user).status_code == 200
    history = c.get("/api/me/sap/history", headers=user).json()
    assert [item["data"]["terms"] for item in history] == [["Fall 2026"], ["Winter 2027"]]
    assert [item["decision"]["approved"] for item in history] == [True, False]
    for term in ("Fall 2026", "Winter 2027"):
        assert c.put("/api/forms/student_aid_bursary/submission",
                     json={**body, "data": {**data, "terms": [term]}}, headers=user).status_code == 409
    assert [item["sap_review"]["id"] for item in c.get(path, headers=admin).json()] == [review_id, second_id]
    store = c.app.state.ctx.store
    store.update("submissions", key, lambda current: {
        **current, "sap_previous": [{**current["sap_previous"][0],
                                      "data": {**current["sap_previous"][0]["data"], "terms": ["  fALL   2026 "]}},
                                     *current["sap_previous"][1:]]})
    assert c.put("/api/forms/student_aid_bursary/submission",
                 json={**body, "data": {**data, "terms": ["Fall 2026"]}}, headers=user).status_code == 409
    assert c.put("/api/admin/roles", json={"username": "reviewthree", "roles": ["forms_editor"]},
                 headers=admin).status_code == 200
    assert c.get(path, headers=third).status_code == 403
    general = c.get("/api/admin/forms/student_aid_bursary/submissions", headers=third).json()
    assert len(general) == 1 and not ({"sap_previous", "sap_review", "sap_review_hardship"} & general[0].keys())
    assert review_id not in str(general) and second_id not in str(general)


def test_reviewer_can_flag_conflict_and_release_claim():
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",), sap_review_enabled=True),
                              store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    register(c, mail, "reviewer", "reviewer@x.ca", sn="1111111111")
    reviewer = login(c, mail, "reviewer")
    activate(c, admin, reviewer)
    assert c.put("/api/admin/roles", json={"username": "reviewer", "roles": ["sap_reviewer"]}, headers=admin).status_code == 200
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "private " * 10,
            "contact_email": "ada@example.ca", "consent": True, "bursary_type": "Academic Pursuits Bursary"}
    assert c.put("/api/forms/student_aid_bursary/submission",
                 json={"version": form["version"], "hash": form["hash"], "data": data}, headers=user).status_code == 200
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    rid = c.post(f"/api/admin/sap/applications/{uid}/enqueue", headers=admin).json()["review_id"]
    revision = c.get("/api/admin/sap/applications", headers=admin).json()["items"][0]["revision"]
    assert c.put(f"/api/admin/sap/applications/{uid}/review-hardship",
                 json={"text": "Redacted statement", "confirm_redacted": True, "revision": revision}, headers=admin).status_code == 200
    assert c.post(f"/api/sap/queue/{rid}/claim", headers=reviewer).status_code == 200
    assert c.post(f"/api/sap/review/{rid}/conflict", headers=reviewer).status_code == 200
    assert c.get("/api/sap/my-reviews", headers=reviewer).json() == []
    assert c.get(f"/api/sap/review/{rid}", headers=reviewer).status_code == 403
    assert c.get("/api/sap/queue", headers=reviewer).json() == []
    assert c.post(f"/api/sap/queue/{rid}/claim", headers=reviewer).status_code == 403
    reviewer_uid = c.get("/api/me", headers=reviewer).json()["user"]["uuid"]
    assert c.post(f"/api/admin/sap/review/{rid}/assign", json={"reviewer": reviewer_uid}, headers=admin).status_code == 403


def test_reviewers_with_same_roster_record_cannot_claim():
    inner, mail = MemoryStore(), OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",), sap_review_enabled=True),
                              store=inner, mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    register(c, mail, "reviewer", "reviewer@x.ca", sn="1111111111")
    reviewer = login(c, mail, "reviewer")
    activate(c, admin, reviewer)
    assert c.put("/api/admin/roles", json={"username": "reviewer", "roles": ["sap_reviewer"]}, headers=admin).status_code == 200
    roster = "First Name,Last Name,Truncated Student Number,Faculty,Division\nAda,Lovelace,xxxxx6789x,ARTSC,UC\n"
    assert c.post("/api/admin/roster", json={"csv": roster}, headers=admin).status_code == 200
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    reviewer_uid = c.get("/api/me", headers=reviewer).json()["user"]["uuid"]
    check = c.app.state.ctx.store.get("roster_checks", uid)
    c.app.state.ctx.store.put("roster_checks", reviewer_uid, {**check, "uuid": reviewer_uid})
    form = c.get("/api/forms/student_aid_bursary/static").json()
    data = {"terms": ["Fall 2026"], "enrolment": "Full-time", "monthly_income": 500,
            "monthly_expenses": 1500, "amount_requested": 1000, "hardship": "private " * 10,
            "contact_email": "ada@example.ca", "consent": True, "bursary_type": "Academic Pursuits Bursary"}
    assert c.put("/api/forms/student_aid_bursary/submission",
                 json={"version": form["version"], "hash": form["hash"], "data": data}, headers=user).status_code == 200
    review_id = c.post(f"/api/admin/sap/applications/{uid}/enqueue", headers=admin).json()["review_id"]
    assert c.get("/api/sap/queue", headers=reviewer).json() == []
    assert c.post(f"/api/sap/queue/{review_id}/claim", headers=reviewer).status_code == 403
