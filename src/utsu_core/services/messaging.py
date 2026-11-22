"""Core record-linked messaging: plug-in thread types, append-only messages, read markers and live hints."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..contracts import MessageRateLimited, MessageRejected, ThreadAccess, ThreadDenied, ThreadType
from ..pagination import PageOrder
from ..store import Doc, Store

_TYPE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MAX_BODY_LIMIT = 20_000
ACCOUNT_POSTS_PER_MINUTE = 30
THREAD_POSTS_PER_MINUTE = 12
MAX_PAGE = 200


@dataclass(frozen=True)
class RegisteredThreadType:
    owner: str
    identifier: str
    definition: ThreadType


class ThreadTypeRegistry:
    def __init__(self) -> None:
        self._types: dict[str, RegisteredThreadType] = {}
        self._frozen = False

    def register(self, owner: str, identifier: str, definition: ThreadType) -> None:
        if self._frozen:
            raise RuntimeError("thread type registry is frozen")
        if not isinstance(definition, ThreadType):
            raise TypeError(f"thread type {identifier} is not a ThreadType")
        if not _TYPE_ID.fullmatch(identifier) or not (identifier == owner or identifier.startswith(f"{owner}-")):
            raise ValueError(f"thread type {identifier!r} must be {owner!r} or start with '{owner}-'")
        if identifier in self._types:
            raise ValueError(f"thread type {identifier} is already registered")
        if not callable(definition.authorize):
            raise TypeError(f"thread type {identifier} needs an authorizer")
        if not 1 <= definition.max_body <= MAX_BODY_LIMIT:
            raise ValueError(f"thread type {identifier} max_body must be between 1 and {MAX_BODY_LIMIT}")
        self._types[identifier] = RegisteredThreadType(owner, identifier, definition)

    def freeze(self) -> None:
        self._frozen = True

    def get(self, identifier: str) -> RegisteredThreadType | None:
        return self._types.get(identifier)


def thread_key(thread_type: str, record_id: str) -> str:
    return f"{thread_type}:{record_id}"


def clean_body(body: object, limit: int) -> str:
    if not isinstance(body, str):
        raise MessageRejected("message must be text")
    text = unicodedata.normalize("NFC", body.replace("\r\n", "\n").replace("\r", "\n"))
    text = _CONTROL.sub("", text).strip()
    if not text:
        raise MessageRejected("message is empty")
    if len(text) > limit:
        raise MessageRejected(f"message is longer than {limit} characters")
    return text


class MessagingRuntime:
    """Every operation re-checks the owning plug-in's authorizer; unknown or disabled types deny."""

    def __init__(self, store: Store, audit: Any, bus: Any, limiter: Any, registry: ThreadTypeRegistry,
                 display_name: Callable[[str], str | None], now: Callable[[], int],
                 user_lookup: Callable[[str], dict | None] | None = None) -> None:
        self._store = store
        self._audit = audit
        self._bus = bus
        self._limiter = limiter
        self._registry = registry
        self._display_name = display_name
        self._user_lookup = user_lookup
        self._now = now

    def for_owner(self, owner: str) -> "MessagingView":
        return MessagingView(self, owner)

    def owner(self, thread_type: str) -> str | None:
        registered = self._registry.get(thread_type)
        return registered.owner if registered else None

    def resolve(self, thread_type: str, record_id: str,
                account: Mapping[str, Any]) -> tuple[RegisteredThreadType, ThreadAccess]:
        registered = self._registry.get(thread_type)
        if registered is None or not _RECORD_ID.fullmatch(record_id or ""):
            raise LookupError("thread not found")
        access = registered.definition.authorize(account, record_id)
        if not isinstance(access, ThreadAccess) or not (access.read or access.staff):
            raise ThreadDenied("thread is not available to this account")
        return registered, access

    def describe(self, thread_type: str, record_id: str, account: Mapping[str, Any]) -> dict[str, Any]:
        registered, access = self.resolve(thread_type, record_id, account)
        info = self.thread_info(thread_type, record_id) or {}
        return {
            "post": access.post or access.staff,
            "staff": access.staff,
            "internal_notes": registered.definition.internal_notes and access.staff,
            "max_body": registered.definition.max_body,
            "last_seq": info.get("seq", 0),
            "read": self._read_marker(thread_key(thread_type, record_id), account["uuid"]),
        }

    def post(self, thread_type: str, record_id: str, account: Mapping[str, Any], body: str,
             internal: bool = False, name_choice: str = "real") -> Doc:
        registered, access = self.resolve(thread_type, record_id, account)
        if internal and not (registered.definition.internal_notes and access.staff):
            raise ThreadDenied("internal notes are not available here")
        if not internal and not (access.post or access.staff):
            raise ThreadDenied("posting is closed for this account")
        text = clean_body(body, registered.definition.max_body)
        key = thread_key(thread_type, record_id)
        uid = account["uuid"]
        if name_choice not in ("real", "username"):
            raise MessageRejected("Choose a valid author name")
        user = self._user_lookup(uid) if self._user_lookup else None
        if name_choice == "username":
            author_name = (user or {}).get("username")
            if not author_name:
                raise MessageRejected("Set a username on your account before posting with it")
        else:
            author_name = " ".join(part for part in ((user or {}).get("first_name"), (user or {}).get("last_name")) if part)
            author_name = author_name or self._display_name(uid) or "Member"
        if not (self._limiter.allow(f"msg:{uid}", ACCOUNT_POSTS_PER_MINUTE, 60)
                and self._limiter.allow(f"msg:{uid}:{key}", THREAD_POSTS_PER_MINUTE, 60)):
            raise MessageRateLimited("posting too quickly; wait a minute and try again")
        now = self._now()

        def allocate(current: Doc | None) -> Doc:
            thread = current or {"type": thread_type, "owner": registered.owner, "record_id": record_id,
                                 "seq": 0, "created_at": now}
            return {**thread, "seq": int(thread.get("seq", 0)) + 1, "last_at": now}

        thread = self._store.update("message_threads", key, allocate)
        assert thread is not None
        seq = int(thread["seq"])
        message = {"thread": key, "seq": seq, "author": uid, "author_name": author_name,
                   "internal": bool(internal), "body": text, "at": now}
        if not self._store.create("messages", f"{key}:{seq:010d}", message):
            raise RuntimeError("message sequence collision")
        self._audit.record(uid, "message.post", key, None, None, {"seq": seq, "internal": bool(internal)})
        self._bus.publish(f"thread:{key}", {"seq": seq})
        self._advance_read(key, uid, seq)
        return self._public(message, access, uid, {})

    def messages(self, thread_type: str, record_id: str, account: Mapping[str, Any], after: int = 0,
                 limit: int = 100) -> list[Doc]:
        _, access = self.resolve(thread_type, record_id, account)
        return self.visible_after(thread_key(thread_type, record_id), access, account["uuid"], after, limit)

    def visible_after(self, key: str, access: ThreadAccess, viewer: str, after: int = 0,
                      limit: int = 100) -> list[Doc]:
        limit = max(1, min(int(limit), MAX_PAGE))
        rows = self._visible_rows(key, access, after)[:limit]
        names: dict[str, str | None] = {}
        return [self._public(row, access, viewer, names) for row in rows]

    def snapshot(self, key: str, access: ThreadAccess, viewer: str) -> list[Doc]:
        """Complete authorized state for stream reconciliation, including late writes and old redactions."""
        names: dict[str, str | None] = {}
        return [self._public(row, access, viewer, names) for row in self._visible_rows(key, access)]

    def _visible_rows(self, key: str, access: ThreadAccess, after: int = 0) -> list[Doc]:
        return sorted((row for row in self._store.list("messages", thread=key)
                       if int(row["seq"]) > after and (access.staff or not row.get("internal"))),
                      key=lambda row: int(row["seq"]))

    def message(self, key: str, seq: int, access: ThreadAccess, viewer: str) -> Doc | None:
        row = self._store.get("messages", f"{key}:{int(seq):010d}")
        if row is None or (row.get("internal") and not access.staff):
            return None
        return self._public(row, access, viewer, {})

    def redact(self, thread_type: str, record_id: str, seq: int, account: Mapping[str, Any],
               reason: str) -> Doc:
        _, access = self.resolve(thread_type, record_id, account)
        if not access.staff:
            raise ThreadDenied("only moderators can redact messages")
        reason = clean_body(reason, 200)
        key = thread_key(thread_type, record_id)
        uid = account["uuid"]
        now = self._now()

        def blank(current: Doc | None) -> Doc | None:
            if current is None:
                raise LookupError("message not found")
            if current.get("redacted"):
                return None
            return {**current, "body": "", "redacted": {"by": uid, "at": now}}

        row = self._store.update("messages", f"{key}:{int(seq):010d}", blank)
        assert row is not None
        self._audit.record(uid, "message.redact", key, None, None, {"seq": int(seq), "reason": reason})
        self._bus.publish(f"thread:{key}", {"redacted": int(seq)})
        return self._public(row, access, uid, {})

    def mark_read(self, thread_type: str, record_id: str, account: Mapping[str, Any], seq: int) -> int:
        self.resolve(thread_type, record_id, account)
        key = thread_key(thread_type, record_id)
        info = self._store.get("message_threads", key) or {}
        return self._advance_read(key, account["uuid"], min(max(int(seq), 0), int(info.get("seq", 0))))

    def thread_info(self, thread_type: str, record_id: str) -> Doc | None:
        thread = self._store.get("message_threads", thread_key(thread_type, record_id))
        if thread is None:
            return None
        return {"seq": int(thread.get("seq", 0)), "created_at": thread.get("created_at"),
                "last_at": thread.get("last_at")}

    def set_thread_metadata(
        self, thread_type: str, record_id: str, owner: str, metadata: Mapping[str, Any]
    ) -> None:
        registered = self._registry.get(thread_type)
        if registered is None or registered.owner != owner:
            raise PermissionError(f"thread type {thread_type} is outside this messaging scope")
        if not _RECORD_ID.fullmatch(record_id or ""):
            raise ValueError("invalid thread record id")
        key = thread_key(thread_type, record_id)

        def update(current: Doc | None) -> Doc:
            if current is not None and (
                current.get("type") != thread_type or current.get("owner") != owner
            ):
                raise PermissionError("thread metadata cannot be changed outside its owner scope")
            base = current or {
                "type": thread_type,
                "owner": owner,
                "record_id": record_id,
                "seq": 0,
                "created_at": self._now(),
                "last_at": self._now(),
            }
            return {**base, "metadata": {**base.get("metadata", {}), **metadata}}

        self._store.update("message_threads", key, update)

    def page_threads(
        self,
        thread_type: str,
        owner: str,
        *,
        order_by: tuple[PageOrder, ...],
        after: tuple[Any, ...] | None,
        limit: int,
        equals: Mapping[str, Any] | None = None,
    ) -> list[Doc]:
        registered = self._registry.get(thread_type)
        if registered is None or registered.owner != owner:
            raise PermissionError(f"thread type {thread_type} is outside this messaging scope")
        filters = dict(equals or {})
        if "type" in filters or "owner" in filters:
            raise ValueError("thread type and owner filters are controlled by the messaging service")
        return self._store.page(
            "message_threads",
            order_by=order_by,
            after=after,
            limit=limit,
            equals={"type": thread_type, "owner": owner, **filters},
        )

    def _read_marker(self, key: str, uid: str) -> int:
        marker = self._store.get("message_reads", f"{key}:{uid}")
        return int(marker["seq"]) if marker else 0

    def _advance_read(self, key: str, uid: str, seq: int) -> int:
        def advance(current: Doc | None) -> Doc | None:
            if current is not None and int(current.get("seq", 0)) >= seq:
                return None
            return {"thread": key, "uuid": uid, "seq": seq}

        marker = self._store.update("message_reads", f"{key}:{uid}", advance)
        return int(marker["seq"]) if marker else 0

    def _public(self, row: Doc, access: ThreadAccess, viewer: str, names: dict[str, str | None]) -> Doc:
        author = row["author"]
        if not row.get("author_name") and author not in names:
            names[author] = self._display_name(author)
        result = {
            "seq": int(row["seq"]),
            "author_name": row.get("author_name") or names.get(author) or "Member",
            "mine": author == viewer,
            "internal": bool(row.get("internal")),
            "body": "" if row.get("redacted") else row.get("body", ""),
            "redacted": bool(row.get("redacted")),
            "at": row.get("at"),
        }
        if access.staff:
            result["author"] = author
        return result


class MessagingView:
    """The `MessagingService` handed to one plug-in: only its own thread types."""

    def __init__(self, runtime: MessagingRuntime, owner: str) -> None:
        self._runtime = runtime
        self._owner = owner

    def _check(self, thread_type: str) -> None:
        if self._runtime.owner(thread_type) != self._owner:
            raise PermissionError(f"thread type {thread_type} is outside this messaging scope")

    def post(self, thread_type: str, record_id: str, account: Mapping[str, Any], body: str,
             internal: bool = False, name_choice: str = "real") -> Doc:
        self._check(thread_type)
        return self._runtime.post(thread_type, record_id, account, body, internal, name_choice)

    def messages(self, thread_type: str, record_id: str, account: Mapping[str, Any], after: int = 0,
                 limit: int = 100) -> list[Doc]:
        self._check(thread_type)
        return self._runtime.messages(thread_type, record_id, account, after, limit)

    def redact(self, thread_type: str, record_id: str, seq: int, account: Mapping[str, Any],
               reason: str) -> Doc:
        self._check(thread_type)
        return self._runtime.redact(thread_type, record_id, seq, account, reason)

    def mark_read(self, thread_type: str, record_id: str, account: Mapping[str, Any], seq: int) -> int:
        self._check(thread_type)
        return self._runtime.mark_read(thread_type, record_id, account, seq)

    def thread_info(self, thread_type: str, record_id: str) -> Doc | None:
        self._check(thread_type)
        return self._runtime.thread_info(thread_type, record_id)

    def set_thread_metadata(
        self, thread_type: str, record_id: str, metadata: Mapping[str, Any]
    ) -> None:
        self._check(thread_type)
        self._runtime.set_thread_metadata(thread_type, record_id, self._owner, metadata)

    def page_threads(
        self,
        thread_type: str,
        *,
        order_by: tuple[PageOrder, ...],
        after: tuple[Any, ...] | None,
        limit: int,
        equals: Mapping[str, Any] | None = None,
    ) -> list[Doc]:
        self._check(thread_type)
        return self._runtime.page_threads(
            thread_type,
            self._owner,
            order_by=order_by,
            after=after,
            limit=limit,
            equals=equals,
        )
