<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { isBlank } from "../lib/forms.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";

  let { id } = $props();
  const load = new Action(), act = new Action();
  let item = $state(null), level = $state("");

  onMount(() => load.run(async () => { item = await api(`/sap/review/${id}`); }));

  const back = () => go("sapReview");

  const rate = () => act.run(async () => {
    if (isBlank(level)) throw new Error("Enter a need level.");
    await api(`/sap/review/${id}`, { method: "PUT", body: { level: Number(level) } });
    back();
  });
  const release = () => act.run(async () => { await api(`/sap/queue/${id}/release`, { method: "POST" }); back(); });
  const conflict = () => act.run(async () => { await api(`/sap/review/${id}/conflict`, { method: "POST" }); back(); });
</script>

<Status action={load} />
{#if item}
  <div class="card">
    <h2>Review {id}</h2>
    <p>Stage: {item.stage}</p>
    <pre>{JSON.stringify({
      amount_requested: item.amount_requested, enrolment: item.enrolment,
      monthly_income: item.monthly_income, monthly_expenses: item.monthly_expenses,
      bursary_type: item.bursary_type, emergency: item.emergency, wfni: item.wfni
    }, null, 2)}</pre>
    <h3>Administrator-redacted statement</h3>
    <p>{item.hardship}</p>
    <label for="level">Need level (0–5)</label>
    <input id="level" type="number" min="0" max="5" step="any" bind:value={level} />
    <button class="primary" onclick={rate} disabled={act.busy}>Submit rating</button>
    <button class="secondary" onclick={release} disabled={act.busy}>Release unrated claim</button>
    <button class="secondary" onclick={conflict} disabled={act.busy}>Declare conflict</button>
    <Status action={act} />
  </div>
{/if}
