<script>
  import NavNode from "./NavNode.svelte";
  import { layout, toggleSidebar } from "../lib/layout.svelte.js";
  import { theme } from "../lib/theme.svelte.js";
  import { isStaff, session } from "../lib/session.svelte.js";

  let { menu, onaction, onmode } = $props();
  const expanded = $derived(layout.mobileOpen || !layout.collapsed);
</script>

<div class="topbar">
  {#if theme.logoUrl}<img class="logo" src={theme.logoUrl} alt={theme.name} />{:else}<strong>{theme.name}</strong>{/if}
  <button class="hamburger" onclick={toggleSidebar} aria-label="Open menu" aria-controls="sidebar" aria-expanded={layout.mobileOpen}>☰</button>
</div>

<aside id="sidebar" class="sidebar" class:collapsed={layout.collapsed} class:open={layout.mobileOpen} aria-label="Main menu">
  <div class="side-head">
    <strong class="side-name">{theme.name}</strong>
    <button class="hamburger" onclick={toggleSidebar} aria-label={expanded ? "Collapse menu" : "Expand menu"}
      aria-controls="side-nav" aria-expanded={expanded}>☰</button>
  </div>
  <nav id="side-nav" class="side-nav" aria-label="Main">
    <ul>{#each menu as node (node.id)}<NavNode {node} {onaction} />{/each}</ul>
  </nav>
  <div class="side-bottom">
    {#if isStaff()}
      <div class="side-foot">
        <button onclick={() => onmode(!session.adminMode)} aria-pressed={session.adminMode}>
          {session.adminMode ? "Exit admin mode" : "Enter admin mode"}
        </button>
      </div>
    {/if}
    <div class="side-logo">
      {#if theme.logoUrl}<img src={theme.logoUrl} alt={theme.name} />{/if}
    </div>
  </div>
</aside>
