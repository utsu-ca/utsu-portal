<script>
  import { go } from "../lib/nav.svelte.js";
  import { pages, pageCopy } from "../lib/content.svelte.js";
  import PageCopy from "../components/PageCopy.svelte";
</script>

<PageCopy content={pageCopy("home")}>
  <button class="primary" onclick={() => go("profile")}>Sign in or create an account</button>
</PageCopy>
{#each pages.slice(1) as key (key)}
  {@const content = pageCopy(key)}
  <div class="card">
    <h3>{content.title}</h3>
    {#if content.tagline}<p>{content.tagline}</p>{/if}
    <button class="secondary" onclick={() => go("program", { program: key })}>Learn more</button>
  </div>
{/each}
