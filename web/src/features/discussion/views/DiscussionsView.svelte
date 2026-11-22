<script>
  // The discussion boards this person can see. Moderators (in admin mode) also create and archive boards here.
  import { onMount } from "svelte";
  import { api, apiAllPages } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { go } from "../../../lib/nav.svelte.js";
  import { session } from "../../../lib/session.svelte.js";
  import Status from "../../../components/Status.svelte";

  const blank = () => ({ id: "", title: "", description: "", audience: "members" });
  const load = new Action();
  const create = new Action();
  const archive = new Action();
  let data = $state(null);
  let draft = $state(blank());

  const moderating = $derived(!!data?.moderator && session.adminMode);
  const idOk = $derived(/^[a-z0-9][a-z0-9-]{1,47}$/.test(draft.id));
  const refresh = () => load.run(async () => { data = await apiAllPages("/discussion/boards", "boards"); });
  onMount(refresh);

  async function createBoard(event) {
    event.preventDefault();
    if (await create.run(() => api("/discussion/boards", { method: "POST", body: draft }), "Board created.")) {
      draft = blank();
      await refresh();
    }
  }

  async function setArchived(board, archived) {
    const done = await archive.run(() => api(`/discussion/boards/${board.id}`, { method: "POST", body: { archived } }),
      archived ? `${board.title} is archived.` : `${board.title} is open again.`);
    if (done) await refresh();
  }
</script>

<div class="card">
  <h2>Discussions</h2>
  <p class="help">Talk through motions, ballots and campus questions with other members. Posts can be read by everyone who can see the board.</p>
</div>
<Status action={load} />
<Status action={archive} />

{#if data}
  {#if data.boards.length === 0}
    <div class="card">
      {#if data.member || data.moderator}
        <p>There are no boards yet.</p>
      {:else}
        <p>Member boards are open to students on the membership roster.</p>
        <p class="help">If you are a current student, check that the student number on your account matches the roster.</p>
        <button class="secondary" onclick={() => go("account")}>Check my account</button>
      {/if}
    </div>
  {/if}
  {#each data.boards as board (board.id)}
    <div class="card">
      <h3><button class="link" onclick={() => go("discussionBoard", { id: board.id })}>{board.title}</button></h3>
      {#if board.audience === "staff"}<span class="badge">Staff only</span>{/if}
      {#if board.archived}<span class="badge">Archived</span>{/if}
      {#if board.description}<p class="thread-body help">{board.description}</p>{/if}
      {#if moderating && !board.owner}
        <button class="secondary" disabled={archive.busy} onclick={() => setArchived(board, !board.archived)}>
          {board.archived ? "Reopen board" : "Archive board"}
        </button>
      {/if}
    </div>
  {/each}

  {#if moderating}
    <form class="card" onsubmit={createBoard} aria-label="New board">
      <h3>New board</h3>
      <label for="board-title">Title</label>
      <input id="board-title" bind:value={draft.title} required maxlength="120" />
      <label for="board-id">Short name for links</label>
      <span class="help">Lower-case letters, digits and hyphens, 2 to 48 characters, for example <code>general-meeting</code>.</span>
      <input id="board-id" bind:value={draft.id} required maxlength="48" aria-invalid={!!draft.id && !idOk} />
      {#if draft.id && !idOk}<p class="err">Use only lower-case letters, digits and hyphens, starting with a letter or digit.</p>{/if}
      <label for="board-description">Description (optional)</label>
      <textarea id="board-description" bind:value={draft.description} rows="3" maxlength="2000"></textarea>
      <label for="board-audience">Who can read it</label>
      <select id="board-audience" bind:value={draft.audience}>
        <option value="members">Members on the roster</option>
        <option value="staff">Moderators only</option>
      </select>
      <button class="primary" disabled={create.busy || !idOk}>Create board</button>
      <Status action={create} />
    </form>
  {/if}
{/if}
