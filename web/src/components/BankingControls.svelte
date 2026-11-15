<script>
  import { api, downloadReport } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "./Status.svelte";

  // Banking actions for one applicant. `paths` names the endpoints (the finance portal and the SABP admin use
  // different prefixes); `status`, when known, hides the actions that do not apply.
  let { paths, name = "", status = undefined, onchange = () => {} } = $props();

  const details = new Action(), flag = new Action(), compare = new Action(), accept = new Action(),
    cheque = new Action(), destroy = new Action();
  let numbers = $state(null), comparison = $state(null);

  const canFlag = () => !status || ["on_file", "reverify_requested"].includes(status);
  const canCompare = () => !status || status === "reverify_received";

  const showDetails = () => details.run(async () => { numbers = await api(paths.details); });

  // One click: the applicant is emailed a link to the banking information verification page.
  const validationFailed = () => flag.run(async () => {
    await api(paths.flag, { method: "POST" });
    await onchange();
  }, `${name || "The applicant"} was emailed a link to re-enter their banking information and upload a void cheque.`);

  const review = () => compare.run(async () => {
    comparison = await api(paths.pending);
    numbers = null;
  });

  const downloadCheque = () => cheque.run(() => downloadReport(paths.cheque, "void-cheque.pdf"));

  const acceptPending = () => accept.run(async () => {
    if (!confirm("Replace the active banking details with the new details?")) return;
    await api(paths.accept, { method: "POST", body: { confirm: true, revision: comparison.revision } });
    comparison = null;
    await onchange();
  }, "New banking details accepted.");

  const destroyDetails = () => destroy.run(async () => {
    if (!confirm("Remove banking details from the active store? Backups and database history are not erased.")) return;
    await api(paths.details, { method: "DELETE", body: { confirm: true } });
    numbers = comparison = null;
    await onchange();
  }, "Banking details removed from the active store.");
</script>

<div>
  {#if status !== "destroyed"}
    <button class="secondary" onclick={showDetails} disabled={details.busy}>View banking details</button>
    {#if canFlag()}
      <button class="primary" onclick={validationFailed} disabled={flag.busy}>
        {status === "reverify_requested" ? "Send the request again" : "Banking failed validation"}
      </button>
    {/if}
    {#if canCompare()}
      <button class="secondary" onclick={review} disabled={compare.busy}>Review new details</button>
    {/if}
    {#if paths.destroy}
      <button class="danger" onclick={destroyDetails} disabled={destroy.busy}>Destroy banking details</button>
    {/if}
  {/if}
  <Status action={details} />
  <Status action={flag} />
  <Status action={compare} />
  <Status action={destroy} />
  {#if numbers}
    <dl class="bank-details">
      <dt>Institution</dt><dd>{numbers.institution_number}</dd>
      <dt>Transit</dt><dd>{numbers.transit_number}</dd>
      <dt>Account</dt><dd>{numbers.account_number}</dd>
    </dl>
  {/if}
  {#if comparison}
    <table class="grid bank-details">
      <thead><tr><th></th><th>On file</th><th>Newly submitted</th></tr></thead>
      <tbody>
        <tr><th>Institution</th><td>{comparison.active.institution_number}</td><td>{comparison.pending.institution_number}</td></tr>
        <tr><th>Transit</th><td>{comparison.active.transit_number}</td><td>{comparison.pending.transit_number}</td></tr>
        <tr><th>Account</th><td>{comparison.active.account_number}</td><td>{comparison.pending.account_number}</td></tr>
      </tbody>
    </table>
    <p class="help">Void cheque: PDF, {Math.round(comparison.void_cheque.size / 1024)} KB, SHA-256 {comparison.void_cheque.sha256.slice(0, 12)}…</p>
    <button class="secondary" onclick={downloadCheque} disabled={cheque.busy}>Download void cheque (PDF)</button>
    <button class="primary" onclick={acceptPending} disabled={accept.busy}>Accept new details</button>
    <Status action={cheque} />
    <Status action={accept} />
  {/if}
</div>
