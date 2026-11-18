"""Role administration, served by the auth service (the sole issuer of roles). Only `admin` may grant or revoke.

Roles live on the account record (`roles`, list) and reach the portal via the auth service's `/internal/accounts`
projection, which the portal caches for ~15 s: a revocation takes effect everywhere within that time. Session tokens
carry no roles, so a stolen old token never keeps privileges that have been revoked."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..context import Context, roles_of
from ..security import clean_text

name = "roles"


class RoleSet(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    roles: list[str] = Field(max_length=64)


class RolesModule:
    name = name

    def router(self, ctx: Context) -> APIRouter:
        r = APIRouter(prefix="/api/admin/roles")
        S = ctx.store
        admin = ctx.require_admin()

        @r.get("")
        def catalogue(_: dict = admin):
            return ctx.roles

        @r.get("/holders")
        def holders(_: dict = admin):
            out = []
            for a in S.list("accounts"):
                rs = roles_of(a, ctx.roles)
                if rs or a.get("google_staff_username"):
                    u = S.get("users", a["uuid"]) or {}
                    holder = {"uuid": a["uuid"], "preferred_name": u.get("preferred_name", ""), "roles": sorted(rs)}
                    if a.get("google_staff_username"):
                        holder.update(username=a["google_staff_username"], email=u.get("email", ""))
                    out.append(holder)
            return out

        @r.put("")
        def set_roles(b: RoleSet, actor: dict = admin):
            new = set(b.roles)
            if not new <= set(ctx.roles):
                raise HTTPException(422, "unknown role")
            uid = ctx.uid(clean_text(b.username))
            if uid == actor["uuid"]:
                raise HTTPException(403, "you cannot change your own roles")
            seen: dict = {}

            def go(a):
                if not a:
                    raise HTTPException(404, "no such account")
                if a["status"] != "verified":
                    raise HTTPException(409, "roles can only be granted to verified accounts")
                seen["before"] = sorted(roles_of(a, ctx.roles))
                if "admin" in roles_of(a, ctx.roles) and "admin" not in new:
                    others = [x for x in S.list("accounts") if x["uuid"] != uid and "admin" in roles_of(x, ctx.roles) and x["status"] == "verified"]
                    if not others:
                        raise HTTPException(409, "cannot remove the last admin")
                inactive = set(a.get("roles") or []) - set(ctx.roles)
                return {**a, "roles": sorted(new | inactive), "role": "admin" if "admin" in new else "applicant"}

            after = S.update("accounts", uid, go)  # optimistic lock: concurrent edits retry on fresh data
            ctx.audit.record(actor["uuid"], "role.set", f"user:{uid}", {"roles": seen["before"]}, {"roles": sorted(new)})
            return {"uuid": uid, "roles": sorted(new)}

        return r
