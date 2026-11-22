"""Clubs: registration, approval, ledger, receipts and tax (HST) reporting.

Money is always integer cents. Rates are basis points (1300 = 13%). Expenses only count in the ledger once approved.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from core.contracts import FileKind, PluginManifest, PluginServices, RegistrationContext
from core.pagination import PageOrder, paginate_store
from core.permissions import PERM_CLUBS_FINANCE, PERM_CLUBS_MANAGE
from core.security import clean_text
from core.services.email import EmailDeliveryUnknown, EmailNotConfigured
from core.services.receipt_storage import TORONTO, academic_year

name = "clubs"
ROLES = ("owner", "treasurer", "executive")
FINANCE_ROLES = ("owner", "treasurer")
RECEIPT_TYPES = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "pdf": "application/pdf"}
CLUB_STATUSES = {"pending", "approved", "rejected", "expired"}
TRANSACTION_STATUSES = {"pending", "approved", "rejected"}


def approval_year(c: dict) -> int | None:
    if c.get("approval_year") is not None:
        return c["approval_year"]
    approvals = [vote["at"] for vote in c.get("approvals", []) if vote.get("approve") and vote.get("at") is not None]
    return academic_year(max(approvals)) if approvals and c["status"] == "approved" else None


def effective_status(c: dict, now: int) -> str:
    if c["status"] == "approved" and approval_year(c) != academic_year(now):
        return "expired"
    return c["status"]


def _has_admin_role(account: dict) -> bool:
    return account.get("role") == "admin" or "admin" in set(account.get("roles") or [])


def split_tax(amount: int, bps: int, inclusive: bool) -> tuple[int, int, int]:
    """Return (net, tax, gross) in cents, rounding half up. `amount` is gross when inclusive, else net."""
    if inclusive:
        tax = (amount * bps * 2 + (10_000 + bps)) // (2 * (10_000 + bps))
        return amount - tax, tax, amount
    tax = (amount * bps * 2 + 10_000) // 20_000
    return amount, tax, amount + tax


def record_decision(doc: dict, admin_uuid: str, approve: bool, note: str, required: int, now: int) -> str:
    """Generic approval mechanism: N distinct approvers, any rejection is final. Returns the resulting state."""
    if any(a["by"] == admin_uuid for a in doc["approvals"]):
        raise HTTPException(409, "you have already decided on this")
    doc["approvals"].append({"by": admin_uuid, "approve": approve, "note": clean_text(note, 500), "at": now})
    if not approve:
        return "rejected"
    return "approved" if sum(a["approve"] for a in doc["approvals"]) >= required else "pending"


class ClubIn(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=10, max_length=2000)
    category: str = Field(min_length=2, max_length=50)

    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, v):
        return clean_text(v, 2000, multiline=True) if isinstance(v, str) else v


class MemberIn(BaseModel):
    username: str
    role: str


class Decision(BaseModel):
    approve: bool
    note: str = ""


class TxIn(BaseModel):
    kind: str  # expense | income
    amount_cents: int = Field(gt=0, le=100_000_000)
    tax_inclusive: bool = True
    tax_bps: int | None = Field(None, ge=0, le=3000)  # 0 = exempt / zero-rated
    category: str = Field(min_length=2, max_length=50)
    description: str = Field(min_length=3, max_length=500)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    receipt_ids: list[str] = Field(default_factory=list, max_length=10)


class GrantIn(BaseModel):
    amount_cents: int = Field(gt=0, le=100_000_000)
    note: str = Field("", max_length=500)


class ReceiptIn(BaseModel):
    data_url: str


class ClubsModule:
    name = name
    manifest = PluginManifest(
        "clubs",
        "0.1.0",
        1,
        "clubs.module:ClubsModule",
        interface="plugin",
        order=100,
        frontend=True,
        roles=(
            ("clubs", "club registration decisions"),
            ("finance", "club transactions and grants"),
        ),
        protection=(("receipts", ("data_url",)),),
        settings=(
            "default_tax_bps",
            "expense_approvals_large",
            "expense_approvals_small",
            "expense_large_cents",
            "max_receipt_bytes",
        ),
        services=(
            "audit",
            "auth",
            "club_invitation_link",
            "club_receipts",
            "email",
            "files",
            "identity",
            "now",
            "quotas",
            "settings",
            "store",
        ),
        collections=("clubs", "receipts", "transactions"),
        quota_domains=("club",),
        permissions=(
            (PERM_CLUBS_MANAGE, "Review club registration applications."),
            (PERM_CLUBS_FINANCE, "Manage club transactions and grants."),
        ),
        role_permissions=(
            ("clubs", (PERM_CLUBS_MANAGE,)),
            ("finance", (PERM_CLUBS_FINANCE,)),
        ),
        audit_collections=("receipts", "transactions"),
    )

    def configure(self, registration: RegistrationContext) -> None:
        def file_kind(services: PluginServices) -> FileKind:
            def authorized(_action: str, actor: str, club_id: str, receipt_id: str) -> bool:
                account = services.identity.account(actor)
                club = services.store.get("clubs", club_id)
                receipt = services.store.get("receipts", receipt_id)
                return bool(
                    account
                    and club
                    and receipt
                    and receipt["club_id"] == club_id
                    and (actor in club["members"] or _has_admin_role(account))
                )

            def read(reference: str, resource: str) -> bytes | None:
                return services.club_receipts.read(reference, resource)

            return FileKind(
                authorize=authorized,
                read=read,
                audit_action="file.read",
                audit_target=lambda resource, _reference: f"club:{resource}",
                audit_extra=lambda _resource, reference, _content: {"receipt": reference},
            )

        registration.register_file_kind("club_receipt", file_kind)

    def startup(self, services: PluginServices) -> None:
        for existing in services.store.list("clubs"):
            year = approval_year(existing)
            if year is not None and year not in existing.get("registered_years", []):
                def backfill(current):
                    return {**current, "registered_years": sorted(set(current.get("registered_years", [])) | {year})}
                services.store.update("clubs", existing["id"], backfill)

    def router(self, services: PluginServices) -> APIRouter:
        r = APIRouter(prefix="/api")
        S = services.store
        expense_approvals_small = services.settings["expense_approvals_small"]
        expense_approvals_large = services.settings["expense_approvals_large"]
        expense_large_cents = services.settings["expense_large_cents"]
        default_tax_bps = services.settings["default_tax_bps"]
        max_receipt_bytes = services.settings["max_receipt_bytes"]
        verified, admin = services.auth.require_verified(), services.auth.require_permission(PERM_CLUBS_MANAGE)
        finance = services.auth.require_permission(PERM_CLUBS_FINANCE)

        def club(cid: str) -> dict:
            c = S.get("clubs", cid)
            if not c:
                raise HTTPException(404, "unknown club")
            return c

        def display_club(c: dict) -> dict:
            return {**{k: v for k, v in c.items() if k != "invitations"},
                    "status": effective_status(c, services.now()), "approval_year": approval_year(c),
                    "storage_usage": services.quotas.usage("club", c["id"])}

        def member(c: dict, uid: str, roles=ROLES) -> None:
            if c["members"].get(uid) not in roles:
                raise HTTPException(403, "not permitted for this club")

        def conflict(c: dict, admin_uuid: str) -> None:
            if admin_uuid in c["members"]:
                raise HTTPException(403, "admins who belong to a club cannot approve its items")

        def balance(cid: str) -> int:
            return sum((t["gross_cents"] if t["kind"] != "expense" else -t["gross_cents"])
                       for t in S.list("transactions", club_id=cid) if t["status"] == "approved")

        # ---------------- registration ----------------
        @r.post("/clubs", status_code=201)
        def register(b: ClubIn, a: dict = verified):
            key = re.sub(r"\W+", " ", b.name).lower().strip()
            if any(re.sub(r"\W+", " ", c["name"]).lower().strip() == key and c["status"] != "rejected" for c in S.list("clubs")):
                raise HTTPException(409, "a club with that name already exists")
            cid = secrets.token_hex(8)
            S.put("clubs", cid, {"id": cid, "name": b.name, "description": b.description, "category": b.category, "status": "pending",
                                 "members": {a["uuid"]: "owner"}, "approvals": [], "created_at": services.now()})
            services.audit.record(a["uuid"], "club.registered", f"club:{cid}", None, S.get("clubs", cid))
            return {"id": cid, "status": "pending"}

        @r.get("/clubs/mine")
        def mine(a: dict = verified, cursor: str = Query("", max_length=2048),
                 limit: int = Query(100, ge=1, le=200)):
            clubs, next_cursor = paginate_store(
                S,
                "clubs",
                cursor,
                limit,
                (PageOrder("id", str),),
                predicate=lambda club: a["uuid"] in club["members"],
            )
            items = [display_club(club) for club in clubs]
            return {"items": items, "next_cursor": next_cursor}

        @r.get("/clubs/invitations")
        def invitations(a: dict = verified, cursor: str = Query("", max_length=2048),
                        limit: int = Query(100, ge=1, le=200)):
            clubs, next_cursor = paginate_store(
                S,
                "clubs",
                cursor,
                limit,
                (PageOrder("id", str),),
                predicate=lambda club: bool(club.get("invitations", {}).get(a["uuid"])),
            )
            items = [
                {"id": club["invitations"][a["uuid"]]["id"], "club_id": club["id"],
                 "club_name": club["name"], "role": club["invitations"][a["uuid"]]["role"]}
                for club in clubs
            ]
            return {"items": items, "next_cursor": next_cursor}

        @r.get("/clubs/directory")
        def directory(cursor: str = Query("", max_length=2048), limit: int = Query(100, ge=1, le=200)):
            now = services.now()
            clubs, next_cursor = paginate_store(
                S,
                "clubs",
                cursor,
                limit,
                (PageOrder("name", str, casefold=True), PageOrder("id", str)),
                predicate=lambda club: effective_status(club, now) == "approved",
            )
            items = [
                {"id": club["id"], "name": club["name"], "category": club["category"],
                 "description": club["description"]}
                for club in clubs
            ]
            return {"items": items, "next_cursor": next_cursor}

        @r.get("/clubs/{cid}")
        def get_club(cid: str, a: dict = services.auth.require_user()):
            c = club(cid)
            if a["uuid"] not in c["members"] and not _has_admin_role(a):
                raise HTTPException(403, "not permitted for this club")
            return {**display_club(c), "balance_cents": balance(cid)}

        @r.post("/clubs/{cid}/renewal")
        def request_renewal(cid: str, a: dict = verified):
            def renew(cur):
                if not cur:
                    raise HTTPException(404, "unknown club")
                member(cur, a["uuid"], ("owner",))
                year = approval_year(cur)
                if effective_status(cur, services.now()) != "expired" and not (cur["status"] == "rejected" and year is not None):
                    raise HTTPException(409, "club is not eligible for renewal")
                cur["approval_year"] = year
                if year is not None:
                    cur["registered_years"] = sorted(set(cur.get("registered_years", [])) | {year})
                cur["status"] = "pending"
                cur["approvals"] = []
                return cur

            c = S.update("clubs", cid, renew)
            services.audit.record(a["uuid"], "club.renewal_requested", f"club:{cid}", None,
                             {"status": "pending", "previous_approval_year": c["approval_year"]})
            return {"status": "pending"}

        @r.post("/clubs/{cid}/members")
        def add_member(cid: str, b: MemberIn, a: dict = verified):
            c = club(cid)
            member(c, a["uuid"], ("owner",))
            if b.role not in ROLES[1:]:
                raise HTTPException(422, f"role must be one of {ROLES[1:]}")
            contact = services.identity.resolve_contact(b.username)
            if not contact or contact.status != "verified":
                raise HTTPException(422, "member must be a verified account")
            uid = contact.uuid
            if not contact.email_verified or not contact.email:
                raise HTTPException(409, "verified member email unavailable")
            if not services.email.is_configured:
                raise HTTPException(503, "email delivery is not configured")
            invitation_id = secrets.token_hex(16)
            def invite(cur):
                if not cur:
                    raise HTTPException(404, "unknown club")
                member(cur, a["uuid"], ("owner",))
                if cur["members"].get(uid) == b.role or cur["members"].get(uid) == "owner":
                    raise HTTPException(409, "account already has this club role")
                pending = cur.setdefault("invitations", {})
                if uid in pending:
                    raise HTTPException(409, "an invitation is already pending for this account")
                pending[uid] = {"id": invitation_id, "role": b.role, "by": a["uuid"], "at": services.now()}
                return cur

            c = S.update("clubs", cid, invite)
            services.audit.record(a["uuid"], "club.member_invited", f"club:{cid}", None,
                             {"role": b.role}, {"member": uid, "invitation": invitation_id})
            try:
                services.email.send_template(contact.email, "club_invitation",
                                        {"club_name": c["name"], "role": b.role,
                                         "link": services.club_invitation_link.portal_link()},
                                        actor=a["uuid"])
            except EmailNotConfigured as exc:
                raise HTTPException(503, "Invitation saved, but email is not configured. The recipient can respond in the portal.") from exc
            except EmailDeliveryUnknown as exc:
                raise HTTPException(502, "Invitation saved, but email delivery could not be confirmed. The recipient can respond in the portal.") from exc
            return {"status": "pending"}

        @r.post("/clubs/{cid}/invitations/{invitation_id}/decision")
        def decide_invitation(cid: str, invitation_id: str, b: Decision, a: dict = verified):
            old_role = {}
            def decide(cur):
                if not cur:
                    raise HTTPException(404, "unknown club")
                pending = cur.get("invitations", {})
                invitation = pending.get(a["uuid"])
                if not invitation or invitation["id"] != invitation_id:
                    raise HTTPException(404, "invitation not found")
                old_role["value"] = cur["members"].get(a["uuid"])
                if b.approve:
                    cur["members"][a["uuid"]] = invitation["role"]
                del pending[a["uuid"]]
                return cur

            c = S.update("clubs", cid, decide)
            role = c["members"].get(a["uuid"])
            services.audit.record(a["uuid"], "club.invitation_accepted" if b.approve else "club.invitation_declined",
                             f"club:{cid}", {"role": old_role["value"]}, {"role": role},
                             {"invitation": invitation_id})
            return {"status": "accepted" if b.approve else "declined"}

        @r.get("/admin/clubs")
        def admin_clubs(status: str | None = None, _: dict = admin, cursor: str = Query("", max_length=2048),
                        limit: int = Query(100, ge=1, le=200)):
            if status is not None and status not in CLUB_STATUSES:
                raise HTTPException(422, f"status must be one of {', '.join(sorted(CLUB_STATUSES))}")
            clubs, next_cursor = paginate_store(
                S,
                "clubs",
                cursor,
                limit,
                (PageOrder("id", str),),
                predicate=lambda club: status is None or display_club(club)["status"] == status,
            )
            items = [display_club(club) for club in clubs]
            return {"items": items, "next_cursor": next_cursor}

        @r.post("/admin/clubs/{cid}/decision")
        def decide_club(cid: str, b: Decision, a: dict = admin):
            c = club(cid)
            if c["status"] != "pending":
                raise HTTPException(409, f"club is already {c['status']}")
            conflict(c, a["uuid"])
            def vote(cur):  # re-evaluated on a fresh copy if another admin voted meanwhile, so no vote is lost
                if cur["status"] != "pending":
                    raise HTTPException(409, f"club is already {cur['status']}")
                conflict(cur, a["uuid"])
                previous = next((vote for vote in cur["approvals"] if vote["by"] == a["uuid"]), None)
                if previous and previous["approve"] and b.approve:
                    cur["status"] = "approved"
                else:
                    cur["status"] = record_decision(cur, a["uuid"], b.approve, b.note, 1, services.now())
                if cur["status"] == "approved":
                    years = set(cur.get("registered_years", []))
                    previous_year = approval_year(cur)
                    if previous_year is not None:
                        years.add(previous_year)
                    cur["approval_year"] = academic_year(services.now())
                    cur["registered_years"] = sorted(years | {cur["approval_year"]})
                return cur

            c = S.update("clubs", cid, vote)
            services.audit.record(a["uuid"], "club.decision", f"club:{cid}", {"status": "pending"},
                             {"status": c["status"], "approval_year": c.get("approval_year")},
                             {"approve": b.approve, "note": b.note[:200]})
            return {"status": c["status"], "approvals": len(c["approvals"]), "approval_year": c.get("approval_year")}

        # ---------------- receipts ----------------
        @r.post("/clubs/{cid}/receipts", status_code=201)
        def upload_receipt(cid: str, b: ReceiptIn, a: dict = verified):
            c = club(cid)
            member(c, a["uuid"], FINANCE_ROLES)
            m = re.fullmatch(r"data:(?:image/(jpeg|png|webp)|application/(pdf));base64,([A-Za-z0-9+/=]+)", b.data_url)
            if not m:
                raise HTTPException(422, "receipt must be a base64 jpeg/png/webp/pdf data URL")
            try:
                raw = base64.b64decode(m.group(3), validate=True)
            except ValueError as exc:
                raise HTTPException(422, "receipt could not be decoded") from exc
            if len(raw) > max_receipt_bytes:
                raise HTTPException(413, "receipt too large")
            digest = hashlib.sha256(raw).hexdigest()
            for old in S.list("receipts", club_id=cid, sha256=digest):
                raise HTTPException(409, {"error": "duplicate receipt", "id": old["id"]})
            rid = secrets.token_hex(8)
            services.club_receipts.create(
                {"id": rid, "club_id": cid, "sha256": digest, "data_url": b.data_url, "by": a["uuid"],
                 "at": services.now(), "transaction_id": None})
            services.audit.record(a["uuid"], "receipt.uploaded", f"club:{cid}", None, None, {"receipt": rid, "sha256": digest})
            return {"id": rid, "sha256": digest}

        @r.get("/clubs/{cid}/receipts/{rid}")
        def get_receipt(cid: str, rid: str, a: dict = services.auth.require_user()):
            c, rc = club(cid), S.get("receipts", rid)
            if a["uuid"] not in c["members"] and not _has_admin_role(a):
                raise HTTPException(403, "not permitted for this club")
            if not rc or rc["club_id"] != cid:
                raise HTTPException(404, "unknown receipt")
            try:
                data_url = services.files.read("club_receipt", rid, a["uuid"], cid).decode()
            except FileNotFoundError as exc:
                raise HTTPException(404, "unknown receipt") from exc
            except PermissionError as exc:
                raise HTTPException(403, "not permitted for this club") from exc
            return {**{k: v for k, v in rc.items() if k != "storage"}, "data_url": data_url}

        # ---------------- ledger ----------------
        @r.post("/clubs/{cid}/transactions", status_code=201)
        def add_tx(cid: str, b: TxIn, a: dict = verified):
            c = club(cid)
            member(c, a["uuid"], FINANCE_ROLES)
            if effective_status(c, services.now()) != "approved":
                raise HTTPException(409, "club is not approved")
            if b.kind not in ("expense", "income"):
                raise HTTPException(422, "kind must be expense or income")
            bps = default_tax_bps if b.tax_bps is None else b.tax_bps
            net, tax, gross = split_tax(b.amount_cents, bps, b.tax_inclusive)
            rcpts = []
            for rid in dict.fromkeys(b.receipt_ids):
                rc = S.get("receipts", rid)
                if not rc or rc["club_id"] != cid or rc["transaction_id"]:
                    raise HTTPException(422, f"receipt {rid} is unknown or already used")
                rcpts.append(rc)
            if not rcpts:
                raise HTTPException(422, "at least one receipt is required")
            tid = secrets.token_hex(8)
            doc = {"id": tid, "club_id": cid, "kind": b.kind, "net_cents": net, "tax_cents": tax, "gross_cents": gross,
                   "tax_bps": bps, "category": clean_text(b.category, 50), "description": clean_text(b.description, 500),
                   "date": b.date, "receipt_ids": [x["id"] for x in rcpts], "created_by": a["uuid"], "created_at": services.now(),
                   "approvals": [], "status": "pending" if b.kind == "expense" else "approved",
                   "required_approvals": (expense_approvals_large if gross >= expense_large_cents else expense_approvals_small)
                   if b.kind == "expense" else 0}
            claimed = []

            def attach(rc):  # a receipt can back one transaction only: the optimistic claim decides races
                if not rc or rc["transaction_id"]:
                    raise HTTPException(422, f"receipt {rc and rc['id']} is unknown or already used")
                return {**rc, "transaction_id": tid}

            try:
                for rc in rcpts:
                    S.update("receipts", rc["id"], attach)
                    claimed.append(rc["id"])
            except HTTPException:
                for rid in claimed:
                    S.update("receipts", rid, lambda r: {**r, "transaction_id": None})
                raise
            S.put("transactions", tid, doc)
            services.audit.record(a["uuid"], "transaction.created", f"club:{cid}", None, doc, {"transaction": tid})
            return {k: doc[k] for k in ("id", "status", "net_cents", "tax_cents", "gross_cents", "required_approvals")}

        @r.get("/clubs/{cid}/transactions")
        def list_tx(cid: str, a: dict = services.auth.require_user(), cursor: str = Query("", max_length=2048),
                    limit: int = Query(100, ge=1, le=200)):
            c = club(cid)
            if a["uuid"] not in c["members"] and not _has_admin_role(a):
                raise HTTPException(403, "not permitted for this club")
            items, next_cursor = paginate_store(
                S,
                "transactions",
                cursor,
                limit,
                (PageOrder("date", str), PageOrder("created_at", float), PageOrder("id", str)),
                equals={"club_id": cid},
            )
            return {"items": items, "next_cursor": next_cursor}

        @r.get("/admin/transactions")
        def pending_tx(status: str = "pending", _: dict = finance, cursor: str = Query("", max_length=2048),
                       limit: int = Query(100, ge=1, le=200)):
            if status not in TRANSACTION_STATUSES:
                raise HTTPException(422, f"status must be one of {', '.join(sorted(TRANSACTION_STATUSES))}")
            items, next_cursor = paginate_store(
                S,
                "transactions",
                cursor,
                limit,
                (PageOrder("id", str),),
                equals={"status": status},
            )
            return {"items": items, "next_cursor": next_cursor}

        @r.post("/admin/transactions/{tid}/decision")
        def decide_tx(tid: str, b: Decision, a: dict = finance):
            def vote(t):
                if not t or t["status"] != "pending":
                    raise HTTPException(404, "no pending transaction")
                conflict(club(t["club_id"]), a["uuid"])
                state = record_decision(t, a["uuid"], b.approve, b.note, t["required_approvals"], services.now())
                if state == "approved" and balance(t["club_id"]) < t["gross_cents"]:
                    raise HTTPException(409, "insufficient club balance; add a grant or income first")
                t["status"] = state
                return t

            t = S.update("transactions", tid, vote)
            state = t["status"]
            services.audit.record(a["uuid"], "transaction.decision", f"club:{t['club_id']}", {"status": "pending"}, {"status": state},
                             {"transaction": tid, "approve": b.approve, "note": b.note[:200]})
            return {"status": state, "approvals": sum(x["approve"] for x in t["approvals"]), "required": t["required_approvals"]}

        @r.post("/admin/clubs/{cid}/grants", status_code=201)
        def grant(cid: str, b: GrantIn, a: dict = finance):
            c = club(cid)
            if effective_status(c, services.now()) != "approved":
                raise HTTPException(409, "club is not approved")
            conflict(c, a["uuid"])
            tid = secrets.token_hex(8)
            S.put("transactions", tid, {"id": tid, "club_id": cid, "kind": "grant", "net_cents": b.amount_cents, "tax_cents": 0,
                                        "gross_cents": b.amount_cents, "tax_bps": 0, "category": "grant", "description": clean_text(b.note, 500) or "Grant",
                                        "date": "", "receipt_ids": [], "created_by": a["uuid"], "created_at": services.now(),
                                        "approvals": [], "status": "approved", "required_approvals": 0})
            services.audit.record(a["uuid"], "grant.issued", f"club:{cid}", None, {"amount_cents": b.amount_cents}, {"transaction": tid})
            return {"id": tid}

        @r.get("/clubs/{cid}/summary")
        def summary(cid: str, a: dict = services.auth.require_user()):
            """Tax summary: tax paid on approved expenses (input tax credit eligible) and tax collected on income."""
            c = club(cid)
            if a["uuid"] not in c["members"] and not _has_admin_role(a):
                raise HTTPException(403, "not permitted for this club")
            totals = defaultdict(int)
            by_cat: dict[str, int] = defaultdict(int)
            pending_count = 0
            for t in S.list("transactions", club_id=cid):
                if t["status"] == "pending":
                    pending_count += 1
                if t["status"] != "approved":
                    continue
                k = "expense" if t["kind"] == "expense" else "income"
                totals[f"{k}_gross"] += t["gross_cents"]
                totals[f"{k}_net"] += t["net_cents"]
                totals[f"{k}_tax"] += t["tax_cents"]
                if k == "expense":
                    by_cat[t["category"]] += t["gross_cents"]
            return {"income_cents": totals["income_gross"], "expense_cents": totals["expense_gross"],
                    "balance_cents": totals["income_gross"] - totals["expense_gross"],
                    "tax_paid_cents": totals["expense_tax"], "tax_collected_cents": totals["income_tax"],
                    "net_tax_position_cents": totals["income_tax"] - totals["expense_tax"],
                    "expenses_by_category": dict(by_cat),
                    "pending_expenses": pending_count}

        return r
