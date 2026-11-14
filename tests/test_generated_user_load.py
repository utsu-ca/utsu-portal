"""Exercise portal workflows with generated users, without touching an external service or database."""

import csv
import io
import os
from pathlib import Path

from fastapi.testclient import TestClient

from test_portal import login, register, scan
from utsu_core import create_app
from utsu_core.mailer import OutboxMailer
from utsu_core.settings import Settings


def test_generated_users_agm_workflow_at_scale(tmp_path, monkeypatch):
    count = int(os.environ.get("UTSU_FAKE_USER_COUNT", "24"))
    assert 1 <= count <= 2000, "UTSU_FAKE_USER_COUNT must be between 1 and 2000"
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "testing"))
    import generate_fake_agm
    import generate_fake_data
    import generate_fake_student_data

    roster_path = tmp_path / "roster.csv"
    agm_path = tmp_path / "agm.csv"
    monkeypatch.setattr(generate_fake_student_data, "OUT", roster_path)
    monkeypatch.setattr(generate_fake_agm, "OUT", agm_path)
    monkeypatch.setattr(generate_fake_agm, "load_students", lambda: generate_fake_data.load_students(roster_path))
    generate_fake_student_data.generate(size=max(500, count * 2))
    generate_fake_agm.generate(rows=max(500, count * 2), seed=7)
    students = generate_fake_data.load_students(roster_path)
    by_name = {(student["first"], student["last"]): student for student in students}
    with agm_path.open(encoding="utf-8-sig", newline="") as stream:
        applications = list(csv.DictReader(stream))
    selected = {}
    for row in applications:
        key = (row["ACORN-registered First Name"], row["ACORN-registered Last Name "])
        if key in by_name:
            selected[key] = row
        if len(selected) == count:
            break
    assert len(selected) == count

    mail = OutboxMailer()
    app = create_app(Settings(secret="s" * 32, admin_emails=("admin@example.test",),
                              rate_limit_auth=0), mailer=mail)
    with TestClient(app) as client:
        register(client, mail, "loadadmin", "admin@example.test", sn="9999999999", first="Load", last="Admin")
        admin = login(client, mail, "loadadmin")
        response = client.post("/api/admin/roster", json={"csv": roster_path.read_text(encoding="utf-8-sig")},
                               headers=admin)
        assert response.status_code == 200, response.text
        assert response.json()["count"] == len(students)
        form = client.get("/api/forms/agm_rsvp/static").json()
        expected = {}
        for index, (key, row) in enumerate(selected.items()):
            student = by_name[key]
            username = f"loaduser{index:05d}"
            register(client, mail, username, f"{username}@example.test",
                     sn=f"12345{student['trunc']}0", first=student["first"], last=student["last"])
            user = login(client, mail, username)
            verification = client.post("/api/verification", json=scan(
                client, user, first_name=student["first"], last_name=student["last"]), headers=user)
            assert verification.status_code == 201, verification.text
            response = client.post(f"/api/admin/verifications/{verification.json()['id']}/decision",
                                   json={"approve": True}, headers=admin)
            assert response.status_code == 200, response.text
            attendance = "In person" if row["Meeting Preference"] == generate_fake_agm.MEETING[0] else "Online"
            data = {"attendance": attendance, "division": "UTSG", "eligible": True,
                    "pronouns": row["Preferred Pronouns"], "accessibility": row["Accessibility Requirements"]}
            response = client.put("/api/forms/agm_rsvp/submission",
                                  json={"version": form["version"], "hash": form["hash"], "data": data},
                                  headers=user)
            assert response.status_code == 200, response.text
            invitation = client.get("/api/agm/invitation", headers=user)
            assert invitation.status_code == 200, invitation.text
            assert invitation.json()["mode"] == ("in_person" if attendance == "In person" else "online")
            uid = client.get("/api/me", headers=user).json()["user"]["uuid"]
            expected[uid] = (f"{student['first']} {student['last']}", attendance, data["accessibility"])

        collected = {}
        cursor = ""
        while True:
            response = client.get("/api/admin/agm/rsvps", params={"limit": 17, "cursor": cursor}, headers=admin)
            assert response.status_code == 200, response.text
            page = response.json()
            for item in page["items"]:
                assert item["uuid"] not in collected
                assert item["eligible"] and not item["checked_in"]
                collected[item["uuid"]] = (item["name"], item["attendance"], item["accessibility"])
            if page["next_cursor"] is None:
                break
            assert page["next_cursor"] > cursor
            cursor = page["next_cursor"]
        assert collected == expected

        uid = next(iter(expected))
        first = client.post("/api/admin/agm/checkin", json={"uuid": uid}, headers=admin)
        second = client.post("/api/admin/agm/checkin", json={"uuid": uid}, headers=admin)
        assert first.status_code == second.status_code == 200
        assert not first.json()["already_checked_in"] and second.json()["already_checked_in"]
        response = client.get("/api/admin/agm/rsvps.csv", headers=admin)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        exported = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert len(exported) == count
        assert {row["uuid"] for row in exported} == set(expected)
        assert sum(row["checked_in"] == "True" for row in exported) == 1
