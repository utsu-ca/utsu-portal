from fastapi.testclient import TestClient

from test_portal import activate, setup_admin_and_user
from core import create_app
from core.mailer import OutboxMailer
from core.settings import Settings
from core.store import MemoryStore


def test_admin_publishes_public_copy_with_revision_guard_and_audit():
    settings = Settings(secret="s" * 32, admin_emails=("boss@x.ca",), enabled_plugins=())
    store, mail = MemoryStore(), OutboxMailer()
    app = create_app(settings, store, mail)
    client = TestClient(app)
    admin, member = setup_admin_and_user(client, mail)
    activate(client, admin, member)
    copy = {"title": "Bursaries", "tagline": "New dates", "summary": ["Updated for members."],
            "eligibility": [], "steps": [], "deadlines": ["January 20"]}
    route = "/api/admin/content/bursaries"
    payload = {"base_rev": 0, "copy": copy}

    assert client.get("/api/content").json() == {"pages": {}}
    assert client.put(route, json=payload).status_code == 401
    assert client.put(route, headers=member, json=payload).status_code == 403
    invalid = {"base_rev": 0, "copy": {**copy, "summary": ["   "]}}
    assert client.put(route, headers=admin, json=invalid).status_code == 422
    assert client.put(route, headers=admin, json=payload).json() == {"rev": 1, "copy": copy}
    assert client.put(route, headers=admin, json=payload).status_code == 409
    next_copy = {**copy, "deadlines": ["February 1"]}
    assert client.put(route, headers=admin, json={"base_rev": 1, "copy": next_copy}).status_code == 200

    reopened = TestClient(create_app(settings, store, mail))
    assert reopened.get("/api/content").json() == {"pages": {"bursaries": {"rev": 2, "copy": next_copy}}}
    entries = client.get("/api/admin/audit?action=content.published", headers=admin).json()
    assert len(entries["entries"]) == 2
