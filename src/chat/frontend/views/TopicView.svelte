<script>
  // A topic: its live thread, and the panel of the plug-in that owns the topic kind (a ballot, for example).
  import { onMount } from "svelte";
  import { Action, api, go, panels, session, Status, ThreadPanel } from "@utsu/portal";

  let { id } = $props();

  const load = new Action();
  const moderate = new Action();
  let data = $state(null), maskAuthors = $state(false);

  const panel = $derived(data?.topic.ref ? panels[data.topic.panel] : undefined);
  const when = at => (at ? new Date(at * 1000).toLocaleString() : "");
  onMount(() => load.run(async () => { data = await api(`/discussion/topics/${encodeURIComponent(id)}`); }));

  async function set(change, okMessage) {
    const topic = await moderate.run(() => api(`/discussion/topics/${encodeURIComponent(id)}/moderate`, { method: "POST", body: change }), okMessage);
    if (topic) data.topic = topic;
  }
</script>

<Status action={load} />
{#if data}
  <button class="link" onclick={() => go("discussionBoard", { id: data.board.id })}>Back to {data.board.title}</button>
  <div class="card">
    <h2>{data.topic.title}</h2>
    <p class="meta">
      Started by {maskAuthors ? "Hidden author" : data.topic.author_name} · {when(data.topic.created_at)}
      {#if data.topic.pinned}<span class="badge">Pinned</span>{/if}
      {#if data.topic.locked}<span class="badge">Locked</span>{/if}
    </p>
    {#if data.topic.locked}<p class="help">This topic is locked: it can be read but not replied to.</p>{/if}
    {#if data.moderator && session.adminMode}
      <div class="actions">
        <button class="secondary" disabled={moderate.busy} onclick={() => set({ locked: !data.topic.locked }, data.topic.locked ? "Topic unlocked." : "Topic locked.")}>
          {data.topic.locked ? "Unlock topic" : "Lock topic"}
        </button>
        <button class="secondary" disabled={moderate.busy} onclick={() => set({ pinned: !data.topic.pinned }, data.topic.pinned ? "Topic unpinned." : "Topic pinned.")}>
          {data.topic.pinned ? "Unpin topic" : "Pin topic"}
        </button>
      </div>
      <Status action={moderate} />
    {/if}
  </div>

  {#if panel}
    {#await panel() then module}
      {@const Panel = module.default}
      <Panel id={data.topic.ref} embedded />
    {:catch}
      <p class="err">This part of the topic could not be loaded. Check your connection and reload the page.</p>
    {/await}
  {/if}

  {#key data.topic.locked}
    <ThreadPanel type="discussion-topic" id={data.topic.id} title="Replies" bind:maskAuthors />
  {/key}
{/if}
