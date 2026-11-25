<script>
  import { onMount } from "svelte";
  import Status from "../../../components/Status.svelte";
  import { Action } from "../../../lib/action.svelte.js";
  import { api, apiAllPages } from "../../../lib/api.js";
  import { formatCents, formatDate, STATUS_LABELS } from "../lib/money.js";

  const load = new Action(), act = new Action();
  let status = $state("refund_requested"), items = $state([]), notes = $state({}), banking = $state({});

  const refresh = () => load.run(async () => { items = await apiAllPages(`/finance/fees/refunds?status=${status}`); });
  onMount(refresh);

  const reveal = item => act.run(async () => {
    banking[item.id] = await api(`/finance/fees/refunds/${encodeURIComponent(item.id)}/banking`);
  });

  const decide = (item, decision) => act.run(async () => {
    await api(`/finance/fees/refunds/${encodeURIComponent(item.id)}/decision`, {
      method: "POST", body: { decision, note: notes[item.id] || "" },
    });
    delete banking[item.id];
    await refresh();
  }, decision === "refunded" ? "Refund recorded as paid." : "Refund denied.");
</script>

<div class="card">
  <h2>Fee refund requests</h2>
  <p>Refunds are paid to the requester's banking information. Viewing it is recorded in the audit log.</p>
  <label for="refund-status">Show</label>
  <select id="refund-status" bind:value={status} onchange={refresh}>
    {#each ["refund_requested", "refunded", "denied"] as option}<option value={option}>{STATUS_LABELS[option]}</option>{/each}
  </select>
  <Status action={load} />
  <Status action={act} />
  {#each items as item (item.id)}
    <div class="card">
      <strong>{item.name || item.uuid}</strong>: {item.fee_name}, {item.session_label}
      <span class="badge">{formatCents(item.amount_cents)}</span>
      <p class="help">Requested {formatDate(item.requested_at)}. Banking: {item.banking_status.replaceAll("_", " ")}.</p>
      {#if item.note}<p class="help">Note: {item.note}</p>{/if}
      {#if item.status === "refund_requested"}
        {#if banking[item.id]}
          <p>Institution {banking[item.id].institution_number}, transit {banking[item.id].transit_number},
            account {banking[item.id].account_number}</p>
        {:else}
          <button class="secondary" disabled={act.busy} onclick={() => reveal(item)}>Show banking details</button>
        {/if}
        <label for={`note-${item.id}`}>Note (required to deny)</label>
        <input id={`note-${item.id}`} maxlength="500" bind:value={notes[item.id]} />
        <button class="primary" disabled={act.busy} onclick={() => decide(item, "refunded")}>Mark refunded</button>
        <button class="secondary" disabled={act.busy} onclick={() => decide(item, "denied")}>Deny</button>
      {/if}
    </div>
  {:else}
    {#if !load.busy && !load.error}<p>No requests.</p>{/if}
  {/each}
</div>
