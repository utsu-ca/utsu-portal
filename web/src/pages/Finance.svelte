<script>
  import { onMount } from "svelte";
  import { api, getToken } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";
  import BankingControls from "../components/BankingControls.svelte";

  // The financial controller's portal: SABP banking only. It lists who has banking details, lets the controller
  // flag details that failed validation with one click, and review the details and void cheque that come back.
  const signedIn = !!getToken();
  const LABELS = {
    on_file: "On file", reverify_requested: "Waiting for applicant", reverify_received: "New details to review",
    destroyed: "Removed",
  };
  const FILTERS = [["all", "Everyone"], ["reverify_received", "New details to review"],
    ["reverify_requested", "Waiting for applicant"], ["on_file", "On file"]];

  const load = new Action();
  let items = $state([]), next = $state(null), filter = $state("all");
  const shown = $derived(items.filter(i => filter === "all" || i.status === filter));
  const toReview = $derived(items.filter(i => i.status === "reverify_received").length);

  async function fetchPage(cursor = "") {
    const page = await api("/finance/sap/banking?limit=100&cursor=" + encodeURIComponent(cursor));
    items = cursor ? [...items, ...page.items] : page.items;
    next = page.next_cursor;
  }
  // after any change reload everything loaded so far, so statuses stay current
  const reload = () => load.run(async () => {
    const loaded = Math.max(items.length, 1);
    await fetchPage();
    while (next && items.length < loaded) await fetchPage(next);
  });
  onMount(() => signedIn && reload());

  const expires = item => item.request_expires ? ` (link expires ${new Date(item.request_expires * 1000).toLocaleString()})` : "";
  const award = item => item.approved == null ? "No decision yet"
    : item.approved ? `Approved — $${item.awarded_amount}` : "Not approved";
</script>

<header><h1>Finance — SABP banking</h1><nav><a href="/">Portal</a></nav></header>
<main class="wide">
  {#if !signedIn}
    <div class="card"><p class="err">Sign in to the portal with your finance account first.</p></div>
  {:else}
    <div class="card">
      <h2>Banking information</h2>
      <p>When an applicant's banking details fail validation, use <strong>Banking failed validation</strong>. The
        applicant is emailed a link to a verification page where they re-enter their details and upload a PDF of a void
        cheque. It appears here under "New details to review".</p>
      <label for="finance-filter">Show</label>
      <select id="finance-filter" bind:value={filter}>
        {#each FILTERS as [value, label]}<option {value}>{label}{value === "reverify_received" && toReview ? ` (${toReview})` : ""}</option>{/each}
      </select>
      <Status action={load} />
    </div>
    {#if !shown.length && !load.busy && !load.error}<div class="card"><p>No banking records to show.</p></div>{/if}
    {#each shown as item (item.uuid)}
      <div class="card">
        <h3>{item.name || item.uuid}</h3>
        <p><span class="badge">{LABELS[item.status] || item.status}</span>{expires(item)}</p>
        <p>{award(item)}</p>
        <BankingControls name={item.name} status={item.status} onchange={reload} paths={{
          details: `/finance/sap/banking/${item.uuid}`, flag: `/finance/sap/banking/${item.uuid}/validation-failed`,
          pending: `/finance/sap/banking/${item.uuid}/pending`, cheque: `/finance/sap/banking/${item.uuid}/cheque`,
          accept: `/finance/sap/banking/${item.uuid}/accept`,
        }} />
      </div>
    {/each}
    {#if next}<button class="secondary" onclick={() => load.run(() => fetchPage(next))}>Load more</button>{/if}
  {/if}
</main>
