"""Admin review of account activation / recovery requests (face + id scans)."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..context import Context, roles_of
from ..contracts import FileKind
from ..services.account_check import check_one
from ..services.roster import import_roster

name = "admin"


class Decision(BaseModel):
    approve: bool
    note: str = ""


class RosterIn(BaseModel):
    csv: str
    replace: bool = True


class AdminModule:
    name = name

    def configure(self, ctx: Context) -> None:
        def authorized(action: str, actor: str, resource: str, reference: str) -> bool:
            account = ctx.identity.account(actor)
            return bool(account and roles_of(account, ctx.roles) & {"verifier", "admin"})

        def read(reference: str, _resource: str) -> bytes | None:
            media = ctx.store.get("media", reference)
            return json.dumps(media).encode() if media else None

        ctx.files.register_kind(
            "admin",
            "identity_scan",
            FileKind(
                authorize=authorized,
                read=read,
                delete=lambda reference, _resource: ctx.store.delete("media", reference),
                audit_action="file.read",
                audit_target=lambda resource, _reference: f"user:{resource}",
                audit_extra=lambda _resource, reference, _content: {"verification": reference},
            ),
        )

    def router(self, ctx: Context) -> APIRouter:
        r = APIRouter(prefix="/api/admin")
        S = ctx.store
        admin = ctx.require_role("verifier")

        @r.post("/roster")
        def upload_roster(b: RosterIn, a: dict = ctx.require_admin()):
            try:
                out = import_roster(S, b.csv, b.replace, ctx.now())
            except ValueError as e:
                raise HTTPException(422, str(e))
            ctx.audit.record(a["uuid"], "roster.uploaded", "roster", None, out)
            return out

        @r.get("/verifications")
        def pending(_: dict = admin):
            return [v for v in S.list("verifications", status="pending")]

        @r.get("/verifications/{vid}/media")
        def media(vid: str, actor: dict = admin):
            try:
                body = ctx.files.read("identity_scan", vid, actor["uuid"], vid)
            except FileNotFoundError as exc:
                raise HTTPException(404, "media gone (decision already made)") from exc
            except PermissionError as exc:
                raise HTTPException(403, "not permitted to read verification media") from exc
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("stored verification media is invalid") from exc

        @r.post("/verifications/{vid}/decision")
        def decide(vid: str, b: Decision, a: dict = admin):
            def claim(v):  # optimistic lock: of two admins deciding at once exactly one wins
                if not v or v["status"] != "pending":
                    raise HTTPException(404, "no pending request")
                if v["uuid"] == a["uuid"]:
                    raise HTTPException(403, "cannot review your own request")
                return {**v, "status": "processing"}

            v = S.update("verifications", vid, claim)
            uid = v["uuid"]
            try:
                acct, user = S.get("accounts", uid), S.get("users", uid)
                snap = lambda ac, us: {"status": ac["status"], **{k: us.get(k) for k in ("email", "first_name", "last_name")}}
                before = snap(acct, user)
                ch, user_ch, acct_ch = v.get("changes") or {}, {}, {}
                if b.approve:
                    if "email" in ch:
                        S.create("emails", ctx.email_key(ch["email"]), {"uuid": uid}) or _conflict()
                        S.delete("emails", acct["email_key"])
                        acct_ch["email_key"], user_ch["email"] = ctx.email_key(ch["email"]), ch["email"]
                    if "first_name" in ch:
                        user_ch["first_name"], user_ch["last_name"] = ch["first_name"], ch["last_name"]
                    acct_ch["status"] = "verified"
                elif v["kind"] == "activation":
                    acct_ch["status"] = "rejected"
                if user_ch:
                    user = S.update("users", uid, lambda u: {**u, **user_ch})
                acct = S.update("accounts", uid, lambda ac: {**ac, **acct_ch})
                if b.approve:
                    check_one(S, uid, ctx.now())
            except HTTPException:
                S.update("verifications", vid, lambda x: {**x, "status": "pending"})
                raise
            final = "approved" if b.approve else "rejected"
            S.update("verifications", vid, lambda x: {**x, "status": final, "note": b.note[:500], "decided_by": a["uuid"], "decided_at": ctx.now()})
            ctx.audit.record(a["uuid"], "verification.decided", f"user:{uid}", before, snap(acct, user),
                             {"verification": vid, "kind": v["kind"], "approved": b.approve, "note": b.note[:200]})
            ctx.files.delete("identity_scan", vid, a["uuid"], uid)
            return {"status": final}

        def _conflict():
            raise HTTPException(409, "email now in use by another account")

        return r
