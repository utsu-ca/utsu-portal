<script>
  import { onMount } from "svelte";
  import { copyrightLine, theme } from "../lib/theme.svelte.js";
  import { buildLabel, loadServer, server } from "../lib/server.svelte.js";

  onMount(() => { loadServer(); });
  const build = $derived(buildLabel(server));
</script>

<footer class="site-footer">{copyrightLine(theme.copyright)}{#if build}{" · "}<span title="Server version">{build}</span>{/if}</footer>
