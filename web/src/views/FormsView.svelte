<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";

  const load = new Action();
  let forms = $state([]);
  onMount(() => load.run(async () => { forms = await api("/forms"); }));
</script>

<Status action={load} />
{#each forms as form (form.id)}
  <div class="card">
    <h3>{form.title}</h3>
    <span class="badge">v{form.version}</span><br />
    <button class="primary" onclick={() => go("form", { id: form.id })}>Open</button>
  </div>
{/each}
