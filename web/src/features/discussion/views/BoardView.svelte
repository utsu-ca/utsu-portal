<script>
  // One board's topics, newest activity first with pinned topics on top, and the form to start a topic.
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { go } from "../../../lib/nav.svelte.js";
  import Status from "../../../components/Status.svelte";

  let { id } = $props();

  const load = new Action();
  const start = new Action();
  let data = $state(null);
  let title = $state("");
  let body = $state("");

  const when = at => (at ? new Date(at * 1000).toLocaleString() : "");
  onMount(() => load.run(async () => { data = await api(`/discussion/boards/${encodeURIComponent(id)}/topics`); }));

  async function startTopic(event) {
    event.preventDefault();
    const topic = await start.run(() => api(`/discussion/boards/${encodeURIComponent(id)}/topics`, { method: "POST", body: { title, body } }));
    if (topic) go("discussionTopic", { id: topic.id });
  }
</script>

<button class="link" onclick={() => go("discussions")}>All boards</button>
<Status action={load} />
{#if data}
  <div class="card">
    <h2>{data.board.title}</h2>
    {#if data.board.archived}<p class="help">This board is archived: topics can be read but no new topics can be started.</p>{/if}
    {#if data.board.description}<p class="thread-body help">{data.board.description}</p>{/if}
  </div>

  <div class="card">
    {#if data.topics.length === 0}
      <p class="help">No topics yet.</p>
    {:else}
      <ul class="thread-list">
        {#each data.topics as topic (topic.id)}
          <li>
            <button class="link" onclick={() => go("discussionTopic", { id: topic.id })}>{topic.title}</button>
            {#if topic.pinned}<span class="badge">Pinned</span>{/if}
            {#if topic.locked}<span class="badge">Locked</span>{/if}
            {#if topic.kind_label}<span class="badge">{topic.kind_label}</span>{/if}
            <div class="meta">Started by {topic.author_name} · {topic.posts} {topic.posts === 1 ? "post" : "posts"} · last activity {when(topic.last_at)}</div>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  {#if data.can_post}
    <form class="card" onsubmit={startTopic} aria-label="New topic">
      <h3>Start a topic</h3>
      <label for="topic-title">Title</label>
      <input id="topic-title" bind:value={title} required maxlength="200" />
      <label for="topic-body">Opening post</label>
      <textarea id="topic-body" bind:value={body} rows="5" required maxlength="8000"></textarea>
      <button class="primary" disabled={start.busy || !title.trim() || !body.trim()}>Start topic</button>
      <Status action={start} />
    </form>
  {/if}
{/if}
