import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to exercise notice previews")
def test_notice_client_previews_confirms_and_refuses_repeat():
    source = Path(__file__).resolve().parents[1] / "src/utsu_core/static/app.js"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("  function noticeButton("), end = source.indexOf("\n  async function senatePublicView()", start);
assert(start >= 0 && end > start);
const calls = [], messages = [];
let confirmSend = false, delivered = null;
const el = (tag, props, text) => ({tag, props, text});
const act = (host, fn) => async () => { try { await fn(); } catch (e) { messages.push(e.message); } };
const msg = (cls, text) => text, host = {append: text => messages.push(text)};
const api = async (path, opts) => {
  calls.push({path, opts});
  if (path.endsWith("/preview")) return {to: "test@example.test", subject: "Subject", body: "Body",
    preview_hash: "hash", delivery_status: delivered};
  return {status: "sent"};
};
const noticeButton = vm.runInNewContext(source.slice(start, end) + "\nnoticeButton", {
  el, act, msg, api, confirm: text => { assert(text.includes("test@example.test")); return confirmSend; }
});
(async () => {
  const button = noticeButton(host, "sap", "student");
  await button.onclick();
  assert.strictEqual(calls.length, 1);
  confirmSend = true;
  await button.onclick();
  assert.strictEqual(calls[2].path, "/admin/notices/sap/send");
  assert.strictEqual(calls[2].opts.body.preview_hash, "hash");
  assert.strictEqual(calls[2].opts.body.confirm, true);
  delivered = "unknown";
  await button.onclick();
  assert.strictEqual(calls.length, 4);
  assert(messages.includes("This notice was already sent or its delivery is unresolved."));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", script, str(source)], check=True, capture_output=True, text=True)
