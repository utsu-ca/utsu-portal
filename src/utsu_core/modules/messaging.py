"""Record-linked threads (live chat) for any plug-in-registered thread type. Streams use header auth (fetch-based SSE),
never a token in the URL, and every batch re-checks the owning plug-in's authorizer."""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..context import Context
from ..contracts import MessageRateLimited, MessageRejected, ThreadDenied
from ..services.messaging import MAX_BODY_LIMIT, MAX_PAGE, MessagingRuntime, thread_key
from ..store import Conflict

name = "messaging"
STREAM_LIFETIME = 600  # seconds; clients reconnect from their cursor


class PostIn(BaseModel):
    body: str = Field(max_length=MAX_BODY_LIMIT * 2)
    internal: bool = False


class ReadIn(BaseModel):
    seq: int = Field(ge=0)


class RedactIn(BaseModel):
    reason: str = Field(max_length=1000)


def _call(fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc) or "not found") from exc
    except ThreadDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    except MessageRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
    except MessageRejected as exc:
        raise HTTPException(422, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, "the thread is busy; try again") from exc


def _sse(event: str, data: Any, ident: int | None = None) -> str:
    head = f"id: {ident}\n" if ident is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def thread_events(runtime: MessagingRuntime, bus: Any, thread_type: str, record_id: str, account: dict,
                        after: int, disconnected: Callable[[], Awaitable[bool]],
                        lifetime: float = STREAM_LIFETIME,
                        clock: Callable[[], float] = time.monotonic) -> AsyncIterator[str]:
    """Reconcile authorized state on connection, then send changes on hints or keepalive ticks.
    Sequence allocation precedes persistence, so late writes and redactions can fall behind the cursor."""
    key = thread_key(thread_type, record_id)
    viewer = account["uuid"]
    cursor = max(0, int(after))
    deadline = clock() + lifetime
    seen: dict[int, bool] | None = None

    async def batch() -> list[str] | None:
        nonlocal cursor, seen
        try:
            _, access = await run_in_threadpool(runtime.resolve, thread_type, record_id, account)
        except (LookupError, PermissionError):
            return None
        rows = await run_in_threadpool(runtime.snapshot, key, access, viewer)
        current = {row["seq"]: row["redacted"] for row in rows}
        out: list[str] = []
        if seen is None or seen.keys() != current.keys():
            out.append(_sse("sync", {"seqs": list(current)}))
        for row in rows:
            cursor = max(cursor, row["seq"])
            if seen is None or row["seq"] not in seen:
                out.append(_sse("message", row, row["seq"]))
            elif seen[row["seq"]] != row["redacted"]:
                out.append(_sse("redact", row))
        seen = current
        return out

    yield "retry: 3000\n\n"
    first = await batch()
    if first is None:
        yield _sse("denied", {})
        return
    for chunk in first:
        yield chunk
    yield _sse("ready", {"cursor": cursor})
    async for event in bus.listen(f"thread:{key}", disconnected):
        chunks = await batch()
        if chunks is None:
            yield _sse("denied", {})
            return
        for chunk in chunks:
            yield chunk
        if event is None:
            yield ": keepalive\n\n"
        if clock() >= deadline:
            yield _sse("reconnect", {"cursor": cursor})
            return


class MessagingModule:
    name = name

    def router(self, ctx: Context) -> APIRouter:
        r = APIRouter(prefix="/api/threads")
        runtime = ctx.messaging
        verified = ctx.require_verified()

        @r.get("/{thread_type}/{record_id}")
        def describe(thread_type: str, record_id: str, a: dict = verified):
            return _call(runtime.describe, thread_type, record_id, a)

        @r.get("/{thread_type}/{record_id}/messages")
        def messages(thread_type: str, record_id: str, after: int = Query(0, ge=0),
                     limit: int = Query(100, ge=1, le=MAX_PAGE), a: dict = verified):
            return _call(runtime.messages, thread_type, record_id, a, after, limit)

        @r.post("/{thread_type}/{record_id}/messages")
        def post(thread_type: str, record_id: str, b: PostIn, a: dict = verified):
            return _call(runtime.post, thread_type, record_id, a, b.body, b.internal)

        @r.post("/{thread_type}/{record_id}/read")
        def read(thread_type: str, record_id: str, b: ReadIn, a: dict = verified):
            return {"read": _call(runtime.mark_read, thread_type, record_id, a, b.seq)}

        @r.post("/{thread_type}/{record_id}/messages/{seq}/redact")
        def redact(thread_type: str, record_id: str, seq: int, b: RedactIn, a: dict = verified):
            return _call(runtime.redact, thread_type, record_id, seq, a, b.reason)

        @r.get("/{thread_type}/{record_id}/stream")
        async def stream(thread_type: str, record_id: str, request: Request, after: int = Query(0, ge=0),
                         a: dict = verified):
            await run_in_threadpool(_call, runtime.resolve, thread_type, record_id, a)
            events = thread_events(runtime, ctx.bus, thread_type, record_id, a, after, request.is_disconnected)
            return StreamingResponse(events, media_type="text/event-stream",
                                     headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

        return r
