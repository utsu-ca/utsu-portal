<script>
  // One board's topics, newest activity first with pinned topics on top, and the form to start a topic.
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { go } from "../../../lib/nav.svelte.js";
  import { session } from "../../../lib/session.svelte.js";
  import Status from "../../../components/Status.svelte";

  let { id } = $props();

  const load = new Action();
  const start = new Action();
  let data = $state(null);
  let title = $state("");
  let body = $state("");
  let nameChoice = $state("real"), maskAuthors = $state(false);

  const when = at => (at ? new Date(at * 1000).toLocaleString() : "");
  onMount(() => load.run(async () => { data = await api(`/discussion/boards/${encodeURIComponent(id)}/topics`); }));

  async function startTopic(event) {
    event.preventDefault();
    const topic = await start.run(() => api(`/discussion/boards/${encodeURIComponent(id)}/topics`, { method: "POST", body: { title, body, name_choice: nameChoice } }));
    if (topic) go("discussionTopic", { id: topic.id });
  }
</script>

<button class="link" onclick={() => go("discussions")}>All boards</button>
<Status action={load} />
{#if data}
  <div class="card">
    <label class="opt"><input type="checkbox" bind:checked={maskAuthors} /> Mask author names on this screen</label>
    {#if maskAuthors}<p class="help">This only hides names from your view, not from other viewers or the server.</p>{/if}
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
            <div class="meta">Started by {maskAuthors ? "Hidden author" : topic.author_name} · {topic.posts} {topic.posts === 1 ? "post" : "posts"} · last activity {when(topic.last_at)}</div>
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
      <label for="topic-name">Post as</label>
      <select id="topic-name" bind:value={nameChoice}>
        <option value="real">Real name</option>
        <option value="username" disabled={!session.me?.user?.username}>Username{session.me?.user?.username ? `: ${session.me.user.username}` : " (set one in your account)"}</option>
      </select>
      <button class="primary" disabled={start.busy || !title.trim() || !body.trim()}>Start topic</button>
      <Status action={start} />
    </form>
  {/if}
{/if}
