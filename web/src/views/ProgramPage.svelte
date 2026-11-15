<script>
  import { go } from "../lib/nav.svelte.js";
  import { CONTENT } from "../lib/content.js";
  import { findNode } from "../lib/menu.js";
  import { currentMenu } from "../lib/appmenu.svelte.js";
  import { session } from "../lib/session.svelte.js";
  import SenatePublicView from "./SenatePublicView.svelte";

  // A program's public page. Signing in adds buttons for what the program lets you do; staff tools appear in admin mode.
  let { program } = $props();
  const content = $derived(CONTENT[program]);
  const actions = $derived(findNode(currentMenu(), n => n.program === program)?.items || []);
  const signedIn = $derived(!!session.token && !!session.me);
  const verified = $derived(session.me?.status === "verified");

  const open = action => (action.href ? (location.href = action.href) : go(action.view, action.props || {}));
</script>

<div class="card" id="overview">
  <h2>{content.title}</h2>
  <p>{content.tagline}</p>
  {#if content.comingSoon}<span class="badge">Coming soon</span>{/if}
  {#each content.summary as paragraph}<p>{paragraph}</p>{/each}
</div>

{#if content.eligibility.length}
  <div class="card" id="eligibility">
    <h3>Eligibility</h3>
    <ul>{#each content.eligibility as line}<li>{line}</li>{/each}</ul>
  </div>
{/if}
{#if content.steps.length}
  <div class="card" id="how-to-apply">
    <h3>How to apply</h3>
    <ol>{#each content.steps as line}<li>{line}</li>{/each}</ol>
  </div>
{/if}
{#if content.deadlines.length}
  <div class="card" id="key-dates">
    <h3>Key dates</h3>
    <ul>{#each content.deadlines as line}<li>{line}</li>{/each}</ul>
  </div>
{/if}

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

{#if program === "senate"}<SenatePublicView />{/if}
