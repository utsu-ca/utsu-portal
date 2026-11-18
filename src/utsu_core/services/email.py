"""Core live-email rendering, delivery, and outcome ledger."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from typing import Any, Callable

from ..contracts import AuditService, EmailMessage
from ..mail.templates import load_live_template
from ..mailer import Mailer
from ..store import Store


class EmailDeliveryError(RuntimeError):
    pass


class EmailNotConfigured(EmailDeliveryError):
    pass


class EmailDeliveryUnknown(EmailDeliveryError):
    pass


class EmailDeliveryConflict(EmailDeliveryError):
    pass


class EmailDeliveryService:
    def __init__(self, store: Store, audit: AuditService, mailer: Mailer, secret: str,
                 now: Callable[[], int]) -> None:
        self._store = store
        self._audit = audit
        self._mailer = mailer
        self._secret = secret.encode()
        self._now = now

    @property
    def is_configured(self) -> bool:
        return self._mailer.is_configured

    @property
    def transport(self) -> Mailer:
        return self._mailer

    @transport.setter
    def transport(self, mailer: Mailer) -> None:
        self._mailer = mailer

    def render_template(self, template_id: str, params: Mapping[str, Any]) -> EmailMessage:
        rendered = load_live_template(template_id).render(params)
        return EmailMessage(subject=rendered.subject, body=rendered.text)

    def send_template(self, to: str, template_id: str, params: Mapping[str, Any],
                      dedupe_key: str | None = None, actor: str | None = None) -> str:
        if not to or "\r" in to or "\n" in to:
            raise ValueError("email recipient is invalid")
        message = self.render_template(template_id, params)
        delivery_id = self._delivery_id(dedupe_key)
        recipient_hash = hmac.new(self._secret, to.strip().lower().encode(), hashlib.sha256).hexdigest()
        record = {"id": delivery_id, "status": "sending", "recipient_hash": recipient_hash,
                  "template": template_id, "created_at": self._now()}
        if not self._store.create("email_delivery", delivery_id, record):
            raise EmailDeliveryConflict("email delivery is already recorded; do not retry")
        if not self.is_configured:
            self._store.update("email_delivery", delivery_id,
                               lambda current: {**current, "status": "failed", "error": "not_configured"})
            self._audit.record(actor, "email.delivery_failed", f"recipient:{recipient_hash}", None, None,
                               {"delivery": delivery_id, "template": template_id, "reason": "not_configured"})
            raise EmailNotConfigured("email delivery is not configured")
        try:
            self._mailer.send(to, message.subject, message.body)
        except Exception as exc:
            self._store.update("email_delivery", delivery_id,
                               lambda current: {**current, "status": "unknown"})
            self._audit.record(actor, "email.delivery_unknown", f"recipient:{recipient_hash}", None, None,
                               {"delivery": delivery_id, "template": template_id})
            raise EmailDeliveryUnknown("email delivery could not be confirmed; do not retry") from exc
        self._store.update("email_delivery", delivery_id,
                           lambda current: {**current, "status": "sent", "sent_at": self._now()})
        self._audit.record(actor, "email.sent", f"recipient:{recipient_hash}", None, None,
                           {"delivery": delivery_id, "template": template_id})
        return delivery_id

    def _delivery_id(self, dedupe_key: str | None) -> str:
        if dedupe_key is None:
            return secrets.token_hex(24)
        return hmac.new(self._secret, b"email-delivery:" + dedupe_key.encode(), hashlib.sha256).hexdigest()

