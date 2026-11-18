"""Implementation-free contracts for plug-ins that extend discussions. A consumer lists `discussion` in its manifest
`dependencies`, adds `provided` to its services and reaches the service with `registration.require("discussion")`
(configure time) or `services.provided["discussion"]` (requests). Consumers import this module, never the plug-in."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

AUDIENCES = ("members", "staff")
THREAD_TYPE = "discussion-topic"
DEFAULT_KIND = "discussion"


@dataclass(frozen=True)
class TopicKind:
    """A topic extension such as a ballot. The client renders `panel` above the thread with the topic's `ref`.
    Kind IDs are namespaced: they must equal the registering plug-in's ID or start with `{plugin}-`."""

    label: str
    panel: str


class DiscussionService(Protocol):
    def register_topic_kind(self, identifier: str, kind: TopicKind) -> None: ...
    def boards(self) -> list[dict[str, Any]]: ...
    def ensure_board(self, board_id: str, title: str, description: str = "",
                     audience: str = "members") -> dict[str, Any]: ...
    def create_topic(self, board_id: str, title: str, body: str, author: Mapping[str, Any], kind: str,
                     ref: str = "") -> dict[str, Any]: ...
    def topic(self, topic_id: str) -> dict[str, Any] | None: ...
    def can_read(self, topic_id: str, account: Mapping[str, Any]) -> bool: ...
    def set_locked(self, topic_id: str, locked: bool, actor: Mapping[str, Any]) -> dict[str, Any]: ...
