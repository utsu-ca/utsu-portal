<script>
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import Status from "../../../components/Status.svelte";

  // UTSU administrators decide club registrations (more than one approval may be needed).
  const load = new Action(), act = new Action();
  let clubs = $state([]), notes = $state({});

  const refresh = async () => { clubs = await api("/admin/clubs?status=pending"); };
  onMount(() => load.run(refresh));

  const decide = (club, approve) => act.run(async () => {
    await api(`/admin/clubs/${club.id}/decision`, { method: "POST", body: { approve, note: notes[club.id] || "" } });
    await refresh();
  });
</script>

<Status action={load} />
<div class="card"><h2>Club registrations to review</h2></div>
{#if !clubs.length && !load.busy}<div class="card"><p>Nothing pending.</p></div>{/if}
{#each clubs as club (club.id)}
  <div class="card">
    <h3>{club.name}</h3>
    <p>{club.category} — {club.approvals.length} decision(s) so far</p>
    <p>{club.description}</p>
    <input placeholder="note" aria-label={"Note for " + club.name} bind:value={notes[club.id]} />
    <button class="primary" onclick={() => decide(club, true)} disabled={act.busy}>Approve</button>
    <button class="danger" onclick={() => decide(club, false)} disabled={act.busy}>Reject</button>
  </div>
{/each}
<Status action={act} />
