<script>
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import Status from "../../../components/Status.svelte";

  const base = "/admin/agm/verifications";
  const load = new Action(), review = new Action();
  let items = $state(null), selected = $state(null), scans = $state(null), note = $state("");
  const refresh = () => load.run(async () => {
    items = await api(base);
    if (!items.some(item => item.id === selected)) close();
  });
  function close() { selected = null; scans = null; note = ""; }
  onMount(() => { refresh(); });

  const open = item => review.run(async () => {
    close();
    selected = item.id;
    scans = await api(`${base}/${item.id}/media`);
  });

  const decide = approve => review.run(async () => {
    try {
      await api(`${base}/${selected}/decision`, { method: "POST", body: { approve, note } });
    } catch (error) {
      if (error.status === 404) {
        await refresh();
        throw new Error("This check is no longer pending. The queue has been refreshed.");
      }
      throw error;
    }
    close();
    await refresh();
  }, approve ? "Approved. The attendee can now sign in to Zoom." : "Returned to the attendee for resubmission.");
</script>

<div class="card">
  <h2>AGM online ID review queue</h2>
  <p>Compare the submitted face and ID photos before approving access to Zoom. If they cannot be verified, tell the attendee what to capture again.</p>
  <button class="secondary" onclick={refresh} disabled={load.busy || review.busy}>Refresh queue</button>
  <Status action={load} />
  <Status action={review} />
  {#if items}<p>{items.length ? `${items.length} pending` : "Nothing pending."}</p>{/if}
</div>
{#each items || [] as item (item.id)}
  <div class="card">
    <h3>#{item.position} · {item.first_name} {item.last_name}</h3>
    {#if item.student_number}<p>Student number ending in {String(item.student_number).slice(-4)}</p>{/if}
    <p class="meta">Submitted {new Date(item.submitted_at * 1000).toLocaleString()}</p>
    {#if !item.can_review}
      <p class="help">Another reviewer must decide your own check.</p>
    {:else if selected === item.id && scans}
      <div class="row">
        <img class="shot" alt="Face scan" src={scans.face} />
        <img class="shot" alt="Student ID card" src={scans.id_card} />
      </div>
      <label for="review-note">Resubmission instructions (required to return)</label>
      <textarea id="review-note" rows="3" maxlength="500" bind:value={note} disabled={review.busy}></textarea>
      <div class="actions">
        <button class="primary" onclick={() => decide(true)} disabled={review.busy || load.busy}>Approve Zoom sign-in</button>
        <button class="secondary" onclick={() => decide(false)} disabled={!note.trim() || review.busy || load.busy}>Request new photos</button>
        <button class="secondary" onclick={close} disabled={review.busy || load.busy}>Hide photos</button>
      </div>
    {:else}
      <button class="secondary" onclick={() => open(item)} disabled={review.busy || load.busy}>Review photos</button>
    {/if}
  </div>
{/each}
