<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";

  const load = new Action(), act = new Action();
  let mine = $state([]), queue = $state([]);

  const refresh = async () => {
    mine = await api("/sap/my-reviews");
    queue = await api("/sap/queue");
  };
  onMount(() => load.run(refresh));

  const claim = item => act.run(async () => {
    await api(`/sap/queue/${item.review_id}/claim`, { method: "POST" });
    go("sapReviewDetail", { id: item.review_id });
  });

  const conflict = item => act.run(async () => {
    await api(`/sap/review/${item.review_id}/conflict`, { method: "POST" });
    await refresh();
  });

  const priority = item =>
    item.emergency === true ? " — emergency" : item.emergency === null ? " — emergency status unknown" : "";
</script>

<div class="card">
  <h2>SABP review (experimental)</h2>
  <p>Only manually reviewed statements are available. Declare a conflict if you cannot review an application.</p>
</div>
<Status action={load} />
<div class="card"><h3>My reviews</h3></div>
{#if !mine.length}<div class="card"><p>No active reviews.</p></div>{/if}
{#each mine as item (item.review_id)}
  <div class="card">
    <p>Review {item.review_id} — {item.stage}</p>
    <button class="primary" onclick={() => go("sapReviewDetail", { id: item.review_id })}>Open</button>
  </div>
{/each}
<div class="card"><h3>Available reviews</h3></div>
{#if !queue.length}<div class="card"><p>No reviews available.</p></div>{/if}
{#each queue as item (item.review_id)}
  <div class="card">
    <p>Review {item.review_id} — {item.stage}{priority(item)}</p>
    <button class="primary" onclick={() => claim(item)}>Claim</button>
    <button class="secondary" onclick={() => conflict(item)}>Declare conflict</button>
  </div>
{/each}
<Status action={act} />
