"""testing/seed_portal.py is an external client: it must work against a portal it can only reach over HTTP."""
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from core import create_app
from core.mailer import SMTPMailer
from core.services.roster import get_trunc_id
from core.settings import Settings
from senate.module import PREFERENCES

TESTING = Path(__file__).resolve().parents[2] / "testing"
ROSTER = 500  # the generator's minimum: its SABP export needs that many students


@pytest.fixture
def seed_portal(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTING))
    import seed_portal as module

    return module


@pytest.fixture
def sink(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTING))
    from mail_sink import MailSink

    with MailSink(smtp_port=0, http_port=0) as running:
        yield running


class RunningPortal:
    def __init__(self, sink, plugins=None):
        settings = Settings(secret="s" * 40, dev_admin=True, rate_limit_auth=0, api_only=True, enabled_plugins=plugins)
        mailer = SMTPMailer(f"smtp://127.0.0.1:{sink.smtp_port}", "portal@dev-local.test")
        self.app = create_app(settings, mailer=mailer)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="warning"))
        self.server.install_signal_handlers = lambda: None
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.sink = sink

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            assert time.monotonic() < deadline, "the portal did not start"
            time.sleep(0.05)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url, self.mail_url = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{self.sink.http_port}"
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def store(self):
        return self.app.state.ctx.store

    def run(self, seed_portal, *args):
        return seed_portal.main(["--url", self.url, "--mail-url", self.mail_url, "--students", str(ROSTER), *args])

    def submissions(self, form):
        return self.store.list("submissions", form_id=form)

    def students(self):
        return [user for user in self.store.list("users") if user["email"].startswith("student+")]


def test_seeds_students_and_every_program_over_http(seed_portal, sink, capsys):
    with RunningPortal(sink) as portal:
        assert portal.run(seed_portal, "--per-program", "3") == 0
        for form in ("agm_rsvp", "senate_application", "student_aid_bursary"):
            assert len(portal.submissions(form)) == 3, form
        assert portal.store.get("roster_meta", "current")["count"] == ROSTER
        students = portal.students()
        assert 3 <= len(students) <= 9
        assert all(portal.store.get("accounts", user["uuid"])["status"] == "verified" for user in students)
        assert len(portal.store.list("sap_banking")) + len(portal.store.list("financial_profiles")) >= 1
        assert "3 submitted" in capsys.readouterr().out

        accounts_before = len(portal.store.list("accounts"))
        # A second run refuses to overwrite the roster unless told it is the same data...
        assert portal.run(seed_portal, "--per-program", "3") == 1
        assert "already has a roster" in capsys.readouterr().err
        # ...and is then idempotent: same accounts, same submissions.
        assert portal.run(seed_portal, "--per-program", "3", "--skip-roster") == 0
        assert len(portal.store.list("accounts")) == accounts_before
        assert len(portal.submissions("senate_application")) == 3
        assert "0 new accounts" in capsys.readouterr().out


def test_skips_programs_that_are_not_enabled_and_needs_no_agm(seed_portal, sink, capsys):
    with RunningPortal(sink, plugins=("senate",)) as portal:
        assert portal.run(seed_portal, "--per-program", "3") == 0
        output = capsys.readouterr().out
        assert "Skipping agm" in output and "Skipping sabp" in output
        assert len(portal.submissions("senate_application")) == 3
        assert portal.submissions("agm_rsvp") == [] and portal.submissions("student_aid_bursary") == []


def test_refuses_a_non_local_portal_without_contacting_it(seed_portal, capsys):
    assert seed_portal.main(["--url", "https://portal.example.org"]) == 1
    assert "not a local address" in capsys.readouterr().err


def test_reports_when_the_portal_is_not_a_dev_instance(seed_portal, sink, capsys):
    settings = Settings(secret="s" * 40, rate_limit_auth=0, api_only=True)  # no PORTAL_DEV_ADMIN
    mailer = SMTPMailer(f"smtp://127.0.0.1:{sink.smtp_port}", "portal@dev-local.test")
    server = uvicorn.Server(uvicorn.Config(create_app(settings, mailer=mailer), host="127.0.0.1", port=0,
                                           log_level="warning"))
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        assert seed_portal.main(["--url", f"http://127.0.0.1:{port}", "--code-timeout", "1",
                                 "--mail-url", f"http://127.0.0.1:{sink.http_port}"]) == 1
        assert "PORTAL_DEV_ADMIN=1" in capsys.readouterr().err
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("number", ["1006639715", "xxxxx3971x", "3971", "12ab", "", "  1006 6397 15 ", "short"])
def test_student_digits_match_the_portals_roster_rules(seed_portal, number):
    assert seed_portal.student_digits(number) == get_trunc_id(number)


def test_senate_preference_fields_come_from_the_live_form_and_match_the_server(seed_portal):
    client = TestClient(create_app(Settings(secret="s" * 40, api_only=True)))
    definition = client.get("/api/forms/senate_application/static").json()
    assert seed_portal.preference_fields(definition) == PREFERENCES


def test_names_compare_without_case_spacing_or_typographic_punctuation(seed_portal):
    assert seed_portal.fold("  Mary\u2013Jane   O\u2019NEIL ") == seed_portal.fold("mary-jane o'neil")
