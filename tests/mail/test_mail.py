import csv
import random
import sqlite3
from pathlib import Path

import pytest

from core.mail import campaign, transport
from core.mail.notices import NAME_FIELD, NUMBER_FIELD, build_notices, diagnose, write_notices
from core.mail.recipients import read_csv as read_csv_rows, read_sql, split_by_validity
from core.mailer import SMTPMailer
from core.resources import package_text, schema_dirs
from core.mail.schema import schema_fields
from core.mail.templates import (
    RenderedMessage,
    Template,
    TemplateError,
    list_live_templates,
    list_templates,
    load_live_template,
    load_template,
    parse_template,
)
from core.std.utils import student_uuid_for

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = transport.SmtpSettings(host="localhost", from_addr="elections@utsu.ca", from_name="UTSU", security="none", delay=0)


def test_render_and_missing():
    t = Template("t", "Hi {{ full_name }}", "Dear {{full_name}} of {{ division }}", "<p>{{ full_name }}</p>")
    out = t.render({"full_name": "A <b>", "division": "UC"})
    assert out.subject == "Hi A <b>" and out.html == "<p>A &lt;b&gt;</p>"
    assert t.missing_fields({"full_name": "x"}) == ["division"]
    with pytest.raises(TemplateError):
        t.render({"full_name": "x"})


def test_subject_cannot_inject_headers():
    t = Template("t", "Hi {{ n }}", "body")
    assert "\n" not in t.render({"n": "a\r\nBcc: x@y.ca"}).subject


def test_parse_requires_subject():
    with pytest.raises(TemplateError):
        parse_template("x", "no header here")
    with pytest.raises(TemplateError):
        parse_template("x", "From: a\n\nbody")


def test_bundled_templates_only_use_schema_or_form_fields():
    allowed = {"full_name", "division", "id"}
    for name in ("senate_verified", "senate_unverified"):
        assert load_template(name, ROOT / "templates" / "email").fields <= allowed


def test_packaged_historical_templates_load_outside_the_repository(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    template = load_template("senate_more_info")
    assert "application_uuid" in template.fields
    assert "Please reply to this email" in template.text
    assert "senate_more_info" in list_templates()


def test_live_email_templates_are_separate_from_historical_campaign_templates():
    assert "verification_code" in list_live_templates()
    assert "verification_code" not in list_templates()
    rendered = load_live_template("verification_code").render(
        {"code": "123456", "expires_minutes": 10}
    )
    assert rendered.subject == "Your UTSU verification code"
    assert rendered.text == "Your code is 123456. It expires in 10 minutes.\n"


def test_notice_templates_are_tracked_and_use_known_fields():
    tracking = {"application_uuid", "student_uuid", "submission_ts"}
    extra = {"more_info": {"unverified_fields"}, "rejection": {"rejection_reason"}}
    for program, own in (("senate", {"full_name", "division"}), ("sabp", {"full_name", "terms"}),
                         ("agm", {"full_name"})):
        for kind, kind_fields in extra.items():
            t = load_template(f"{program}_{kind}", ROOT / "templates" / "email")
            assert tracking <= t.fields and kind_fields <= t.fields
            assert t.fields <= tracking | kind_fields | own
            assert "{{ application_uuid }}" in t.subject


def test_schema_fields_reads_sql_files():
    fields = schema_fields(schema_dirs("core", "core.programs"))
    assert "first_name" in fields["uoft_data"]
    assert {"senate_application", "student_aid", "agm_rsvp"} <= set(fields)  # every program's schema is found


def test_split_by_validity():
    rows = [{"email": "a@b.ca"}, {"email": "A@b.ca"}, {"email": "bad"}, {"email": ""}]
    kept, skipped = split_by_validity(rows, "email")
    assert kept == [rows[0]] and [n for n, _ in skipped] == [2, 3, 4]


def test_read_sql_is_readonly(tmp_path):
    import sqlite3
    db = tmp_path / "d.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("create table t (a text)")
    conn.execute("insert into t values ('x')")
    conn.commit()
    conn.close()
    assert read_sql(db, "select a as email from t") == [{"email": "x"}]
    with pytest.raises(ValueError):
        read_sql(db, "delete from t")


def test_review_requires_exact_confirmation():
    ready = [campaign.Prepared("a@b.ca", Template("t", "s", "b").render({}))]
    answers = iter(["yes", "r", "send"])
    out = []
    assert campaign.review(SETTINGS, ready, [], lambda i: None, lambda _: next(answers), out.append, random.Random(1))
    assert not campaign.review(SETTINGS, ready, [], lambda i: None, lambda _: "n", out.append)
    assert not campaign.review(SETTINGS, ready, [], lambda i: None, lambda _: (_ for _ in ()).throw(EOFError), out.append)


def test_shared_smtp_sender_does_not_retry_an_uncertain_send(monkeypatch):
    class DisconnectingSMTP:
        sends = 0

        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def send_message(self, msg):
            type(self).sends += 1
            raise transport.smtplib.SMTPServerDisconnected("connection lost after DATA")

        def quit(self):
            raise transport.smtplib.SMTPServerDisconnected("connection closed")

        def close(self):
            pass

    monkeypatch.setattr(transport.smtplib, "SMTP", DisconnectingSMTP)
    with transport.SmtpSender(SETTINGS) as sender:
        with pytest.raises(transport.smtplib.SMTPServerDisconnected):
            sender.send(transport.build_message(SETTINGS, "a@b.ca", RenderedMessage("s", "b")))
    assert DisconnectingSMTP.sends == 1


def test_remote_smtp_without_tls_is_rejected():
    settings = transport.SmtpSettings(host="smtp.example.com", from_addr="elections@utsu.ca", security="none")
    with pytest.raises(ValueError, match="without TLS"):
        settings.validate()


def test_smtp_mailer_uses_shared_transport_and_decodes_credentials(monkeypatch):
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout, context):
            seen.update(host=host, port=port, context=context)

        def ehlo(self):
            pass

        def login(self, username, password):
            seen["credentials"] = (username, password)

        def send_message(self, message):
            seen["message"] = message

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(transport.smtplib, "SMTP_SSL", FakeSMTP)
    mailer = SMTPMailer("smtps://us%40er:p%3Ass@smtp.example.com:2465", "elections@utsu.ca")
    mailer.send("ada@example.com", "Subject", "Body")

    assert seen["credentials"] == ("us@er", "p:ss")
    assert (seen["host"], seen["port"]) == ("smtp.example.com", 2465)
    assert seen["message"]["From"] == "elections@utsu.ca"
    assert seen["message"]["To"] == "ada@example.com"
    assert seen["message"].get_content() == "Body\n"


def test_portal_default_sender_remains_accepted_by_shared_transport():
    transport.SmtpSettings(host="localhost", from_addr="no-reply@localhost").validate()


def test_smtps_uses_verified_tls(monkeypatch):
    import ssl

    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout, context):
            captured["context"] = context

        def ehlo(self):
            pass

        def send_message(self, msg):
            pass

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(transport.smtplib, "SMTP_SSL", FakeSMTP)
    settings = transport.SmtpSettings(host="smtp.example.com", from_addr="elections@utsu.ca", security="ssl")
    with transport.SmtpSender(settings) as sender:
        sender.send(transport.build_message(settings, "a@b.ca", RenderedMessage("s", "b")))
    assert captured["context"].verify_mode == ssl.CERT_REQUIRED
    assert captured["context"].check_hostname


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k): pass
    def ehlo(self): pass
    def quit(self): pass
    def close(self): pass
    def send_message(self, msg): FakeSMTP.sent.append(msg)


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_send_all_logs_and_resumes(monkeypatch, tmp_path):
    monkeypatch.setattr(transport.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    t = Template("t", "Hi {{ n }}", "body {{ n }}")
    ready, skipped = campaign.prepare(t, [{"email": "a@b.ca", "n": "A"}, {"email": "c@d.ca", "n": ""}], "email")
    assert len(ready) == 1 and len(skipped) == 1
    log = tmp_path / "log.csv"
    assert campaign.send_all(SETTINGS, ready, log)["sent"] == 1
    assert FakeSMTP.sent[0]["To"] == "a@b.ca" and FakeSMTP.sent[0]["Subject"] == "Hi A"
    assert campaign.already_sent(log) == {"a@b.ca"}


def make_uoft_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(package_text("core", "schema/uoft_data.sql"))
    conn.executemany(
        "INSERT INTO uoft_data (uuid, trunc_id, first_name, last_name, full_name, division, faculty) "
        "VALUES (?, ?, 'f', 'l', ?, 'UC', 'A&S')",
        [("u-ada", 3971, "Ada Lovelace"), ("u-sam1", 1111, "Sam Lee"), ("u-sam2", 2222, "Sam Lee")])
    conn.commit()
    conn.close()


def test_diagnose_names_the_unverified_fields(tmp_path):
    make_uoft_db(tmp_path / "db.sqlite")
    cur = sqlite3.connect(tmp_path / "db.sqlite").cursor()
    ada = {"full_name": "Ada Lovelace", "id": "1006639715", "email": "ada@x.ca"}
    ada_id = student_uuid_for("ada@x.ca")  # the internal student id comes from the email, verified or not

    assert diagnose(ada, cur) == (ada_id, [])
    assert diagnose({**ada, "id": "1000000009"}, cur) == (ada_id, [NUMBER_FIELD])
    assert diagnose({**ada, "full_name": "Adda Lovelace"}, cur) == (ada_id, [NAME_FIELD])
    assert diagnose({"full_name": "Zed Nobody", "id": "1009999999", "email": "z@x.ca"}, cur) == \
        (student_uuid_for("z@x.ca"), [NAME_FIELD])
    # only a name: a shared name needs a student number, an unknown name needs a better name
    assert diagnose({"full_name": "Sam Lee", "email": "s@x.ca"}, cur) == (student_uuid_for("s@x.ca"), [NUMBER_FIELD])
    assert diagnose({"full_name": "Nobody", "email": "N@x.ca"}, cur) == (student_uuid_for("n@x.ca"), [NAME_FIELD])
    # a student number alone matches nobody, so the name is what is asked for
    assert diagnose({"id": "1000011119", "email": "s1@x.ca"}, cur) == (student_uuid_for("s1@x.ca"), [NAME_FIELD])
    # no email: the student number identifies them
    assert diagnose({"id": "1000099999"}, cur)[0] == student_uuid_for(student_number="1000099999")
    assert diagnose({"email": "e@x.ca"}, cur)[1] == [NAME_FIELD]


def test_notices_to_tracked_email(monkeypatch, tmp_path):
    monkeypatch.setattr(transport.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    make_uoft_db(tmp_path / "db.sqlite")
    applications = tmp_path / "unverified.csv"
    write_csv(applications, [
        {"uuid": "app-1", "submission_ts": "2026/09/12 3:19:43 p.m. AST", "full_name": "Ada Lovelace",
         "id": "1000000009", "email": "ada@x.ca", "division": "UC"},
        {"uuid": "app-2", "submission_ts": "2026/09/13 1:00:00 p.m. AST", "full_name": "Ada Lovelace",
         "id": "1006639715", "email": "ok@x.ca", "division": "UC"}])
    out = tmp_path / "notices.csv"
    with sqlite3.connect(tmp_path / "db.sqlite") as conn:
        notices = build_notices(read_csv_rows(applications), conn.cursor(), "more_info", program="senate")
    write_notices(out, notices)

    ada_id = student_uuid_for("ada@x.ca")
    rows = read_csv_rows(out)
    assert [r["application_uuid"] for r in rows] == ["app-1"]  # app-2 is fully matched, so nothing to ask
    assert rows[0]["student_uuid"] == ada_id and rows[0]["unverified_fields"] == NUMBER_FIELD

    ready, skipped = campaign.prepare(load_template("senate_more_info", ROOT / "templates" / "email"), rows, "email")
    assert not skipped and "[Ref app-1]" in ready[0].rendered.subject
    assert f"Student ID:     {ada_id}" in ready[0].rendered.text and NUMBER_FIELD in ready[0].rendered.text

    log = tmp_path / "log.csv"
    assert campaign.send_all(SETTINGS, ready, log)["sent"] == 1
    logged = read_csv_rows(log)
    assert (logged[0]["application_uuid"], logged[0]["student_uuid"]) == ("app-1", ada_id)

    with sqlite3.connect(tmp_path / "db.sqlite") as conn:
        rejected = build_notices(read_csv_rows(applications), conn.cursor(), "rejection", "Not eligible", "senate")
    assert {r["rejection_reason"] for r in rejected} == {"Not eligible"}


def test_notice_validation(tmp_path):
    make_uoft_db(tmp_path / "db.sqlite")
    with sqlite3.connect(tmp_path / "db.sqlite") as conn:
        with pytest.raises(ValueError, match="needs a reason"):
            build_notices([{"uuid": "app"}], conn.cursor(), "rejection")
        with pytest.raises(ValueError, match="Unknown notice kind"):
            build_notices([], conn.cursor(), "other")
