<script>
  import { onMount } from "svelte";
  import PageCopy from "../components/PageCopy.svelte";
  import Status from "../components/Status.svelte";
  import { Action } from "../lib/action.svelte.js";
  import { contentLoad, loadContent, pageCopy, pages, publishCopy, revision } from "../lib/content.svelte.js";

  const sections = [
    ["summary", "Overview paragraphs"], ["eligibility", "Eligibility"],
    ["steps", "How to apply"], ["deadlines", "Key dates"],
  ];
  const save = new Action();
  let selected = $state("home");
  let draft = $state(null);
  let baseRev = $state(0);
  let original = $state("");
  const lines = text => text.split("\n").map(line => line.trim()).filter(Boolean);
  const copy = $derived(draft && {
    title: draft.title.trim(), tagline: draft.tagline.trim(),
    ...Object.fromEntries(sections.map(([key]) => [key, lines(draft[key])])),
  });
  const dirty = $derived(draft && JSON.stringify(draft) !== original);
  const problem = $derived(!copy ? "" : !copy.title ? "Enter a title."
    : !copy.summary.length ? "Enter at least one overview paragraph."
    : sections.some(([key]) => copy[key].length > 20 || copy[key].some(line => line.length > 2000))
      ? "Use at most 20 lines per section and 2,000 characters per line." : "");

  function edit(id) {
    selected = id;
    baseRev = revision(id);
    const page = pageCopy(id);
    const fields = { title: page.title, tagline: page.tagline,
      ...Object.fromEntries(sections.map(([key]) => [key, page[key].join("\n")])) };
    original = JSON.stringify(fields);
    draft = fields;
  }

  async function reload() {
    if (await loadContent()) {
      save.error = "";
      save.ok = "";
      edit(selected);
    }
  }
  onMount(() => { reload(); });

  async function publish(event) {
    event.preventDefault();
    if (!dirty || problem || save.busy || contentLoad.busy) return;
    await save.run(async () => {
      await publishCopy(selected, baseRev, copy);
      edit(selected);
    }, "Published. Visitors will see the updated copy when they load the portal.");
  }
</script>

<div class="card">
  <h2>Page content</h2>
  <p>Edit the public home and program pages. Preview changes below, then publish when ready. This changes public copy only, not eligibility rules, forms, or application settings.</p>
  <Status action={contentLoad} />
  {#if !draft && contentLoad.error}
    <button class="secondary" onclick={reload}>Retry loading content</button>
  {/if}
</div>
{#if draft}
  <form class="card" aria-label="Edit page copy" onsubmit={publish}>
    <label for="content-page">Page</label>
    <select id="content-page" value={selected} disabled={dirty || save.busy || contentLoad.busy}
      onchange={event => { edit(event.currentTarget.value); save.error = ""; save.ok = ""; }}>
      {#each pages as id (id)}
        <option value={id}>{id === "home" ? "Home" : pageCopy(id).title}</option>
      {/each}
    </select>
    <fieldset disabled={save.busy || contentLoad.busy}>
      <label for="content-title">Title</label>
      <input id="content-title" bind:value={draft.title} required maxlength="160" />
      <label for="content-tagline">Short blurb (optional)</label>
      <input id="content-tagline" bind:value={draft.tagline} maxlength="280" />
      <p class="help">Use one paragraph or list item per line. Leave optional sections empty to hide them. Text is displayed as written; HTML and code do not run.</p>
      {#each sections as [key, label] (key)}
        <label for="content-{key}">{label}{key === "summary" ? "" : " (optional)"}</label>
        <textarea id="content-{key}" bind:value={draft[key]} required={key === "summary"} rows="4"></textarea>
      {/each}
    </fieldset>
    {#if problem}<p class="err">{problem}</p>{/if}
    <div class="actions">
      <button class="primary" disabled={!dirty || !!problem || save.busy || contentLoad.busy}>Publish to site</button>
      <button type="button" class="secondary" disabled={save.busy || contentLoad.busy}
        onclick={reload}>{dirty ? "Discard edits and reload" : "Reload published copy"}</button>
    </div>
    <Status action={save} />
  </form>
  <section aria-label="Public copy preview">
    <h3>Preview (not published)</h3>
    <PageCopy content={{ ...copy, comingSoon: pageCopy(selected).comingSoon }} preview />
  </section>
{/if}
