(function () {
  const { el, render } = PortalForms;
  const main = document.getElementById("main"), nav = document.getElementById("nav");
  let token = sessionStorage.getItem("token"), me = null;

  async function api(path, opts = {}) {
    const h = Object.assign({ "Content-Type": "application/json" }, token ? { Authorization: "Bearer " + token } : {});
    const r = await fetch("/api" + path, Object.assign({ headers: h }, opts, opts.body !== undefined ? { body: JSON.stringify(opts.body) } : {}));
    const body = r.status === 204 ? null : await r.json().catch(() => null);
    if (!r.ok) { const d = body && body.detail; throw new Error(typeof d === "string" ? d : d && d.error ? d.error : JSON.stringify(d || r.status)); }
    return body;
  }
  async function downloadReport(path, filename) {
    const response = await fetch("/api" + path, { headers: token ? { Authorization: "Bearer " + token } : {} });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(typeof body?.detail === "string" ? body.detail : `Download failed (${response.status}).`);
    }
    const url = URL.createObjectURL(await response.blob());
    try {
      const link = el("a", { href: url, download: filename });
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  const msg = (cls, t) => el("p", { className: cls }, t);
  const card = (...k) => { const c = el("div", { className: "card" }, ...k); main.append(c); return c; };
  const field = (label, props) => { const i = el("input", props); return [el("label", {}, label), i, i]; };
  const act = (host, fn) => async () => { try { await fn(); } catch (e) { host.querySelector(".status")?.remove(); host.append(el("p", { className: "err status" }, e.message)); } };

  function redactHardship(value, name) {
    const words = new Set(((name || "").normalize("NFC").match(/[\p{L}\p{N}_]+/gu) || [])
      .map(w => w.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()));
    return (value || "").normalize("NFC")
      .replace(/https?:\/\/\S+/gi, "[link]")
      .replace(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g, "[email]")
      .replace(/(?<!\d)\d{7,10}(?!\d)/g, "[student number]")
      .replace(/(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)/g, "[phone]")
      .replace(/\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b/g, "[postal code]")
      .replace(/\[(?:name|email|student number|phone|postal code|link)\]|[\p{L}\p{M}\p{N}_]+/gu, word => {
        if (word.startsWith("[")) return word;
        const normalized = word.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
        return words.has(normalized) ? "[name]" : word;
      });
  }

  function setNav() {
    nav.replaceChildren();
    const link = (t, f) => { const b = el("button", {}, t); b.onclick = f; nav.append(b); };
    link("Senate results", () => act(main, senatePublicView)());
    if (!token) return link("Sign in", authView);
    link("Forms", formsView); link("AGM", agmView); link("My applications", statusView); link("Account", accountView);
    if (me && me.status === "verified") link("SABP banking", () => act(main, sapBankingView)());
    const has = r => me && (me.roles || []).some(x => x === "admin" || x === r);
    if (has("forms_editor")) link("Admin: forms", adminForms);
    if (has("senate_admin")) link("Admin: Senate", () => act(main, senateAdminView)());
    if (has("agm_admin")) link("Admin: AGM", () => act(main, agmAdminView)());
    if (has("sap_reviewer")) link("SABP: review", () => act(main, sapReviewView)());
    if (has("sap_admin")) link("Admin: SABP", () => act(main, sapAdminView)());
    if (has("verifier")) { link("Admin: verifications", adminVerifications); link("Admin: AGM verifications", adminAgmChecks); }
    if (me && (me.roles || []).includes("admin")) {
      link("Admin: roles", adminRoles);
      link("Admin: roster", adminRoster);
    }
    if (has("checkin")) link("AGM check-in", () => { location.href = "/static/checkin.html"; });
    link("Sign out", () => { token = null; me = null; sessionStorage.removeItem("token"); setNav(); authView(); });
  }

  function authView() {
    main.replaceChildren();
    const [l1, u, _u] = field("Username", { autocomplete: "username" }), [l2, c, _c] = field("Email code", { autocomplete: "one-time-code" });
    const c1 = card(el("h2", {}, "Sign in"), l1, u);
    const send = el("button", { className: "primary" }, "Email me a code"); send.onclick = act(c1, async () => { await api("/auth/login", { method: "POST", body: { username: u.value } }); c1.append(msg("ok status", "If the account exists, a code was sent.")); });
    const go = el("button", { className: "primary" }, "Sign in"); go.onclick = act(c1, async () => {
      const r = await api("/auth/token", { method: "POST", body: { username: u.value, code: c.value } });
      token = r.token; sessionStorage.setItem("token", token); await start();
    });
    c1.append(send, l2, c, go);
    const b = el("button", { className: "primary" }, "Create an account"), r = el("button", { className: "primary" }, "Account recovery");
    b.onclick = registerView; r.onclick = recoveryView; c1.append(el("hr"), b, r);
  }

  function registerView() {
    main.replaceChildren();
    const f = {}; const c = card(el("h2", {}, "Create an account"));
    [["username", "Username"], ["email", "Email"], ["first_name", "Legal first name"], ["last_name", "Legal last name"], ["preferred_name", "Preferred name (optional)"], ["student_number", "Student number"]]
      .forEach(([k, l]) => { const [a, i] = field(l, {}); f[k] = i; c.append(a, i); });
    const [lc, code] = field("Email code", {});
    const go = el("button", { className: "primary" }, "Register"); go.onclick = act(c, async () => {
      const body = Object.fromEntries(Object.entries(f).map(([k, i]) => [k, i.value]));
      await api("/auth/register", { method: "POST", body }); c.append(msg("ok status", "Check your email for a code."), lc, code, ver);
    });
    const ver = el("button", { className: "primary" }, "Verify email"); ver.onclick = act(c, async () => { await api("/auth/verify-email", { method: "POST", body: { username: f.username.value, code: code.value } }); authView(); });
    c.append(go);
  }

  async function scanWidget(host) {
    const ch = await api("/verification/challenge");
    const video = el("video", { autoplay: true, playsInline: true, muted: true }), shots = {};
    host.append(el("p", {}, "Hold your face, then your student ID, to the camera. Write or say this phrase in the capture: " + ch.phrase), video);
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
    video.srcObject = stream;
    ["face", "id_card"].forEach(k => {
      const b = el("button", { className: "primary" }, "Capture " + (k === "face" ? "face" : "ID card")), img = el("img", { className: "shot" });
      b.onclick = () => { const cv = el("canvas"); cv.width = 640; cv.height = Math.round(640 * (video.videoHeight || 480) / (video.videoWidth || 640)); cv.getContext("2d").drawImage(video, 0, 0, cv.width, cv.height); shots[k] = cv.toDataURL("image/jpeg", 0.8); img.src = shots[k]; };
      host.append(b, img);
    });
    return { challenge: ch.challenge, shots, stop: () => stream.getTracks().forEach(t => t.stop()) };
  }

  async function accountView() {
    main.replaceChildren(); await refreshMe();
    const u = me.user, c = card(el("h2", {}, "Account"), el("p", {}, `${u.first_name} ${u.last_name} · ${u.email} · ID ending ${u.student_number} · `), el("span", { className: "badge" }, me.status));
    const [l, pn] = field("Preferred name", { value: u.preferred_name }); const save = el("button", { className: "primary" }, "Save");
    save.onclick = act(c, async () => { await api("/me", { method: "PATCH", body: { preferred_name: pn.value } }); c.append(msg("ok status", "Saved")); });
    c.append(l, pn, save);
    const [numberLabel, rosterNumber] = field("Confirm student number for roster matching", { inputMode: "numeric", autocomplete: "off", maxLength: 10 });
    const confirmNumber = el("button", { className: "secondary" }, "Confirm student number");
    confirmNumber.onclick = act(c, async () => {
      await api("/me/roster-number", { method: "POST", body: { student_number: rosterNumber.value } });
      rosterNumber.value = "";
      c.append(msg("ok status", "Student number confirmed for roster matching."));
    });
    c.append(numberLabel, rosterNumber, confirmNumber);
    if (me.status === "pending_review" || me.status === "pending_email") {
      const v = card(el("h2", {}, "Verify your identity")); const [l1, div] = field("Division codes (comma separated, e.g. UTSG)", {});
      const [l2, fn] = field("Legal first name", { value: u.first_name }), [l3, ln] = field("Legal last name", { value: u.last_name });
      v.append(l1, div, l2, fn, l3, ln); let w;
      const start = el("button", { className: "primary" }, "Start camera"); start.onclick = act(v, async () => { w = await scanWidget(v); });
      const sub = el("button", { className: "primary" }, "Submit for review"); sub.onclick = act(v, async () => {
        await api("/verification", { method: "POST", body: { first_name: fn.value, last_name: ln.value, division_codes: div.value.split(",").map(s => s.trim()), face: w.shots.face, id_card: w.shots.id_card, challenge: w.challenge } });
        w.stop(); v.append(msg("ok status", "Submitted. An administrator will review it."));
      });
      v.append(start, sub);
    }
  }

  async function formsView() {
    main.replaceChildren();
    const list = await api("/forms");
    for (const f of list) { const b = el("button", { className: "primary" }, "Open"); b.onclick = () => formView(f.id); card(el("h3", {}, f.title), el("span", { className: "badge" }, "v" + f.version), el("br"), b); }
  }

  // The whole form is reconstructed client side from the cached static JSON; the server is only asked to store the submission.
  async function formView(id) {
    main.replaceChildren();
    const f = await (await fetch(`/api/forms/${id}/static`)).json();
    const prev = (await api("/me/submissions")).find(s => s.form_id === id);
    const c = card(el("h2", {}, f.schema.title), el("p", {}, f.schema.description || "")), host = el("div"); c.append(host);
    const newSap = id === "student_aid_bursary" && prev && ["accepted", "rejected"].includes(prev.status);
    if (newSap) c.append(el("p", {}, "A new application may only cover terms not included in an earlier decision."));
    const r = render(host, f.schema, prev && !newSap ? prev.data : {});
    const b = el("button", { className: "primary" }, "Submit"); c.append(b);
    b.onclick = act(c, async () => {
      const { data, errors } = r.getValues(); r.setErrors(errors); if (Object.keys(errors).length) return;
      await api(`/forms/${id}/submission`, { method: "PUT", body: { version: f.version, hash: f.hash, data } });
      statusView();
    });
  }

  async function statusView() {
    main.replaceChildren(); const subs = await api("/me/submissions");
    if (!subs.length) card(el("p", {}, "No applications yet."));
    for (const s of subs) card(el("h3", {}, s.form_id), el("span", { className: "badge" }, s.status), el("p", {}, s.note || ""),
      el("ul", {}, ...s.history.map(h => el("li", {}, `${new Date(h.at * 1000).toLocaleString()} — ${h.status} ${h.note || ""}`))));
    if (subs.some(s => s.form_id === "student_aid_bursary")) {
      const previous = await api("/me/sap/history");
      for (const item of previous) {
        card(el("h3", {}, "Previous SABP application"),
          el("p", {}, `Terms: ${item.data.terms.join(", ")} — ${item.status}`),
          el("p", {}, `Decision: ${item.decision.approved ? "approved" : "rejected"} — $${item.decision.amount}`));
      }
    }
  }

  function noticeButton(host, program, uid) {
    const button = el("button", { className: "secondary" }, "Preview status notice");
    button.onclick = act(host, async () => {
      const base = `/admin/notices/${program}`, preview = await api(base + "/preview", { method: "POST", body: { uid } });
      if (preview.delivery_status) throw new Error("This notice was already sent or its delivery is unresolved.");
      if (!confirm(`Send this notice?\n\nTo: ${preview.to}\nSubject: ${preview.subject}\n\n${preview.body}`)) return;
      await api(base + "/send", { method: "POST", body: { uid, preview_hash: preview.preview_hash, confirm: true } });
      host.append(msg("ok status", "Notice sent."));
    });
    return button;
  }

  async function senatePublicView() {
    main.replaceChildren();
    const c = card(el("h2", {}, "Published Senate results"));
    const result = await api("/senate/current");
    if (!result) { c.append(el("p", {}, "No results have been published.")); return; }
    c.append(el("p", {}, `Version ${result.version} — seed ${result.seed}`),
      el("p", {}, "Inputs hash: " + result.inputs_hash));
    if (!result.seated.length) c.append(el("p", {}, "No seats filled."));
    for (const seat of result.seated) {
      c.append(el("p", {}, `${seat.constituency}${seat.division ? " — " + seat.division : ""}: ${seat.name}`));
    }
  }

  async function senateAdminView(cursor = "") {
    main.replaceChildren();
    const c = card(el("h2", {}, "Senate administration"));
    const config = await api("/admin/senate/seats");
    const rows = el("div"), constituencies = [
      "First-Year Student", "Commuter", "Residence", "Mature Students and Student Parents",
      "International Students", "First Generation Student", "Small and Specialized Program",
      "General Member", "Faculty/College"
    ];
    let preview = null, revision = 0, dirty = false;
    const invalidate = () => { revision++; preview = null; publish.disabled = true; output.textContent = ""; };
    const changed = () => { dirty = true; invalidate(); };
    function addSeat(seat = {}) {
      const constituency = el("select", {}, ...constituencies.map(name => el("option", { value: name }, name)));
      constituency.value = seat.constituency || constituencies[0];
      const division = el("input", { placeholder: "Division (Faculty/College only)", value: seat.division || "" });
      const count = el("input", { type: "number", min: 0, max: 500, step: 1, value: seat.seats ?? 0 });
      const remove = el("button", { className: "secondary" }, "Remove");
      const row = el("div", { className: "row" }, constituency, division, count, remove);
      row.seatInputs = { constituency, division, count };
      for (const input of [constituency, division, count]) input.oninput = changed;
      remove.onclick = () => { row.remove(); changed(); };
      rows.append(row); changed();
    }
    const add = el("button", { className: "secondary" }, "Add seat");
    const save = el("button", { className: "primary" }, "Save seat configuration");
    const [seedLabel, seed] = field("Seating seed (integer)", { type: "number", step: 1, value: "2026" });
    const run = el("button", { className: "primary" }, "Preview seating");
    const publish = el("button", { className: "primary", disabled: true }, "Publish reviewed preview");
    const output = el("pre");
    add.onclick = () => addSeat();
    seed.oninput = invalidate;
    save.onclick = act(c, async () => {
      const seats = Array.from(rows.children, row => {
        const { constituency, division, count } = row.seatInputs;
        if (!count.value.trim() || !Number.isInteger(Number(count.value))) throw new Error("Enter an integer seat count.");
        return { constituency: constituency.value, division: division.value, seats: Number(count.value) };
      });
      const requestRevision = revision;
      await api("/admin/senate/seats", { method: "PUT", body: { seats } });
      if (requestRevision === revision) dirty = false;
      invalidate(); c.append(msg("ok status", dirty ? "Saved; newer edits still need saving." : "Seat configuration saved."));
    });
    run.onclick = act(c, async () => {
      invalidate();
      if (dirty) throw new Error("Save seat configuration changes before previewing.");
      const value = Number(seed.value);
      if (!seed.value.trim() || !Number.isSafeInteger(value)) throw new Error("Enter a safe integer seed.");
      const requestRevision = revision;
      const result = await api("/admin/senate/seating", { method: "POST", body: { seed: value } });
      if (requestRevision !== revision) throw new Error("Inputs changed while computing; request a new preview.");
      preview = { seed: value, inputs_hash: result.inputs_hash };
      output.textContent = JSON.stringify(result, null, 2);
      publish.disabled = false;
    });
    publish.onclick = act(c, async () => {
      if (!preview) throw new Error("Preview seating before publishing.");
      if (!confirm("Publish the reviewed seating results?")) return;
      const reviewed = preview, requestRevision = revision;
      publish.disabled = true;
      let result;
      try {
        result = await api("/admin/senate/seating/publish", { method: "POST", body: reviewed });
      } catch (error) {
        if (revision === requestRevision) publish.disabled = false;
        throw error;
      }
      invalidate(); c.append(msg("ok status", `Published version ${result.version}.`));
    });
    c.append(el("h3", {}, "Seats"), rows, add, save, seedLabel, seed, run, publish, output);
    for (const seat of config.seats) addSeat(seat);
    dirty = false;
    const page = await api("/admin/senate/applications?limit=100&cursor=" + encodeURIComponent(cursor));
    card(el("h3", {}, "Applications"));
    if (!page.items.length) card(el("p", {}, "No applications."));
    for (const item of page.items) {
      const application = card(el("h3", {}, item.name), el("p", {}, `Eligible: ${item.eligible} — ${item.roster_reason}`),
        el("p", {}, "Status: " + item.submission_status), el("pre", {}, JSON.stringify(item.data, null, 2)));
      application.append(noticeButton(application, "senate", item.uuid));
    }
    const alerts = await api("/admin/senate/alerts?limit=100&cursor=" + encodeURIComponent(cursor));
    const warning = card(el("h3", {}, "Application alerts"));
    if (!alerts.items.length) warning.append(el("p", {}, "No alerts on this page."));
    for (const item of alerts.items) warning.append(el("p", {}, `${item.name}: ${item.detail}`));
    const exportButton = el("button", { className: "secondary" }, "Download published results CSV");
    exportButton.onclick = act(warning, () => downloadReport("/admin/senate/results.csv", "senate-results.csv"));
    warning.append(exportButton);
    if (page.next_cursor) {
      const next = el("button", { className: "secondary" }, "Next page");
      next.onclick = () => act(main, () => senateAdminView(page.next_cursor))();
      main.append(next);
    }
  }

  async function agmAdminView(cursor = "") {
    main.replaceChildren();
    const c = card(el("h2", {}, "AGM administration"), el("h3", {}, "Check the student roster"));
    const [nameLabel, name] = field("Full legal name", {});
    const [numberLabel, number] = field("Student number (optional)", {});
    const [divisionLabel, division] = field("Division (optional)", {});
    const check = el("button", { className: "primary" }, "Check roster"), result = el("p");
    let revision = 0;
    for (const input of [name, number, division]) input.oninput = () => { revision++; result.textContent = ""; };
    check.onclick = act(c, async () => {
      result.textContent = "";
      const requestRevision = revision;
      const match = await api("/admin/agm/verify", { method: "POST", body: {
        full_name: name.value, student_number: number.value, division: division.value
      } });
      if (requestRevision !== revision) throw new Error("Inputs changed while checking; check the roster again.");
      result.textContent = `${match.verified ? "Verified" : "Not verified"} — ${match.reason}`;
    });
    c.append(nameLabel, name, numberLabel, number, divisionLabel, division, check, result);
    const page = await api("/admin/agm/rsvps?limit=100&cursor=" + encodeURIComponent(cursor));
    const list = card(el("h3", {}, "Registrations"));
    if (!page.items.length) list.append(el("p", {}, "No registrations."));
    for (const item of page.items) {
      list.append(el("p", {}, `${item.name} — ${item.attendance} — ${item.submission_status} — ` +
        `${item.eligible ? "eligible" : "not eligible"} (${item.roster_reason}) — ` +
        `${item.checked_in ? "checked in" : "not checked in"}`));
      if (item.accessibility) list.append(el("p", {}, "Accessibility: " + item.accessibility));
      if (item.proxy) list.append(el("p", {}, "Proxy holder: " + item.proxy_name));
      list.append(noticeButton(list, "agm", item.uuid));
    }
    const alerts = await api("/admin/agm/alerts?limit=100&cursor=" + encodeURIComponent(cursor));
    const warnings = card(el("h3", {}, "Registration alerts"));
    if (!alerts.items.length) warnings.append(el("p", {}, "No alerts on this page."));
    for (const item of alerts.items) warnings.append(el("p", {}, `${item.name}: ${item.detail}`));
    const exportButton = el("button", { className: "secondary" }, "Download registrations CSV");
    exportButton.onclick = act(warnings, () => downloadReport("/admin/agm/rsvps.csv", "agm-registrations.csv"));
    warnings.append(exportButton);
    if (page.next_cursor) {
      const next = el("button", { className: "secondary" }, "Next page");
      next.onclick = () => act(main, () => agmAdminView(page.next_cursor))();
      list.append(next);
    }
  }

  async function sapReviewView() {
    main.replaceChildren();
    card(el("h2", {}, "SABP review (experimental)"),
      el("p", {}, "Only manually reviewed statements are available. Declare a conflict if you cannot review an application."));
    const mine = await api("/sap/my-reviews"), queue = await api("/sap/queue");
    card(el("h3", {}, "My reviews"));
    if (!mine.length) card(el("p", {}, "No active reviews."));
    for (const item of mine) {
      const open = el("button", { className: "primary" }, "Open");
      open.onclick = () => act(main, () => sapReviewDetail(item.review_id))();
      card(el("p", {}, `Review ${item.review_id} — ${item.stage}`), open);
    }
    card(el("h3", {}, "Available reviews"));
    if (!queue.length) card(el("p", {}, "No reviews available."));
    for (const item of queue) {
      const priority = item.emergency === true ? " — emergency" : item.emergency === null ? " — emergency status unknown" : "";
      const c = card(el("p", {}, `Review ${item.review_id} — ${item.stage}${priority}`));
      const claim = el("button", { className: "primary" }, "Claim");
      claim.onclick = act(c, async () => {
        await api(`/sap/queue/${item.review_id}/claim`, { method: "POST" });
        await sapReviewDetail(item.review_id);
      });
      const conflict = el("button", { className: "secondary" }, "Declare conflict");
      conflict.onclick = act(c, async () => {
        await api(`/sap/review/${item.review_id}/conflict`, { method: "POST" });
        await sapReviewView();
      });
      c.append(claim, conflict);
    }
  }

  async function sapBankingView() {
    main.replaceChildren();
    const bank = await api("/sap/banking");
    const fragment = new URLSearchParams(location.hash.slice(1));
    const reverifyToken = fragment.get("sap-banking");
    const c = card(el("h2", {}, "SABP banking"), el("p", {}, "Status: " + bank.status),
      el("p", {}, "An active SABP application is required. Changes require an administrator's re-verification request."));
    if (bank.status !== "not_submitted" && !(bank.status === "reverify_requested" && reverifyToken)) return;
    const [il, institution] = field("Institution number (3 digits)", { inputMode: "numeric", maxLength: 3 });
    const [tl, transit] = field("Transit number (5 digits)", { inputMode: "numeric", maxLength: 5 });
    const [al, account] = field("Account number (up to 12 digits)", { inputMode: "numeric", maxLength: 12, autocomplete: "off" });
    const submit = el("button", { className: "primary" }, "Submit banking details");
    submit.onclick = act(c, async () => {
      await api(reverifyToken ? "/sap/banking/reverify" : "/sap/banking", { method: "PUT", body: {
        institution_number: institution.value, transit_number: transit.value, account_number: account.value,
        ...(reverifyToken ? { token: reverifyToken } : {})
      } });
      if (reverifyToken) history.replaceState(null, "", location.pathname + location.search);
      await sapBankingView();
    });
    c.append(il, institution, tl, transit, al, account, submit);
  }

  async function sapReviewDetail(id) {
    const item = await api(`/sap/review/${id}`);
    main.replaceChildren();
    const c = card(el("h2", {}, "Review " + id),
      el("p", {}, `Stage: ${item.stage}`),
      el("pre", {}, JSON.stringify({
        amount_requested: item.amount_requested, enrolment: item.enrolment,
        monthly_income: item.monthly_income, monthly_expenses: item.monthly_expenses,
        bursary_type: item.bursary_type, emergency: item.emergency, wfni: item.wfni
      }, null, 2)), el("h3", {}, "Administrator-redacted statement"), el("p", {}, item.hardship));
    const [label, level] = field("Need level (0–5)", { type: "number", min: 0, max: 5, step: "any" });
    const rate = el("button", { className: "primary" }, "Submit rating");
    rate.onclick = act(c, async () => {
      if (!level.value.trim()) throw new Error("Enter a need level.");
      await api(`/sap/review/${id}`, { method: "PUT", body: { level: Number(level.value) } });
      await sapReviewView();
    });
    const release = el("button", { className: "secondary" }, "Release unrated claim");
    release.onclick = act(c, async () => { await api(`/sap/queue/${id}/release`, { method: "POST" }); await sapReviewView(); });
    const conflict = el("button", { className: "secondary" }, "Declare conflict");
    conflict.onclick = act(c, async () => { await api(`/sap/review/${id}/conflict`, { method: "POST" }); await sapReviewView(); });
    c.append(label, level, rate, release, conflict);
  }

  async function sapAdminView(cursor = "") {
    main.replaceChildren();
    const heading = card(el("h2", {}, "SABP administration (experimental)"),
      el("p", {}, "Review statements manually for all identifying information before confirming redaction."));
    const exportButton = el("button", { className: "secondary" }, "Download applications CSV");
    exportButton.onclick = act(heading, () => downloadReport("/admin/sap/applications.csv", "sap-applications.csv"));
    heading.append(exportButton);
    const weightsCard = card(el("h3", {}, "WFNI weights"),
      el("p", {}, "Paste a complete weights CSV to configure or replace scoring weights."));
    const weights = el("textarea", { rows: 8, placeholder: "field,answer,points" });
    const saveWeights = el("button", { className: "primary" }, "Save WFNI weights");
    saveWeights.onclick = act(weightsCard, async () => {
      const result = await api("/admin/sap/weights", { method: "PUT", body: { csv: weights.value } });
      weightsCard.append(msg("ok status", `Weights saved (SHA-256: ${result.hash}). Reload applications to see updated scores.`));
    });
    weightsCard.append(weights, saveWeights);
    const page = await api("/admin/sap/applications?limit=100&cursor=" + encodeURIComponent(cursor));
    const report = await api("/admin/sap/review-report");
    for (const item of page.items) {
      const review = report.find(r => r.uuid === item.uuid);
      const c = card(el("h3", {}, item.name), el("p", {}, `Eligible: ${item.eligible} — ${item.roster_reason}`),
        el("p", {}, `Status: ${item.submission_status}; review: ${item.review_stage || "not started"}`),
        el("pre", {}, JSON.stringify(item.data, null, 2)), el("pre", {}, JSON.stringify(item.wfni, null, 2)));
      const base = `/admin/sap/applications/${encodeURIComponent(item.uuid)}`;
      const past = el("button", { className: "secondary" }, "View previous applications");
      past.onclick = act(c, async () => {
        const history = await api(base + "/history");
        c.querySelector(".sap-history")?.remove();
        c.append(el("pre", { className: "sap-history" }, JSON.stringify(history, null, 2)));
      });
      const [sourceLabel, source] = field("Historical source reference", { maxLength: 200 });
      const [termsLabel, terms] = field("Historical terms (semicolon-separated)", {});
      const [dateLabel, date] = field("Historical decision date", { type: "date" });
      const [amountLabel, amount] = field("Historical awarded amount", { type: "number", min: 0, step: "0.01" });
      const approved = el("input", { type: "checkbox" });
      const ratings = el("textarea", { rows: 3, placeholder: '[{"reviewer":"Name","level":3,"notes":""}]' });
      const importHistory = el("button", { className: "secondary" }, "Import historical decision");
      importHistory.onclick = act(c, async () => {
        if (!source.value.trim() || !terms.value.trim() || !date.value || !amount.value.trim())
          throw new Error("Enter the source, terms, decision date and amount.");
        let historicalRatings;
        try { historicalRatings = ratings.value.trim() ? JSON.parse(ratings.value) : []; }
        catch { throw new Error("Historical ratings must be a JSON array."); }
        if (!Array.isArray(historicalRatings)) throw new Error("Historical ratings must be a JSON array.");
        if (!confirm("Import this historical decision for " + item.name + "? Verify the source and account first.")) return;
        await api(base + "/history", { method: "POST", body: {
          source: source.value, terms: terms.value.split(";").map(t => t.trim()), decided_on: date.value,
          amount: Number(amount.value), approved: approved.checked, ratings: historicalRatings,
          revision: item.revision, confirm: true
        } });
        await sapAdminView(cursor);
      });
      c.append(el("h4", {}, "Import a completed historical decision"),
        el("p", { className: "help" }, "Only import verified records for this account. This does not record a payment."),
        sourceLabel, source, termsLabel, terms, dateLabel, date, amountLabel, amount,
        el("label", { className: "opt" }, approved, " Approved"), el("label", {}, "Historical ratings (optional JSON)"),
        ratings, importHistory);
      const bank = el("button", { className: "secondary" }, "View banking details");
      bank.onclick = act(c, async () => {
        const values = await api(base + "/banking");
        c.querySelector(".bank-details")?.remove();
        c.append(el("pre", { className: "bank-details" }, JSON.stringify(values, null, 2)));
      });
      const destroy = el("button", { className: "danger" }, "Destroy banking details");
      destroy.onclick = act(c, async () => {
        if (!confirm("Remove banking details from the active store? Backups and database history are not erased.")) return;
        await api(base + "/banking", { method: "DELETE", body: { confirm: true } });
        c.querySelector(".bank-details")?.remove();
        c.append(msg("ok status", "Banking details removed from the active store."));
      });
      const requestBank = el("button", { className: "secondary" }, "Request banking re-verification");
      requestBank.onclick = act(c, async () => {
        await api(base + "/banking/reverify", { method: "POST" });
        c.append(msg("ok status", "Re-verification link sent to the applicant."));
      });
      const compareBank = el("button", { className: "secondary" }, "Compare pending banking details");
      compareBank.onclick = act(c, async () => {
        const values = await api(base + "/banking/pending");
        c.querySelector(".bank-details")?.remove();
        const details = el("div", { className: "bank-details" },
          el("pre", {}, JSON.stringify({ active: values.active, pending: values.pending }, null, 2)));
        const accept = el("button", { className: "primary" }, "Accept pending details");
        accept.onclick = act(details, async () => {
          if (!confirm("Replace the active banking details with this pending version?")) return;
          await api(base + "/banking/accept", { method: "POST", body: { confirm: true, revision: values.revision } });
          details.remove();
          c.append(msg("ok status", "Pending banking details accepted."));
        });
        details.append(accept); c.append(details);
      });
      c.append(past, bank, requestBank, compareBank, destroy, noticeButton(c, "sap", item.uuid));
      if (!review) {
        const enqueue = el("button", { className: "primary" }, "Start review");
        enqueue.onclick = act(c, async () => { await api(base + "/enqueue", { method: "POST" }); await sapAdminView(cursor); });
        c.append(enqueue);
      } else {
        c.append(el("p", {}, "Review ID: " + review.review_id),
          el("pre", {}, JSON.stringify({ reviews: review.reviews, resolution: review.resolution, decision: review.decision }, null, 2)));
        if (!item.review_hardship_prepared) {
          const names = [item.name, item.preferred_name].filter(Boolean).join(" ");
          const text = el("textarea", { maxLength: 3000, value: redactHardship(item.data.hardship, names) });
          const confirm = el("input", { type: "checkbox" });
          text.oninput = () => { confirm.checked = false; };
          const redact = el("button", { className: "secondary" }, "Apply automatic redaction again");
          redact.onclick = () => { text.value = redactHardship(text.value, names); confirm.checked = false; };
          const save = el("button", { className: "primary" }, "Save redacted statement");
          save.onclick = act(c, async () => {
            await api(base + "/review-hardship", { method: "PUT", body: {
              text: text.value, confirm_redacted: confirm.checked, revision: item.revision
            } });
            await sapAdminView(cursor);
          });
          c.append(el("p", { className: "help" }, "Automatic redaction is incomplete. Check for names, identifying circumstances and contact details before confirming."),
            el("label", {}, "Redacted statement"), text, redact,
            el("label", {}, confirm, " I have reviewed and removed identifying information"), save);
        }
        const [rl, reviewer] = field("Reviewer account UUID", {});
        const assign = el("button", { className: "secondary" }, "Assign reviewer");
        assign.onclick = act(c, async () => {
          await api(`/admin/sap/review/${review.review_id}/assign`, { method: "POST", body: { reviewer: reviewer.value } });
          await sapAdminView(cursor);
        });
        c.append(rl, reviewer, assign);
        if (!review.decision && !review.resolution) {
          const [countLabel, count] = field("Required reviews (increase only, 2–5)", { type: "number", min: 2, max: 5, step: 1 });
          const setCount = el("button", { className: "secondary" }, "Increase required reviews");
          setCount.onclick = act(c, async () => {
            const value = Number(count.value);
            if (!count.value.trim() || !Number.isInteger(value) || value < 2 || value > 5)
              throw new Error("Enter a whole number between 2 and 5.");
            await api(base + "/review-count", { method: "PUT", body: { required_reviews: value } });
            await sapAdminView(cursor);
          });
          c.append(countLabel, count, setCount);
          if (review.stage === "discuss") {
            const reason = el("textarea", { maxLength: 500, placeholder: "Reason for resolving the disagreement" });
            const resolve = el("button", { className: "secondary" }, "Resolve disagreement");
            resolve.onclick = act(c, async () => {
              if (!reason.value.trim()) throw new Error("Enter a reason for resolving the disagreement.");
              if (!confirm("Resolve the disagreement and lock the current ratings?")) return;
              await api(base + "/resolve-review", { method: "POST", body: { reason: reason.value, revision: item.revision } });
              await sapAdminView(cursor);
            });
            c.append(reason, resolve);
          }
        }
        if (!review.decision) {
          const [al, amount] = field("Award amount (0 to reject)", { type: "number", min: 0, step: "0.01" });
          const approve = el("button", { className: "primary" }, "Approve");
          const reject = el("button", { className: "secondary" }, "Reject");
          approve.onclick = act(c, async () => {
            if (!amount.value.trim()) throw new Error("Enter an award amount.");
            await api(base + "/decision", { method: "POST", body: { approved: true, amount: Number(amount.value) } });
            await sapAdminView(cursor);
          });
          reject.onclick = act(c, async () => {
            await api(base + "/decision", { method: "POST", body: { approved: false, amount: 0 } });
            await sapAdminView(cursor);
          });
          c.append(al, amount, approve, reject);
        }
      }
    }
    if (page.next_cursor) {
      const next = el("button", { className: "secondary" }, "Next page");
      next.onclick = () => act(main, () => sapAdminView(page.next_cursor))();
      main.append(next);
    }
  }

  async function adminForms() {
    main.replaceChildren();
    for (const f of await api("/admin/forms")) { const b = el("button", { className: "primary" }, "Edit"); b.onclick = () => editor(f.id); card(el("h3", {}, f.id), el("span", { className: "badge" }, f.published_version ? "published v" + f.published_version : "unpublished"), el("br"), b); }
    const n = el("button", { className: "primary" }, "New form from template"); n.onclick = () => editor(prompt("New form id (a-z, 0-9, _)"), true); main.append(n);
  }

  async function editor(id, isNew) {
    main.replaceChildren();
    const f = isNew ? { rev: 0, draft_yaml: `id: ${id}\nversion: 1\ntitle: New form\nfields:\n  - {id: name, type: text, label: Name, required: true}\n` } : await api("/admin/forms/" + id);
    let rev = f.rev, schema = null;
    const c = card(el("h2", {}, "Edit " + id)), ta = el("textarea", { className: "yaml", value: f.draft_yaml, spellcheck: false }), prevHost = el("div", { className: "card" });
    c.append(el("div", { className: "row" }, ta, prevHost));
    const status = el("p", {}); c.append(status);
    const preview = async () => { try { schema = (await api("/admin/forms/convert", { method: "POST", body: { yaml: ta.value } })).schema; render(prevHost, schema, {}); status.className = "ok"; status.textContent = "valid"; } catch (e) { status.className = "err"; status.textContent = e.message; } };
    let t; ta.oninput = () => { clearTimeout(t); t = setTimeout(preview, 400); }; preview();
    // WYSIWYG: add fields to the schema and regenerate the YAML.
    const add = el("select", {}, ...["text", "textarea", "email", "number", "date", "select", "radio", "multiselect", "checkbox", "info"].map(x => el("option", { value: x }, x)));
    const addB = el("button", { className: "primary" }, "Add field"); addB.onclick = act(c, async () => {
      const s = schema || (await api("/admin/forms/convert", { method: "POST", body: { yaml: ta.value } })).schema, type = add.value;
      const nf = { id: "field_" + (s.fields.length + 1), type, label: "New " + type }; if (["select", "radio", "multiselect"].includes(type)) nf.options = ["Option 1", "Option 2"];
      s.fields.push(nf); ta.value = (await api("/admin/forms/convert", { method: "POST", body: { schema: s } })).yaml; preview();
    });
    const saveB = el("button", { className: "primary" }, "Save draft"); saveB.onclick = act(c, async () => { const r = await api(`/admin/forms/${id}/draft`, { method: "PUT", body: { yaml: ta.value, base_rev: rev } }); rev = r.rev; status.className = "ok"; status.textContent = "saved rev " + rev; });
    const pub = el("button", { className: "primary" }, "Publish"), unp = el("button", { className: "secondary" }, "Unpublish");
    pub.onclick = act(c, async () => { await api(`/admin/forms/${id}/publish`, { method: "POST" }); status.textContent = "published"; });
    unp.onclick = act(c, async () => { await api(`/admin/forms/${id}/unpublish`, { method: "POST" }); status.textContent = "unpublished"; });
    c.append(add, addB, saveB, pub, unp);
    if (!isNew) { // realtime sync of edits made by other admins
      const es = new EventSource(`/api/admin/forms/${id}/events?token=${encodeURIComponent(token)}`);
      es.onmessage = ev => { const d = JSON.parse(ev.data); if (d.yaml && d.rev !== rev) { rev = d.rev; ta.value = d.yaml; preview(); status.textContent = "updated by another admin (rev " + d.rev + ")"; } };
      const stop = () => es.close(); nav.addEventListener("click", stop, { once: true });
    }
    const subs = await (isNew ? [] : api(`/admin/forms/${id}/submissions`));
    for (const s of subs) {
      const sel = el("select", {}, ...["submitted", "under_review", "needs_info", "accepted", "rejected"].map(x => el("option", { value: x, selected: x === s.status }, x))), note = el("input", { placeholder: "note" }), b = el("button", { className: "primary" }, "Set status");
      const cc = card(el("strong", {}, `${s.applicant.first_name} ${s.applicant.last_name} (${s.applicant.preferred_name}) ***${s.applicant.student_number}`), el("pre", {}, JSON.stringify(s.data, null, 2)), sel, note, b);
      b.onclick = act(cc, async () => { await api(`/admin/submissions/${id}/${s.uuid}/status`, { method: "POST", body: { status: sel.value, note: note.value } }); cc.append(msg("ok status", "updated")); });
    }
  }

  function adminRoster() {
    main.replaceChildren();
    const file = el("input", { type: "file", accept: ".csv,text/csv" });
    const replace = el("input", { type: "checkbox" });
    const upload = el("button", { className: "primary" }, "Import roster");
    const c = card(el("h2", {}, "Student roster"),
      el("p", { className: "help" }, "CSV must contain First Name, Last Name, Truncated Student Number, Faculty and Division. Roster data is private."),
      el("p", { className: "help" }, "Replacing a roster changes eligibility. Do not replace it during seating or reviews."),
      file, el("label", { className: "opt" }, replace, " Replace the existing roster"), upload);
    upload.onclick = act(c, async () => {
      if (!file.files || file.files.length !== 1) throw new Error("Choose one roster CSV file.");
      if (replace.checked && !confirm("Replace the current roster and recheck eligibility?")) return;
      const csv = await file.files[0].text();
      const result = await api("/admin/roster", { method: "POST", body: { csv, replace: replace.checked } });
      file.value = "";
      c.querySelector(".status")?.remove();
      c.append(msg("ok status", result.imported
        ? `Roster imported: ${result.count} students; ${result.rostered} of ${result.checked} accounts matched.`
        : "A roster already exists. Select replacement to import a new one."));
    });
  }

  async function adminRoles() {
    main.replaceChildren(); const cat = await api("/admin/roles"), holders = await api("/admin/roles/holders");
    const [l1, uname] = field("Username of a verified account", { autocomplete: "off" });
    const boxes = Object.keys(cat).map(r => { const c = el("input", { type: "checkbox" }); return [r, c, el("label", { className: "opt", title: cat[r] }, c, ` ${r} — ${cat[r]}`)]; });
    const save = el("button", { className: "primary" }, "Set roles");
    const form = card(el("h3", {}, "Assign roles"), l1, uname, ...boxes.map(b => b[2]), save);
    save.onclick = act(form, async () => { await api("/admin/roles", { method: "PUT", body: { username: uname.value, roles: boxes.filter(b => b[1].checked).map(b => b[0]) } }); adminRoles(); });
    for (const h of holders) card(el("strong", {}, h.preferred_name || h.uuid), el("p", {}, h.roles.join(", ")), el("p", { className: "help" }, h.uuid));
  }

  async function adminVerifications() {
    main.replaceChildren(); const list = await api("/admin/verifications");
    if (!list.length) card(el("p", {}, "Nothing pending."));
    for (const v of list) {
      const c = card(el("strong", {}, `${v.kind}: ${v.first_name} ${v.last_name}`), el("p", {}, "Divisions: " + v.division_codes.join(", ")), el("pre", {}, JSON.stringify(v.changes)));
      const show = el("button", { className: "secondary" }, "Show scans"); show.onclick = act(c, async () => { const m = await api(`/admin/verifications/${v.id}/media`); c.append(el("div", { className: "row" }, el("img", { className: "shot", src: m.face }), el("img", { className: "shot", src: m.id_card }))); });
      const yes = el("button", { className: "primary" }, "Approve"), no = el("button", { className: "danger" }, "Reject");
      yes.onclick = act(c, async () => { await api(`/admin/verifications/${v.id}/decision`, { method: "POST", body: { approve: true } }); adminVerifications(); });
      no.onclick = act(c, async () => { await api(`/admin/verifications/${v.id}/decision`, { method: "POST", body: { approve: false } }); adminVerifications(); });
      c.append(show, yes, no);
    }
  }

  // AGM invitation panel: in-person registrants show a QR code; online registrants verify their ID, then sign in to Zoom via SSO.
  async function agmView() {
    main.replaceChildren(); const inv = await api("/agm/invitation"), c = card(el("h2", {}, "AGM"));
    if (!inv.registered) { const b = el("button", { className: "primary" }, "Register for the AGM"); b.onclick = () => formView("agm_rsvp"); c.append(el("p", {}, "You are not registered yet."), b); return; }
    c.append(el("p", {}, "Registration: "), el("span", { className: "badge" }, inv.mode === "online" ? "Online" : "In person"), inv.checked_in ? msg("ok", "You are checked in.") : el("span"));
    if (inv.mode === "in_person") {
      const q = await api("/agm/qr"); c.append(el("p", {}, "Show this code at the door:"));
      if (q.svg) { const img = el("img", { className: "shot", alt: "Check-in QR code", width: 220, height: 220 }); img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(q.svg); c.append(img); }
      c.append(el("p", { className: "help" }, "Code: " + q.payload));
      return;
    }
    if (!inv.id_verified) {
      const st = await api("/agm/verify");
      if (st.status === "pending") { c.append(msg("", "Your ID check is awaiting review.")); return; }
      c.append(el("p", {}, "Verify your identity to receive your Zoom sign-on."));
      const ch = await api("/agm/verify/challenge"), video = el("video", { autoplay: true, playsInline: true, muted: true }), shots = {};
      c.append(el("p", {}, "Phrase for the capture: " + ch.phrase), video);
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false }); video.srcObject = stream;
      ["face", "id_card"].forEach(k => { const b = el("button", { className: "primary" }, "Capture " + (k === "face" ? "face" : "ID card")), im = el("img", { className: "shot" });
        b.onclick = () => { const cv = el("canvas"); cv.width = 640; cv.height = Math.round(640 * (video.videoHeight || 480) / (video.videoWidth || 640)); cv.getContext("2d").drawImage(video, 0, 0, cv.width, cv.height); shots[k] = cv.toDataURL("image/jpeg", 0.8); im.src = shots[k]; }; c.append(b, im); });
      const go = el("button", { className: "primary" }, "Submit"); go.onclick = act(c, async () => { await api("/agm/verify", { method: "POST", body: { face: shots.face, id_card: shots.id_card, challenge: ch.challenge } }); stream.getTracks().forEach(t => t.stop()); agmView(); });
      c.append(go); return;
    }
    const j = el("button", { className: "primary" }, "Log in to Zoom");
    j.onclick = act(c, async () => { const z = await api("/agm/zoom/join", { method: "POST" }); c.append(el("p", {}, el("a", { href: z.sso_url || z.zoom_url, target: "_blank", rel: "noopener" }, "Open Zoom"))); window.open(z.sso_url || z.zoom_url, "_blank", "noopener"); });
    c.append(msg("ok", "ID verified."), j);
  }

  async function adminAgmChecks() {
    main.replaceChildren(); const list = await api("/admin/agm/verifications");
    if (!list.length) card(el("p", {}, "Nothing pending."));
    for (const v of list) {
      const c = card(el("strong", {}, `${v.first_name} ${v.last_name} ***${v.student_number}`));
      const show = el("button", { className: "secondary" }, "Show scans"), yes = el("button", { className: "primary" }, "Approve"), no = el("button", { className: "danger" }, "Reject");
      show.onclick = act(c, async () => { const m = await api(`/admin/agm/verifications/${v.id}/media`); c.append(el("div", { className: "row" }, el("img", { className: "shot", src: m.face }), el("img", { className: "shot", src: m.id_card }))); });
      yes.onclick = act(c, async () => { await api(`/admin/agm/verifications/${v.id}/decision`, { method: "POST", body: { approve: true } }); adminAgmChecks(); });
      no.onclick = act(c, async () => { await api(`/admin/agm/verifications/${v.id}/decision`, { method: "POST", body: { approve: false } }); adminAgmChecks(); });
      c.append(show, yes, no);
    }
  }

  function recoveryView() {
    main.replaceChildren(); const c = card(el("h2", {}, "Account recovery"));
    const [l1, u] = field("Username", {}), [l2, sn] = field("Last 4 digits of student number", { maxLength: 4 });
    c.append(l1, u, l2, sn);
    const s = el("button", { className: "primary" }, "Email me a sign-in code (email on file)"); s.onclick = act(c, async () => { await api("/recovery/start", { method: "POST", body: { username: u.value, student_number_trunc: sn.value } }); c.append(msg("ok status", "If the details match, a code was sent. Sign in with it.")); });
    c.append(s, el("hr"), el("h3", {}, "Change email or legal name (requires face + ID scan)"));
    const [l3, ne] = field("New email (optional)", {}), [l4, otp] = field("Code sent to new email", {}), [l5, fn] = field("Legal first name", {}), [l6, ln] = field("Legal last name", {}), [l7, dv] = field("Division codes (comma separated)", {});
    const o = el("button", { className: "primary" }, "Send code to new email"); o.onclick = act(c, async () => { await api("/recovery/email-otp", { method: "POST", body: { username: u.value, student_number_trunc: sn.value, new_email: ne.value } }); c.append(msg("ok status", "Code sent")); });
    c.append(l3, ne, o, l4, otp, l5, fn, l6, ln, l7, dv); let w;
    const cam = el("button", { className: "primary" }, "Start camera"); cam.onclick = act(c, async () => { w = await scanWidget(c); });
    const go = el("button", { className: "primary" }, "Submit request"); go.onclick = act(c, async () => {
      await api("/recovery/request", { method: "POST", body: { username: u.value, student_number_trunc: sn.value, new_email: ne.value || null, otp: otp.value || null, first_name: fn.value, last_name: ln.value, division_codes: dv.value.split(",").map(x => x.trim()), face: w.shots.face, id_card: w.shots.id_card, challenge: w.challenge } });
      w.stop(); c.append(msg("ok status", "Submitted for administrator review."));
    });
    c.append(cam, go);
  }

  async function refreshMe() { try { me = await api("/me"); } catch (e) { token = null; sessionStorage.removeItem("token"); me = null; } }
  async function start() { if (token) await refreshMe(); setNav();
    const next = sessionStorage.getItem("sso_next"); if (token && next && me) { sessionStorage.removeItem("sso_next"); location.href = next; return; }
    if (token && me && me.status === "verified" && new URLSearchParams(location.hash.slice(1)).has("sap-banking")) {
      await act(main, sapBankingView)(); return;
    }
    token ? (me.status === "verified" ? formsView() : accountView()) : authView(); }
  start();
})();
