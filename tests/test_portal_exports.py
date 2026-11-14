import csv
import io
import shutil
import subprocess
from pathlib import Path

import pytest
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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to exercise report downloads")
def test_client_download_uses_auth_and_releases_object_url():
    source = Path(__file__).resolve().parents[1] / "src/utsu_core/static/app.js"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("  async function downloadReport("), end = source.indexOf("\n  const msg", start);
assert(start >= 0 && end > start);
const events = [], token = "session";
let ok = true;
const fetch = async (path, opts) => {
  events.push({path, opts});
  return ok ? {ok: true, blob: async () => "blob"} : {ok: false, status: 404, json: async () => ({detail: "no results"})};
};
const URL = {createObjectURL: blob => { events.push(blob); return "blob:report"; },
  revokeObjectURL: url => events.push("revoke " + url)};
const document = {body: {append: link => events.push("append " + link.href)}};
const el = (tag, props) => ({...props, click: () => events.push("click"), remove: () => events.push("remove")});
const download = vm.runInNewContext(source.slice(start, end) + "\ndownloadReport", {fetch, token, URL, document, el});
(async () => {
  await download("/admin/agm/rsvps.csv", "agm.csv");
  assert.strictEqual(events[0].path, "/api/admin/agm/rsvps.csv");
  assert.strictEqual(events[0].opts.headers.Authorization, "Bearer session");
  assert.deepStrictEqual(events.slice(1), ["blob", "append blob:report", "click", "remove", "revoke blob:report"]);
  ok = false;
  await assert.rejects(() => download("/admin/senate/results.csv", "senate.csv"), /no results/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", script, str(source)], check=True, capture_output=True, text=True)
