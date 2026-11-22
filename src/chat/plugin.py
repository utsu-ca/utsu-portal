"""Discussion boards and topics. Replies are core messages in thread type `discussion-topic`, so live updates,
redaction and limits come from core messaging; this plug-in owns boards, topics, access and moderation."""
from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from utsu_core.contracts import (
    MessageRateLimited,
    MessageRejected,
    PluginManifest,
    PluginServices,
    RegistrationContext,
    ThreadAccess,
    ThreadType,
)
from utsu_core.permissions import PERM_DISCUSSION_MODERATE
from utsu_core.pagination import PageOrder, paginate_page, paginate_store

from .contracts import AUDIENCES, DEFAULT_KIND, THREAD_TYPE, TopicKind

MAX_POST = 8000
_BOARD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
_KIND_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_TOPIC_ID = re.compile(r"^[0-9a-f]{16}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

MANIFEST = PluginManifest(
    id="discussion",
    version="0.1.0",
    core_api_major=1,
    factory="chat.plugin:DiscussionPlugin",
    interface="plugin",
)


class AlreadyExists(Exception):
    """The record already exists."""


def clean_line(value: object, limit: int, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text")
    text = " ".join(_CONTROL.sub(" ", unicodedata.normalize("NFC", value)).split())
    if not text:
        raise ValueError(f"{what} is required")
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return text


def clean_text(value: object, limit: int, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text")
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    text = _CONTROL.sub("", text).strip()
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return text


def _board_public(board: Mapping[str, Any]) -> dict[str, Any]:
    return {key: board.get(key) for key in ("id", "title", "description", "audience", "archived", "owner")}


class DiscussionRuntime:
    """Boards, topics and the topic-kind extension point; the `discussion` provided service."""

    def __init__(self, services: PluginServices) -> None:
        self._s = services
        self._kinds: dict[str, tuple[str, TopicKind]] = {}
        self._frozen = False

    def for_consumer(self, consumer: str) -> "DiscussionClient":
        return DiscussionClient(self, consumer)

    def freeze(self) -> None:
        self._frozen = True

    def register_kind(self, owner: str, identifier: str, kind: TopicKind) -> None:
        if self._frozen:
            raise RuntimeError("discussion topic kinds are frozen")
        if not isinstance(kind, TopicKind):
            raise TypeError(f"topic kind {identifier} is not a TopicKind")
        if not _KIND_ID.fullmatch(identifier) or not (identifier == owner or identifier.startswith(f"{owner}-")):
            raise ValueError(f"topic kind {identifier!r} must be {owner!r} or start with '{owner}-'")
        if identifier == DEFAULT_KIND or identifier in self._kinds:
            raise ValueError(f"topic kind {identifier} is already registered")
        self._kinds[identifier] = (owner, kind)

    def kind_owner(self, identifier: str) -> str | None:
        entry = self._kinds.get(identifier)
        return entry[0] if entry else None

    # Access

    def viewer(self, account: Mapping[str, Any]) -> tuple[bool, bool]:
        """(moderator, member) for a verified account; one roster lookup per call."""
        if account.get("status") != "verified":
            return False, False
        moderator = self._s.auth.has_permission(account, PERM_DISCUSSION_MODERATE)
        return moderator, moderator or self._s.auth.is_rostered(account)

    @staticmethod
    def board_visible(board: Mapping[str, Any], moderator: bool, member: bool) -> bool:
        return moderator or (member and board.get("audience") == "members")

    def access(self, account: Mapping[str, Any], topic_id: str) -> ThreadAccess | None:
        """Thread authorizer. A topic's author keeps access to it, so a plug-in can open a topic on behalf of an
        official (for example a ballot) who is not a member of the board's audience."""
        if not _TOPIC_ID.fullmatch(topic_id or "") or account.get("status") != "verified":
            return None
        topic = self._s.store.get("discussion_topics", topic_id)
        board = self._s.store.get("discussion_boards", topic["board"]) if topic else None
        if topic is None or board is None:
            return None
        moderator, member = self.viewer(account)
        if not (topic.get("author") == account.get("uuid") or self.board_visible(board, moderator, member)):
            return None
        return ThreadAccess(read=True, post=not topic.get("locked") and not board.get("archived"), staff=moderator)

    # Boards

    def board(self, board_id: str) -> dict[str, Any] | None:
        return self._s.store.get("discussion_boards", board_id) if _BOARD_ID.fullmatch(board_id or "") else None

    def all_boards(self) -> list[dict[str, Any]]:
        boards = [_board_public(board) for board in self._s.store.list("discussion_boards")]
        return sorted(boards, key=lambda board: (
            bool(board["archived"]), str(board["title"]).casefold(), str(board["id"])
        ))

    def ensure_thread_page_metadata(self) -> None:
        migration_id = "thread_page_metadata_v1"
        if self._s.store.get("discussion_pagination_migrations", migration_id):
            return
        topics = self._s.store.list("discussion_topics")
        for topic in topics:
            self._s.messaging.set_thread_metadata(
                THREAD_TYPE,
                topic["id"],
                {"board": topic["board"], "pinned": bool(topic.get("pinned"))},
            )
        created = self._s.store.create(
            "discussion_pagination_migrations", migration_id, {"id": migration_id}
        )
        if created and topics:
            self._s.audit.record(
                "system",
                "discussion.pagination_migrated",
                "discussion_topics",
                None,
                {"threads_updated": len(topics)},
            )

    def create_board(self, board_id: str, title: str, description: str, audience: str, actor: str | None,
                     owner: str = "discussion") -> dict[str, Any]:
        if not _BOARD_ID.fullmatch(board_id or ""):
            raise ValueError("board ID must be 2-48 lowercase letters, digits or hyphens")
        if audience not in AUDIENCES:
            raise ValueError(f"audience must be one of {', '.join(AUDIENCES)}")
        board = {"id": board_id, "title": clean_line(title, 120, "title"),
                 "description": clean_text(description, 2000, "description"), "audience": audience,
                 "archived": False, "owner": owner, "created_by": actor, "created_at": self._s.now()}
        if not self._s.store.create("discussion_boards", board_id, board):
            raise AlreadyExists(f"board {board_id} already exists")
        self._s.audit.record(actor or "system", "discussion.board.created", f"discussion_boards/{board_id}", None,
                             {"title": board["title"], "audience": audience}, {"owner": owner})
        return _board_public(board)

    def update_board(self, board_id: str, changes: Mapping[str, Any], actor: str) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        if changes.get("title") is not None:
            clean["title"] = clean_line(changes["title"], 120, "title")
        if changes.get("description") is not None:
            clean["description"] = clean_text(changes["description"], 2000, "description")
        if changes.get("audience") is not None:
            if changes["audience"] not in AUDIENCES:
                raise ValueError(f"audience must be one of {', '.join(AUDIENCES)}")
            clean["audience"] = changes["audience"]
        if changes.get("archived") is not None:
            clean["archived"] = bool(changes["archived"])
        before: dict[str, Any] = {}

        def apply(current: dict[str, Any] | None) -> dict[str, Any] | None:
            if current is None:
                raise LookupError("board not found")
            before.clear()
            before.update({key: current.get(key) for key in clean})
            return None if all(current.get(key) == value for key, value in clean.items()) else {**current, **clean}

        if self.board(board_id) is None:
            raise LookupError("board not found")
        board = self._s.store.update("discussion_boards", board_id, apply)
        assert board is not None
        if before != clean:
            self._s.audit.record(actor, "discussion.board.updated", f"discussion_boards/{board_id}", before, clean)
        return _board_public(board)

    # Topics

    def public_topic(
        self,
        topic: Mapping[str, Any],
        names: dict[str, str | None] | None = None,
        thread: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        names = {} if names is None else names
        kind = topic.get("kind") or DEFAULT_KIND
        entry = self._kinds.get(kind)
        info = thread if thread is not None else self._s.messaging.thread_info(THREAD_TYPE, topic["id"]) or {}
        author = topic.get("author") or ""
        if author not in names:
            names[author] = self._s.auth.display_name(author) if author else None
        return {
            "id": topic["id"],
            "board": topic["board"],
            "title": topic["title"],
            "kind": kind if entry else DEFAULT_KIND,
            "kind_label": entry[1].label if entry else None,
            "panel": entry[1].panel if entry else None,
            "ref": topic.get("ref") if entry else None,
            "author_name": topic.get("author_name") or names[author] or "Member",
            "pinned": bool(topic.get("pinned")),
            "locked": bool(topic.get("locked")),
            "created_at": topic.get("created_at"),
            "last_at": info.get("last_at") or topic.get("created_at"),
            "posts": info.get("seq", 0),
        }

    def topic(self, topic_id: str) -> dict[str, Any] | None:
        return self._s.store.get("discussion_topics", topic_id) if _TOPIC_ID.fullmatch(topic_id or "") else None

    def topics(self, board_id: str) -> list[dict[str, Any]]:
        names: dict[str, str | None] = {}
        rows = [self.public_topic(row, names) for row in self._s.store.list("discussion_topics", board=board_id)]
        return sorted(rows, key=lambda row: (
            not bool(row["pinned"]), -int(row["last_at"] or 0), str(row["id"])
        ))

    def create_topic(self, board_id: str, title: str, body: str, author: Mapping[str, Any],
                     kind: str = DEFAULT_KIND, ref: str = "", name_choice: str = "real") -> dict[str, Any]:
        board = self.board(board_id)
        if board is None:
            raise LookupError("board not found")
        if board.get("archived"):
            raise PermissionError("this board is archived")
        if author.get("status") != "verified":
            raise PermissionError("only verified accounts can start topics")
        if kind != DEFAULT_KIND and kind not in self._kinds:
            raise ValueError(f"unknown topic kind {kind}")
        if not isinstance(ref, str) or len(ref) > 128:
            raise ValueError("topic reference must be at most 128 characters")
        if not isinstance(body, str) or not body.strip():
            raise MessageRejected("the opening post is empty")
        topic_id = secrets.token_hex(8)
        topic = {"id": topic_id, "board": board_id, "title": clean_line(title, 200, "title"), "kind": kind,
                 "ref": ref, "author": author["uuid"], "author_name": "Member", "pinned": False, "locked": False,
                 "created_at": self._s.now()}
        if not self._s.store.create("discussion_topics", topic_id, topic):
            raise RuntimeError("topic ID collision")
        try:
            self._s.messaging.set_thread_metadata(
                THREAD_TYPE, topic_id, {"board": board_id, "pinned": False}
            )
            opening = self._s.messaging.post(THREAD_TYPE, topic_id, author, body, name_choice=name_choice)
            topic = self._s.store.update("discussion_topics", topic_id, lambda current: {**current, "author_name": opening["author_name"]})
        except BaseException:
            self._s.store.delete("discussion_topics", topic_id)
            raise
        self._s.audit.record(author["uuid"], "discussion.topic.created", f"discussion_topics/{topic_id}", None, None,
                             {"board": board_id, "kind": kind})
        return self.public_topic(topic)

    def moderate(self, topic_id: str, actor: str, locked: bool | None = None,
                 pinned: bool | None = None) -> dict[str, Any]:
        changes = {key: value for key, value in (("locked", locked), ("pinned", pinned)) if value is not None}
        before: dict[str, Any] = {}

        def apply(current: dict[str, Any] | None) -> dict[str, Any] | None:
            if current is None:
                raise LookupError("topic not found")
            before.clear()
            before.update({key: bool(current.get(key)) for key in changes})
            return None if before == changes else {**current, **changes}

        if self.topic(topic_id) is None:
            raise LookupError("topic not found")
        topic = self._s.store.update("discussion_topics", topic_id, apply)
        assert topic is not None
        if "pinned" in changes:
            self._s.messaging.set_thread_metadata(
                THREAD_TYPE, topic_id, {"board": topic["board"], "pinned": bool(topic["pinned"])}
            )
        if before != changes:
            self._s.audit.record(actor, "discussion.topic.moderated", f"discussion_topics/{topic_id}", before, changes)
        return self.public_topic(topic)


class DiscussionClient:
    """The `DiscussionService` handed to one consumer plug-in: its own topic kinds only."""

    def __init__(self, runtime: DiscussionRuntime, consumer: str) -> None:
        self._runtime = runtime
        self._consumer = consumer

    def register_topic_kind(self, identifier: str, kind: TopicKind) -> None:
        self._runtime.register_kind(self._consumer, identifier, kind)

    def boards(self) -> list[dict[str, Any]]:
        return self._runtime.all_boards()

    def ensure_board(self, board_id: str, title: str, description: str = "",
                     audience: str = "members") -> dict[str, Any]:
        board = self._runtime.board(board_id)
        if board is not None:
            return _board_public(board)
        try:
            return self._runtime.create_board(board_id, title, description, audience, None, self._consumer)
        except AlreadyExists:
            board = self._runtime.board(board_id)
            assert board is not None
            return _board_public(board)

    def _own(self, topic_id: str) -> dict[str, Any]:
        topic = self._runtime.topic(topic_id)
        if topic is None:
            raise LookupError("topic not found")
        if self._runtime.kind_owner(topic.get("kind") or DEFAULT_KIND) != self._consumer:
            raise PermissionError(f"topic {topic_id} is outside this discussion scope")
        return topic

    def create_topic(self, board_id: str, title: str, body: str, author: Mapping[str, Any], kind: str,
                     ref: str = "") -> dict[str, Any]:
        if self._runtime.kind_owner(kind) != self._consumer:
            raise PermissionError(f"topic kind {kind} is outside this discussion scope")
        return self._runtime.create_topic(board_id, title, body, author, kind, ref)

    def topic(self, topic_id: str) -> dict[str, Any] | None:
        topic = self._runtime.topic(topic_id)
        return self._runtime.public_topic(topic) if topic else None

    def can_read(self, topic_id: str, account: Mapping[str, Any]) -> bool:
        return self._runtime.access(account, topic_id) is not None

    def set_locked(self, topic_id: str, locked: bool, actor: Mapping[str, Any]) -> dict[str, Any]:
        self._own(topic_id)
        return self._runtime.moderate(topic_id, actor["uuid"], locked=locked)


class BoardIn(BaseModel):
    id: str = Field(max_length=48)
    title: str = Field(max_length=400)
    description: str = Field("", max_length=4000)
    audience: Literal["members", "staff"] = "members"


class BoardUpdate(BaseModel):
    title: str | None = Field(None, max_length=400)
    description: str | None = Field(None, max_length=4000)
    audience: Literal["members", "staff"] | None = None
    archived: bool | None = None


class TopicIn(BaseModel):
    title: str = Field(max_length=800)
    body: str = Field(max_length=MAX_POST * 2)
    name_choice: Literal["real", "username"] = "real"


class ModerateIn(BaseModel):
    locked: bool | None = None
    pinned: bool | None = None


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc) or "not found") from exc
    except AlreadyExists as exc:
        raise HTTPException(409, str(exc)) from exc
    except MessageRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class DiscussionPlugin:
    manifest = MANIFEST

    def __init__(self) -> None:
        self._runtime: DiscussionRuntime | None = None

    def configure(self, registration: RegistrationContext) -> None:
        registration.register_thread_type(THREAD_TYPE, ThreadType(authorize=self._authorize, max_body=MAX_POST))
        registration.provide("discussion", self._build)

    def _build(self, services: PluginServices) -> DiscussionRuntime:
        self._runtime = DiscussionRuntime(services)
        return self._runtime

    def _authorize(self, account: Mapping[str, Any], topic_id: str) -> ThreadAccess | None:
        return self._runtime.access(account, topic_id) if self._runtime else None

    def router(self, services: PluginServices) -> APIRouter:
        runtime = self._runtime
        if runtime is None:
            raise RuntimeError("discussion services were not built")
        r = APIRouter(prefix="/api/discussion")
        verified = services.auth.require_verified()
        moderator_only = services.auth.require_permission(PERM_DISCUSSION_MODERATE)

        def visible_board(board_id: str, account: Mapping[str, Any]) -> tuple[dict[str, Any], bool, bool]:
            board = runtime.board(board_id)
            moderator, member = runtime.viewer(account)
            if board is None or not runtime.board_visible(board, moderator, member):
                raise HTTPException(404, "board not found")
            return board, moderator, member

        @r.get("/boards")
        def boards(a: dict = verified, cursor: str = Query("", max_length=2048),
                   limit: int = Query(100, ge=1, le=200)):
            moderator, member = runtime.viewer(a)
            if moderator or member:
                page, next_cursor = paginate_store(
                    services.store,
                    "discussion_boards",
                    cursor,
                    limit,
                    (PageOrder("archived", bool), PageOrder("title", str, casefold=True), PageOrder("id", str)),
                    equals={} if moderator else {"audience": "members"},
                )
            else:
                page, next_cursor = [], None
            return {
                "boards": [_board_public(board) for board in page],
                "next_cursor": next_cursor,
                "moderator": moderator,
                "member": member,
            }

        @r.post("/boards")
        def create_board(b: BoardIn, a: dict = moderator_only):
            return _call(runtime.create_board, b.id, b.title, b.description, b.audience, a["uuid"])

        @r.post("/boards/{board_id}")
        def update_board(board_id: str, b: BoardUpdate, a: dict = moderator_only):
            return _call(runtime.update_board, board_id, b.model_dump(), a["uuid"])

        @r.get("/boards/{board_id}/topics")
        def topics(board_id: str, a: dict = verified, cursor: str = Query("", max_length=2048),
                   limit: int = Query(100, ge=1, le=200)):
            board, moderator, _ = visible_board(board_id, a)
            runtime.ensure_thread_page_metadata()
            order_by = (
                PageOrder("metadata.pinned", bool, descending=True),
                PageOrder("last_at", int, descending=True),
                PageOrder("record_id", str),
            )
            threads, next_cursor = paginate_page(
                lambda after, page_limit: services.messaging.page_threads(
                    THREAD_TYPE,
                    order_by=order_by,
                    after=after,
                    limit=page_limit,
                    equals={"metadata.board": board_id},
                ),
                cursor,
                limit,
                order_by,
            )
            names: dict[str, str | None] = {}
            page = []
            for thread in threads:
                topic = runtime.topic(thread["record_id"])
                if topic is not None:
                    page.append(runtime.public_topic(topic, names, thread))
            return {"board": _board_public(board), "topics": page, "next_cursor": next_cursor,
                    "moderator": moderator, "can_post": not board.get("archived")}

        @r.post("/boards/{board_id}/topics")
        def create_topic(board_id: str, b: TopicIn, a: dict = verified):
            visible_board(board_id, a)
            return _call(runtime.create_topic, board_id, b.title, b.body, a, name_choice=b.name_choice)

        @r.get("/topics/{topic_id}")
        def topic(topic_id: str, a: dict = verified):
            access = runtime.access(a, topic_id)
            row = runtime.topic(topic_id)
            board = runtime.board(row["board"]) if row else None
            if access is None or row is None or board is None:
                raise HTTPException(404, "topic not found")
            return {"topic": runtime.public_topic(row), "board": _board_public(board), "moderator": access.staff,
                    "can_post": access.post or access.staff}

        @r.post("/topics/{topic_id}/moderate")
        def moderate(topic_id: str, b: ModerateIn, a: dict = moderator_only):
            return _call(runtime.moderate, topic_id, a["uuid"], locked=b.locked, pinned=b.pinned)

        return r
