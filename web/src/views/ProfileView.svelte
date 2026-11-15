<script>
  import AccountView from "./AccountView.svelte";
  import AuthView from "./AuthView.svelte";
  import { session } from "../lib/session.svelte.js";
</script>

{#if session.token && session.me}<AccountView />{:else}<AuthView />{/if}
