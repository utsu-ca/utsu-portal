<script>
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { readVoidCheque } from "../lib/cheque.js";
  import { bankingStatus, loadBankingStatus } from "../state/banking.svelte.js";
  import Status from "../../../components/Status.svelte";

  let {
    endpoint = "/profile/financial",
    heading = "Financial profile",
    initialMessage = "Banking details are stored separately from your profile and reused for payouts.",
  } = $props();
  // The banking information verification page. Applicants enter their details once; if the finance team cannot
  // validate them, an emailed link brings them here to re-enter the details and upload a PDF of a void cheque.
  const load = new Action(), submit = new Action();
  let bank = $derived(bankingStatus.status), institution = $state(""), transit = $state(""), account = $state(""), file = $state(null);
  const token = new URLSearchParams(location.hash.slice(1)).get("sap-banking");

  const refresh = async (force = false) => {
    await loadBankingStatus({ force });
  };
  onMount(() => load.run(refresh));

  const details = () => ({ institution_number: institution, transit_number: transit, account_number: account });

  const send = () => submit.run(async () => {
    if (bank.status === "reverify_requested") {
      const void_cheque = await readVoidCheque(file);
      await api(endpoint + "/reverify", { method: "PUT", body: { ...details(), token, void_cheque } });
      history.replaceState(null, "", location.pathname + location.search);
    } else {
      await api(endpoint, { method: "PUT", body: details() });
    }
    institution = transit = account = "";
    file = null;
    await refresh(true);
  });

  const expires = () => bank?.expires_at ? new Date(bank.expires_at * 1000).toLocaleString() : "";
</script>

<Status action={load} />
{#if bank}
  <div class="card">
    {#if bank.status === "reverify_requested"}
      <h2>Banking information verification</h2>
      <p class="err" role="alert">We could not verify the banking information you gave us.</p>
      {#if token}
        <p>Re-enter your details below and upload a PDF copy of your void cheque. The PDF must be under 3 MB.</p>
      {:else}
        <p>Open the link in the email we sent you to continue{expires() ? ` (it expires ${expires()})` : ""}.</p>
      {/if}
    {:else}
      <h2>{heading}</h2>
      {#if bank.status === "on_file"}<p class="ok">Your banking details are on file.</p>{/if}
      {#if bank.status === "reverify_received"}
        <p>Thank you. We received your new details and void cheque; our finance team will review them.</p>
      {/if}
      {#if bank.status === "destroyed"}<p>Your banking details were removed. Contact the UTSU if this is unexpected.</p>{/if}
      {#if bank.status === "not_submitted"}
        <p>{initialMessage}
          Later changes need a re-verification request from the UTSU.</p>
      {/if}
    {/if}

    {#if bank.status === "not_submitted" || (bank.status === "reverify_requested" && token)}
      <label for="bank-institution">Institution number (3 digits)</label>
      <input id="bank-institution" inputmode="numeric" maxlength="3" bind:value={institution} />
      <label for="bank-transit">Transit number (5 digits)</label>
      <input id="bank-transit" inputmode="numeric" maxlength="5" bind:value={transit} />
      <label for="bank-account">Account number (up to 12 digits)</label>
      <input id="bank-account" inputmode="numeric" maxlength="12" autocomplete="off" bind:value={account} />
      {#if bank.status === "reverify_requested"}
        <label for="bank-cheque">Void cheque (PDF)</label>
        <input id="bank-cheque" type="file" accept="application/pdf,.pdf"
          onchange={e => (file = e.currentTarget.files?.[0] ?? null)} />
        <span class="help">Your name, institution, transit and account numbers must be visible.</span>
      {/if}
      <button class="primary" onclick={send} disabled={submit.busy}>
        {bank.status === "reverify_requested" ? "Submit for verification" : "Submit banking details"}
      </button>
      <Status action={submit} />
    {/if}
  </div>
{/if}
