<script>
  import { onMount } from "svelte";
  import { Action, api, downloadReport, isBlank, NoticeButton, Status } from "@utsu/portal";

  const constituencies = [
    "First-Year Student", "Commuter", "Residence", "Mature Students and Student Parents",
    "International Students", "First Generation Student", "Small and Specialized Program",
    "General Member", "Faculty/College"
  ];

  const load = new Action(), save = new Action(), run = new Action(), publish = new Action(), exporting = new Action();
  let seats = $state([]), seed = $state("2026"), preview = $state(null), output = $state("");
  let revision = 0, dirty = false, nextId = 0;
  let page = $state(null), cursor = $state("");

  const addSeat = (seat = {}) => {
    seats.push({ id: nextId++, constituency: seat.constituency || constituencies[0], division: seat.division || "",
      count: String(seat.seats ?? 0) });
  };

  // Any edit makes an earlier preview stale: it can no longer be published.
  function invalidate() {
    revision++;
    preview = null;
    output = "";
  }
  function changed() {
    dirty = true;
    invalidate();
  }

  async function refresh() {
    const config = await api("/admin/senate/seats");
    seats = [];
    for (const seat of config.seats) addSeat(seat);
    dirty = false;
    invalidate();
    page = await api("/admin/senate/applications?limit=100&cursor=" + encodeURIComponent(cursor));
  }
  onMount(() => load.run(refresh));

  const saveSeats = () => save.run(async () => {
    const body = seats.map(row => {
      if (isBlank(row.count) || !Number.isInteger(Number(row.count))) throw new Error("Enter an integer seat count.");
      return { constituency: row.constituency, division: row.division, seats: Number(row.count) };
    });
    const requestRevision = revision;
    await api("/admin/senate/seats", { method: "PUT", body: { seats: body } });
    if (requestRevision === revision) dirty = false;
    invalidate();
    save.ok = dirty ? "Saved; newer edits still need saving." : "Seat configuration saved.";
  });

  const previewSeating = () => run.run(async () => {
    invalidate();
    if (dirty) throw new Error("Save seat configuration changes before previewing.");
    const value = Number(seed);
    if (isBlank(seed) || !Number.isSafeInteger(value)) throw new Error("Enter a safe integer seed.");
    const requestRevision = revision;
    const result = await api("/admin/senate/seating", { method: "POST", body: { seed: value } });
    if (requestRevision !== revision) throw new Error("Inputs changed while computing; request a new preview.");
    preview = { seed: value, inputs_hash: result.inputs_hash };
    output = JSON.stringify(result, null, 2);
  });

  const publishSeating = () => publish.run(async () => {
    if (!preview) throw new Error("Preview seating before publishing.");
    if (!confirm("Publish the reviewed seating results?")) return;
    const result = await api("/admin/senate/seating/publish", { method: "POST", body: preview });
    invalidate();
    publish.ok = `Published version ${result.version}.`;
  });

  const goTo = next => {
    cursor = next;
    load.run(refresh);
  };
</script>

<div class="card">
  <h2>Senate administration</h2>
  <Status action={load} />
  <h3>Seats</h3>
  {#each seats as row (row.id)}
    <div class="row">
      <select aria-label="Constituency" bind:value={row.constituency} oninput={changed}>
        {#each constituencies as name}<option value={name}>{name}</option>{/each}
      </select>
      <input aria-label="Division" placeholder="Division (Faculty/College only)" bind:value={row.division} oninput={changed} />
      <input aria-label="Seat count" type="number" min="0" max="500" step="1" bind:value={row.count} oninput={changed} />
      <button class="secondary" onclick={() => { seats = seats.filter(s => s.id !== row.id); changed(); }}>Remove</button>
    </div>
  {/each}
  <button class="secondary" onclick={() => { addSeat(); changed(); }}>Add seat</button>
  <button class="primary" onclick={saveSeats} disabled={save.busy}>Save seat configuration</button>
  <Status action={save} />
  <label for="seed">Seating seed (integer)</label>
  <input id="seed" type="number" step="1" bind:value={seed} oninput={invalidate} />
  <button class="primary" onclick={previewSeating} disabled={run.busy}>Preview seating</button>
  <button class="primary" onclick={publishSeating} disabled={!preview || publish.busy}>Publish reviewed preview</button>
  <Status action={run} />
  <Status action={publish} />
  <pre>{output}</pre>
</div>

{#if page}
  <div class="card"><h3>Applications</h3></div>
  {#if !page.items.length}<div class="card"><p>No applications.</p></div>{/if}
  {#each page.items as item (item.id)}
    <div class="card">
      <h3>{item.name}</h3>
      <p>Eligible: {item.eligible} — {item.roster_reason}</p>
      <p>Status: {item.submission_status}</p>
      <pre>{JSON.stringify(item.data, null, 2)}</pre>
      <NoticeButton program="senate" uid={item.uuid} />
    </div>
  {/each}
  <button class="secondary" onclick={() => exporting.run(() => downloadReport("/admin/senate/results.csv", "senate-results.csv"))}>
    Download published results CSV
  </button>
  <Status action={exporting} />
  {#if page.next_cursor}<button class="secondary" onclick={() => goTo(page.next_cursor)}>Next page</button>{/if}
{/if}
