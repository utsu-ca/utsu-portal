<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";

  const base = "/admin/verifications";
  const load = new Action(), act = new Action();
  let list = $state([]), scans = $state({});

  const refresh = async () => { list = await api(base); };
  onMount(() => load.run(refresh));

  const show = v => act.run(async () => { scans[v.id] = await api(`${base}/${v.id}/media`); });
  const decide = (v, approve) => act.run(async () => {
    await api(`${base}/${v.id}/decision`, { method: "POST", body: { approve } });
    await refresh();
  });
</script>

<Status action={load} />
{#if !list.length && !load.busy}<div class="card"><p>Nothing pending.</p></div>{/if}
{#each list as v (v.id)}
  <div class="card">
    <strong>{v.kind}: {v.first_name} {v.last_name}</strong>
    <p>Divisions: {v.division_codes.join(", ")}</p>
    <pre>{JSON.stringify(v.changes)}</pre>
    {#if scans[v.id]}
      <div class="row">
        <img class="shot" alt="Face scan" src={scans[v.id].face} />
        <img class="shot" alt="ID card scan" src={scans[v.id].id_card} />
      </div>
    {/if}
    <button class="secondary" onclick={() => show(v)}>Show scans</button>
    <button class="primary" onclick={() => decide(v, true)}>Approve</button>
    <button class="danger" onclick={() => decide(v, false)}>Reject</button>
  </div>
{/each}
<Status action={act} />
