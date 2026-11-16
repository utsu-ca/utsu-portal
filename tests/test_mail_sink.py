import json
import smtplib
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utsu_core import create_app
from utsu_core.mailer import SMTPMailer, mailer_from_env
from utsu_core.settings import Settings


@pytest.fixture
def sink(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "testing"))
    from mail_sink import MailSink

    with MailSink(smtp_port=0, http_port=0, reject=r"@bounce\.test$") as s:
        yield s


def get_json(sink, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{sink.http_port}{path}") as r:
        return json.load(r)


def test_portal_sign_up_code_arrives_in_the_sink(sink, monkeypatch):
    monkeypatch.setenv("PORTAL_SMTP_URL", f"smtp://user:p%40ss@127.0.0.1:{sink.smtp_port}")
    monkeypatch.setenv("PORTAL_MAIL_FROM", "portal@example.local")
    c = TestClient(create_app(Settings(secret="s" * 32), mailer=mailer_from_env()))
    r = c.post("/api/auth/register", json={"username": "ada", "email": "ada@x.ca", "first_name": "Ada",
                                           "last_name": "Lovelace", "student_number": "1234567890"})
    assert r.status_code == 201, r.text

    msg = get_json(sink, "/messages/latest?to=ada@x.ca")
    assert msg["from"] == "portal@example.local" and msg["subject"] == "Your UTSU verification code"
    code = msg["text"].split("code is ")[1][:6]
    assert c.post("/api/auth/verify-email", json={"username": "ada", "code": code}).status_code == 200
    assert get_json(sink, "/messages?to=nobody@x.ca") == []


def test_rejected_recipient_fails_and_nothing_is_kept(sink):
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        SMTPMailer(f"smtp://127.0.0.1:{sink.smtp_port}", "p@example.local").send("x@bounce.test", "s", "b")
    assert sink.messages == []


def test_remote_hosts_still_require_starttls(sink, monkeypatch):
    monkeypatch.setattr(SMTPMailer, "LOOPBACK", set())
    with pytest.raises(smtplib.SMTPNotSupportedError):
        SMTPMailer(f"smtp://127.0.0.1:{sink.smtp_port}", "p@example.local").send("a@x.ca", "s", "b")
    assert sink.messages == []


def test_dot_stuffing_and_clear(sink):
    SMTPMailer(f"smtp://127.0.0.1:{sink.smtp_port}", "p@example.local").send("a@x.ca", "s", ".hidden\n..two\n")
    assert sink.messages[0].summary()["text"] == ".hidden\n..two\n"
    req = urllib.request.Request(f"http://127.0.0.1:{sink.http_port}/messages", method="DELETE")
    assert urllib.request.urlopen(req).status == 204
    assert sink.messages == []
