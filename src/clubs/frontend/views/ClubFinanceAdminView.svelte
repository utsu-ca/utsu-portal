<script>
  import { onMount } from "svelte";
  import { Action, api, apiAllPages, Status } from "@utsu/portal";
  import { formatCents, parseDollars } from "../lib/money.js";

  // Finance administrators approve club expenses and issue grants.
  const load = new Action(), act = new Action(), grantAction = new Action();
  let pending = $state([]), clubs = $state([]), notes = $state({});
  let grantClub = $state(""), grantAmount = $state(""), grantNote = $state("");

  const refresh = async () => {
    pending = await apiAllPages("/admin/transactions?status=pending");
    clubs = await apiAllPages("/admin/clubs?status=approved");
  };
  onMount(() => load.run(refresh));
  const clubName = id => clubs.find(c => c.id === id)?.name || id;

  const decide = (tx, approve) => act.run(async () => {
    await api(`/admin/transactions/${tx.id}/decision`, { method: "POST", body: { approve, note: notes[tx.id] || "" } });
    await refresh();
  });

  const grant = () => grantAction.run(async () => {
    const cents = parseDollars(grantAmount);
    if (!grantClub) throw new Error("Choose a club.");
    if (cents === null) throw new Error("Enter the grant in dollars, for example 250.00.");
    await api(`/admin/clubs/${grantClub}/grants`, { method: "POST", body: { amount_cents: cents, note: grantNote } });
    grantAmount = grantNote = "";
  }, "Grant issued.");
</script>

<Status action={load} />
<div class="card">
  <h2>Issue a grant</h2>
  <label for="grant-club">Club</label>
  <select id="grant-club" bind:value={grantClub}>
    <option value="">—</option>
    {#each clubs as club (club.id)}<option value={club.id}>{club.name}</option>{/each}
  </select>
  <label for="grant-amount">Amount (CAD)</label>
  <input id="grant-amount" inputmode="decimal" bind:value={grantAmount} />
  <label for="grant-note">Note</label>
  <input id="grant-note" maxlength="500" bind:value={grantNote} />
  <button class="primary" onclick={grant} disabled={grantAction.busy}>Issue grant</button>
  <Status action={grantAction} />
</div>

<div class="card"><h3>Expenses awaiting approval</h3></div>
{#if !pending.length && !load.busy}<div class="card"><p>Nothing pending.</p></div>{/if}
{#each pending as tx (tx.id)}
  <div class="card">
    <h3>{clubName(tx.club_id)} — {formatCents(tx.gross_cents)}</h3>
    <p>{tx.category}: {tx.description} ({tx.date})</p>
    <p class="help">{tx.approvals.filter(a => a.approve).length} of {tx.required_approvals} approvals · {tx.receipt_ids.length} receipt(s)</p>
    <input placeholder="note" aria-label={"Note for " + tx.id} bind:value={notes[tx.id]} />
    <button class="primary" onclick={() => decide(tx, true)} disabled={act.busy}>Approve</button>
    <button class="danger" onclick={() => decide(tx, false)} disabled={act.busy}>Reject</button>
  </div>
{/each}
<Status action={act} />
