<script>
  import { untrack } from "svelte";
  import Status from "../../../components/Status.svelte";
  import { go, nav } from "../../../lib/nav.svelte.js";
  import { session } from "../../../lib/session.svelte.js";
  import { bankingStatus, clearBankingStatus, refreshBankingStatus } from "../state/banking.svelte.js";

  const needsAction = $derived(
    session.me?.status === "verified" &&
    bankingStatus.status?.status === "reverify_requested" &&
    nav.name !== "banking" && !nav.props?.bankingView
  );

  $effect(() => {
    const token = session.token;
    const account = session.me;
    if (!token || account?.status !== "verified") {
      untrack(clearBankingStatus);
      return;
    }
    untrack(refreshBankingStatus);
  });
</script>

{#if needsAction}
  <div class="banner-action" role="alert">
    <span>Action needed: we could not verify your banking information.</span>
    <button class="primary" onclick={() => go("banking", { shared: true })}>Verify banking information</button>
  </div>
{/if}
{#if session.token && session.me?.status === "verified" && bankingStatus.error && nav.name !== "banking" && !nav.props?.bankingView}
  <div class="banner-action">
    <div>
      <p>We could not load your banking status. Retry or open banking information.</p>
      <Status action={bankingStatus} />
    </div>
    <button class="secondary" onclick={refreshBankingStatus} disabled={bankingStatus.busy}>Retry banking status</button>
    <button class="primary" onclick={() => go("banking", { shared: true })}>Banking information</button>
  </div>
{/if}
