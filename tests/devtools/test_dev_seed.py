import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_portal import login, register
from core import create_app
from core import security
from core.devadmin import DEV_ADMIN_EMAIL
from core.mailer import OutboxMailer
from core.settings import Settings
from core.store import MemoryStore


def test_dev_restart_only_inserts_missing_seed_rows():
    class SeedStore(MemoryStore):
        def create(self, col, id, doc):
            if col in {"dev_environment", "emails", "users", "accounts", "audit_months"}:
                assert self.get(col, id) is None, f"redundant insert into {col}"
            return super().create(col, id, doc)

    settings = Settings(secret="s" * 40, dev_admin=True)
    store = SeedStore()
    ctx = create_app(settings, store, OutboxMailer()).state.ctx
    legacy_uid = ctx.uid(DEV_ADMIN_EMAIL)
    applicant_uid = ctx.uid("applicant+1@dev-local.test")
    profile = store.get("users", applicant_uid)
    store.delete("users", applicant_uid)

    restarted = create_app(settings, store, OutboxMailer()).state.ctx
    assert restarted.uid(DEV_ADMIN_EMAIL) == legacy_uid
    assert restarted.uid("applicant+1@dev-local.test") == applicant_uid
    assert store.get("users", applicant_uid) == profile
    before = len(restarted.audit.entries())
    restarted.audit.record(None, "test.dev_restart")
    assert len(restarted.audit.entries()) == before + 1


@pytest.mark.parametrize("plugins", [None, ("discussion",)])
def test_dev_seeds_verified_roles_and_applicants(plugins):
    settings = Settings(secret="s" * 40, enabled_plugins=plugins, dev_admin=True, rate_limit_auth=0,
                        applicant_email_domains=("school.example.ca",))
    mail, store = OutboxMailer(), MemoryStore()
    c = TestClient(create_app(settings, store, mail))
    admin = login(c, mail, "admin@dev-local.test")
    response = c.get("/api/admin/roles", headers=admin)
    assert response.status_code == 200
    roles = response.json()
    assert ("discussion_moderator" in roles) == (plugins is not None)
    expected = {f"{role}@dev-local.test": [role] for role in roles}
    expected.update({f"applicant+{n}@dev-local.test": [] for n in (1, 2, 3)})
    user_ids = set()
    for email, assigned in expected.items():
        me = c.get("/api/me", headers=login(c, mail, email)).json()
        assert (me["user"]["email"], me["status"], me["roles"]) == (email, "verified", assigned)
        uid = uuid.UUID(me["user"]["uuid"])
        assert uid.version == 4
        user_ids.add(uid)
    assert len(user_ids) == len(expected)
    accounts_before = {a["uuid"]: a for a in store.list("accounts")}
    create_app(settings, store, mail)
    assert {a["uuid"]: a for a in store.list("accounts")} == accounts_before


def test_dev_seeding_does_not_grant_roles_to_an_existing_account(monkeypatch):
    monkeypatch.setattr("core.services.turnstile.verify_registration", lambda *_: None)
    mail, store = OutboxMailer(), MemoryStore()
    c = TestClient(create_app(Settings(secret="s" * 40), store, mail))
    email = "admin@dev-local.test"
    register(c, mail, "nonadmin", email)
    uid = c.app.state.ctx.uid(email)
    before = store.get("accounts", uid)
    c = TestClient(create_app(Settings(secret="s" * 40, dev_admin=True), store, mail))
    assert c.app.state.ctx.uid(email) == uid
    assert store.get("accounts", uid) == before


def test_generated_seed_does_not_overwrite_dev_data(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "testing"))
    import dev_seed

    def exercise(_, __, target):
        target.put("accounts", "fixture", {"name": "Generated"})
        target.put("financial_profiles", "fixture", {"status": "on_file", "account_number": "original"})
        return {}

    monkeypatch.setattr(dev_seed.subprocess, "run", lambda *_, **__: None)
    monkeypatch.setattr(dev_seed, "exercise_api", exercise)
    settings = Settings(dev_admin=True)
    ordinary = MemoryStore()
    ordinary.put("accounts", "existing", {"name": "Keep me"})
    with pytest.raises(RuntimeError, match="populated database"):
        dev_seed.prepare_database(settings, ordinary)
    generated = MemoryStore()
    dev_seed.prepare_database(settings, generated)
    assert generated.get("accounts", "fixture") == {"name": "Generated"}
    generated.put("accounts", "fixture", {"name": "Edited"})
    generated.put("financial_profiles", "fixture", {"status": "on_file", "account_number": "edited"})
    dev_seed.prepare_database(settings, generated)
    assert generated.get("accounts", "fixture") == {"name": "Edited"}
    assert generated.get("financial_profiles", "fixture")["account_number"] == "edited"


def test_interrupted_generated_seed_requires_fresh_project(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "testing"))
    import dev_seed

    store = MemoryStore()
    store.put(*dev_seed.SEED_MARKER, {"status": "in_progress"})
    with pytest.raises(RuntimeError, match="did not complete"):
        dev_seed.prepare_database(Settings(dev_admin=True), store)


def test_generated_seed_populates_students_roster_and_program_submissions(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "testing"))
    import dev_seed

    settings = Settings(secret="s" * 40, dev_admin=True, rate_limit_auth=0)
    store = MemoryStore()
    dev_seed.prepare_database(settings, store)
    ctx = create_app(settings, store, OutboxMailer()).state.ctx
    students = [user for user in ctx.store.list("users") if user["email"].startswith("student+")]
    assert len(students) == 1000
    assert store.get("roster_meta", "current")["count"] == 1200
    student_ids = {user["uuid"] for user in students}
    for user in students:
        uid = user["uuid"]
        digits = user["email"].split("+", 1)[1].split("@", 1)[0]
        sid = security.student_id_uuid(settings.secret, f"12345{digits}0")
        assert store.get("student_ids", sid)["uuid"] == uid
        assert ctx.store.get("roster_identifiers", uid)["digits"] == digits
        assert store.get("accounts", uid)["status"] == "verified"
        assert ctx.identity.roster_check(uid)["rostered"]
    for form in ("agm_rsvp", "senate_application", "student_aid_bursary"):
        submissions = store.list("submissions", form_id=form)
        submitters = {submission["uuid"] for submission in submissions}
        assert len(submissions) == len(submitters) == 30
        assert submitters <= student_ids

    sap_submitters = {submission["uuid"] for submission in store.list("submissions", form_id="student_aid_bursary")}
    banking = ctx.store.list("financial_profiles")
    assert len(banking) == 15
    assert {bank["uuid"] for bank in banking} <= sap_submitters
    by_id = {user["uuid"]: user for user in students}
    for bank in banking:
        digits = by_id[bank["uuid"]]["email"].split("+", 1)[1].split("@", 1)[0]
        assert bank["status"] == "on_file"
        assert (bank["institution_number"], bank["transit_number"], bank["account_number"]) == ("000", "12345", f"12345{digits}")
