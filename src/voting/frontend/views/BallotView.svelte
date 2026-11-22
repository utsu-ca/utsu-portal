<script>
  import { go, session } from "@utsu/portal";
  import BallotPanel from "../components/BallotPanel.svelte";

  let { id } = $props();
</script>

{#if session.adminMode}
  <button class="link" onclick={() => go("electionsAdmin")}>All ballots and polls</button>
{/if}
<BallotPanel {id} />
