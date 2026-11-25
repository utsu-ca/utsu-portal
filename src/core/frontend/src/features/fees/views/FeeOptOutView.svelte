<script>
  import { onMount } from "svelte";
  import Status from "../../../components/Status.svelte";
  import { Action } from "../../../lib/action.svelte.js";
  import { api } from "../../../lib/api.js";
  import { go } from "../../../lib/nav.svelte.js";
  import { formatCents, formatDate, STATUS_LABELS } from "../lib/money.js";

  // Members opt out of optional fees while a window is open, and ask for a refund of any fee they have already paid.
  // Refunds are paid to the banking information in the member's financial profile.
  const load = new Action(), submit = new Action(), withdraw = new Action();
  let data = $state(null), picked = $state({}), paid = $state({});

  const refresh = () => load.run(async () => { data = await api("/fees/optout"); });
  onMount(refresh);

  const chosen = window => window.fees.filter(fee => picked[`${window.id}:${fee.id}`]);
  const wantsRefund = window => chosen(window).some(fee => paid[`${window.id}:${fee.id}`]);
  const needsBanking = window => wantsRefund(window) && !["on_file", "reverify_requested", "reverify_received"].includes(data.banking_status);

  const send = window => submit.run(async () => {
    await api("/fees/optout", { method: "POST", body: {
      window: window.id,
      items: chosen(window).map(fee => ({ fee_id: fee.id, already_paid: !!paid[`${window.id}:${fee.id}`] })),
    } });
    picked = {};
    paid = {};
    await refresh();
  }, "Your request was recorded.");

  const undo = request => withdraw.run(async () => {
    await api(`/fees/optout/${encodeURIComponent(request.id)}`, { method: "DELETE" });
    await refresh();
  }, "Your opt-out was withdrawn.");
</script>

<Status action={load} />
{#if data}
  <div class="card">
    <h2>Fee opt-outs and refunds</h2>
    <p>Opt-outs open once for the Fall-Winter session and once for the Summer session. If you have not paid a fee yet,
      opting out removes it. If you already paid, say so and request a refund.</p>
  </div>

  {#each data.windows as window (window.id)}
    <div class="card">
      <h3>{window.session_label}</h3>
      {#if window.open}
        <p class="ok">Open until {formatDate(window.closes_at - 1)}.</p>
        {#if window.fees.length === 0}
          <p>There are no fees to opt out of this session.</p>
        {/if}
        {#each window.fees as fee (fee.id)}
          <div class="card">
            <strong>{fee.name}</strong> <span class="badge">{formatCents(fee.amount_cents)}</span>
            {#if fee.description}<span class="help">{fee.description}</span>{/if}
            {#if fee.request}
              <p>{STATUS_LABELS[fee.request.status]}</p>
              {#if ["opted_out", "refund_requested"].includes(fee.request.status)}
                <button class="secondary" disabled={withdraw.busy} onclick={() => undo(fee.request)}>Withdraw and keep this fee</button>
              {/if}
            {:else}
              <label class="opt"><input type="checkbox" bind:checked={picked[`${window.id}:${fee.id}`]} /> Opt out of {fee.name}</label>
              {#if picked[`${window.id}:${fee.id}`]}
                {#if fee.refundable}
                  <label class="opt"><input type="checkbox" bind:checked={paid[`${window.id}:${fee.id}`]} /> I have already paid this fee; request a refund</label>
                {:else}
                  <span class="help">This fee is not refundable once paid.</span>
                {/if}
              {/if}
            {/if}
          </div>
        {/each}
        {#if needsBanking(window)}
          <p class="err" role="alert">A refund is paid to your banking information, which we do not have yet.</p>
          <button class="secondary" onclick={() => go("banking", { shared: true })}>Add banking information</button>
        {/if}
        {#if chosen(window).length}
          <button class="primary" disabled={submit.busy || needsBanking(window)} onclick={() => send(window)}>
            Submit {chosen(window).length === 1 ? "opt-out" : `${chosen(window).length} opt-outs`}
          </button>
        {/if}
      {:else}
        <p>Closed. The next window opens {formatDate(window.opens_at)}.</p>
      {/if}
    </div>
  {/each}
  <Status action={submit} />
  <Status action={withdraw} />

  <div class="card">
    <h3>Your requests</h3>
    {#each data.requests.filter(request => request.status !== "withdrawn") as request (request.id)}
      <p><strong>{request.fee_name}</strong>, {request.session_label}: {formatCents(request.amount_cents)}
        <span class="badge">{STATUS_LABELS[request.status]}</span></p>
    {:else}
      <p>You have not opted out of any fees.</p>
    {/each}
  </div>
{/if}
