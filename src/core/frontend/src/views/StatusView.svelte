<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";

  const load = new Action();
  let submissions = $state(null), previous = $state([]);

  onMount(() => load.run(async () => {
    submissions = await api("/me/submissions");
    if (submissions.some(s => s.form_id === "student_aid_bursary")) previous = await api("/me/sap/history");
  }));
</script>

<Status action={load} />
{#if submissions}
  {#if !submissions.length}<div class="card"><p>No applications yet.</p></div>{/if}
  {#each submissions as s (s.form_id)}
    <div class="card">
      <h3>{s.form_id}</h3>
      <span class="badge">{s.status}</span>
      <p>{s.note || ""}</p>
      <ul>
        {#each s.history as h}<li>{new Date(h.at * 1000).toLocaleString()} — {h.status} {h.note || ""}</li>{/each}
      </ul>
    </div>
  {/each}
  {#each previous as item}
    <div class="card">
      <h3>Previous SABP application</h3>
      <p>Terms: {item.data.terms.join(", ")} — {item.status}</p>
      <p>Decision: {item.decision.approved ? "approved" : "rejected"} — ${item.decision.amount}</p>
    </div>
  {/each}
{/if}
