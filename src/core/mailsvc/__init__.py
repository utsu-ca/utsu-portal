"""Optional HTTP email API: `uvicorn --factory utsu_core.mailsvc:create_mail_app`.

This service records requests and can dispatch high-priority messages once through
an in-process background task. It has no durable worker, delivery guarantee, or
automatic retry. Queue and retry requests are rate limited per service and recipient.
Messages left pending require an explicit dispatch.
The existing DEFERRED state means claimed/in progress, or unresolved if interrupted.
FAILED may also mean delivery is uncertain; check the provider before creating a
replacement message. Only an unattempted PENDING request may be dispatched.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Header, BackgroundTasks, Query
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import Column, String, Integer, DateTime, Enum as SQLEnum, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from ..bootstrap import build_app
from ..context import Context
from ..mailer import Mailer, mailer_from_env
from ..settings import Settings
from ..store import Store

log = logging.getLogger("utsu_core.mail")

Base = declarative_base()


class EmailStatus(str, Enum):
    """Existing stored states; DEFERRED and FAILED must not be automatically retried."""
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    DEFERRED = "deferred"


class EmailRecord(Base):
    """Database record for email tracking and audit."""
    __tablename__ = "emails"
    
    id = Column(String(36), primary_key=True)
    recipient = Column(String(255), nullable=False, index=True)
    subject = Column(String(255), nullable=False)
    body = Column(String(8192), nullable=False)
    status = Column(SQLEnum(EmailStatus), default=EmailStatus.PENDING, index=True)
    attempt_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    sent_at = Column(DateTime, nullable=True)
    last_error = Column(String(512), nullable=True)
    context_user_id = Column(String(36), nullable=True, index=True)  # Who requested this email


class EmailRequest(BaseModel):
    """Request to queue an email."""
    to: EmailStr
    subject: str = Field(max_length=255)
    body: str = Field(max_length=8192)
    template_id: Optional[str] = Field(default=None, max_length=64)
    template_vars: dict[str, str] = Field(default_factory=dict, max_length=20)
    priority: int = Field(default=0, ge=0, le=10)

    @field_validator("template_vars")
    @classmethod
    def limit_template_vars(cls, values: dict[str, str]) -> dict[str, str]:
        if any(len(key) > 64 or len(value) > 1024 for key, value in values.items()):
            raise ValueError("template variable names and values are too long")
        return values


class EmailResponse(BaseModel):
    """Response after queueing an email."""
    email_id: str
    status: EmailStatus
    scheduled_at: datetime


class EmailStatusResponse(BaseModel):
    """Query email delivery status."""
    email_id: str
    status: EmailStatus
    created_at: datetime
    sent_at: Optional[datetime]
    attempt_count: int


class MailModule:
    """Best-effort request ledger with optional one-shot dispatch."""
    
    name = "mail"

    def __init__(self, mailer: Mailer | None = None, db_url: str | None = None):
        self.mailer = mailer or mailer_from_env()
        self.db_url = db_url or "sqlite:///./emails.db"
        self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False} if "sqlite" in self.db_url else {})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def router(self, ctx: Context) -> APIRouter:
        r = APIRouter(prefix="/mail", tags=["mail"])
        wants = tuple(t.strip() for t in (ctx.settings.service_token or "").split(",") if t.strip())
        service_key = hmac.new(ctx.settings.secret.encode(), b"mail-service", hashlib.sha256).hexdigest()

        def guard(authorization: str | None) -> None:
            """Verify service token for internal endpoints."""
            got = (authorization or "").removeprefix("Bearer ").strip()
            accepted = [hmac.compare_digest(got.encode(), want.encode()) for want in wants]
            if not got or not any(accepted):
                raise HTTPException(status_code=403, detail="Invalid service token")

        def rate_limit(key: str, limit: int) -> None:
            if not ctx.limiter.allow(key, limit, ctx.settings.rate_window):
                raise HTTPException(
                    status_code=429,
                    detail="too many requests",
                    headers={"Retry-After": str(ctx.settings.rate_window)},
                )

        def recipient_key(address: str) -> str:
            digest = hmac.new(
                ctx.settings.secret.encode(), address.casefold().encode(), hashlib.sha256
            ).hexdigest()
            return f"mail:{service_key}:recipient:{digest}"

        def render_template(template_id: str, variables: dict) -> tuple[str, str]:
            """Render live portal account notifications, separate from historical mail templates."""
            # These live generic messages are not substitutes for historical reply-based correspondence.
            templates = {
                "welcome": {
                    "subject": "Welcome {first_name}",
                    "body": "Hi {first_name},\n\nWelcome to the portal!\n\nBest regards,\nUTSU"
                },
                "verify_email": {
                    "subject": "Verify your email",
                    "body": "Click here to verify: {verification_link}"
                },
                "reset_password": {
                    "subject": "Reset your password",
                    "body": "Click here to reset: {reset_link}\n\nExpires in: {expires_in}"
                }
            }
            
            if template_id not in templates:
                raise HTTPException(status_code=400, detail=f"Unknown template: {template_id}")
            
            tmpl = templates[template_id]
            try:
                subject = tmpl["subject"].format(**variables)
                body = tmpl["body"].format(**variables)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail="Required template variables are missing") from exc
            if len(subject) > 255 or len(body) > 8192:
                raise HTTPException(status_code=422, detail="Rendered email exceeds the allowed size")
            return subject, body

        @r.post("/queue", response_model=EmailResponse)
        def queue_email(req: EmailRequest, background_tasks: BackgroundTasks, authorization: str | None = Header(None)):
            """Queue an email for sending."""
            guard(authorization)
            rate_limit(f"mail:{service_key}:writes", ctx.settings.rate_limit_auth)
            rate_limit(recipient_key(str(req.to)), ctx.settings.mail_recipient_rate_limit)
            
            session = self.Session()
            try:
                import uuid
                email_id = str(uuid.uuid4())
                
                # Use template if provided, otherwise use provided subject/body
                if req.template_id:
                    subject, body = render_template(req.template_id, req.template_vars)
                else:
                    subject, body = req.subject, req.body

                status = EmailStatus.DEFERRED if req.priority >= 10 else EmailStatus.PENDING
                record = EmailRecord(
                    id=email_id,
                    recipient=req.to,
                    subject=subject,
                    body=body,
                    status=status,
                )
                session.add(record)
                session.commit()
                
                if status == EmailStatus.DEFERRED:
                    background_tasks.add_task(self._send_email, email_id)
                
                log.info("Queued email %s", email_id)
                return EmailResponse(
                    email_id=email_id,
                    status=status,
                    scheduled_at=datetime.utcnow()
                )
            finally:
                session.close()

        @r.get("/status/{email_id}", response_model=EmailStatusResponse)
        def get_email_status(email_id: str, authorization: str | None = Header(None)):
            """Check delivery status of an email."""
            guard(authorization)
            
            session = self.Session()
            try:
                record = session.query(EmailRecord).filter_by(id=email_id).first()
                if not record:
                    raise HTTPException(status_code=404, detail="Email not found")
                
                return EmailStatusResponse(
                    email_id=record.id,
                    status=record.status,
                    created_at=record.created_at,
                    sent_at=record.sent_at,
                    attempt_count=record.attempt_count,
                )
            finally:
                session.close()

        @r.post("/retry/{email_id}")
        def retry_email(email_id: str, background_tasks: BackgroundTasks, authorization: str | None = Header(None)):
            """Dispatch an unattempted pending email; never retry an uncertain delivery."""
            guard(authorization)
            rate_limit(f"mail:{service_key}:writes", ctx.settings.rate_limit_auth)
            
            session = self.Session()
            try:
                record = session.query(EmailRecord).filter_by(id=email_id).first()
                if not record:
                    raise HTTPException(status_code=404)
                
                if record.status == EmailStatus.SENT:
                    raise HTTPException(status_code=409, detail="Email already sent")

                if record.status != EmailStatus.PENDING or record.attempt_count:
                    raise HTTPException(
                        status_code=409,
                        detail="Only unattempted pending emails can be dispatched; deferred or failed delivery "
                               "may be unresolved. Verify the provider outcome before sending a replacement",
                    )

                rate_limit(recipient_key(record.recipient), ctx.settings.mail_recipient_rate_limit)
                claimed = session.query(EmailRecord).filter_by(
                    id=email_id, status=EmailStatus.PENDING, attempt_count=0
                ).update({EmailRecord.status: EmailStatus.DEFERRED}, synchronize_session=False)
                session.commit()
                if not claimed:
                    raise HTTPException(status_code=409, detail="Email is already being dispatched")
                background_tasks.add_task(self._send_email, email_id)
                
                return {"message": "Dispatching email", "email_id": email_id}
            finally:
                session.close()

        @r.get("/pending")
        def get_pending_emails(
            limit: int = Query(100, ge=1, le=100),
            offset: int = Query(0, ge=0, le=1_000_000),
            authorization: str | None = Header(None),
        ):
            """List pending emails (for manual processing)."""
            guard(authorization)
            
            session = self.Session()
            try:
                query = session.query(EmailRecord).filter(
                    EmailRecord.status == EmailStatus.PENDING
                ).order_by(EmailRecord.created_at, EmailRecord.id)
                records = query.offset(offset).limit(limit + 1).all()
                has_more = len(records) > limit
                records = records[:limit]
                
                return {
                    "count": len(records),
                    "emails": [
                        {
                            "id": r.id,
                            "status": r.status,
                            "attempts": r.attempt_count,
                            "created_at": r.created_at.isoformat()
                        }
                        for r in records
                    ],
                    "next_offset": offset + limit if has_more else None,
                }
            finally:
                session.close()

        return r

    def _send_email(self, email_id: str):
        """Dispatch a claimed message once and preserve uncertain outcomes."""
        session = self.Session()
        try:
            record = session.query(EmailRecord).filter_by(id=email_id).first()
            if not record:
                log.error(f"Email {email_id} not found")
                return

            if record.status != EmailStatus.DEFERRED or record.attempt_count:
                return

            if type(self.mailer).send is Mailer.send:
                record.status = EmailStatus.FAILED
                record.last_error = "Email delivery is not configured"
                session.commit()
                return

            claimed = session.query(EmailRecord).filter_by(
                id=email_id, status=EmailStatus.DEFERRED, attempt_count=0
            ).update({EmailRecord.attempt_count: EmailRecord.attempt_count + 1}, synchronize_session=False)
            session.commit()
            if not claimed:
                return
            try:
                self.mailer.send(record.recipient, record.subject, record.body)
                record.status = EmailStatus.SENT
                record.sent_at = datetime.utcnow()
                log.info("Sent email %s", email_id)
            except Exception as e:
                record.status = EmailStatus.FAILED
                record.last_error = "Delivery outcome is unknown; verify provider before sending a replacement"
                log.warning("Email %s delivery outcome is unknown (%s)", email_id, type(e).__name__)

            session.commit()
        finally:
            session.close()


def create_mail_app(settings: Settings | None = None, store: Store | None = None, mailer: Mailer | None = None):
    """Create the standalone email microservice application."""
    settings = settings or Settings.from_env()
    if settings.service_token is not None and len(settings.service_token) < 32:
        raise RuntimeError("PORTAL_SERVICE_TOKEN must be at least 32 characters")
    
    active_mailer = mailer or mailer_from_env()
    mods = (MailModule(active_mailer),)
    return build_app(settings, store, active_mailer, mods, seed=False, ui=False)
