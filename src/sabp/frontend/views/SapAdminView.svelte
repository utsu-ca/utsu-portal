<script>
  import { onMount } from "svelte";
  import { Action, api, apiAllPages, downloadReport, Status } from "@utsu/portal";
  import SapApplicationCard from "../components/SapApplicationCard.svelte";
  import { defaultWeights } from "@utsu/portal";

  const load = new Action(), loadWeights = new Action(), saveWeights = new Action(), exporting = new Action();
  let weights = $state(""), page = $state(null), report = $state([]), cursor = $state("");
  let weightsConfigured = $state(null);

  const refresh = async () => {
    page = await api("/admin/sap/applications?limit=100&cursor=" + encodeURIComponent(cursor));
    report = await apiAllPages("/admin/sap/review-report");
  };
  const reload = () => load.run(refresh);
  const fetchWeights = () => loadWeights.run(async () => {
    try {
      const config = await api("/admin/sap/weights");
      if (typeof config?.csv !== "string" || !config.csv) throw new Error("The saved weights response is invalid.");
      weights = config.csv;
      weightsConfigured = true;
    } catch (error) {
      if (error.status !== 404) throw error;
      weights = defaultWeights;
      weightsConfigured = false;
    }
  });
  onMount(() => { reload(); fetchWeights(); });

  const save = () => saveWeights.run(async () => {
    const result = await api("/admin/sap/weights", { method: "PUT", body: { csv: weights } });
    weightsConfigured = true;
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
  {#if weightsConfigured === false}
    <p>These are draft default weights. Review and save them before they are used for scoring.</p>
  {:else if weightsConfigured}
    <p>These are the saved scoring weights. Review changes before saving.</p>
  {/if}
  <Status action={loadWeights} />
  {#if loadWeights.error}<button class="secondary" onclick={fetchWeights}>Retry loading weights</button>{/if}
  <textarea rows="8" placeholder="field,answer,points" aria-label="WFNI weights CSV" bind:value={weights}
    disabled={weightsConfigured === null || loadWeights.busy}></textarea>
  <button class="primary" onclick={save} disabled={saveWeights.busy || weightsConfigured === null || loadWeights.busy}>Save WFNI weights</button>
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
