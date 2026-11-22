<script>
  import { onMount } from "svelte";
  import { Action, api, go, Status } from "@utsu/portal";

  const load = new Action();
  let application = $state(undefined);
  onMount(() => load.run(async () => { application = await api("/me/senate"); }));
</script>

<Status action={load} />
{#if application === null}
  <div class="card">
    <h2>My Senate application</h2>
    <p>You have not applied yet.</p>
    <button class="primary" onclick={() => go("form", { id: "senate_application" })}>Apply for a seat</button>
  </div>
{:else if application}
  <div class="card">
    <h2>My Senate application</h2>
    <p>Status: <span class="badge">{application.submission_status}</span></p>
    <p>{application.eligible ? "You are eligible for seating." : "You are not eligible: " + application.roster_reason}</p>
    <button class="secondary" onclick={() => go("form", { id: "senate_application" })}>Edit my application</button>
  </div>
{/if}
