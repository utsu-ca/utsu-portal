<script>
  import NavNode from "./NavNode.svelte";
  import { go, nav } from "../lib/nav.svelte.js";
  import { closeMobile, desktop, layout, toggleFold, toggleSidebar } from "../lib/layout.svelte.js";
  import { containsCurrent, isCurrent } from "../lib/menu.js";

  // One entry of the sidebar. Entries with children fold; deeper entries (a program's extra functions) are indented.
  let { node, depth = 0, onaction } = $props();
  const items = $derived(node.items || []);
  const folded = $derived(!!layout.folded[node.id]);
  const current = $derived(isCurrent(node, nav));
  const active = $derived(depth === 0 && containsCurrent(node, nav));
  // In the collapsed rail only icons show, so a group opens the sidebar to let the person pick a page.
  const opensRail = () => (layout.collapsed && desktop() ? toggleSidebar() : toggleFold(node.id));

  function open() {
    if (node.action) onaction(node.action);
    else if (node.href) location.href = node.href;
    else if (node.view) go(node.view, node.props || {});
    closeMobile();
  }
</script>

<li class={"depth-" + depth}>
  <div class="side-row">
    {#if node.view || node.href || node.action}
      <button class="side-link" onclick={open} aria-current={current ? "page" : undefined} data-active={active ? "" : undefined}
        title={node.label}>
        {#if node.icon}<span class="side-icon" aria-hidden="true">{node.icon}</span>{/if}<span class="side-label">{node.label}</span>
      </button>
    {:else}
      <button class="side-link side-group-label" onclick={opensRail} aria-expanded={!folded} data-active={active ? "" : undefined}
        title={node.label}>
        {#if node.icon}<span class="side-icon" aria-hidden="true">{node.icon}</span>{/if}<span class="side-label">{node.label}</span>
      </button>
    {/if}
    {#if items.length}
      <button class="side-fold" onclick={() => toggleFold(node.id)} aria-expanded={!folded}
        aria-label={(folded ? "Expand " : "Collapse ") + node.label}>{folded ? "▸" : "▾"}</button>
    {/if}
  </div>
  {#if items.length && !folded}
    <ul>{#each items as child (child.id)}<NavNode node={child} depth={depth + 1} {onaction} />{/each}</ul>
  {/if}
</li>
