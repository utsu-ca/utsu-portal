<script>
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { isBlank } from "../../../lib/forms.js";
  import { redactHardship } from "../lib/redact.js";
  import Status from "../../../components/Status.svelte";
  import NoticeButton from "../../../components/NoticeButton.svelte";
  import BankingControls from "./BankingControls.svelte";

  let { item, review = undefined, onchange } = $props();
  const act = new Action();
  const base = $derived(`/admin/sap/applications/${encodeURIComponent(item.uuid)}`);
  const bankingPaths = $derived({
    details: base + "/banking", flag: base + "/banking/reverify", pending: base + "/banking/pending",
    cheque: base + "/banking/cheque", accept: base + "/banking/accept", destroy: true,
  });

  let history = $state(null);
  let source = $state(""), terms = $state(""), decidedOn = $state(""), amountHistorical = $state(""),
    approvedHistorical = $state(false), ratings = $state("");
  const names = $derived([item.name, item.preferred_name].filter(Boolean).join(" "));
  // the automatic redaction is only a starting point for the administrator's edit
  // svelte-ignore state_referenced_locally
  let hardship = $state(redactHardship(item.data.hardship, names)), redactionConfirmed = $state(false);
  let reviewer = $state(""), requiredReviews = $state(""), reason = $state(""), award = $state("");

  const viewHistory = () => act.run(async () => { history = await api(base + "/history"); });

  const importHistory = () => act.run(async () => {
    if (!source.trim() || !terms.trim() || !decidedOn || isBlank(amountHistorical))
      throw new Error("Enter the source, terms, decision date and amount.");
    let parsed;
    try { parsed = ratings.trim() ? JSON.parse(ratings) : []; }
    catch { throw new Error("Historical ratings must be a JSON array."); }
    if (!Array.isArray(parsed)) throw new Error("Historical ratings must be a JSON array.");
    if (!confirm("Import this historical decision for " + item.name + "? Verify the source and account first.")) return;
    await api(base + "/history", { method: "POST", body: {
      source, terms: terms.split(";").map(t => t.trim()), decided_on: decidedOn,
      amount: Number(amountHistorical), approved: approvedHistorical, ratings: parsed,
      revision: item.revision, confirm: true
    } });
    await onchange();
  });

  const enqueue = () => act.run(async () => { await api(base + "/enqueue", { method: "POST" }); await onchange(); });

  const saveHardship = () => act.run(async () => {
    await api(base + "/review-hardship", { method: "PUT", body: {
      text: hardship, confirm_redacted: redactionConfirmed, revision: item.revision
    } });
    await onchange();
  });

  const assign = () => act.run(async () => {
    await api(`/admin/sap/review/${review.review_id}/assign`, { method: "POST", body: { reviewer } });
    await onchange();
  });

  const increaseReviews = () => act.run(async () => {
    const value = Number(requiredReviews);
    if (isBlank(requiredReviews) || !Number.isInteger(value) || value < 2 || value > 5)
      throw new Error("Enter a whole number between 2 and 5.");
    await api(base + "/review-count", { method: "PUT", body: { required_reviews: value } });
    await onchange();
  });

  const resolve = () => act.run(async () => {
    if (!reason.trim()) throw new Error("Enter a reason for resolving the disagreement.");
    if (!confirm("Resolve the disagreement and lock the current ratings?")) return;
    await api(base + "/resolve-review", { method: "POST", body: { reason, revision: item.revision } });
    await onchange();
  });

  const approve = () => act.run(async () => {
    if (isBlank(award)) throw new Error("Enter an award amount.");
    await api(base + "/decision", { method: "POST", body: { approved: true, amount: Number(award) } });
    await onchange();
  });

  const reject = () => act.run(async () => {
    await api(base + "/decision", { method: "POST", body: { approved: false, amount: 0 } });
    await onchange();
  });
</script>

<div class="card">
  <h3>{item.name}</h3>
  <p>Eligible: {item.eligible} — {item.roster_reason}</p>
  <p>Status: {item.submission_status}; review: {item.review_stage || "not started"}</p>
  <pre>{JSON.stringify(item.data, null, 2)}</pre>
  <pre>{JSON.stringify(item.wfni, null, 2)}</pre>

  <button class="secondary" onclick={viewHistory}>View previous applications</button>
  {#if history}<pre>{JSON.stringify(history, null, 2)}</pre>{/if}

  <h4>Import a completed historical decision</h4>
  <p class="help">Only import verified records for this account. This does not record a payment.</p>
  <label for={"hs-" + item.uuid}>Historical source reference</label>
  <input id={"hs-" + item.uuid} maxlength="200" bind:value={source} />
  <label for={"ht-" + item.uuid}>Historical terms (semicolon-separated)</label>
  <input id={"ht-" + item.uuid} bind:value={terms} />
  <label for={"hd-" + item.uuid}>Historical decision date</label>
  <input id={"hd-" + item.uuid} type="date" bind:value={decidedOn} />
  <label for={"ha-" + item.uuid}>Historical awarded amount</label>
  <input id={"ha-" + item.uuid} type="number" min="0" step="0.01" bind:value={amountHistorical} />
  <label class="opt"><input type="checkbox" bind:checked={approvedHistorical} /> Approved</label>
  <label for={"hr-" + item.uuid}>Historical ratings (optional JSON)</label>
  <textarea id={"hr-" + item.uuid} rows="3" placeholder={'[{"reviewer":"Name","level":3,"notes":""}]'} bind:value={ratings}></textarea>
  <button class="secondary" onclick={importHistory}>Import historical decision</button>

  <h4>Banking</h4>
  <BankingControls paths={bankingPaths} name={item.name} {onchange} />
  <NoticeButton program="sap" uid={item.uuid} />

  {#if !review}
    <button class="primary" onclick={enqueue}>Start review</button>
  {:else}
    <p>Review ID: {review.review_id}</p>
    <pre>{JSON.stringify({ reviews: review.reviews, resolution: review.resolution, decision: review.decision }, null, 2)}</pre>
    {#if !item.review_hardship_prepared}
      <p class="help">Automatic redaction is incomplete. Check for names, identifying circumstances and contact details before confirming.</p>
      <label for={"rs-" + item.uuid}>Redacted statement</label>
      <textarea id={"rs-" + item.uuid} maxlength="3000" bind:value={hardship} oninput={() => (redactionConfirmed = false)}></textarea>
      <button class="secondary" onclick={() => { hardship = redactHardship(hardship, names); redactionConfirmed = false; }}>
        Apply automatic redaction again
      </button>
      <label><input type="checkbox" bind:checked={redactionConfirmed} /> I have reviewed and removed identifying information</label>
      <button class="primary" onclick={saveHardship}>Save redacted statement</button>
    {/if}
    <label for={"rv-" + item.uuid}>Reviewer account UUID</label>
    <input id={"rv-" + item.uuid} bind:value={reviewer} />
    <button class="secondary" onclick={assign}>Assign reviewer</button>
    {#if !review.decision && !review.resolution}
      <label for={"rc-" + item.uuid}>Required reviews (increase only, 2–5)</label>
      <input id={"rc-" + item.uuid} type="number" min="2" max="5" step="1" bind:value={requiredReviews} />
      <button class="secondary" onclick={increaseReviews}>Increase required reviews</button>
      {#if review.stage === "discuss"}
        <textarea maxlength="500" placeholder="Reason for resolving the disagreement" bind:value={reason}></textarea>
        <button class="secondary" onclick={resolve}>Resolve disagreement</button>
      {/if}
    {/if}
    {#if !review.decision}
      <label for={"aw-" + item.uuid}>Award amount (0 to reject)</label>
      <input id={"aw-" + item.uuid} type="number" min="0" step="0.01" bind:value={award} />
      <button class="primary" onclick={approve}>Approve</button>
      <button class="secondary" onclick={reject}>Reject</button>
    {/if}
  {/if}
  <Status action={act} />
</div>
