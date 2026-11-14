import csv
import json
import sqlite3

import pytest

from utsu_core.programs.sap.eft import create_eft
from utsu_core.programs.sap import export_banking
from test_export_banking import inputs, write_csv


@pytest.fixture
def config():
    return {
        "originator_id": "1234567890",
        "file_creation_number": 23,
        "creation_date": "2026-11-08",
        "payment_date": "2026-11-10",
        "destination_data_center": "00610",
        "originating_data_center": "12345",
        "transaction_code": "450",
        "originator_short_name": "UTSU",
        "originator_long_name": "Student Union",
        "return_institution_id": "000100023",
        "return_account_number": "123456",
        "record_separator": "lf",
    }


@pytest.fixture
def payment():
    return {"record_id": "B001-02", "approved_amount": "123.45", "first_name": "Ann", "last_name": "Lee",
            "institution_number": "001", "transit_number": "00023", "account_number": "0000456"}


def test_record_positions_and_totals(config, payment):
    rows = [dict(payment, record_id=f"B001-{i:02d}", approved_amount=f"{i}.25") for i in range(1, 8)]
    raw = create_eft(rows, config)
    lines = raw.split(b"\n")
    assert len(lines) == 4 and [line[:1] for line in lines] == [b"A", b"C", b"C", b"Z"]
    assert all(len(line) == 1464 for line in lines)
    assert lines[0][:24] == b"A00000000112345678900023"
    assert lines[0][24:35] == b"02631200610"
    assert lines[0][55:58] == b"CAD"
    assert lines[1][:24] == b"C00000000212345678900023"
    first = lines[1][24:264]
    assert first[0:3] == b"450" and first[3:13] == b"0000000125"
    assert first[13:19] == b"026314" and first[19:28] == b"000100023"
    assert first[28:40] == b"0000456     "
    assert first[40:62] == b"0061123450023000000001"
    assert first[65:80].strip() == b"UTSU" and first[80:110].strip() == b"Ann Lee"
    assert first[150:169].strip() == b"B001-01"
    assert first[169:178] == b"000100023" and first[178:190].strip() == b"123456"
    assert lines[2][264:] == b" " * 1200
    assert lines[-1][24:46] == b"0" * 22
    assert lines[-1][46:60] == b"00000000002975"
    assert lines[-1][60:68] == b"00000007"


@pytest.mark.parametrize("separator, marker", [("none", b""), ("lf", b"\n"), ("crlf", b"\r\n")])
def test_separators(config, payment, separator, marker):
    config["record_separator"] = separator
    output = create_eft([payment], config)
    assert len(output) == 3 * 1464 + 2 * len(marker)
    assert output[1464:1464 + len(marker)] == marker


@pytest.mark.parametrize("field,value", [
    ("approved_amount", ""), ("approved_amount", "0"), ("approved_amount", "-1"),
    ("approved_amount", "1.001"), ("approved_amount", "NaN"),
    ("institution_number", "01"), ("transit_number", "1234"), ("account_number", "123-4"),
    ("account_number", "1234567890123"), ("first_name", "Renée"), ("first_name", "a" * 31),
])
def test_invalid_payment_is_rejected(config, payment, field, value):
    payment[field] = value
    with pytest.raises(ValueError):
        create_eft([payment], config)


@pytest.mark.parametrize("field,value", [
    ("originator_id", ""), ("file_creation_number", 0), ("file_creation_number", True),
    ("creation_date", "2026-11-31"), ("payment_date", "2026-11-07"),
    ("destination_data_center", "610"), ("originating_data_center", "abcde"),
    ("return_institution_id", "123456789"), ("record_separator", "unknown"),
])
def test_invalid_config_is_rejected(config, payment, field, value):
    config[field] = value
    with pytest.raises(ValueError):
        create_eft([payment], config)


def test_joined_reviews_can_generate_eft(inputs, config, tmp_path):
    reviews, applications, database, output, _, _ = inputs
    write_csv(reviews, ["record_id", "approved_amount"], [{"record_id": "B001-02", "approved_amount": "123.45"}])
    config_path = tmp_path / "eft_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _, rows = export_banking.join_banking(reviews, applications, database)
    assert export_banking.make_eft(rows, config_path).split(b"\n")[1][27:37] == b"0000012345"
    assert rows[0]["approved_amount"] == "123.45"


def test_invalid_approved_amount_is_rejected(inputs, config, tmp_path):
    reviews, applications, database, output, _, _ = inputs
    config_path = tmp_path / "eft_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _, rows = export_banking.join_banking(reviews, applications, database)
    with pytest.raises(ValueError, match="approved_amount"):
        export_banking.make_eft(rows, config_path)
    assert not output.exists()


@pytest.mark.parametrize("reason", ["missing_database_record", "missing_application"])
def test_missing_application_blocks_eft(inputs, config, tmp_path, reason):
    reviews, applications, database, output, _, _ = inputs
    with sqlite3.connect(database) as conn:
        if reason == "missing_application":
            conn.execute("INSERT INTO student_aid VALUES ('not-in-export', 1, 3)")
    write_csv(reviews, ["record_id", "approved_amount"], [
        {"record_id": "B001-02", "approved_amount": "123.45"},
        {"record_id": "B001-03", "approved_amount": "10.00"},
    ])
    config_path = tmp_path / "eft_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _, rows = export_banking.join_banking(reviews, applications, database)
    assert len(rows) == 2 and rows[1]["export_flags"] == f"manual_reconciliation;{reason}"
    with pytest.raises(ValueError, match="EFT blocked"):
        export_banking.make_eft(rows, config_path)
