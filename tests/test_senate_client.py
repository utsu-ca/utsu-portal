import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to exercise the Senate client")
def test_senate_and_agm_administration_clients():
    source = Path(__file__).resolve().parents[1] / "src/utsu_core/static/app.js"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("  async function senatePublicView()");
const end = source.indexOf("\n  async function ", source.indexOf("\n  async function agmAdminView", start) + 1);
assert(start >= 0 && end > start);
class Element {
  constructor(tag, props = {}, ...children) {
    this.tag = tag; this.children = []; this.textContent = ""; this.value = "";
    Object.assign(this, props); this.append(...children);
  }
  append(...children) { for (const child of children) { this.children.push(child); if (child instanceof Element) child.parent = this; } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { this.parent.children = this.parent.children.filter(c => c !== this); }
  querySelector() { return null; }
}
const el = (tag, props, ...children) => new Element(tag, props, ...children);
const main = el("main"), calls = [];
const card = (...children) => { const c = el("div", {}, ...children); main.append(c); return c; };
const field = (label, props) => [el("label", {}, label), el("input", props)];
const act = (host, fn) => async () => { try { await fn(); } catch (e) { host.append(el("p", {}, e.message)); } };
const msg = (cls, text) => el("p", {className: cls}, text);
let delayed;
const api = async (path, opts = {}) => {
  calls.push({path, opts});
  if (path === "/senate/current") return {version: 1, seed: 42, inputs_hash: "hash",
    seated: [{constituency: "General Member", division: "", name: "seat filled"}]};
  if (path === "/admin/senate/seats" && !opts.method) return {seats: [{constituency: "General Member", division: "", seats: 1}]};
  if (path.startsWith("/admin/senate/applications")) return {items: [], next_cursor: null};
  if (path.startsWith("/admin/senate/alerts")) return {items: [], next_cursor: null};
  if (path === "/admin/senate/seating") {
    if (delayed) return await new Promise(resolve => { delayed.resolve = resolve; });
    return {inputs_hash: "a".repeat(64), result: {seated: []}};
  }
  if (path === "/admin/senate/seating/publish") return {version: 2};
  if (path.startsWith("/admin/agm/rsvps")) return {items: [{name: "Ada Lovelace", attendance: "Online",
    submission_status: "submitted", eligible: true, roster_reason: "matched", checked_in: false}], next_cursor: null};
  if (path.startsWith("/admin/agm/alerts")) return {items: [], next_cursor: null};
  if (path === "/admin/agm/verify") return {verified: true, reason: "matched"};
  return {};
};
const noticeButton = () => el("button", {}, "Preview status notice");
const context = {el, main, card, field, act, msg, api, noticeButton, confirm: () => true, console};
const views = vm.runInNewContext(source.slice(start, end) + "\n({senatePublicView, senateAdminView, agmAdminView})", context);
function find(node, text) {
  if (node instanceof Element) {
    if (node.children.includes(text)) return node;
    for (const child of node.children) { const result = find(child, text); if (result) return result; }
  }
}
(async () => {
  await views.senatePublicView();
  assert(find(main, "General Member: seat filled"));
  await views.senateAdminView();
  const c = main.children[0], row = c.children[2].children[0];
  const seed = c.children.find(x => x instanceof Element && x.value === "2026");
  const run = find(c, "Preview seating"), publish = find(c, "Publish reviewed preview");
  const save = find(c, "Save seat configuration");
  assert(publish.disabled);
  row.seatInputs.count.value = "2"; row.seatInputs.count.oninput();
  await run.onclick();
  assert(!calls.some(c => c.path === "/admin/senate/seating"));
  await save.onclick(); await run.onclick();
  assert(!publish.disabled);
  await publish.onclick();
  const request = calls.find(c => c.path === "/admin/senate/seating/publish");
  assert.strictEqual(request.opts.body.seed, 2026);
  assert.strictEqual(request.opts.body.inputs_hash, "a".repeat(64));
  assert(publish.disabled);
  delayed = {};
  const pending = run.onclick();
  seed.value = "2027"; seed.oninput();
  delayed.resolve({inputs_hash: "b".repeat(64), result: {seated: []}});
  await pending;
  assert(publish.disabled);
  assert(find(c, "Inputs changed while computing; request a new preview."));
  await views.agmAdminView();
  const agm = main.children[0];
  assert(find(main, "Ada Lovelace — Online — submitted — eligible (matched) — not checked in"));
  agm.children[3].value = "Ada Lovelace";
  agm.children[5].value = "1234567890";
  await find(agm, "Check roster").onclick();
  const check = calls.find(c => c.path === "/admin/agm/verify");
  assert.strictEqual(check.opts.body.full_name, "Ada Lovelace");
  assert.strictEqual(check.opts.body.student_number, "1234567890");
  assert(agm.children.some(child => child.textContent === "Verified — matched"));
  agm.children[3].oninput();
  assert(!agm.children.some(child => child.textContent === "Verified — matched"));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", script, str(source)], check=True, capture_output=True, text=True)
