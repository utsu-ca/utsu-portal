<script>
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import Status from "../../../components/Status.svelte";

  const load = new Action();
  let result = $state(null);
  onMount(() => load.run(async () => { result = await api("/senate/current"); }));
</script>

<div class="card">
  <h2>Published Senate results</h2>
  <Status action={load} />
  {#if load.busy || load.error}
    <!-- loading or failed -->
  {:else if !result}
    <p>No results have been published.</p>
  {:else}
    <p>Version {result.version} — seed {result.seed}</p>
    <p>Inputs hash: {result.inputs_hash}</p>
    {#if !result.seated.length}<p>No seats filled.</p>{/if}
    {#each result.seated as seat}
      <p>{seat.constituency}{seat.division ? " — " + seat.division : ""}: {seat.name}</p>
    {/each}
  {/if}
</div>
