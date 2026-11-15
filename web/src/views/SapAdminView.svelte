<script>
  import { onMount } from "svelte";
  import { api, downloadReport } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";
  import SapApplicationCard from "../components/SapApplicationCard.svelte";

  const load = new Action(), saveWeights = new Action(), exporting = new Action();
  let weights = $state(""), page = $state(null), report = $state([]), cursor = $state("");

  const refresh = async () => {
    page = await api("/admin/sap/applications?limit=100&cursor=" + encodeURIComponent(cursor));
    report = await api("/admin/sap/review-report");
  };
  const reload = () => load.run(refresh);
  onMount(reload);

  const save = () => saveWeights.run(async () => {
    const result = await api("/admin/sap/weights", { method: "PUT", body: { csv: weights } });
    saveWeights.ok = `Weights saved (SHA-256: ${result.hash}). Reload applications to see updated scores.`;
  });
</script>

<div class="card">
  <h2>SABP administration (experimental)</h2>
  <p>Review statements manually for all identifying information before confirming redaction.</p>
  <button class="secondary" onclick={() => exporting.run(() => downloadReport("/admin/sap/applications.csv", "sap-applications.csv"))}>
    Download applications CSV
  </button>
  <Status action={exporting} />
</div>
<div class="card">
  <h3>WFNI weights</h3>
  <p>Paste a complete weights CSV to configure or replace scoring weights.</p>
  <textarea rows="8" placeholder="field,answer,points" aria-label="WFNI weights CSV" bind:value={weights}></textarea>
  <button class="primary" onclick={save} disabled={saveWeights.busy}>Save WFNI weights</button>
  <Status action={saveWeights} />
</div>
<Status action={load} />
{#if page}
  {#each page.items as item (item.uuid)}
    <SapApplicationCard {item} review={report.find(r => r.uuid === item.uuid)} onchange={reload} />
  {/each}
  {#if page.next_cursor}
    <button class="secondary" onclick={() => { cursor = page.next_cursor; reload(); }}>Next page</button>
  {/if}
{/if}
