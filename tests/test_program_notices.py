from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from test_portal import activate, login, register
from utsu_core import create_app
from utsu_core.mailer import Mailer, OutboxMailer
from utsu_core.settings import Settings


@pytest.mark.parametrize(("program", "form_id"), [
    ("agm", "agm_rsvp"),
    ("senate", "senate_application"),
    ("sap", "student_aid_bursary"),
])
def test_program_notice_requires_preview_and_sends_only_once(program, form_id):
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    store = c.app.state.ctx.store
    key = f"{form_id}:{uid}"
    store.update("submissions", key, lambda _: {"id": key, "uuid": uid, "form_id": form_id, "status": "submitted"})
    base = f"/api/admin/notices/{program}"
    assert c.post(base + "/preview", json={"uid": uid}, headers=user).status_code == 403
    assert c.post(base + "/send", json={"uid": uid, "confirm": True, "preview_hash": "0" * 64}, headers=user).status_code == 403
    preview = c.post(base + "/preview", json={"uid": uid}, headers=admin).json()
    assert preview["to"] == "ada@x.ca" and preview["delivery_status"] is None
    assert "submitted" not in preview["body"]
    assert preview["subject"] == "Your UTSU portal account"
    assert "Bursary" not in preview["subject"] + preview["body"]
    payload = {"uid": uid, "confirm": True, "preview_hash": preview["preview_hash"]}
    assert c.post(base + "/send", json={**payload, "confirm": False}, headers=admin).status_code == 422
    store.update("submissions", key, lambda current: {**current, "status": "needs_info"})
    assert c.post(base + "/send", json=payload, headers=admin).status_code == 409
    preview = c.post(base + "/preview", json={"uid": uid}, headers=admin).json()
    payload["preview_hash"] = preview["preview_hash"]
    before = len(mail.outbox)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: c.post(base + "/send", json=payload, headers=admin), range(2)))
    assert sorted(result.status_code for result in results) == [200, 409]
    assert len(mail.outbox) == before + 1
    assert mail.outbox[-1] == (preview["to"], preview["subject"], preview["body"])
    assert c.post(base + "/preview", json={"uid": uid}, headers=admin).json()["delivery_status"] == "sent"
    assert "notice.sent" in {e["action"] for e in c.get("/api/admin/audit?limit=200", headers=admin).json()["entries"]}


def test_notice_delivery_failure_is_unresolved_and_not_retried():
    mail = OutboxMailer()
    c = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), mailer=mail))
    register(c, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(c, mail, "boss")
    register(c, mail, "ada", "ada@x.ca")
    user = login(c, mail, "ada")
    activate(c, admin, user)
    uid = c.get("/api/me", headers=user).json()["user"]["uuid"]
    key = f"agm_rsvp:{uid}"
    c.app.state.ctx.store.update("submissions", key, lambda _: {"id": key, "uuid": uid, "status": "submitted"})
    preview = c.post("/api/admin/notices/agm/preview", json={"uid": uid}, headers=admin).json()
    payload = {"uid": uid, "confirm": True, "preview_hash": preview["preview_hash"]}
    c.app.state.ctx.mailer = Mailer()
    assert c.post("/api/admin/notices/agm/send", json=payload, headers=admin).status_code == 503

    class UncertainMailer(Mailer):
        def send(self, to, subject, body):
            raise OSError("delivery unknown")

    c.app.state.ctx.mailer = UncertainMailer()
    assert c.post("/api/admin/notices/agm/send", json=payload, headers=admin).status_code == 502
    assert c.post("/api/admin/notices/agm/send", json=payload, headers=admin).status_code == 409
    assert c.post("/api/admin/notices/agm/preview", json={"uid": uid}, headers=admin).json()["delivery_status"] == "unknown"
