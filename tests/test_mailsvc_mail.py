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


TOKEN = "service-token-for-mail-tests"


def make_client(tmp_path: Path, mailer: Mailer) -> tuple[MailModule, TestClient]:
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
    app.include_router(module.router(SimpleNamespace(settings=SimpleNamespace(service_token=TOKEN))))
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
        assert "connection lost" in record.last_error

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
