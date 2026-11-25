"""Opt-out fees: admin-managed fees and windows, member opt-out and refund requests, and levy group reporting."""
from __future__ import annotations

import secrets
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..context import Context
from ..pagination import PageOrder, paginate_store
from ..permissions import PERM_FEES_LEVY, PERM_FEES_MANAGE, PERM_FEES_REFUNDS
from ..security import clean_text
from ..services.banking import BANKING_ACTIVE, BANKING_FIELDS
from ..services.fees import (
    DEFAULT_WINDOWS, DENIED, OPTED_OUT, REFUNDED, REFUND_REQUESTED, SESSION_KEY, WINDOW_IDS, WITHDRAWN,
    levy_summary, session_label, session_order, valid_window_config, window_months, window_state,
)

WindowId = Literal["fall", "summer"]
MAX_AMOUNT_CENTS = 1_000_000
Cents = Annotated[int, Field(strict=True, ge=1, le=MAX_AMOUNT_CENTS)]
Month = Annotated[int, Field(strict=True, ge=1, le=12)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeeIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    amounts_cents: dict[WindowId, Cents] = Field(min_length=1)
    levy_group_id: str | None = Field(None, max_length=64)
    refundable: bool = True
    active: bool = True


class GroupIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    members: list[str] = Field(default_factory=list, max_length=200)


class WindowIn(Strict):
    start_month: Month
    length_months: Month


class EnrolmentIn(Strict):
    expected_enrolment: int | None = Field(None, strict=True, ge=0, le=1_000_000)


class OptOutItem(Strict):
    fee_id: str = Field(min_length=1, max_length=64)
    already_paid: bool


class OptOutIn(Strict):
    window: WindowId
    items: list[OptOutItem] = Field(min_length=1, max_length=50)


class DecisionIn(Strict):
    decision: Literal["refunded", "denied"]
    note: str = Field("", max_length=500)


def public(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in doc.items() if not key.startswith("_")}


class FeesModule:
    name = "fees"

    def router(self, ctx: Context) -> APIRouter:
        router = APIRouter(prefix="/api")
        S = ctx.store
        manage = ctx.require_permission(PERM_FEES_MANAGE)
        refunds = ctx.require_permission(PERM_FEES_REFUNDS)
        levy = ctx.require_permission(PERM_FEES_LEVY)
        member = ctx.require_rostered()

        # ---- shared lookups ------------------------------------------------------------------------------------
        def window_config(window_id: str) -> dict[str, int]:
            stored = S.get("fee_windows", window_id) or {}
            return {key: stored.get(key, DEFAULT_WINDOWS[window_id][key]) for key in ("start_month", "length_months")}

        def state(window_id: str) -> dict[str, Any]:
            return window_state(window_id, window_config(window_id), ctx.now())

        def display_name(uid: str) -> str:
            user = ctx.identity.user(uid) or {}
            name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            return name or user.get("preferred_name") or ""

        def banking_status(uid: str) -> str:
            bank = S.get("financial_profiles", uid)
            return bank["status"] if bank else "not_submitted"

        def get_fee(fee_id: str) -> dict[str, Any]:
            fee = S.get("fees", fee_id)
            if not fee:
                raise HTTPException(404, "no such fee")
            return fee

        def get_group(group_id: str) -> dict[str, Any]:
            group = S.get("fee_groups", group_id)
            if not group:
                raise HTTPException(404, "no such levy group")
            return group

        def group_view(group: dict[str, Any]) -> dict[str, Any]:
            return {**public(group), "members": [
                {"uuid": uid, "name": display_name(uid)} for uid in group.get("members", [])
            ]}

        def new_id() -> str:
            return secrets.token_hex(8)

        # ---- admin: fees, windows, levy groups, session headcounts --------------------------------------------
        @router.get("/admin/fees")
        def overview(_: dict = manage):
            windows = [{**state(window_id), "label": label} for window_id, label in (
                ("fall", "Fall (Fall-Winter session)"), ("summer", "Summer (Summer session)"))]
            fees = sorted((public(fee) for fee in S.list("fees")), key=lambda fee: fee["name"].casefold())
            groups = sorted((group_view(group) for group in S.list("fee_groups")),
                            key=lambda group: group["name"].casefold())
            sessions = []
            for window in windows:
                stored = S.get("fee_sessions", window["session"]) or {}
                sessions.append({"session": window["session"], "label": window["session_label"],
                                 "expected_enrolment": stored.get("expected_enrolment"),
                                 "roster_count": (S.get("roster_meta", "current") or {}).get("count")})
            return {"fees": fees, "groups": groups, "windows": windows, "sessions": sessions}

        def checked_fee(body: FeeIn) -> dict[str, Any]:
            if body.levy_group_id is not None:
                get_group(body.levy_group_id)
            return {**body.model_dump(), "name": clean_text(body.name), "description": clean_text(body.description)}

        @router.post("/admin/fees", status_code=201)
        def create_fee(body: FeeIn, actor: dict = manage):
            fee = {"id": new_id(), **checked_fee(body), "created_at": ctx.now(), "updated_at": ctx.now()}
            S.put("fees", fee["id"], fee)
            ctx.audit.record(actor["uuid"], "fees.fee_created", f"fee:{fee['id']}", None, public(fee))
            return public(fee)

        @router.put("/admin/fees/windows/{window_id}")
        def set_window(window_id: WindowId, body: WindowIn, actor: dict = manage):
            if not valid_window_config(body.start_month, body.length_months):
                raise HTTPException(422, "the window must fit within one calendar year")
            other = "summer" if window_id == "fall" else "fall"
            if set(window_months(body.model_dump())) & set(window_months(window_config(other))):
                raise HTTPException(422, f"the window overlaps the {other} window")
            before = window_config(window_id)
            S.put("fee_windows", window_id, {"id": window_id, **body.model_dump()})
            ctx.audit.record(actor["uuid"], "fees.window_changed", f"fee_window:{window_id}", before, body.model_dump())
            return state(window_id)

        @router.put("/admin/fees/sessions/{session}")
        def set_enrolment(session: str, body: EnrolmentIn, actor: dict = manage):
            if not SESSION_KEY.fullmatch(session):
                raise HTTPException(404, "no such session")
            before = S.get("fee_sessions", session) or {}
            S.put("fee_sessions", session, {"id": session, "expected_enrolment": body.expected_enrolment})
            ctx.audit.record(actor["uuid"], "fees.enrolment_changed", f"fee_session:{session}",
                             {"expected_enrolment": before.get("expected_enrolment")},
                             {"expected_enrolment": body.expected_enrolment})
            return {"session": session, "expected_enrolment": body.expected_enrolment}

        def resolve_members(entries: list[str]) -> list[str]:
            members: list[str] = []
            for entry in entries:
                text = clean_text(entry)
                if not text:
                    raise HTTPException(422, "blank member")
                uid = text if ctx.identity.account(text) else ctx.uid(text)
                if not ctx.identity.account(uid):
                    raise HTTPException(422, f"no account found for {text}")
                if uid not in members:
                    members.append(uid)
            return members

        @router.post("/admin/fees/groups", status_code=201)
        def create_group(body: GroupIn, actor: dict = manage):
            group = {"id": new_id(), "name": clean_text(body.name), "description": clean_text(body.description),
                     "members": resolve_members(body.members), "created_at": ctx.now()}
            S.put("fee_groups", group["id"], group)
            ctx.audit.record(actor["uuid"], "fees.group_created", f"fee_group:{group['id']}", None, public(group))
            return group_view(group)

        @router.put("/admin/fees/groups/{group_id}")
        def update_group(group_id: str, body: GroupIn, actor: dict = manage):
            before = get_group(group_id)
            group = {**before, "name": clean_text(body.name), "description": clean_text(body.description),
                     "members": resolve_members(body.members), "updated_at": ctx.now()}
            S.put("fee_groups", group_id, group)
            ctx.audit.record(actor["uuid"], "fees.group_changed", f"fee_group:{group_id}", public(before), public(group))
            return group_view(group)

        @router.delete("/admin/fees/groups/{group_id}")
        def delete_group(group_id: str, actor: dict = manage):
            group = get_group(group_id)
            if S.list("fees", levy_group_id=group_id):
                raise HTTPException(409, "reassign this group's fees before deleting it")
            S.delete("fee_groups", group_id)
            ctx.audit.record(actor["uuid"], "fees.group_deleted", f"fee_group:{group_id}", public(group), None)
            return {"deleted": group_id}

        @router.put("/admin/fees/{fee_id}")
        def update_fee(fee_id: str, body: FeeIn, actor: dict = manage):
            before = get_fee(fee_id)
            fee = {**before, **checked_fee(body), "updated_at": ctx.now()}
            S.put("fees", fee_id, fee)
            ctx.audit.record(actor["uuid"], "fees.fee_changed", f"fee:{fee_id}", public(before), public(fee))
            return public(fee)

        @router.delete("/admin/fees/{fee_id}")
        def delete_fee(fee_id: str, actor: dict = manage):
            fee = get_fee(fee_id)
            if S.list("fee_optouts", fee_id=fee_id):
                raise HTTPException(409, "this fee has opt-outs on record; deactivate it instead")
            S.delete("fees", fee_id)
            ctx.audit.record(actor["uuid"], "fees.fee_deleted", f"fee:{fee_id}", public(fee), None)
            return {"deleted": fee_id}

        # ---- members: opt out and request refunds --------------------------------------------------------------
        def request_view(record: dict[str, Any]) -> dict[str, Any]:
            return {key: record.get(key) for key in (
                "id", "fee_id", "fee_name", "window", "session_key", "session_label", "amount_cents",
                "already_paid", "status", "requested_at", "decided_at",
            )}

        def offers(window: dict[str, Any], uid: str) -> list[dict[str, Any]]:
            mine = {record["fee_id"]: record
                    for record in S.list("fee_optouts", uuid=uid, session_key=window["session"])}
            result = []
            for fee in S.list("fees"):
                if not fee.get("active"):
                    continue
                amount = fee["amounts_cents"].get(window["id"])
                if amount is None:
                    continue
                current = mine.get(fee["id"])
                result.append({
                    "id": fee["id"], "name": fee["name"], "description": fee.get("description", ""),
                    "amount_cents": amount, "refundable": fee.get("refundable", True),
                    "request": request_view(current) if current and current["status"] != WITHDRAWN else None,
                })
            return sorted(result, key=lambda offer: offer["name"].casefold())

        @router.get("/fees/optout")
        def my_optout(actor: dict = member):
            windows = []
            for window_id in WINDOW_IDS:
                current = state(window_id)
                windows.append({**current, "fees": offers(current, actor["uuid"]) if current["open"] else []})
            history = sorted((request_view(record) for record in S.list("fee_optouts", uuid=actor["uuid"])),
                             key=lambda record: record["requested_at"], reverse=True)
            return {"windows": windows, "requests": history, "banking_status": banking_status(actor["uuid"])}

        @router.post("/fees/optout", status_code=201)
        def submit_optout(body: OptOutIn, actor: dict = member):
            uid = actor["uuid"]
            window = state(body.window)
            if not window["open"]:
                raise HTTPException(409, f"opt-outs for the {window['session_label']} are not open")
            if len({item.fee_id for item in body.items}) != len(body.items):
                raise HTTPException(422, "each fee may appear only once")
            chosen = []
            for item in body.items:
                fee = S.get("fees", item.fee_id)
                amount = fee and fee["amounts_cents"].get(body.window)
                if not fee or not fee.get("active") or amount is None:
                    raise HTTPException(422, "a selected fee cannot be opted out of this session")
                if item.already_paid and not fee.get("refundable", True):
                    raise HTTPException(422, f"{fee['name']} is not refundable")
                chosen.append((fee, amount, item.already_paid))
            if any(paid for _, _, paid in chosen) and banking_status(uid) not in BANKING_ACTIVE:
                raise HTTPException(409, "banking information is required to request a refund")
            created = []
            for fee, amount, paid in chosen:
                rid = f"{window['session']}:{fee['id']}:{uid}"

                def save(current, fee=fee, amount=amount, paid=paid, rid=rid):
                    if current and current["status"] != WITHDRAWN:
                        raise HTTPException(409, f"you have already opted out of {fee['name']}")
                    return {
                        "id": rid, "uuid": uid, "fee_id": fee["id"], "fee_name": fee["name"],
                        "levy_group_id": fee.get("levy_group_id"), "window": body.window,
                        "session_key": window["session"], "session_label": window["session_label"],
                        "amount_cents": amount, "already_paid": paid,
                        "status": REFUND_REQUESTED if paid else OPTED_OUT, "requested_at": ctx.now(),
                    }

                record = S.update("fee_optouts", rid, save)
                ctx.audit.record(uid, "fees.optout_submitted", f"fee_optout:{rid}", None,
                                 {"status": record["status"], "amount_cents": amount})
                created.append(request_view(record))
            return {"requests": created}

        @router.delete("/fees/optout/{request_id}")
        def withdraw_optout(request_id: str, actor: dict = member):
            def withdraw(current):
                if not current or current["uuid"] != actor["uuid"]:
                    raise HTTPException(404, "no such opt-out")
                window = state(current["window"])
                if not window["open"] or window["session"] != current["session_key"]:
                    raise HTTPException(409, "the opt-out window has closed")
                if current["status"] not in (OPTED_OUT, REFUND_REQUESTED):
                    raise HTTPException(409, "this opt-out can no longer be withdrawn")
                return {**current, "status": WITHDRAWN, "withdrawn_at": ctx.now()}

            record = S.update("fee_optouts", request_id, withdraw)
            ctx.audit.record(actor["uuid"], "fees.optout_withdrawn", f"fee_optout:{request_id}", None,
                             {"status": WITHDRAWN})
            return request_view(record)

        # ---- finance: refund queue, payout banking and decisions ------------------------------------------------
        def refund_view(record: dict[str, Any]) -> dict[str, Any]:
            return {**request_view(record), "uuid": record["uuid"], "name": display_name(record["uuid"]),
                    "banking_status": banking_status(record["uuid"]), "note": record.get("note", ""),
                    "decided_by": record.get("decided_by")}

        def get_refund(request_id: str) -> dict[str, Any]:
            record = S.get("fee_optouts", request_id)
            if not record or not record.get("already_paid"):
                raise HTTPException(404, "no such refund request")
            return record

        @router.get("/finance/fees/refunds")
        def refund_queue(status: Literal["refund_requested", "refunded", "denied", "withdrawn"] = REFUND_REQUESTED,
                         cursor: str = Query("", max_length=2048), limit: int = Query(100, ge=1, le=200),
                         _: dict = refunds):
            page, next_cursor = paginate_store(
                S, "fee_optouts", cursor, limit, (PageOrder("requested_at", int), PageOrder("id", str)),
                equals={"status": status}, predicate=lambda record: bool(record.get("already_paid")),
            )
            return {"items": [refund_view(record) for record in page], "next_cursor": next_cursor}

        @router.get("/finance/fees/refunds/{request_id}/banking")
        def refund_banking(request_id: str, actor: dict = refunds):
            record = get_refund(request_id)
            bank = S.get("financial_profiles", record["uuid"])
            if not bank or bank["status"] not in BANKING_ACTIVE:
                raise HTTPException(404, "banking details unavailable")
            ctx.audit.record(actor["uuid"], "fees.banking_accessed", f"user:{record['uuid']}", None,
                             {"fee_optout": request_id})
            return {field: bank[field] for field in BANKING_FIELDS}

        @router.post("/finance/fees/refunds/{request_id}/decision")
        def decide_refund(request_id: str, body: DecisionIn, actor: dict = refunds):
            record = get_refund(request_id)
            note = clean_text(body.note)
            if body.decision == DENIED and not note:
                raise HTTPException(422, "explain why the refund is denied")
            if body.decision == REFUNDED and banking_status(record["uuid"]) != "on_file":
                raise HTTPException(409, "verified banking details on file are required to pay a refund")

            def decide(current):
                if not current or current["status"] != REFUND_REQUESTED:
                    raise HTTPException(409, "this refund request has already been decided or withdrawn")
                return {**current, "status": body.decision, "note": note, "decided_by": actor["uuid"],
                        "decided_at": ctx.now()}

            updated = S.update("fee_optouts", request_id, decide)
            ctx.audit.record(actor["uuid"], f"fees.refund_{body.decision}", f"fee_optout:{request_id}",
                             {"status": REFUND_REQUESTED}, {"status": body.decision})
            return refund_view(updated)

        # ---- levy groups: who opted out and what the levy is predicted to bring in -----------------------------
        def visible_groups(actor: dict) -> list[dict[str, Any]]:
            groups = S.list("fee_groups")
            if not ctx.has_permission(actor, PERM_FEES_MANAGE):
                groups = [group for group in groups if actor["uuid"] in group.get("members", [])]
            return sorted(groups, key=lambda group: group["name"].casefold())

        def levy_group(group_id: str, actor: dict) -> dict[str, Any]:
            group = next((item for item in visible_groups(actor) if item["id"] == group_id), None)
            if not group:
                raise HTTPException(404, "no such levy group")
            return group

        def enrolment_for(session: str) -> tuple[int | None, str]:
            stored = (S.get("fee_sessions", session) or {}).get("expected_enrolment")
            if stored is not None:
                return stored, "admin"
            count = (S.get("roster_meta", "current") or {}).get("count")
            return (count, "roster") if count is not None else (None, "unset")

        @router.get("/levy-groups")
        def my_levy_groups(actor: dict = levy):
            return {"items": [{"id": group["id"], "name": group["name"], "description": group.get("description", "")}
                              for group in visible_groups(actor)]}

        @router.get("/levy-groups/{group_id}")
        def levy_report(group_id: str, session: str = Query("", max_length=16), actor: dict = levy):
            group = levy_group(group_id, actor)
            states = {window["session"]: window for window in map(state, WINDOW_IDS)}
            keys = set(states) | {record["session_key"] for record in S.list("fee_optouts", levy_group_id=group_id)}
            ordered = sorted(keys, key=session_order, reverse=True)
            if session and session not in keys:
                raise HTTPException(404, "no such session")
            chosen = session or next((key for key in ordered if states.get(key, {}).get("open")), ordered[0])
            window_id = chosen.split(":")[0]
            records = S.list("fee_optouts", levy_group_id=group_id, session_key=chosen)
            fees = {fee["id"]: fee for fee in S.list("fees", levy_group_id=group_id)}
            for record in records:
                fees.setdefault(record["fee_id"], {"id": record["fee_id"], "name": record["fee_name"],
                                                   "amounts_cents": {}})
            enrolment, source = enrolment_for(chosen)
            rows = [levy_summary(fee, window_id, [r for r in records if r["fee_id"] == fee["id"]], enrolment)
                    for fee in sorted(fees.values(), key=lambda fee: fee["name"].casefold())]

            def total(field: str) -> int | None:
                values = [row[field] for row in rows]
                return None if not values or any(value is None for value in values) else sum(values)

            return {
                "group": {"id": group["id"], "name": group["name"], "description": group.get("description", "")},
                "sessions": [{"session": key, "label": session_label(key), "open": states.get(key, {}).get("open", False),
                              "opens_at": states.get(key, {}).get("opens_at")} for key in ordered],
                "session": chosen, "session_label": session_label(chosen),
                "enrolment": {"value": enrolment, "source": source},
                "fees": rows,
                "totals": {field: total(field) for field in (
                    "gross_cents", "confirmed_reduction_cents", "pending_reduction_cents",
                    "confirmed_net_cents", "predicted_net_cents", "opt_out_count")},
            }

        @router.get("/levy-groups/{group_id}/optouts")
        def levy_optouts(group_id: str, session: str = Query(..., max_length=16),
                         fee_id: str | None = Query(None, max_length=64),
                         cursor: str = Query("", max_length=2048), limit: int = Query(100, ge=1, le=200),
                         actor: dict = levy):
            levy_group(group_id, actor)
            if not SESSION_KEY.fullmatch(session):
                raise HTTPException(404, "no such session")
            equals = {"levy_group_id": group_id, "session_key": session}
            if fee_id:
                equals["fee_id"] = fee_id
            page, next_cursor = paginate_store(
                S, "fee_optouts", cursor, limit, (PageOrder("requested_at", int, descending=True), PageOrder("id", str)),
                equals=equals, predicate=lambda record: record["status"] != WITHDRAWN,
            )
            return {"items": [{
                "id": record["id"], "name": display_name(record["uuid"]), "fee_id": record["fee_id"],
                "fee_name": record["fee_name"], "amount_cents": record["amount_cents"], "status": record["status"],
                "requested_at": record["requested_at"],
            } for record in page], "next_cursor": next_cursor}

        return router
