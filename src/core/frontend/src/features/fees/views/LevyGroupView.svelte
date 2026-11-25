<script>
  import { onMount } from "svelte";
  import Status from "../../../components/Status.svelte";
  import { Action } from "../../../lib/action.svelte.js";
  import { api, apiAllPages } from "../../../lib/api.js";
  import { formatCents, formatDate, STATUS_LABELS } from "../lib/money.js";

  // Up-to-date opt-outs and the levy each fee is predicted to bring in, for the levy groups the member belongs to.
  const load = new Action();
  let groups = $state([]), groupId = $state(""), session = $state(""), report = $state(null), optouts = $state([]);

  const refresh = () => load.run(async () => {
    report = await api(`/levy-groups/${groupId}${session ? `?session=${encodeURIComponent(session)}` : ""}`);
    session = report.session;
    optouts = await apiAllPages(`/levy-groups/${groupId}/optouts?session=${encodeURIComponent(session)}`);
  });

  onMount(() => load.run(async () => {
    groups = (await api("/levy-groups")).items;
    if (groups.length) {
      groupId = groups[0].id;
      await refresh();
    }
  }));

  const pick = () => { session = ""; refresh(); };
  const percent = rate => (rate == null ? "—" : `${(rate * 100).toFixed(1)}%`);
</script>

<Status action={load} />
<div class="card">
  <h2>Levy groups</h2>
  {#if groups.length === 0 && !load.busy}
    <p>You are not a member of a levy group.</p>
  {:else}
    {#if groups.length > 1}
      <label for="levy-group">Levy group</label>
      <select id="levy-group" bind:value={groupId} onchange={pick}>
        {#each groups as option (option.id)}<option value={option.id}>{option.name}</option>{/each}
      </select>
    {/if}
  {/if}
</div>

{#if report}
  <div class="card">
    <h3>{report.group.name}</h3>
    <label for="levy-session">Session</label>
    <select id="levy-session" bind:value={session} onchange={refresh}>
      {#each report.sessions as option (option.session)}
        <option value={option.session}>{option.label}{option.open ? " (opt-outs open)" : ""}</option>
      {/each}
    </select>
    <p class="help">
      Headcount: {report.enrolment.value ?? "not set"}
      {#if report.enrolment.source === "roster"}(from the student roster){:else if report.enrolment.source === "admin"}(set by the UTSU){/if}.
      Predictions assume pending refund requests are granted.
    </p>
    <table class="grid">
      <thead>
        <tr><th>Fee</th><th>Amount</th><th>Opt-outs</th><th>Pending refunds</th><th>Rate</th>
          <th>Gross levy</th><th>Confirmed</th><th>Predicted</th></tr>
      </thead>
      <tbody>
        {#each report.fees as row (row.fee_id)}
          <tr>
            <td>{row.name}</td><td>{formatCents(row.amount_cents)}</td><td>{row.opt_out_count}</td>
            <td>{row.refund_requested}</td><td>{percent(row.opt_out_rate)}</td>
            <td>{formatCents(row.gross_cents)}</td><td>{formatCents(row.confirmed_net_cents)}</td>
            <td><strong>{formatCents(row.predicted_net_cents)}</strong></td>
          </tr>
        {/each}
      </tbody>
      <tfoot>
        <tr><th>Total</th><td></td><td>{report.totals.opt_out_count ?? "—"}</td><td></td><td></td>
          <td>{formatCents(report.totals.gross_cents)}</td><td>{formatCents(report.totals.confirmed_net_cents)}</td>
          <td><strong>{formatCents(report.totals.predicted_net_cents)}</strong></td></tr>
      </tfoot>
    </table>
  </div>

  <div class="card">
    <h3>Who opted out</h3>
    <table class="grid">
      <thead><tr><th>Name</th><th>Fee</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead>
      <tbody>
        {#each optouts as record (record.id)}
          <tr><td>{record.name}</td><td>{record.fee_name}</td><td>{formatCents(record.amount_cents)}</td>
            <td>{STATUS_LABELS[record.status]}</td><td>{formatDate(record.requested_at)}</td></tr>
        {:else}
          <tr><td colspan="5">Nobody has opted out yet.</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
