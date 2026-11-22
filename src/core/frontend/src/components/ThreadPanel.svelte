<script>
  // Live chat for any record-linked thread a plug-in registers (core messaging). Messages arrive over a streamed
  // connection that re-checks access on every batch; posting goes through the ordinary API and shows immediately.
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { openStream } from "../lib/stream.js";
  import { session } from "../lib/session.svelte.js";
  import Status from "./Status.svelte";

  let { type, id, title = "Discussion", maskAuthors = $bindable(false) } = $props();

  const base = $derived(`/threads/${encodeURIComponent(type)}/${encodeURIComponent(id)}`);
  const load = new Action();
  const send = new Action();
  const moderate = new Action();
  const read = new Action();
  let info = $state(null);
  let messages = $state([]);
  let connection = $state("connecting");
  let body = $state("");
  let internal = $state(false);
  let nameChoice = $state("real");
  let redacting = $state(null);
  let reason = $state("");
  let readUpTo = 0;
  let closed = false;

  const labels = {
    connecting: "Connecting…",
    live: "Live: new posts appear as they are written.",
    reconnecting: "Connection lost. Reconnecting…",
  };
  const when = at => (at ? new Date(at * 1000).toLocaleString() : "");

  function upsert(row) {
    const index = messages.findIndex(message => message.seq === row.seq);
    if (index >= 0) messages[index] = row;
    else {
      messages.push(row);
      messages.sort((a, b) => a.seq - b.seq);
    }
  }

  async function markRead() {
    const last = messages.at(-1)?.seq ?? 0;
    if (closed || read.busy || last <= readUpTo) return;
    const result = await read.run(() => api(base + "/read", { method: "POST", body: { seq: last } }));
    if (result) {
      readUpTo = Math.max(readUpTo, last);
      markRead();
    }
  }

  onMount(() => {
    let stream = null;
    load.run(async () => {
      info = await api(base);
      readUpTo = info.read;
      if (closed) return;
      stream = openStream(base + "/stream", {
        onEvent: (kind, row) => {
          if (kind === "sync") {
            const visible = new Set(row.seqs);
            messages = messages.filter(message => visible.has(message.seq));
            return;
          }
          upsert(row);
          if (connection === "live") markRead();
        },
        onStatus: state => {
          connection = state;
          if (state === "denied") messages = [];
          if (state === "live") markRead();
        },
      });
    });
    return () => {
      closed = true;
      stream?.close();
    };
  });

  async function post(event) {
    event.preventDefault();
    const row = await send.run(() => api(base + "/messages", { method: "POST", body: { body, internal, name_choice: nameChoice } }));
    if (row) {
      upsert(row);
      readUpTo = Math.max(readUpTo, row.seq);
      body = "";
      internal = false;
    }
  }

  async function redact(event) {
    event.preventDefault();
    const row = await moderate.run(() => api(`${base}/messages/${redacting}/redact`, { method: "POST", body: { reason } }));
    if (row) {
      upsert(row);
      redacting = null;
      reason = "";
    }
  }
</script>

<section class="card" aria-label={title}>
  <h3>{title}</h3>
  <Status action={load} />
  {#if connection === "denied"}
    <p class="err" role="status">You can no longer read this thread. Your access may have changed; go back and reopen it.</p>
  {:else if info}
    <p class="meta" role="status">{labels[connection]}</p>
  {/if}

  <label class="opt"><input type="checkbox" bind:checked={maskAuthors} /> Mask author names on this screen</label>
  {#if maskAuthors}<p class="help">This only hides names from your view, not from other viewers or the server.</p>{/if}
  {#if info && connection === "live" && messages.length === 0}<p class="help">No posts yet.</p>{/if}
  <div role="log" aria-label="{title} messages">
  <ol class="thread-list">
    {#each messages as message (message.seq)}
      <li class="thread-post" class:mine={message.mine} class:internal={message.internal}>
        <div class="meta">
          <strong>{maskAuthors ? "Hidden author" : message.author_name}</strong> · <time>{when(message.at)}</time>
          {#if message.internal}<span class="badge">Internal note</span>{/if}
        </div>
        {#if message.redacted}
          <p class="thread-body help">Removed by a moderator.</p>
        {:else}
          <p class="thread-body">{message.body}</p>
          {#if info?.staff && redacting !== message.seq}
            <button class="secondary" onclick={() => { redacting = message.seq; reason = ""; moderate.error = ""; }}>Remove post</button>
          {/if}
        {/if}
        {#if redacting === message.seq}
          <form onsubmit={redact}>
            <label for="redact-{message.seq}">Reason for removing this post</label>
            <input id="redact-{message.seq}" bind:value={reason} required maxlength="200" />
            <button class="danger" disabled={moderate.busy || !reason.trim()}>Remove</button>
            <button type="button" class="secondary" onclick={() => (redacting = null)}>Keep</button>
            <Status action={moderate} />
          </form>
        {/if}
      </li>
    {/each}
  </ol>
  </div>
  <Status action={read} />
  {#if read.error && connection !== "denied"}
    <button class="secondary" disabled={read.busy} onclick={markRead}>Retry marking as read</button>
  {/if}

  {#if info && connection !== "denied"}
    {#if info.post || info.internal_notes}
      <form onsubmit={post}>
        <label for="thread-{id}-body">Your post</label>
        <textarea id="thread-{id}-body" bind:value={body} rows="4" maxlength={info.max_body} required></textarea>
        <label for="thread-{id}-name">Post as</label>
        <select id="thread-{id}-name" bind:value={nameChoice}>
          <option value="real">Real name</option>
          <option value="username" disabled={!session.me?.user?.username}>Username{session.me?.user?.username ? `: ${session.me.user.username}` : " (set one in your account)"}</option>
        </select>
        {#if info.internal_notes}
          <label class="opt"><input type="checkbox" bind:checked={internal} /> Internal note (staff only)</label>
        {/if}
        <button class="primary" disabled={send.busy || !body.trim() || (!info.post && !internal)}>Post</button>
        <Status action={send} />
      </form>
    {:else}
      <p class="help">Posting is closed for you in this thread.</p>
    {/if}
  {/if}
</section>
