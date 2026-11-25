"""Admin review of account activation / recovery requests (face + id scans)."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import audit, security
from ..context import Context, roles_of
from ..contracts import FileKind
from ..permissions import PERM_IDENTITY_VERIFICATION_REVIEW
from ..services.account_check import COL_HONORARY, check_one, is_active_member, is_live_member, read_check
from ..services.roster import COL_NAME, Student, division_of, import_roster, membership_session, roster_session
from ..services.text import normalize_case

name = "admin"


class Decision(BaseModel):
    approve: bool
    note: str = ""


class RosterIn(BaseModel):
    csv: str
    replace: bool = True


class HonoraryIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)
    division: str = Field("", max_length=64)


def _member_view(ctx: Context, uid: str) -> dict:
    S, now = ctx.store, ctx.now()
    user, account = S.get("users", uid) or {}, S.get("accounts", uid) or {}
    check = read_check(S, uid) or {}
    grant = S.get(COL_HONORARY, uid)
    session = membership_session(now)
    return {
        "uuid": uid,
        "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
        "preferred_name": user.get("preferred_name", ""),
        "email": user.get("email", ""),
        "student_number": f"xxxxx{user['student_number']}x" if user.get("student_number") else None,
        "status": account.get("status"),
        "roles": sorted(roles_of(account, ctx.roles)),
        "roster": {"rostered": bool(check.get("rostered")), "reason": check.get("reason", ""),
                   "division": check.get("division"), "membership_session": check.get("membership_session")},
        "membership": {"session": session, "live_member": is_live_member(check, now),
                       "rostered_member": is_active_member(check, now)},
        "honorary": {**grant, "current": grant["session"] == session} if grant else None,
    }


class AdminModule:
    name = name

    def configure(self, ctx: Context) -> None:
        def authorized(action: str, actor: str, resource: str, reference: str) -> bool:
            account = ctx.identity.account(actor)
            return bool(account and ctx.has_permission(account, PERM_IDENTITY_VERIFICATION_REVIEW))

        def read(reference: str, _resource: str) -> bytes | None:
            media = ctx.store.get("media", reference)
            return json.dumps({k: v for k, v in media.items() if k not in {"id", "uuid"}}).encode() if media else None

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
        admin = ctx.require_permission(PERM_IDENTITY_VERIFICATION_REVIEW)

        @r.get("/roster")
        def list_roster(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                        actor: dict = ctx.require_admin()):
            session, loaded = membership_session(ctx.now()), roster_session(S)
            current = loaded == session  # only a roster of the current membership session lists active members
            students = sorted(
                (Student(*row) for bucket in S.list(COL_NAME) for row in bucket["students"]) if current else (),
                key=lambda student: (student.first_name, student.last_name, student.uuid),
            )
            page = students[offset:offset + limit]
            roster_identities: dict[tuple[str, int | None], int] = {}
            for student in students:
                identity = (student.full_name, student.trunc_id)
                roster_identities[identity] = roster_identities.get(identity, 0) + 1
            page_names: dict[str, list[Student]] = {}
            for student in page:
                page_names.setdefault(student.full_name, []).append(student)
            matches: dict[str, list[dict]] = {}
            for user in S.list("users"):
                name = normalize_case(f"{user.get('first_name', '')} {user.get('last_name', '')}")
                if name in page_names:
                    uid = user["uuid"]
                    digits = (S.get("roster_identifiers", uid) or {}).get("digits")
                    if digits and S.get("accounts", uid):
                        for student in page_names[name]:
                            if student.trunc_id is not None and digits == f"{student.trunc_id:04d}":
                                matches.setdefault(student.uuid, []).append(user)

            users = {}
            for student in page:
                candidates = matches.get(student.uuid, [])
                if len(candidates) == 1 and roster_identities[(student.full_name, student.trunc_id)] == 1:
                    users[student.uuid] = candidates[0]
            wanted = {user["uuid"] for user in users.values()}
            last_logins: dict[str, dict] = {}
            if wanted:
                for month in ctx.audit.months(("hot", "archived", "dropped")):
                    rows = []
                    if month["state"] in ("archived", "dropped"):
                        rows = ctx.audit.read_archive(month["month"])
                        if not rows:
                            raise RuntimeError(f"Missing audit archive for {month['month']}")
                    if month["state"] != "dropped":
                        rows += S.list(audit.coll(month["month"]))
                    for entry in rows:
                        uid = entry["actor"]
                        if (uid in wanted and entry["action"] in ("auth.login", "auth.backup_login")
                                and (uid not in last_logins or entry["seq"] > last_logins[uid]["seq"])):
                            last_logins[uid] = entry

            items = []
            for student in page:
                user = users.get(student.uuid)
                login = last_logins.get(user["uuid"]) if user else None
                items.append({
                    "name": f"{student.first_name} {student.last_name[:1]}.".strip() if student.last_name else student.first_name,
                    "student_number": f"xxxx{student.trunc_id % 10000:04d}" if student.trunc_id is not None else None,
                    "email": user.get("email") if user else None,
                    "last_login": login["ts"] if login else None,
                })
            ctx.audit.record(actor["uuid"], "roster.view", "roster", meta={"offset": offset, "returned": len(items)})
            return {"items": items, "total": len(students), "offset": offset, "limit": limit,
                    "session": session, "roster_session": loaded}

        @r.post("/roster")
        def upload_roster(b: RosterIn, a: dict = ctx.require_admin()):
            try:
                out = import_roster(S, b.csv, b.replace, ctx.now())
            except ValueError as e:
                raise HTTPException(422, str(e))
            ctx.audit.record(a["uuid"], "roster.uploaded", "roster", None, out)
            return out

        def member_uid(email: str, student_number: str) -> str:
            if bool(email.strip()) == bool(student_number.strip()):
                raise HTTPException(422, "give either an email or a student number")
            if email.strip():
                address = email.strip().lower()
                uid = (S.get("emails", ctx.email_key(address)) or {}).get("uuid")
                user = S.get("users", uid) if uid else None
                if not user or user.get("email", "").lower() != address:
                    raise HTTPException(404, "no account with that email")
                return uid
            digits = re.sub(r"\D", "", student_number)
            if not 7 <= len(digits) <= 10:
                raise HTTPException(422, "enter the full student number (7 to 10 digits)")
            uid = (S.get("student_ids", security.student_id_uuid(ctx.settings.secret, digits)) or {}).get("uuid")
            if not uid or not S.get("users", uid):
                raise HTTPException(404, "no account with that student number")
            return uid

        @r.get("/members/lookup")
        def lookup_member(email: str = Query("", max_length=320), student_number: str = Query("", max_length=32),
                          actor: dict = ctx.require_admin()):
            uid = member_uid(email, student_number)
            ctx.audit.record(actor["uuid"], "member.lookup", f"user:{uid}", None, None,
                             {"by": "email" if email.strip() else "student_number"})
            return _member_view(ctx, uid)

        @r.put("/members/{uid}/honorary")
        def grant_honorary(uid: str, b: HonoraryIn, actor: dict = ctx.require_admin()):
            account = S.get("accounts", uid)
            if not account or not S.get("users", uid):
                raise HTTPException(404, "no such account")
            if account["status"] != "verified":
                raise HTTPException(409, "honorary membership can only be granted to verified accounts")
            before = S.get(COL_HONORARY, uid)
            grant = {"uuid": uid, "session": membership_session(ctx.now()), "reason": b.reason.strip(),
                     "division": division_of(b.division) or None,
                     "granted_by": actor["uuid"], "granted_at": ctx.now()}
            S.put(COL_HONORARY, uid, grant)
            ctx.audit.record(actor["uuid"], "membership.honorary_granted", f"user:{uid}", before, grant)
            return _member_view(ctx, uid)

        @r.delete("/members/{uid}/honorary")
        def revoke_honorary(uid: str, actor: dict = ctx.require_admin()):
            before = S.get(COL_HONORARY, uid)
            if not before:
                raise HTTPException(404, "no honorary membership")
            S.delete(COL_HONORARY, uid)
            ctx.audit.record(actor["uuid"], "membership.honorary_revoked", f"user:{uid}", before, None)
            return _member_view(ctx, uid)

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
                        if ch["email"] == acct.get("recovery_email"):
                            raise HTTPException(409, "The primary and backup addresses must be different")
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
