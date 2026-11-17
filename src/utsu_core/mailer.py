from __future__ import annotations

import logging

from .mail.templates import RenderedMessage
from .mail.transport import LOCAL_HOSTS, SmtpSender, SmtpSettings, build_message

log = logging.getLogger("utsu_core.mail")


class Mailer:
    """Mail delivery interface. Override `send` to deliver mail; the base is a logging stub."""

    def send(self, to: str, subject: str, body: str) -> None:
        log.info("mail to %s: %s", to, subject)


class OutboxMailer(Mailer):
    def __init__(self) -> None:
        self.outbox: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.outbox.append((to, subject, body))


class SMTPMailer(Mailer):
    """Provider-neutral delivery over SMTP (SES, SendGrid, Office 365, Gmail relay, Postfix...).

    Configure with PORTAL_SMTP_URL=smtp[s]://user:pass@host:port and PORTAL_MAIL_FROM.
    `smtp://` requires STARTTLS for remote hosts; a loopback host may use plaintext only
    when it does not advertise STARTTLS. `smtps://` uses verified TLS.
    """

    LOOPBACK = LOCAL_HOSTS

    def __init__(self, url: str, sender: str) -> None:
        from urllib.parse import unquote, urlsplit

        u = urlsplit(url)
        if u.scheme not in {"smtp", "smtps"}:
            raise ValueError("SMTP URL scheme must be 'smtp' or 'smtps'")
        if not u.hostname:
            raise ValueError("SMTP URL must include a host")
        self.ssl, self.host = u.scheme == "smtps", u.hostname
        self.port = u.port or (465 if self.ssl else 587)
        self.user, self.password, self.sender = unquote(u.username or ""), unquote(u.password or ""), sender
        self.settings = SmtpSettings(
            host=self.host,
            from_addr=sender,
            port=self.port,
            security="ssl" if self.ssl else "starttls",
            username=self.user,
            password=self.password,
            timeout=20.0,
        )

    def send(self, to: str, subject: str, body: str) -> None:
        msg = build_message(self.settings, to, RenderedMessage(subject, body))
        with SmtpSender(self.settings, loopback_hosts=self.LOOPBACK) as sender:
            sender.send(msg)


def mailer_from_env() -> Mailer:
    import os

    from .settings import env_secret

    url = env_secret("PORTAL_SMTP_URL")
    return SMTPMailer(url, os.environ.get("PORTAL_MAIL_FROM", "no-reply@localhost")) if url else Mailer()
