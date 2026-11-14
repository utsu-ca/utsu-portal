import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to exercise the roster client")
def test_roster_upload_requires_file_and_explicit_replacement():
    source = Path(__file__).resolve().parents[1] / "src/utsu_core/static/app.js"
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("  function adminRoster()");
const end = source.indexOf("\n  async function adminRoles()", start);
assert(start >= 0 && end > start);
class Element {
  constructor(tag, props = {}, ...children) {
    this.tag = tag; this.children = []; this.value = ""; this.checked = false;
    Object.assign(this, props); this.append(...children);
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  querySelector() { return null; }
}
const el = (tag, props, ...children) => new Element(tag, props, ...children);
const main = el("main");
const card = (...children) => { const c = el("div", {}, ...children); main.append(c); return c; };
const act = (host, fn) => async () => { try { await fn(); } catch (e) { host.append(el("p", {}, e.message)); } };
const msg = (cls, text) => el("p", {className: cls}, text);
const calls = [];
const api = async (path, opts) => { calls.push({path, opts}); return {imported: true, count: 2, checked: 1, rostered: 1}; };
let confirmed = false;
const view = vm.runInNewContext(source.slice(start, end) + "\nadminRoster", {
  main, el, card, act, msg, api, confirm: () => confirmed
});
(async () => {
  view();
  const c = main.children[0], file = c.children.find(x => x.type === "file");
  const label = c.children.find(x => x.tag === "label"), checkbox = label.children[0];
  const button = c.children.find(x => x.tag === "button");
  await button.onclick();
  assert.strictEqual(calls.length, 0);
  file.files = [{text: async () => "roster csv"}];
  await button.onclick();
  assert.strictEqual(calls[0].path, "/admin/roster");
  assert.strictEqual(calls[0].opts.body.csv, "roster csv");
  assert.strictEqual(calls[0].opts.body.replace, false);
  checkbox.checked = true;
  await button.onclick();
  assert.strictEqual(calls.length, 1);
  confirmed = true;
  await button.onclick();
  assert.strictEqual(calls[1].opts.body.replace, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", script, str(source)], check=True, capture_output=True, text=True)
