<script>
  import { onMount } from "svelte";
  import { api, downloadReport } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import Status from "../../../components/Status.svelte";
  import NoticeButton from "../../../components/NoticeButton.svelte";

  const load = new Action(), check = new Action(), exporting = new Action();
  let name = $state(""), number = $state(""), division = $state(""), result = $state("");
  let page = $state(null), cursor = $state(""), revision = 0;

  // Editing the inputs while a check is in flight discards that check's answer.
  const edited = () => {
    revision++;
    result = "";
  };

  const refresh = async () => {
    page = await api("/admin/agm/rsvps?limit=100&cursor=" + encodeURIComponent(cursor));
  };
  onMount(() => load.run(refresh));

  const checkRoster = () => check.run(async () => {
    result = "";
    const requestRevision = revision;
    const match = await api("/admin/agm/verify", { method: "POST", body: {
      full_name: name, student_number: number, division
    } });
    if (requestRevision !== revision) throw new Error("Inputs changed while checking; check the roster again.");
    result = `${match.verified ? "Verified" : "Not verified"} — ${match.reason}`;
  });

  const goTo = next => {
    cursor = next;
    load.run(refresh);
  };
</script>

<div class="card">
  <h2>AGM administration</h2>
  <h3>Check the student roster</h3>
  <label for="agm-name">Full legal name</label>
  <input id="agm-name" bind:value={name} oninput={edited} />
  <label for="agm-number">Student number (optional)</label>
  <input id="agm-number" bind:value={number} oninput={edited} />
  <label for="agm-division">Division (optional)</label>
  <input id="agm-division" bind:value={division} oninput={edited} />
  <button class="primary" onclick={checkRoster} disabled={check.busy}>Check roster</button>
  <Status action={check} />
  <p>{result}</p>
</div>

<div class="card">
  <h3>Registrations</h3>
  <Status action={load} />
  {#if page}
    {#if !page.items.length}<p>No registrations.</p>{/if}
    {#each page.items as item (item.id)}
      <p>
        {item.name} — {item.attendance} — {item.submission_status} — {item.eligible ? "eligible" : "not eligible"}
        ({item.roster_reason}) — {item.checked_in ? "checked in" : "not checked in"}
      </p>
      {#if item.accessibility}<p>Accessibility: {item.accessibility}</p>{/if}
      {#if item.proxy}<p>Proxy holder: {item.proxy_name}</p>{/if}
      <NoticeButton program="agm" uid={item.uuid} />
    {/each}
    <button class="secondary" onclick={() => exporting.run(() => downloadReport("/admin/agm/rsvps.csv", "agm-registrations.csv"))}>
      Download registrations CSV
    </button>
    <Status action={exporting} />
    {#if page.next_cursor}<button class="secondary" onclick={() => goTo(page.next_cursor)}>Next page</button>{/if}
  {/if}
</div>
