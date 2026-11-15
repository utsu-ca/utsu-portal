<script>
  import NavNode from "./NavNode.svelte";
  import { layout, toggleSidebar } from "../lib/layout.svelte.js";
  import { theme } from "../lib/theme.svelte.js";
  import { isStaff, session, setAdminMode } from "../lib/session.svelte.js";

  // The menu, fixed to the left. Hamburger collapses it to a rail on desktop and opens it full-screen on mobile.
  let { menu, onaction, onmode } = $props();
  const expanded = $derived(layout.mobileOpen || !layout.collapsed);
</script>

<div class="topbar">
  <button class="hamburger" onclick={toggleSidebar} aria-label="Open menu" aria-controls="sidebar" aria-expanded={layout.mobileOpen}>☰</button>
  {#if theme.logoUrl}<img class="logo" src={theme.logoUrl} alt={theme.name} />{:else}<strong>{theme.name}</strong>{/if}
</div>

<aside id="sidebar" class="sidebar" class:collapsed={layout.collapsed} class:open={layout.mobileOpen} aria-label="Main menu">
  <div class="side-head">
    <button class="hamburger" onclick={toggleSidebar} aria-label={expanded ? "Collapse menu" : "Expand menu"}
      aria-controls="side-nav" aria-expanded={expanded}>☰</button>
    <strong class="side-name">{theme.name}</strong>
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
