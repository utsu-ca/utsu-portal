<script>
  // Elections officials create ballots and polls here and see every ballot, including those other programs run.
  // How private the votes are is chosen when the ballot is created and cannot change afterwards.
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { go } from "../../../lib/nav.svelte.js";
  import Status from "../../../components/Status.svelte";

  const METHODS = {
    yes_no: "Yes / no motion",
    single: "Single choice (most votes wins)",
    approval: "Approval (choose several)",
    ranked: "Ranked choice, one winner (instant runoff)",
    stv: "Ranked choice, several seats (single transferable vote)",
  };
  const VISIBILITY = {
    sealed: { title: "Sealed", use: "Elections and anything contentious. Officials can break the seal with a recorded reason; every opening is shown to voters." },
    open: { title: "Recorded", use: "Polls whose answers someone must act on, such as dietary needs. Officials and organizers see each person's choice." },
    secret: { title: "Secret", use: "Votes that must never be traceable. No link to the voter is stored, so the seal can never be broken." },
  };
  const blank = () => ({
    title: "", description: "", method: "", options: "", seats: 1, max_choices: 0, threshold: "majority", quorum: 0,
    electorate: "members", visibility: "", opens_at: "", closes_at: "", board: "",
  });

  const load = new Action();
  const create = new Action();
  let setup = $state(null);
  let ballots = $state([]);
  let draft = $state(blank());
  let problem = $state("");

  const optionList = $derived(draft.options.split("\n").map(line => line.trim()).filter(Boolean));
  const epoch = value => (value ? Math.floor(new Date(value).getTime() / 1000) : null);

  const refresh = () => load.run(async () => {
    const [options, list] = await Promise.all([api("/voting/setup"), api("/voting/ballots")]);
    setup = options;
    ballots = list.ballots;
  });
  onMount(refresh);

  function validate() {
    if (!draft.method) return "Choose how votes are counted.";
    if (!draft.visibility) return "Choose how private the votes are.";
    if (draft.method !== "yes_no" && optionList.length < 2) return "List at least two options, one per line.";
    if (draft.method !== "yes_no" && new Set(optionList.map(o => o.toLowerCase())).size !== optionList.length) return "Each option must be different.";
    if (["approval", "stv"].includes(draft.method) && !(draft.seats >= 1 && draft.seats < optionList.length)) {
      return "Seats must be at least 1 and fewer than the number of options.";
    }
    const opens = epoch(draft.opens_at);
    const closes = epoch(draft.closes_at);
    if (closes !== null && (closes * 1000 <= Date.now() || (opens !== null && closes <= opens))) {
      return "The closing time must be in the future and after the opening time.";
    }
    return "";
  }

  async function submit(event) {
    event.preventDefault();
    problem = validate();
    if (problem) return;
    const body = {
      title: draft.title, description: draft.description, method: draft.method,
      options: draft.method === "yes_no" ? [] : optionList, seats: Number(draft.seats) || 1,
      max_choices: Number(draft.max_choices) || 0, threshold: draft.threshold, quorum: Number(draft.quorum) || 0,
      electorate: draft.electorate, visibility: draft.visibility, opens_at: epoch(draft.opens_at),
      closes_at: epoch(draft.closes_at), board: draft.board || null,
    };
    const ballot = await create.run(() => api("/voting/ballots", { method: "POST", body }),
      "Ballot created as a draft. Open it from its page when you are ready.");
    if (ballot) {
      draft = blank();
      await refresh();
    }
  }
</script>

<div class="card">
  <h2>Ballots and polls</h2>
  <p class="help">Results are counted once voting closes and published with a record anyone can check. The record's digest is anchored to the configured ledger.</p>
</div>
<Status action={load} />

{#if setup}
  <form class="card" onsubmit={submit} aria-label="New ballot">
    <h3>New ballot or poll</h3>
    <label for="ballot-title">Title</label>
    <input id="ballot-title" bind:value={draft.title} required maxlength="200" />
    <label for="ballot-description">Description (optional)</label>
    <textarea id="ballot-description" bind:value={draft.description} rows="3" maxlength="8000"></textarea>

    <label for="ballot-method">How votes are counted</label>
    <select id="ballot-method" bind:value={draft.method} required>
      <option value="" disabled>Choose…</option>
      {#each setup.methods as method (method)}<option value={method}>{METHODS[method] ?? method}</option>{/each}
    </select>

    {#if draft.method && draft.method !== "yes_no"}
      <label for="ballot-options">Options or candidates, one per line</label>
      <textarea id="ballot-options" bind:value={draft.options} rows="5"></textarea>
    {/if}
    {#if draft.method === "yes_no"}
      <label for="ballot-threshold">Needed to carry</label>
      <select id="ballot-threshold" bind:value={draft.threshold}>
        <option value="majority">More yes than no</option>
        <option value="two_thirds">Two-thirds of yes and no votes</option>
      </select>
    {/if}
    {#if ["approval", "stv"].includes(draft.method)}
      <label for="ballot-seats">Seats to fill</label>
      <input id="ballot-seats" type="number" min="1" bind:value={draft.seats} />
    {/if}
    {#if draft.method === "approval"}
      <label for="ballot-max">Most options a voter may choose (0 for no limit)</label>
      <input id="ballot-max" type="number" min="0" bind:value={draft.max_choices} />
    {/if}
    <label for="ballot-quorum">Quorum: ballots needed for the result to stand (0 for none)</label>
    <input id="ballot-quorum" type="number" min="0" bind:value={draft.quorum} />

    <label for="ballot-electorate">Who can vote</label>
    <select id="ballot-electorate" bind:value={draft.electorate}>
      {#each setup.electorates as electorate (electorate.id)}<option value={electorate.id}>{electorate.description}</option>{/each}
    </select>

    <fieldset>
      <legend>How private the votes are (cannot be changed later)</legend>
      {#each setup.visibilities as level (level.id)}
        <label class="opt">
          <input type="radio" name="ballot-visibility" value={level.id} bind:group={draft.visibility} required />
          <strong>{VISIBILITY[level.id]?.title ?? level.id}</strong>: {VISIBILITY[level.id]?.use ?? ""}
          <span class="help">Voters are told: {level.notice}</span>
        </label>
      {/each}
    </fieldset>

    <div class="row">
      <div>
        <label for="ballot-opens">Opens automatically at (optional)</label>
        <input id="ballot-opens" type="datetime-local" bind:value={draft.opens_at} />
      </div>
      <div>
        <label for="ballot-closes">Closes automatically at (optional)</label>
        <input id="ballot-closes" type="datetime-local" bind:value={draft.closes_at} />
      </div>
    </div>

    <label for="ballot-board">Discussion topic</label>
    <select id="ballot-board" bind:value={draft.board}>
      <option value="">No discussion</option>
      {#each setup.boards as board (board.id)}<option value={board.id}>Start a topic on {board.title}</option>{/each}
    </select>

    {#if problem}<p class="err" role="alert">{problem}</p>{/if}
    <button class="primary" disabled={create.busy}>Create ballot</button>
    <Status action={create} />
  </form>

  <div class="card">
    <h3>All ballots</h3>
    {#if ballots.length === 0}
      <p class="help">No ballots yet.</p>
    {:else}
      <table class="grid">
        <thead><tr><th scope="col">Ballot</th><th scope="col">Status</th><th scope="col">Privacy</th><th scope="col">Counting</th></tr></thead>
        <tbody>
          {#each ballots as ballot (ballot.id)}
            <tr>
              <td>
                <button class="link" onclick={() => go("ballot", { id: ballot.id })}>{ballot.title}</button>
                {#if ballot.owner !== "voting"}<span class="help">Run by {ballot.owner}</span>{/if}
              </td>
              <td>{ballot.counting ? "counting" : ballot.status}{ballot.unsealed.length ? `, seal broken ${ballot.unsealed.length}×` : ""}</td>
              <td>{VISIBILITY[ballot.visibility]?.title ?? ballot.visibility}</td>
              <td>{METHODS[ballot.method] ?? ballot.method}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
{/if}
