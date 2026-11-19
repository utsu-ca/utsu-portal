<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { copyrightLine, theme } from "../lib/theme.svelte.js";

  let build = $state("");
  onMount(async () => {
    try {
      build = buildLabel(await api("/version"));
    } catch {
      /* the footer still shows the copyright */
    }
  });

  function buildLabel(info) {
    const version = typeof info?.version === "string" ? info.version : "";
    const commit = typeof info?.commit === "string" ? info.commit.slice(0, 7) : "";
    if (!version) return "";
    return commit ? `v${version} (${commit})` : `v${version}`;
  }
</script>

<footer class="site-footer">{copyrightLine(theme.copyright)}{#if build}{" · "}<span title="Server version">{build}</span>{/if}</footer>
