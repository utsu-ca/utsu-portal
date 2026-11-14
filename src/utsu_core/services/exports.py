"""Spreadsheet-safe CSV responses for authorized program reports."""
from __future__ import annotations

import io

from fastapi.responses import Response

from ..std.csv_safe import SafeDictWriter


def csv_response(filename: str, fields: list[str], rows: list[dict]) -> Response:
    output = io.StringIO(newline="")
    writer = SafeDictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: ("'" + value if isinstance(value, str) and value != value.lstrip()
                               and value.lstrip().startswith(("=", "+", "-", "@")) else value)
                         for key, value in row.items()})
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )
