import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to exercise client-side redaction")
def test_client_redaction_rules():
    source = Path(__file__).resolve().parents[1] / "src/utsu_core/static/app.js"
    script = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("  function redactHardship(");
const end = source.indexOf("\n  function setNav()", start);
assert(start >= 0 && end > start);
const redact = vm.runInNewContext(source.slice(start, end) + "\nredactHardship");
const result = redact("José and Jose\u0301, 李 王, 1234567, a@example.ca, https://x.ca", "Jose 李 王");
assert.strictEqual(result, "[name] and [name], [name] [name], [student number], [email], [link]");
assert.strictEqual(redact(result, "Jose 李 王"), result);
"""
    subprocess.run(["node", "-e", script, str(source)], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to exercise the SABP client")
def test_sap_admin_weights_review_count_and_resolution():
    source = Path(__file__).resolve().parents[1] / "src/utsu_core/static/app.js"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("  async function sapAdminView(");
const end = source.indexOf("\n  async function adminForms()", start);
assert(start >= 0 && end > start);
class Element {
  constructor(tag, props = {}, ...children) {
    this.tag = tag; this.children = []; this.value = ""; Object.assign(this, props); this.append(...children);
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  querySelector() { return null; }
}
const el = (tag, props, ...children) => new Element(tag, props, ...children);
const main = el("main");
const card = (...children) => { const c = el("div", {}, ...children); main.append(c); return c; };
const field = (label, props) => [el("label", {}, label), el("input", props)];
const act = (host, fn) => async () => { try { await fn(); } catch (e) { host.append(el("p", {}, e.message)); } };
const msg = (cls, text) => el("p", {className: cls}, text);
const calls = [];
const api = async (path, opts = {}) => {
  calls.push({path, opts});
  if (path.startsWith("/admin/sap/applications?")) return {items: [{
    uuid: "student", name: "Student", eligible: true, roster_reason: "", submission_status: "submitted",
    review_stage: "discuss", revision: 7, data: {}, wfni: {}, review_hardship_prepared: true
  }], next_cursor: null};
  if (path === "/admin/sap/review-report") return [{uuid: "student", review_id: "r1", stage: "discuss", reviews: [], decision: null}];
  if (path === "/admin/sap/weights") return {hash: "weights-hash"};
  return {};
};
const view = vm.runInNewContext(source.slice(start, end) + "\nsapAdminView", {
  main, el, card, field, act, msg, api, noticeButton: () => el("button", {}, "Preview status notice"),
  confirm: () => true, redactHardship: text => text
});
function find(node, text) {
  if (node instanceof Element) {
    if (node.children.includes(text)) return node;
    for (const child of node.children) { const result = find(child, text); if (result) return result; }
  }
}
(async () => {
  await view();
  const weightsCard = main.children[1];
  weightsCard.children.find(child => child.tag === "textarea").value = "field,answer,points";
  await find(weightsCard, "Save WFNI weights").onclick();
  const save = calls.find(c => c.path === "/admin/sap/weights");
  assert.strictEqual(save.opts.body.csv, "field,answer,points");
  assert(find(weightsCard, "Weights saved (SHA-256: weights-hash). Reload applications to see updated scores."));
  const application = main.children[2], label = find(application, "Required reviews (increase only, 2–5)");
  const count = application.children[application.children.indexOf(label) + 1];
  count.value = "2.5";
  await find(application, "Increase required reviews").onclick();
  assert(!calls.some(c => c.path.endsWith("/review-count")));
  count.value = "3";
  await find(application, "Increase required reviews").onclick();
  const request = calls.find(c => c.path.endsWith("/review-count"));
  assert.strictEqual(request.path, "/admin/sap/applications/student/review-count");
  assert.strictEqual(request.opts.body.required_reviews, 3);
  const updated = main.children[2], resolution = find(updated, "Resolve disagreement");
  await resolution.onclick();
  assert(!calls.some(c => c.path.endsWith("/resolve-review")));
  const reason = updated.children.find(child => child.placeholder === "Reason for resolving the disagreement");
  reason.value = "Committee reviewed the disagreement.";
  await resolution.onclick();
  const resolveRequest = calls.find(c => c.path.endsWith("/resolve-review"));
  assert.strictEqual(resolveRequest.opts.body.revision, 7);
  assert.strictEqual(resolveRequest.opts.body.reason, reason.value);
  const entry = main.children[2];
  const inputAfter = text => entry.children[entry.children.indexOf(find(entry, text)) + 1];
  inputAfter("Historical source reference").value = "B012-01";
  inputAfter("Historical terms (semicolon-separated)").value = "Fall 2025; Winter 2026";
  inputAfter("Historical decision date").value = "2025-11-01";
  inputAfter("Historical awarded amount").value = "500";
  const approvedLabel = find(entry, " Approved");
  approvedLabel.children[0].checked = true;
  const ratings = entry.children.find(child => child.placeholder && child.placeholder.startsWith('[{"reviewer"'));
  ratings.value = "not json";
  await find(entry, "Import historical decision").onclick();
  assert(!calls.some(c => c.path.endsWith("/history") && c.opts.method === "POST"));
  ratings.value = '[{"reviewer":"Reviewer","level":3}]';
  await find(entry, "Import historical decision").onclick();
  const imported = calls.find(c => c.path.endsWith("/history") && c.opts.method === "POST");
  assert.strictEqual(imported.opts.body.source, "B012-01");
  assert.deepStrictEqual(Array.from(imported.opts.body.terms), ["Fall 2025", "Winter 2026"]);
  assert.strictEqual(imported.opts.body.revision, 7);
  assert.strictEqual(imported.opts.body.approved, true);
  assert.strictEqual(imported.opts.body.ratings[0].level, 3);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", script, str(source)], check=True, capture_output=True, text=True)
