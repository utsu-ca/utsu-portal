import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, formatdate

from core.mail.recipients import valid_email
from core.mail.templates import RenderedMessage

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SECURITY_MODES = ("starttls", "ssl", "none")


@dataclass
class SmtpSettings:
    """Connection and sender settings. The password is never stored in config; see `password`."""
    host: str
    from_addr: str
    port: int = 587
    security: str = "starttls"
    username: str = ""
    password: str = ""
    from_name: str = ""
    reply_to: str = ""
    timeout: float = 30.0
    delay: float = 0.5

    @classmethod
    def from_config(cls, cfg: dict) -> "SmtpSettings":
        """Build settings from the `smtp` section of config.json (unknown keys, including any password, are ignored)."""
        known = {k: v for k, v in (cfg or {}).items() if k in cls.__dataclass_fields__ and k != "password"}
        missing = [k for k in ("host", "from_addr") if not known.get(k)]
        if missing:
            raise ValueError(f"SMTP config is missing: {', '.join(missing)} (set them in the 'smtp' section of the config file)")
        return cls(**known)

    def validate(self):
        if self.security not in SECURITY_MODES:
            raise ValueError(f"smtp security must be one of {SECURITY_MODES}, got '{self.security}'")
        # Preserve the portal mailer's legacy local sender fallback.
        if self.from_addr != "no-reply@localhost" and not valid_email(self.from_addr):
            raise ValueError(f"Invalid from_addr '{self.from_addr}'")
        if self.reply_to and not valid_email(self.reply_to):
            raise ValueError(f"Invalid reply_to '{self.reply_to}'")
        if self.security == "none" and self.host.lower() not in LOCAL_HOSTS:
            raise ValueError("Refusing to send SMTP without TLS to a remote host")


def build_message(settings: SmtpSettings, to_addr: str, rendered: RenderedMessage) -> EmailMessage:
    if not valid_email(to_addr):
        raise ValueError(f"Invalid recipient address {to_addr!r}")
    msg = EmailMessage()
    msg["From"] = formataddr((settings.from_name, settings.from_addr))
    msg["To"] = to_addr
    msg["Subject"] = rendered.subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.from_addr.rpartition("@")[2])
    if settings.reply_to:
        msg["Reply-To"] = settings.reply_to
    msg.set_content(rendered.text)
    if rendered.html is not None:
        msg.add_alternative(rendered.html, subtype="html")
    return msg


class SmtpSender:
    """Context manager around one SMTP session.

    A disconnected send is not retried: the server may have accepted the message
    before the connection was lost, so retrying it could deliver a duplicate.
    """

    def __init__(self, settings: SmtpSettings, loopback_hosts: set[str] | None = None):
        settings.validate()
        self.settings = settings
        self.loopback_hosts = LOCAL_HOSTS if loopback_hosts is None else loopback_hosts
        self._client: smtplib.SMTP | None = None

    def __enter__(self) -> "SmtpSender":
        self._connect()
        return self

    def __exit__(self, *exc):
        self._close()

    def _connect(self):
        s = self.settings
        context = ssl.create_default_context()
        if s.security == "ssl":
            client = smtplib.SMTP_SSL(s.host, s.port, timeout=s.timeout, context=context)
        else:
            client = smtplib.SMTP(s.host, s.port, timeout=s.timeout)
        try:
            client.ehlo()
            if s.security == "starttls":
                if client.has_extn("starttls"):
                    client.starttls(context=context)
                    client.ehlo()
                elif s.host.lower() not in self.loopback_hosts:
                    raise smtplib.SMTPNotSupportedError("STARTTLS is required for remote SMTP delivery")
            if s.username:
                client.login(s.username, s.password)
        except Exception:
            client.close()
            raise
        self._client = client

    def _close(self):
        if self._client is not None:
            try:
                self._client.quit()
            except smtplib.SMTPException:
                self._client.close()
            self._client = None

    def send(self, msg: EmailMessage):
        if self._client is None:
            self._connect()
        try:
            self._client.send_message(msg)
        except smtplib.SMTPServerDisconnected:
            self._close()
            raise
