<script>
  import { onMount } from "svelte";
  import { api, apiAllPages } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { formatCents, parseDollars } from "../lib/money.js";
  import { readReceipt } from "../lib/receipt.js";
  import Status from "../../../components/Status.svelte";

  // A club's ledger: balance and tax summary, transactions, and (owner or treasurer) a new transaction with receipts.
  const load = new Action(), detail = new Action(), add = new Action();
  let clubs = $state([]), selected = $state(""), club = $state(null), summary = $state(null), ledger = $state([]);
  let kind = $state("expense"), amount = $state(""), taxInclusive = $state(true), category = $state(""),
    description = $state(""), date = $state(""), files = $state([]);

  let myRole = $state("");

  onMount(() => load.run(async () => {
    clubs = (await apiAllPages("/clubs/mine")).filter(c => c.status === "approved");
    if (clubs.length) await choose(clubs[0].id);
  }));

  const choose = id => detail.run(async () => {
    selected = id;
    club = await api(`/clubs/${id}`);
    summary = await api(`/clubs/${id}/summary`);
    ledger = await apiAllPages(`/clubs/${id}/transactions`);
    const me = await api("/me");
    myRole = club.members[me.user.uuid] ?? "";
  });

  const record = () => add.run(async () => {
    const cents = parseDollars(amount);
    if (cents === null) throw new Error("Enter the amount in dollars, for example 25.50.");
    if (!files.length) throw new Error("Attach at least one receipt.");
    const receiptIds = [];
    for (const file of files) {
      const dataUrl = await readReceipt(file);
      receiptIds.push((await api(`/clubs/${selected}/receipts`, { method: "POST", body: { data_url: dataUrl } })).id);
    }
    await api(`/clubs/${selected}/transactions`, { method: "POST", body: {
      kind, amount_cents: cents, tax_inclusive: taxInclusive, category, description, date, receipt_ids: receiptIds } });
    amount = category = description = date = "";
    files = [];
    await choose(selected);
  }, "Transaction recorded. Expenses count once approved.");
</script>

<Status action={load} />
<div class="card">
  <h2>Club finances</h2>
  {#if !clubs.length && !load.busy}
    <p>You do not belong to an approved club yet. Register one under Clubs → Registration.</p>
  {:else if clubs.length}
    <label for="club-pick">Club</label>
    <select id="club-pick" value={selected} onchange={e => choose(e.currentTarget.value)}>
      {#each clubs as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
    </select>
  {/if}
  <Status action={detail} />
</div>

{#if club && summary}
  <div class="card">
    <h3>Summary</h3>
    <p>Balance: <strong>{formatCents(club.balance_cents)}</strong></p>
    <p>Income {formatCents(summary.income_cents)} · Expenses {formatCents(summary.expense_cents)} · Pending expenses {summary.pending_expenses}</p>
    <p>Tax paid {formatCents(summary.tax_paid_cents)} · Tax collected {formatCents(summary.tax_collected_cents)} · Net tax position {formatCents(summary.net_tax_position_cents)}</p>
  </div>

  {#if ["owner", "treasurer"].includes(myRole)}
    <div class="card">
      <h3>Record a transaction</h3>
      <label for="tx-kind">Type</label>
      <select id="tx-kind" bind:value={kind}><option value="expense">Expense</option><option value="income">Income</option></select>
      <label for="tx-amount">Amount (CAD)</label>
      <input id="tx-amount" inputmode="decimal" bind:value={amount} />
      <label class="opt"><input type="checkbox" bind:checked={taxInclusive} /> The amount includes HST</label>
      <label for="tx-category">Category</label>
      <input id="tx-category" maxlength="50" bind:value={category} />
      <label for="tx-description">Description</label>
      <input id="tx-description" maxlength="500" bind:value={description} />
      <label for="tx-date">Date</label>
      <input id="tx-date" type="date" bind:value={date} />
      <label for="tx-receipts">Receipts (JPEG, PNG, WebP or PDF, 3 MB each)</label>
      <input id="tx-receipts" type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf"
        onchange={e => (files = [...e.currentTarget.files])} />
      <button class="primary" onclick={record} disabled={add.busy}>Record transaction</button>
      <Status action={add} />
    </div>
  {/if}

  <div class="card">
    <h3>Transactions</h3>
    {#if !ledger.length}<p>No transactions yet.</p>{/if}
    <table class="grid">
      <tbody>
        {#each ledger as t (t.id)}
          <tr><td>{t.date || "—"}</td><td>{t.kind}</td><td>{t.description}</td><td>{formatCents(t.gross_cents)}</td><td><span class="badge">{t.status}</span></td></tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
