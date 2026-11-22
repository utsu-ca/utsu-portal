import smtplib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Enum as SQLEnum, MetaData, create_engine

from utsu_core.mailer import Mailer, OutboxMailer
from utsu_core.mailsvc import EmailRecord, EmailStatus, MailModule
from utsu_core.ratelimit import RateLimiter


TOKEN = "service-token-for-mail-tests"


def make_client(
    tmp_path: Path,
    mailer: Mailer,
    service_token: str = TOKEN,
    *,
    rate_limit_auth: int = 30,
    mail_recipient_rate_limit: int = 5,
) -> tuple[MailModule, TestClient]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{tmp_path / 'emails.sqlite'}"
    metadata = MetaData()
    existing_table = EmailRecord.__table__.to_metadata(metadata)
    existing_table.c.status.type = SQLEnum(
        "PENDING", "SENT", "FAILED", "BOUNCED", "DEFERRED", name="emailstatus", create_constraint=True
    )
    engine = create_engine(db_url)
    metadata.create_all(engine)
    engine.dispose()
    module = MailModule(mailer, db_url)
    app = FastAPI()
    settings = SimpleNamespace(
        service_token=service_token,
        secret="s" * 40,
        rate_limit_auth=rate_limit_auth,
        mail_recipient_rate_limit=mail_recipient_rate_limit,
        rate_window=60,
    )
    app.include_router(module.router(SimpleNamespace(settings=settings, limiter=RateLimiter())))
    return module, TestClient(app)


def test_low_priority_email_stays_pending_without_an_automatic_worker(tmp_path):
    class RecordingMailer(Mailer):
        sent = []

        def send(self, to, subject, body):
            self.sent.append((to, subject, body))

    mailer = RecordingMailer()
    module, client = make_client(tmp_path, mailer)
    response = client.post(
        "/mail/queue",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"to": "ada@example.com", "subject": "Hello", "body": "Welcome", "priority": 0},
    )

    assert response.status_code == 200
    assert response.json()["status"] == EmailStatus.PENDING.value
    assert mailer.sent == []
    with module.Session() as session:
        record = session.query(EmailRecord).filter_by(id=response.json()["email_id"]).one()
        assert record.status == EmailStatus.PENDING


def test_email_request_and_template_output_are_bounded(tmp_path):
    module, client = make_client(tmp_path, OutboxMailer())
    headers = {"Authorization": f"Bearer {TOKEN}"}

    oversized = client.post(
        "/mail/queue",
        headers=headers,
        json={"to": "ada@example.com", "subject": "Hello", "body": "x" * 8193},
    )
    missing_template_value = client.post(
        "/mail/queue",
        headers=headers,
        json={
            "to": "ada@example.com",
            "subject": "",
            "body": "",
            "template_id": "verify_email",
            "template_vars": {},
        },
    )
    assert oversized.status_code == 422
    assert missing_template_value.status_code == 400
    with module.Session() as session:
        assert session.query(EmailRecord).count() == 0


def test_live_generic_notice_copy_is_not_replaced_by_historical_templates(tmp_path):
    class RecordingMailer(Mailer):
        def __init__(self):
            self.sent = []

        def send(self, to, subject, body):
            self.sent.append((to, subject, body))

    mailer = RecordingMailer()
    _, client = make_client(tmp_path, mailer)
    response = client.post(
        "/mail/queue",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "to": "ada@example.com",
            "template_id": "welcome",
            "template_vars": {"first_name": "Ada"},
            "subject": "",
            "body": "",
            "priority": 10,
        },
    )

    assert response.status_code == 200
    assert mailer.sent == [
        ("ada@example.com", "Welcome Ada", "Hi Ada,\n\nWelcome to the portal!\n\nBest regards,\nUTSU")
    ]


def test_unknown_delivery_is_never_retried_by_the_mail_api(tmp_path):
    class DisconnectingMailer(Mailer):
        def __init__(self):
            self.calls = 0

        def send(self, to, subject, body):
            self.calls += 1
            raise smtplib.SMTPServerDisconnected("connection lost after DATA")

    mailer = DisconnectingMailer()
    module, client = make_client(tmp_path, mailer)
    response = client.post(
        "/mail/queue",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"to": "ada@example.com", "subject": "Hello", "body": "Welcome", "priority": 10},
    )

    assert response.status_code == 200
    email_id = response.json()["email_id"]
    with module.Session() as session:
        record = session.query(EmailRecord).filter_by(id=email_id).one()
        assert record.status == EmailStatus.FAILED
        assert record.attempt_count == 1
        assert "Delivery outcome is unknown" in record.last_error
        assert "connection lost" not in record.last_error
        assert "ada@example.com" not in record.last_error

    retry = client.post(f"/mail/retry/{email_id}", headers={"Authorization": f"Bearer {TOKEN}"})
    assert retry.status_code == 409
    assert "unresolved" in retry.json()["detail"]
    assert mailer.calls == 1


def test_pending_email_can_be_claimed_only_once(tmp_path):
    mailer = OutboxMailer()
    module, client = make_client(tmp_path, mailer)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    queued = client.post(
        "/mail/queue", headers=headers,
        json={"to": "ada@example.com", "subject": "Hello", "body": "Welcome"},
    ).json()
    email_id = queued["email_id"]

    assert client.post(f"/mail/retry/{email_id}", headers=headers).status_code == 200
    assert client.post(f"/mail/retry/{email_id}", headers=headers).status_code == 409
    assert mailer.outbox == [("ada@example.com", "Hello", "Welcome")]
    with module.Session() as session:
        record = session.get(EmailRecord, email_id)
        assert record.status == EmailStatus.SENT
        assert record.attempt_count == 1


def test_interrupted_deferred_dispatch_is_not_replayed(tmp_path):
    mailer = OutboxMailer()
    module, client = make_client(tmp_path, mailer)
    with module.Session() as session:
        session.add(EmailRecord(
            id="interrupted", recipient="ada@example.com", subject="Hello", body="Welcome",
            status=EmailStatus.DEFERRED, attempt_count=1,
        ))
        session.commit()

    module._send_email("interrupted")
    response = client.post("/mail/retry/interrupted", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 409
    assert mailer.outbox == []
    with module.Session() as session:
        record = session.get(EmailRecord, "interrupted")
        assert record.status == EmailStatus.DEFERRED
        assert record.attempt_count == 1


def test_parallel_dispatch_observes_persisted_attempt_before_delivery(tmp_path):
    started, release = Event(), Event()

    class BlockingMailer(OutboxMailer):
        def send(self, to, subject, body):
            super().send(to, subject, body)
            started.set()
            assert release.wait(5)

    mailer = BlockingMailer()
    module, _ = make_client(tmp_path, mailer)
    with module.Session() as session:
        session.add(EmailRecord(
            id="claimed", recipient="ada@example.com", subject="Hello", body="Welcome",
            status=EmailStatus.DEFERRED,
        ))
        session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(module._send_email, "claimed")
        try:
            assert started.wait(5)
            pool.submit(module._send_email, "claimed").result(timeout=5)
        finally:
            release.set()
        first.result(timeout=5)

    assert mailer.outbox == [("ada@example.com", "Hello", "Welcome")]
    with module.Session() as session:
        record = session.get(EmailRecord, "claimed")
        assert record.status == EmailStatus.SENT
        assert record.attempt_count == 1


def test_pending_email_listing_is_bounded_paginated_and_minimized(tmp_path):
    module, client = make_client(tmp_path, OutboxMailer())
    with module.Session() as session:
        session.add_all([
            EmailRecord(id=f"mail-{index}", recipient=f"student{index}@example.com",
                        subject=f"Private subject {index}", body="body", status=EmailStatus.PENDING)
            for index in range(2)
        ])
        session.commit()

    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = client.get("/mail/pending?limit=1&offset=0", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1 and data["next_offset"] == 1
    assert set(data["emails"][0]) == {"id", "status", "attempts", "created_at"}
    assert "recipient" not in data["emails"][0] and "subject" not in data["emails"][0]
    assert client.get("/mail/pending?limit=1000000", headers=headers).status_code == 422


def test_delivery_status_does_not_return_recipient_subject_or_provider_error(tmp_path):
    module, client = make_client(tmp_path, OutboxMailer())
    with module.Session() as session:
        session.add(EmailRecord(
            id="private-status", recipient="student@example.com", subject="Private subject",
            body="body", status=EmailStatus.FAILED, last_error="provider error with personal data",
        ))
        session.commit()

    response = client.get(
        "/mail/status/private-status",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email_id"] == "private-status" and data["status"] == "failed"
    assert set(data) == {"email_id", "status", "created_at", "sent_at", "attempt_count"}
    assert "student@example.com" not in response.text and "Private subject" not in response.text
    assert "provider error with personal data" not in response.text


def test_mail_service_token_rotation_accepts_each_token_individually(tmp_path):
    old, new = "o" * 40, "n" * 40
    _, client = make_client(tmp_path, OutboxMailer(), f"{old},{new}")

    for token in (old, new):
        response = client.get("/mail/pending", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
    assert client.get("/mail/pending", headers={"Authorization": f"Bearer {old},{new}"}).status_code == 403


def test_mail_queue_obeys_service_and_recipient_quotas(tmp_path):
    _, service_client = make_client(
        tmp_path / "service", OutboxMailer(), rate_limit_auth=1, mail_recipient_rate_limit=10
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = {"to": "student@example.com", "subject": "Notice", "body": "Hello"}
    assert service_client.post("/mail/queue", json=payload, headers=headers).status_code == 200
    assert service_client.post(
        "/mail/queue", json={**payload, "to": "other@example.com"}, headers=headers
    ).status_code == 429

    _, recipient_client = make_client(
        tmp_path / "recipient", OutboxMailer(), rate_limit_auth=100, mail_recipient_rate_limit=1
    )
    assert recipient_client.post("/mail/queue", json=payload, headers=headers).status_code == 200
    assert recipient_client.post("/mail/queue", json=payload, headers=headers).status_code == 429
    assert recipient_client.post(
        "/mail/queue", json={**payload, "to": "other@example.com"}, headers=headers
    ).status_code == 200
