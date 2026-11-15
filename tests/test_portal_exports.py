import csv
import io

from utsu_core.services.exports import csv_response


def test_csv_response_escapes_spreadsheet_formulas():
    response = csv_response("report.csv", ["name", "count"], [
        {"name": '=HYPERLINK("https://example.test")', "count": -2},
        {"name": "\t=1+1", "count": 3},
        {"name": "  =1+1", "count": 4},
    ])
    rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert rows[0] == {"name": '\'=HYPERLINK("https://example.test")', "count": "-2"}
    assert rows[1]["name"] == "'\t=1+1"
    assert rows[2]["name"] == "'  =1+1"
    assert response.headers["content-disposition"] == 'attachment; filename="report.csv"'
    assert response.headers["cache-control"] == "no-store"
