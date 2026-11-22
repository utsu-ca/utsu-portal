import pytest
from fastapi.testclient import TestClient

from core import create_app
from core.crypto import Crypter, EncryptingStore, PREFIX
from core.identity import RemoteIdentity
from core.mailer import OutboxMailer
from core.services.banking import BankingRoutePolicy, banking_router
from core.settings import Settings
from core.store import MemoryStore

BANK = {"institution_number": "001", "transit_number": "12345", "account_number": "0009876"}


def account(ctx, uid, *, roles=(), status="verified"):
    ctx.store.put("accounts", uid, {"uuid": uid, "email_verified": True, "status": status,
                                    "roles": list(roles), "role": "applicant"})
    ctx.store.put("users", uid, {"uuid": uid, "email": f"{uid}@example.test"})
    return {"Authorization": "Bearer " + ctx.identity.issue(uid)}


def test_profile_separates_banking_and_audits_reads_without_optional_db_auditing(monkeypatch):
    inner = MemoryStore()
    app = create_app(Settings(secret="s" * 32, enabled_plugins=(), audit_db_access=False),
                     store=inner, mailer=OutboxMailer())
    c, ctx = TestClient(app), app.state.ctx
    user = account(ctx, "student")
    outsider = account(ctx, "other")
    finance = account(ctx, "finance", roles=("profile_finance",))
    assert c.put("/api/profile/financial", json=BANK).status_code == 401
    assert c.put("/api/profile/financial", json=BANK, headers=user).status_code == 200
    assert all(inner.get("financial_profiles", "student")[k].startswith(PREFIX) for k in BANK)
    assert inner.get("sap_banking", "student") is None
    assert not any(k in inner.get("users", "student") for k in BANK)
    assert c.get("/api/profile/financial", headers=user).json() == {"status": "on_file"}
    path = "/api/finance/profiles/student"
    assert c.get(path, headers=outsider).status_code == 403
    assert c.get(path, headers=finance).json() == BANK
    entries = ctx.audit.entries()
    assert any(e["action"] == "financial.access" and e["actor"] == "student"
               and e["target"] == "financial_profiles:student" for e in entries)
    assert all("0009876" not in str(e) for e in entries)

    def unavailable(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(ctx.audit, "record", unavailable)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        c.get(path, headers=finance)


def test_legacy_banking_migration_is_encrypted_restartable_and_preserves_tombstones():
    inner = MemoryStore()
    protected = EncryptingStore(inner, Crypter(None, "s" * 32))
    old = {"uuid": "student", **BANK, "status": "on_file", "_rev": 7,
           "pending": {**BANK, "void_cheque": {"data_url": "data:application/pdf;base64,JVBERi0="}}}
    protected.put("sap_banking", "student", old)
    protected.put("sap_banking", "removed", {"uuid": "removed", "status": "destroyed"})
    app = create_app(Settings(secret="s" * 32, enabled_plugins=()), store=inner, mailer=OutboxMailer())
    assert app.state.ctx.store.get("financial_profiles", "student") == old
    assert inner.get("financial_profiles", "student")["pending"].startswith(PREFIX)
    assert inner.list("sap_banking") == []
    create_app(Settings(secret="s" * 32, enabled_plugins=()), store=inner, mailer=OutboxMailer())
    assert protected.get("financial_profiles", "removed")["status"] == "destroyed"


def test_financial_profiles_use_remote_authentication_and_live_account_status():
    local = create_app(Settings(secret="s" * 32, enabled_plugins=()), mailer=OutboxMailer())
    headers = account(local.state.ctx, "remote-student")
    acct = local.state.ctx.identity.account("remote-student")
    from core import security

    remote = RemoteIdentity(
        security.public_pem(local.state.ctx.identity.key),
        lambda path, body: {"account": acct, "user": {"uuid": "remote-student"}}
        if path == "/internal/accounts/remote-student" else None,
    )
    app = create_app(Settings(secret="s" * 32, enabled_plugins=()), identity=remote, mailer=OutboxMailer())
    c = TestClient(app)
    assert c.put("/api/profile/financial", json=BANK, headers=headers).status_code == 200
    acct["status"] = "suspended"
    remote._cache.clear()
    assert c.get("/api/profile/financial", headers=headers).status_code == 403


def test_program_banking_policy_controls_routes_validation_and_listing():
    from fastapi import HTTPException

    app = create_app(Settings(secret="s" * 32, enabled_plugins=()), mailer=OutboxMailer())
    ctx = app.state.ctx

    def validate(uid):
        if uid == "blocked":
            raise HTTPException(409, "not eligible")

    app.include_router(banking_router(ctx, policy=BankingRoutePolicy(
        mine="/custom/banking", staff="/custom/admin/{uid}", finance_path="/custom/finance",
        finance_detail_routes=True, validate_submission=validate,
        listing_fields=lambda uid: {"approved": uid == "student", "awarded_amount": 42},
    )))
    client = TestClient(app)
    blocked = account(ctx, "blocked")
    student = account(ctx, "student")
    finance = account(ctx, "finance", roles=("profile_finance",))
    assert client.put("/api/custom/banking", json=BANK, headers=blocked).status_code == 409
    assert client.put("/api/custom/banking", json=BANK, headers=student).status_code == 200
    item = client.get("/api/custom/finance", headers=finance).json()["items"][0]
    assert (item["approved"], item["awarded_amount"]) == (True, 42)
    assert client.get("/api/custom/finance/student", headers=finance).json() == BANK
    assert client.get("/api/finance/sap/banking", headers=finance).status_code == 404


def test_legacy_shared_banking_router_remains_compatible():
    from core.programs.sap.banking import banking_router as sap_banking_router

    app = create_app(Settings(secret="s" * 32), mailer=OutboxMailer())
    ctx = app.state.ctx
    old = {(route.path, tuple(sorted(route.methods))) for route in banking_router(ctx, legacy=True).routes}
    new = {(route.path, tuple(sorted(route.methods))) for route in sap_banking_router(ctx, legacy=True).routes}
    assert old == new


def test_sap_cheque_authorizer_is_absent_when_program_is_disabled():
    inner = MemoryStore()
    disabled = create_app(Settings(secret="s" * 32, enabled_plugins=()), store=inner, mailer=OutboxMailer())
    ctx = disabled.state.ctx
    account(ctx, "finance", roles=("sap_finance",))
    ctx.store.put("financial_profiles", "student", {
        "uuid": "student", "status": "on_file",
        "void_cheque": {"data_url": "data:application/pdf;base64,JVBERi0="},
    })
    with pytest.raises(PermissionError):
        ctx.files.read("sap_void_cheque", "student", "finance", "student")

    enabled = create_app(Settings(secret="s" * 32, enabled_plugins=("sap",)), store=inner, mailer=OutboxMailer())
    files = enabled.state.ctx.files.for_owner("sap", ("profile",))
    assert files.read("sap_void_cheque", "student", "finance", "student") == b"%PDF-"
