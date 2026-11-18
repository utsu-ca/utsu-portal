"""Registry for program-owned staff notice types."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..contracts import NotificationType
from ..mail.templates import TemplateError, load_live_template
from .forms import FormRegistry

_NOTICE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class RegisteredNotificationType:
    owner: str
    identifier: str
    definition: NotificationType


class NotificationTypeRegistry:
    def __init__(self, forms: FormRegistry) -> None:
        self._forms = forms
        self._types: dict[str, RegisteredNotificationType] = {}
        self._frozen = False

    def register(self, owner: str, identifier: str, definition: NotificationType) -> None:
        if self._frozen:
            raise RuntimeError("notification type registry is frozen")
        if not _NOTICE_ID.fullmatch(identifier):
            raise ValueError(f"invalid notification type id {identifier!r}")
        if identifier in self._types:
            raise ValueError(f"notification type {identifier} is already registered")
        if self._forms.owner(definition.form_id) != owner:
            raise ValueError(f"{owner} does not own form {definition.form_id}")
        try:
            load_live_template(definition.email_template)
        except TemplateError as exc:
            raise ValueError(f"unknown live email template {definition.email_template}") from exc
        self._types[identifier] = RegisteredNotificationType(owner, identifier, definition)

    def freeze(self) -> None:
        self._frozen = True

    def get(self, identifier: str) -> RegisteredNotificationType | None:
        return self._types.get(identifier)

    def registered(self) -> tuple[RegisteredNotificationType, ...]:
        return tuple(self._types[key] for key in sorted(self._types))
