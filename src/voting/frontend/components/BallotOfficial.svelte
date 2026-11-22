<script>
  // Elections-official tools for one ballot: open, close or cancel it, and see who voted how. Seeing votes on a
  // sealed ballot breaks the seal: it needs a reason, is logged in the audit trail and is listed on the ballot for
  // every voter. A secret ballot stores no link to voters, so there is nothing to open.
  import { Action, api, Status } from "@utsu/portal";

  let { ballot, onchange = () => {} } = $props();

  const act = new Action();
  const glass = new Action();
  let cancelling = $state(false);
  let cancelReason = $state("");
  let unsealing = $state(false);
  let reason = $state("");
  let roll = $state(null);

  const sealed = $derived(ballot.visibility === "sealed");
  const path = $derived(`/voting/ballots/${ballot.id}`);
  const reasonOk = $derived(sealed ? reason.trim().length >= 10 : true);

  async function transition(step, okMessage, body) {
    const out = await act.run(() => api(`${path}/${step}`, { method: "POST", ...(body ? { body } : {}) }), okMessage);
    if (out) {
      cancelling = false;
      cancelReason = "";
      onchange();
    }
  }

  async function openRoll(event) {
    event?.preventDefault();
    const out = await glass.run(() => api(`${path}/unseal`, { method: "POST", body: { reason: reason.trim() } }));
    if (out) {
      roll = out.roll;
      unsealing = false;
      reason = "";
      onchange();
    }
  }
</script>

<section class="card" aria-label="Elections official tools">
  <h3>Elections official</h3>
  {#if ballot.manage}
    <div class="actions">
      {#if ballot.status === "draft"}
        <button class="primary" disabled={act.busy} onclick={() => transition("open", "Voting is open.")}>Open voting now</button>
      {/if}
      {#if ballot.status === "open"}
        <button class="primary" disabled={act.busy} onclick={() => transition("close", "Voting is closed. The count follows shortly.")}>Close voting now</button>
      {/if}
      {#if ["draft", "open"].includes(ballot.status) && !cancelling}
        <button class="secondary" onclick={() => (cancelling = true)}>Cancel ballot…</button>
      {/if}
    </div>
    {#if cancelling}
      <form onsubmit={event => { event.preventDefault(); transition("cancel", "The ballot is cancelled.", { reason: cancelReason }); }}>
        <label for="cancel-{ballot.id}">Reason for cancelling (shown to voters)</label>
        <input id="cancel-{ballot.id}" bind:value={cancelReason} required maxlength="500" />
        <button class="danger" disabled={act.busy || !cancelReason.trim()}>Cancel ballot</button>
        <button type="button" class="secondary" onclick={() => (cancelling = false)}>Keep ballot</button>
      </form>
    {/if}
  {:else}
    <p class="help">This ballot is run by the {ballot.owner} program, which opens and closes it.</p>
  {/if}
  <Status action={act} />

  {#if ballot.can_unseal}
    <h4>Voter roll</h4>
    {#if sealed}
      {#if unsealing}
        <form onsubmit={openRoll}>
          <p class="notice">
            Breaking the seal shows how each person voted. It cannot be undone: the opening, your name and your reason
            are recorded in the audit log and listed on this ballot for every voter to see.
          </p>
          <label for="unseal-{ballot.id}">Reason for breaking the seal (at least 10 characters)</label>
          <input id="unseal-{ballot.id}" bind:value={reason} required minlength="10" maxlength="500" />
          <button class="danger" disabled={glass.busy || !reasonOk}>Break the seal</button>
          <button type="button" class="secondary" onclick={() => (unsealing = false)}>Keep sealed</button>
        </form>
      {:else}
        <button class="secondary" onclick={() => (unsealing = true)}>Break the seal…</button>
      {/if}
    {:else}
      <p class="help">This is a recorded vote; viewing the roll is logged in the audit trail.</p>
      <button class="secondary" disabled={glass.busy} onclick={() => openRoll()}>Show voter roll</button>
    {/if}
    <Status action={glass} />
    {#if roll}
      {#if roll.length === 0}
        <p class="help">No votes yet.</p>
      {:else}
        <table class="grid">
          <thead><tr><th scope="col">Voter</th><th scope="col">Choice</th><th scope="col">Receipt</th></tr></thead>
          <tbody>
            {#each roll as row (row.receipt)}
              <tr>
                <td>{row.name}</td>
                <td>{["ranked", "stv"].includes(ballot.method) ? row.choices.map((choice, index) => `${index + 1}. ${choice}`).join(", ") : row.choices.join(", ")}</td>
                <td class="receipt">{row.receipt.slice(0, 12)}…</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {/if}
  {:else if ballot.visibility === "secret"}
    <p class="help">This is a secret ballot. No link between voters and votes is stored, so nobody can see how anyone voted.</p>
  {/if}
</section>
