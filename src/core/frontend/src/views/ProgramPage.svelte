<script>
  import { go } from "../lib/nav.svelte.js";
  import { pageCopy } from "../lib/content.svelte.js";
  import PageCopy from "../components/PageCopy.svelte";
  import { findNode } from "../lib/menu.js";
  import { currentMenu } from "../lib/appmenu.svelte.js";
  import { session } from "../lib/session.svelte.js";
  import { pluginEnabled } from "../lib/plugins.svelte.js";
  import { publicViews } from "../lib/public-views.js";

  // A program's public page. Signing in adds buttons for what the program lets you do; staff tools appear in admin mode.
  let { program } = $props();
  const content = $derived(pageCopy(program));
  const actions = $derived(findNode(currentMenu(), n => n.program === program)?.items || []);
  const signedIn = $derived(!!session.token && !!session.me);
  const verified = $derived(session.me?.status === "verified");
  const publicView = $derived(pluginEnabled(program) ? publicViews[program] : undefined);

  const open = action => (action.href ? (location.href = action.href) : go(action.view, action.props || {}));
</script>

<PageCopy {content} />

{#if !content.comingSoon}
  <div class="card" id="your-actions">
    <h3>Your actions</h3>
    {#if !signedIn}
      <p>Sign in to apply or manage your records.</p>
      <button class="primary" onclick={() => go("profile")}>Sign in</button>
    {:else}
      {#if !verified}<p>Verify your identity in your profile to unlock applications.</p>{/if}
      <div class="actions">
        {#each actions as action (action.id)}
          <button class="primary" onclick={() => open(action)}>{action.label}</button>
        {/each}
      </div>
      {#if !actions.length && verified}<p>There is nothing for you to do here right now.</p>{/if}
    {/if}
  </div>
{/if}

{#if publicView}
  {#await publicView() then module}
    {@const View = module.default}
    <View />
  {:catch}
    <p class="err" role="alert">This program's public information could not be loaded. Check your connection and try again.</p>
  {/await}
{/if}
