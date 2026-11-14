import csv
from pathlib import Path

from fastapi.testclient import TestClient

from utsu_core import create_app
from utsu_core.mailer import OutboxMailer
from utsu_core.settings import Settings
from test_portal import login, register, scan
from utsu_core.programs.senate.module import PREFERENCES
from utsu_core.programs.senate.seating import assign_seats, tier_index
from utsu_core.programs.sap.wfni import compute_wfni, parse_weights


def test_generated_roster_is_processed_by_portal(tmp_path, monkeypatch):
    testing = Path(__file__).resolve().parents[1] / "testing"
    monkeypatch.syspath_prepend(str(testing))
    import generate_fake_agm
    import generate_fake_data
    import generate_fake_sabp
    import generate_fake_senate
    import generate_fake_student_data

    roster = tmp_path / "fake_uoft_data.csv"
    monkeypatch.setattr(generate_fake_student_data, "OUT", roster)
    generate_fake_student_data.generate(size=500)
    with roster.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    student = rows[0]
    mail = OutboxMailer()
    client = TestClient(create_app(Settings(secret="s" * 32, admin_emails=("boss@x.ca",)), mailer=mail))
    register(client, mail, "boss", "boss@x.ca", sn="9999999999", first="Bo", last="Ss")
    admin = login(client, mail, "boss")
    imported = client.post("/api/admin/roster", json={"csv": roster.read_text(encoding="utf-8-sig")}, headers=admin)
    assert imported.status_code == 200 and imported.json()["count"] == len(rows)
    result = client.post("/api/admin/agm/verify", json={
        "full_name": f"{student['First Name'].strip()} {student['Last Name'].strip()}",
        "student_number": student["Truncated Student Number"].strip(),
    }, headers=admin)
    assert result.status_code == 200 and result.json()["verified"]

    agm_file = tmp_path / "fake_agm.csv"
    monkeypatch.setattr(generate_fake_agm, "OUT", agm_file)
    monkeypatch.setattr(generate_fake_agm, "load_students", lambda: generate_fake_data.load_students(roster))
    generate_fake_agm.generate(rows=40, seed=7)
    with agm_file.open(encoding="utf-8-sig", newline="") as stream:
        applications = list(csv.DictReader(stream))
    agm_applications = applications
    assert len(applications) >= 40
    verified = 0
    for application in applications:
        result = client.post("/api/admin/agm/verify", json={
            "full_name": f"{application['ACORN-registered First Name']} "
                         f"{application['ACORN-registered Last Name ']}",
        }, headers=admin)
        assert result.status_code == 200
        verified += result.json()["verified"]
    assert verified >= 30

    for generator in (generate_fake_senate, generate_fake_sabp):
        monkeypatch.setattr(generator, "OUT", tmp_path / generator.OUT.name)
        monkeypatch.setattr(generator, "load_students", lambda: generate_fake_data.load_students(roster))
    generate_fake_senate.generate()
    generate_fake_sabp.generate(rows=40, seed=7)
    generated = {}
    for generator, name_fields in (
        (generate_fake_senate, ("Full ACORN-registered Name",)),
        (generate_fake_sabp, ("ACORN-registered First Name", "ACORN-registered Last Name")),
    ):
        with generator.OUT.open(encoding="utf-8-sig", newline="") as stream:
            applications = list(csv.DictReader(stream))
        generated[generator] = applications
        assert len(applications) >= 40
        verified = 0
        for application in applications[:40]:
            result = client.post("/api/admin/agm/verify", json={
                "full_name": " ".join(application[field] for field in name_fields),
                "student_number": application["Student Number"],
            }, headers=admin)
            assert result.status_code == 200
            verified += result.json()["verified"]
        assert verified >= 25

    # The historical export does not contain enrolment, monthly income, or monthly expenses.
    # Supply explicit synthetic values for these portal-only required fields, not inferred CSV mappings.
    sabp = next(row for row in generated[generate_fake_sabp]
                if row["Bursary Type"] == generate_fake_sabp.BURSARY_LC
                and len(row["LC General Statement"]) >= 50
                and row["Application Semester"]
                and client.post("/api/admin/agm/verify", json={
                    "full_name": f"{row['ACORN-registered First Name']} {row['ACORN-registered Last Name']}",
                    "student_number": row["Student Number"],
                }, headers=admin).json()["verified"])
    register(client, mail, "generated", "generated@example.ca", sn=sabp["Student Number"],
             first=sabp["ACORN-registered First Name"], last=sabp["ACORN-registered Last Name"])
    user = login(client, mail, "generated")
    verification = client.post("/api/verification", json=scan(
        client, user, first_name=sabp["ACORN-registered First Name"],
        last_name=sabp["ACORN-registered Last Name"]), headers=user)
    assert verification.status_code == 201, verification.text
    assert client.post(f"/api/admin/verifications/{verification.json()['id']}/decision",
                       json={"approve": True}, headers=admin).status_code == 200
    fields = {
        "disability_mobility": "Disability Status - Mobility", "disability_invisible": "Disability Status - Invisible",
        "living_situation": "Living Situation", "disposable_income": "Access to Disposable Income",
        "liquidity": "Liquidity", "dependents": "Number of Dependents and/or Remittance members",
        "external_supports": "Access to External Supports", "circumstances": "Extenuating Circumstances",
    }
    answers = {key: sabp[header] for key, header in fields.items()}
    expected, unknown = compute_wfni(answers, parse_weights(
        (Path(__file__).resolve().parents[1] / "data/wfni_weights.csv").read_text()))
    assert not unknown
    if answers["disposable_income"].startswith("A negative amount"):
        answers["disposable_income"] = "A negative amount"
    data = {"terms": sabp["Application Semester"].split(";"), "enrolment": "Full-time",
            "monthly_income": 500, "monthly_expenses": 1500, "amount_requested": float(sabp["LC Requested Amount"]),
            "hardship": sabp["LC General Statement"], "contact_email": sabp["UofT Email Address"], "consent": True,
            "bursary_type": "Living-costs Bursary", "lc_subsidies": sabp["LC Subsidies"].split(";"), **answers}
    form = client.get("/api/forms/student_aid_bursary/static").json()
    response = client.put("/api/forms/student_aid_bursary/submission",
                          json={"version": form["version"], "hash": form["hash"], "data": data}, headers=user)
    assert response.status_code == 200, response.text
    weights = (Path(__file__).resolve().parents[1] / "data/wfni_weights.csv").read_text()
    assert client.put("/api/admin/sap/weights", json={"csv": weights}, headers=admin).status_code == 200
    view = client.get("/api/me/sap", headers=user).json()
    assert view["eligible"] and view["wfni"]["status"] == "complete" and view["wfni"]["score"] == expected
    assert view["data"]["lc_subsidies"] == data["lc_subsidies"]

    def verified_account(username, first, last, number):
        register(client, mail, username, f"{username}@example.ca", sn=number, first=first, last=last)
        auth = login(client, mail, username)
        verification = client.post("/api/verification", json=scan(client, auth, first_name=first, last_name=last),
                                   headers=auth)
        assert verification.status_code == 201, verification.text
        assert client.post(f"/api/admin/verifications/{verification.json()['id']}/decision",
                           json={"approve": True}, headers=admin).status_code == 200
        return auth

    def roster_entry(full_name, trunc=None):
        return next((row for row in rows
                     if f"{row['First Name'].strip()} {row['Last Name'].strip()}".casefold() == full_name.casefold()
                     and (trunc is None or row["Truncated Student Number"].strip()[-5:-1] == trunc)), None)

    senate = next(row for row in generated[generate_fake_senate]
                  if row["Are you an delegated applicant?"] == generate_fake_senate.NOT_DELEGATE
                  and len(row["General Statement"]) >= 50
                  and roster_entry(row["Full ACORN-registered Name"], row["Student Number"][-5:-1]))
    student = roster_entry(senate["Full ACORN-registered Name"], senate["Student Number"][-5:-1])
    auth = verified_account("senatefixture", student["First Name"].strip(), student["Last Name"].strip(),
                            senate["Student Number"])
    division = student["Division"].strip() or student["Faculty"].strip()
    preferences = {field: senate[f"Constituency Choice [{constituency}]"]
                   if senate[f"Constituency Choice [{constituency}]"] in
                   ("First-choice", "Second-choice", "Third-choice", "Interested") else "Not interested"
                   for field, constituency in PREFERENCES.items()}
    # Campus, year and seat type are required by the portal but not present in the generated legacy export.
    senate_data = {"division": "UTSG", "college": division, "year_of_study": "2", "seat_type": "Undergraduate",
                   "statement": senate["General Statement"], "consent": True, **preferences}
    form = client.get("/api/forms/senate_application/static").json()
    response = client.put("/api/forms/senate_application/submission",
                          json={"version": form["version"], "hash": form["hash"], "data": senate_data}, headers=auth)
    assert response.status_code == 200, response.text
    application = client.get("/api/me/senate", headers=auth).json()
    assert application["eligible"]
    seats = {"seats": [{"constituency": "General Member", "seats": 1}]}
    assert client.put("/api/admin/senate/seats", json=seats, headers=admin).status_code == 200
    preview = client.post("/api/admin/senate/seating", json={"seed": 7}, headers=admin).json()
    expected_seats = assign_seats([{"uuid": application["uuid"], "full_name": application["name"],
                                    "division_code": division, "delegated": False,
                                    "prefs": {PREFERENCES[field]: tier_index(value)
                                              for field, value in preferences.items() if tier_index(value) is not None}}],
                                  {("General Member", ""): 1}, 7)
    assert [{k: v for k, v in seat.items() if k != "public_listing"} for seat in preview["result"]["seated"]] == expected_seats["seated"]

    agm = next(row for row in agm_applications if roster_entry(
        f"{row['ACORN-registered First Name']} {row['ACORN-registered Last Name ']}"))
    student = roster_entry(f"{agm['ACORN-registered First Name']} {agm['ACORN-registered Last Name ']}")
    trunc = student["Truncated Student Number"].strip()[-5:-1]
    auth = verified_account("agmfixture", student["First Name"].strip(), student["Last Name"].strip(), "12345" + trunc + "0")
    attendance = "In person" if agm["Meeting Preference"] == generate_fake_agm.MEETING[0] else "Online"
    form = client.get("/api/forms/agm_rsvp/static").json()
    response = client.put("/api/forms/agm_rsvp/submission", json={
        "version": form["version"], "hash": form["hash"], "data": {
            "attendance": attendance, "division": "UTSG", "pronouns": agm["Preferred Pronouns"],
            "accessibility": agm["Accessibility Requirements"], "eligible": True}}, headers=auth)
    assert response.status_code == 200, response.text
    invitation = client.get("/api/agm/invitation", headers=auth)
    assert invitation.status_code == 200 and invitation.json()["mode"] == ("in_person" if attendance == "In person" else "online")
