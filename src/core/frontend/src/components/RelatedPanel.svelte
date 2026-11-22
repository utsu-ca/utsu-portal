<script>
  import { go, nav } from "../lib/nav.svelte.js";
  import { pageCopy } from "../lib/content.svelte.js";
  import { sectionFor } from "../lib/menu.js";
  import { currentMenu } from "../lib/appmenu.svelte.js";

  // A second sidecar for wide screens, like a wiki's contents box: the other pages of the section being read, the
  // actions of the current page, and links to the parts of a program's page.
  const section = $derived(sectionFor(currentMenu(), nav));
  const content = $derived(nav.name === "program" ? pageCopy(nav.props.program) : null);
  const parts = $derived(content ? [
    ["Overview", "overview", true],
    ["Eligibility", "eligibility", content.eligibility.length > 0],
    ["How to apply", "how-to-apply", content.steps.length > 0],
    ["Key dates", "key-dates", content.deadlines.length > 0],
    ["Your actions", "your-actions", !content.comingSoon],
  ].filter(([, , shown]) => shown) : []);

  const open = node => (node.href ? (location.href = node.href) : go(node.view, node.props || {}));
  const scrollTo = id => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
</script>

{#if section || parts.length}
  <aside class="related" aria-label="In this section">
    {#if parts.length}
      <h2 class="related-title">On this page</h2>
      <ul>
        {#each parts as [label, id] (id)}<li><button class="related-link" onclick={() => scrollTo(id)}>{label}</button></li>{/each}
      </ul>
    {/if}
    {#if section}
      <h2 class="related-title">{section.title}</h2>
      <ul>
        {#each section.sections as entry (entry.node.id)}
          <li>
            <button class="related-link" aria-current={entry.current ? "page" : undefined} onclick={() => open(entry.node)}>{entry.node.label}</button>
            {#if entry.children.length}
              <ul class="related-sub">
                {#each entry.children as child (child.id)}
                  <li><button class="related-link" onclick={() => open(child)}>{child.label}</button></li>
                {/each}
              </ul>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </aside>
{/if}
