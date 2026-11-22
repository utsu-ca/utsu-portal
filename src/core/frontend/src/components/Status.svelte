<script>
  // Shows what an action did: the error to correct, or a confirmation.
  let { action } = $props();
</script>

{#if action.error}<p class="err status" role="alert">{action.error}</p>{/if}
{#if action.ok}<p class="ok status" role="status">{action.ok}</p>{/if}
