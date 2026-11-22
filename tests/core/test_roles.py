from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_authsvc import make  # noqa: E402
from test_portal import activate, login, register  # noqa: E402

from utsu_core.context import ROLES
from utsu_core.context import roles_of
from utsu_core.permissions import PERM_AUDIT_READ, PERM_FORMS_MANAGE, permission_catalogue_for, role_permissions_for
from utsu_core.plugin_host import discover_manifests, select_manifests
from utsu_core.role_catalogue import role_catalogue_for


def setup():
    ac, pc, mail, astore, pstore = make()
    register(ac, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    ah = login(ac, mail, "boss")
    register(ac, mail, "ada", "ada@x.ca")
    uh = login(ac, mail, "ada")
    activate(ac, ah, uh)  # ada: verified applicant, no roles
    return ac, pc, mail, astore, ah, uh


def put(ac, h, user, roles):
    return ac.put("/api/admin/roles", json={"username": user, "roles": roles}, headers=h)


def test_roles_scope_access_and_are_issued_by_auth_service():
    ac, pc, mail, astore, ah, uh = setup()
    assert ac.get("/api/admin/verifications", headers=uh).status_code == 403
    assert put(ac, uh, "ada", ["admin"]).status_code == 403
    assert put(ac, ah, "ada", ["auditor"]).json()["roles"] == ["auditor"]
    pc.app.state.ctx.identity._cache.clear()
    # auditor reads the audit log on the portal, but cannot verify identities, edit forms or run maintenance
    assert pc.get("/api/admin/audit", headers=uh).status_code == 200
    denied = pc.get("/api/admin/forms", headers=uh)
    assert denied.status_code == 403
    assert denied.headers["www-authenticate"] == (
        'Bearer error="insufficient_scope", scope="utsu:forms:manage"'
    )
    ada = pc.app.state.ctx.identity.account(ac.app.state.ctx.uid("ada"))
    assert pc.app.state.ctx.has_permission(ada, PERM_AUDIT_READ)
    assert not pc.app.state.ctx.has_permission(ada, PERM_FORMS_MANAGE)
    assert ac.get("/api/admin/verifications", headers=uh).status_code == 403
    assert ac.post("/api/admin/auth-audit/maintenance/run", headers=uh).status_code == 403
    assert put(ac, ah, "ada", ["forms_editor", "checkin"]).status_code == 200
    pc.app.state.ctx.identity._cache.clear()
    assert pc.get("/api/admin/forms", headers=uh).status_code == 200
    assert pc.get("/api/admin/audit", headers=uh).status_code == 403  # revoked
    assert pc.get("/api/admin/agm/attendance", headers=uh).status_code == 200
    assert astore.get("accounts", ac.app.state.ctx.uid("ada"))["roles"] == ["checkin", "forms_editor"]
    # granting is audited with before/after
    entry = next(e for e in ac.app.state.ctx.audit.entries() if e["action"] == "role.set")
    assert entry["actor"] == ac.app.state.ctx.uid("boss")


def test_grant_rules():
    ac, pc, mail, astore, ah, uh = setup()
    assert put(ac, ah, "boss", ["verifier"]).status_code == 403  # no self changes
    assert put(ac, ah, "ada", ["root"]).status_code == 422  # unknown role
    assert put(ac, ah, "nobody", ["auditor"]).status_code == 404
    register(ac, mail, "eve", "eve@x.ca", sn="5555555555")  # pending review, not verified
    assert put(ac, ah, "eve", ["auditor"]).status_code == 409
    assert put(ac, ah, "ada", ["admin"]).status_code == 200
    assert set(ROLES) >= {"admin", "verifier"} and ac.get("/api/admin/roles", headers=ah).json().keys() == ROLES.keys()
    uh2 = login(ac, mail, "ada")
    assert put(ac, uh2, "boss", []).status_code == 200  # a second admin may demote the first
    assert put(ac, ah, "ada", []).status_code == 403  # ...who is no longer admin
    holders = ac.get("/api/admin/roles/holders", headers=uh2).json()["items"]
    assert [h["roles"] for h in holders] == [["admin"]]


def test_role_holders_paginate_in_uuid_order_and_validate_parameters():
    ac, _, _, _, ah, uh = setup()
    store = ac.app.state.ctx.store
    assert put(ac, ah, "ada", ["auditor"]).status_code == 200
    for i in reversed(range(202)):
        uid = f"00000000-0000-0000-0000-{i:012d}"
        store.put("accounts", uid, {"uuid": uid, "status": "verified", "roles": ["auditor"]})
        store.put("users", uid, {"uuid": uid, "preferred_name": f"Holder {i}"})
    staff_uid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    store.put("accounts", staff_uid, {"uuid": staff_uid, "status": "verified", "roles": [],
                                      "google_staff_username": "staff"})
    store.put("users", staff_uid, {"uuid": staff_uid, "preferred_name": "Staff", "email": "staff@example.test"})
    path = "/api/admin/roles/holders"
    first = ac.get(path, headers=ah).json()
    assert len(first["items"]) == 100 and first["next_cursor"]
    large = ac.get(path, headers=ah, params={"limit": 200}).json()
    assert len(large["items"]) == 200 and large["next_cursor"]
    second = ac.get(path, headers=ah, params={"cursor": first["next_cursor"], "limit": 200}).json()
    holders = first["items"] + second["items"]
    ids = [holder["uuid"] for holder in holders]
    assert len(ids) == 205 and ids == sorted(set(ids))
    assert second["next_cursor"] is None
    assert holders[-1] == {"uuid": staff_uid, "preferred_name": "Staff", "roles": [],
                           "username": "staff", "email": "staff@example.test"}
    one = ac.get(path, headers=ah, params={"limit": 1}).json()
    following = ac.get(path, headers=ah, params={"cursor": one["next_cursor"], "limit": 1}).json()
    assert [one["items"][0]["uuid"], following["items"][0]["uuid"]] == ids[:2]
    for params in ({"limit": 0}, {"limit": 201}, {"limit": "bad"}, {"cursor": "not!valid"},
                   {"cursor": "WzFd"}, {"cursor": "a" * 2049}):
        assert ac.get(path, headers=ah, params=params).status_code == 422
    assert ac.get(path, headers=uh).status_code == 403


def test_sole_remaining_admin_cannot_demote_self():
    ac, pc, mail, astore, ah, uh = setup()
    put(ac, ah, "ada", ["admin"])
    uh2 = login(ac, mail, "ada")
    assert put(ac, uh2, "boss", []).status_code == 200
    # ada is now the only admin; boss (no longer admin) cannot act, and ada cannot demote herself
    assert put(ac, uh2, "ada", []).status_code == 403


def test_concurrent_admin_demotions_cannot_remove_the_last_admin():
    ac, pc, mail, astore, ah, uh = setup()
    assert put(ac, ah, "ada", ["admin"]).status_code == 200
    uh2 = login(ac, mail, "ada")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(put, ac, ah, "ada", []),
            pool.submit(put, ac, uh2, "boss", []),
        ]
        statuses = sorted(result.result().status_code for result in results)

    # The second request may be denied at authentication after its actor is demoted,
    # or reach the last-admin check while both requests still have admin credentials.
    assert statuses in ([200, 403], [200, 409])
    admins = [a for a in astore.list("accounts") if "admin" in roles_of(a)]
    assert len(admins) == 1


def test_disabled_program_roles_are_ineffective_but_stored_grants_are_preserved():
    selected = discover_manifests()
    manifests = select_manifests(selected, "local", ())
    active_roles = role_catalogue_for(manifests)
    active_permissions = permission_catalogue_for(manifests)
    active_role_permissions = role_permissions_for(manifests)
    account = {"roles": ["checkin", "auditor"]}

    assert "checkin" not in active_roles
    assert roles_of(account, active_roles) == {"auditor"}
    assert "utsu:agm:checkin:manage" not in active_permissions
    assert "checkin" not in active_role_permissions
    assert account["roles"] == ["checkin", "auditor"]


def test_role_edit_does_not_drop_disabled_program_grants():
    from fastapi.testclient import TestClient

    from utsu_core.authsvc import create_auth_app
    from utsu_core.mailer import OutboxMailer
    from utsu_core.settings import Settings
    from utsu_core.store import MemoryStore

    mail, store = OutboxMailer(), MemoryStore()
    ac = TestClient(create_auth_app(
        Settings(secret="s" * 40, admin_emails=("boss@x.ca",), enabled_plugins=()),
        store,
        mail,
    ))
    register(ac, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    ah = login(ac, mail, "boss")
    register(ac, mail, "ada", "ada@x.ca")
    uh = login(ac, mail, "ada")
    activate(ac, ah, uh)

    uid = ac.app.state.ctx.uid("ada")
    ac.app.state.ctx.store.update("accounts", uid, lambda account: {**account, "roles": ["checkin"]})
    assert put(ac, ah, "ada", ["auditor"]).status_code == 200
    assert ac.app.state.ctx.store.get("accounts", uid)["roles"] == ["auditor", "checkin"]
    assert ac.get("/api/admin/roles", headers=ah).json().get("checkin") is None


def test_service_token_rotation_accepts_old_and_new():
    from fastapi.testclient import TestClient

    from utsu_core.authsvc import create_auth_app
    from utsu_core.mailer import OutboxMailer
    from utsu_core.settings import Settings
    from utsu_core.store import MemoryStore

    old, new = "o" * 40, "n" * 40
    c = TestClient(create_auth_app(Settings(secret="a" * 40, service_token=f"{old},{new}"), MemoryStore(), OutboxMailer()))
    for t in (old, new):
        assert c.get("/internal/ping", headers={"Authorization": "Bearer " + t}).status_code == 200
    assert c.get("/internal/ping", headers={"Authorization": "Bearer " + "x" * 40}).status_code == 404
