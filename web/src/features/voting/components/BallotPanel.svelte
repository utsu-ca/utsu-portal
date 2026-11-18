<script>
  // One ballot or poll, as a voter sees it: how private the vote is (before voting), the form for its counting
  // method, the receipt, the result, and a check of the published record that runs in this browser.
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { go } from "../../../lib/nav.svelte.js";
  import { session } from "../../../lib/session.svelte.js";
  import Status from "../../../components/Status.svelte";
  import { verifyRecord } from "../lib/record.js";
  import BallotOfficial from "./BallotOfficial.svelte";
  import BallotResults from "./BallotResults.svelte";

  let { id, embedded = false } = $props();

  const load = new Action();
  const vote = new Action();
  const check = new Action();
  let ballot = $state(null);
  let chosen = $state("");
  let approved = $state([]);
  let ranking = $state([]);
  let fresh = $state(null);
  let receipt = $state("");
  let verified = $state(null);

  const when = at => (at ? new Date(at * 1000).toLocaleString() : "");
  const label = optionId => ballot.options.find(option => option.id === optionId)?.label ?? optionId;
  const ranked = $derived(ballot && ["ranked", "stv"].includes(ballot.method));
  const limit = $derived(ballot?.method === "approval" ? ballot.max_choices || ballot.options.length : 0);
  const selection = $derived(!ballot ? [] : ranked ? ranking : ballot.method === "approval" ? approved : chosen ? [chosen] : []);
  const unranked = $derived(ballot ? ballot.options.filter(option => !ranking.includes(option.id)) : []);
  const instructions = $derived(ballot && {
    yes_no: ballot.threshold === "two_thirds"
      ? "Choose one. The motion carries with two-thirds of the yes and no votes; abstentions are not counted."
      : "Choose one. The motion carries with more yes than no votes; abstentions are not counted.",
    single: "Choose one.",
    approval: ballot.max_choices ? `Choose up to ${ballot.max_choices}.` : "Choose every option you approve of.",
    ranked: "Rank the options in order of preference, first choice first. You do not have to rank them all. Counted by instant runoff.",
    stv: `Rank the candidates in order of preference for ${ballot.seats} seats, first choice first. You do not have to rank them all. Counted by single transferable vote.`,
  }[ballot.method]);
  const anchorText = anchor => anchor && ({
    confirmed: anchor.block ? `Anchored and confirmed in block ${anchor.block}.` : "Anchored and confirmed.",
    submitted: "Submitted for anchoring; waiting for confirmations.",
    pending: "Waiting to be anchored.",
    disabled: "Anchoring is not configured for this portal.",
  }[anchor.status] ?? anchor.status);

  const refresh = () => load.run(async () => {
    ballot = await api(`/voting/ballots/${id}`);
    if (ballot.receipt && !receipt) receipt = ballot.receipt;
  });
  onMount(refresh);

  function move(index, by) {
    const [item] = ranking.splice(index, 1);
    ranking.splice(index + by, 0, item);
  }

  function toggle(optionId, on) {
    approved = on ? ballot.options.map(o => o.id).filter(o => o === optionId || approved.includes(o)) : approved.filter(o => o !== optionId);
  }

  async function cast(event) {
    event.preventDefault();
    const out = await vote.run(() => api(`/voting/ballots/${id}/vote`, { method: "POST", body: { selection } }));
    if (out) {
      fresh = out;
      receipt = out.receipt;
      await refresh();
    }
  }

  async function verify(event) {
    event.preventDefault();
    verified = null;
    await check.run(async () => {
      const record = await api(`/voting/ballots/${id}/record`);
      verified = await verifyRecord(record, receipt);
    });
  }
</script>

<Status action={load} />
<button class="secondary" disabled={load.busy || vote.busy} onclick={refresh}>Refresh ballot</button>
{#if ballot}
  <section class="card" aria-label="Ballot">
    <h3>{ballot.title}</h3>
    <p class="meta">
      <span class="badge">{{ draft: "Not open yet", open: "Open", closed: "Closed", cancelled: "Cancelled" }[ballot.status]}</span>
      Electorate: {ballot.electorate_label || ballot.electorate}
      {#if ballot.status === "draft" && ballot.opens_at} · opens {when(ballot.opens_at)}{/if}
      {#if ballot.status === "open" && ballot.closes_at} · closes {when(ballot.closes_at)}{/if}
      {#if ballot.status === "open"} · {ballot.turnout} voted so far{/if}
    </p>
    {#if ballot.description}<p class="thread-body">{ballot.description}</p>{/if}
    <p class="notice">{ballot.notice}</p>
    {#if ballot.unsealed.length}
      <div class="notice">
        <strong>The seal on this ballot has been broken {ballot.unsealed.length === 1 ? "once" : `${ballot.unsealed.length} times`}.</strong>
        <ul>
          {#each ballot.unsealed as entry, index (index)}<li>{when(entry.at)}, by {entry.by_name || "an elections official"}: {entry.reason}</li>{/each}
        </ul>
      </div>
    {/if}
    {#if !embedded && ballot.topic}
      <button class="link" onclick={() => go("discussionTopic", { id: ballot.topic })}>Discuss this ballot</button>
    {/if}

    {#if ballot.status === "cancelled"}
      <p class="help">This ballot was cancelled{ballot.cancel_reason ? `: ${ballot.cancel_reason}` : "."}</p>
    {/if}

    {#if fresh && !fresh.kept}
      <div class="banner-action" role="status">
        <div>
          <strong>Save your receipt now.</strong> This is a secret ballot, so the receipt is not kept with your account and
          cannot be shown again. Use it after the count to check that your vote was included.
          <p class="receipt">{fresh.receipt}</p>
        </div>
      </div>
    {/if}

    {#if ballot.voted}
      <p class="ok">You have voted.</p>
      {#if ballot.receipt}
        <p class="help">Your receipt: <span class="receipt">{ballot.receipt}</span></p>
      {:else if !fresh}
        <p class="help">Your receipt was shown once when you voted.</p>
      {/if}
    {:else if ballot.status === "open" && !ballot.eligible}
      <p class="err">
        You are not in this ballot's electorate ({ballot.electorate_label || ballot.electorate}). If you are a current
        student, check that your student number on your account page matches the membership roster.
      </p>
      <button class="secondary" onclick={() => go("account")}>Check my account</button>
    {:else if ballot.status === "open"}
      <form onsubmit={cast}>
        <p class="help">{instructions}</p>
        {#if ranked}
          <p><strong>Your ranking</strong></p>
          {#if ranking.length === 0}<p class="help">Nothing ranked yet. Add options below in order of preference.</p>{/if}
          <ol class="ranking">
            {#each ranking as optionId, index (optionId)}
              <li>
                {label(optionId)}
                <button type="button" class="secondary" aria-label="Move {label(optionId)} up" disabled={index === 0} onclick={() => move(index, -1)}>↑</button>
                <button type="button" class="secondary" aria-label="Move {label(optionId)} down" disabled={index === ranking.length - 1} onclick={() => move(index, 1)}>↓</button>
                <button type="button" class="secondary" aria-label="Unrank {label(optionId)}" onclick={() => ranking.splice(index, 1)}>Remove</button>
              </li>
            {/each}
          </ol>
          {#if unranked.length}
            <p><strong>Not ranked</strong></p>
            <div class="actions">
              {#each unranked as option (option.id)}
                <button type="button" class="secondary" onclick={() => ranking.push(option.id)}>Rank {option.label}</button>
              {/each}
            </div>
          {/if}
        {:else if ballot.method === "approval"}
          <fieldset>
            <legend>Options</legend>
            {#each ballot.options as option (option.id)}
              <label class="opt">
                <input type="checkbox" checked={approved.includes(option.id)} disabled={!approved.includes(option.id) && approved.length >= limit}
                  onchange={event => toggle(option.id, event.currentTarget.checked)} />
                {option.label}
              </label>
            {/each}
          </fieldset>
        {:else}
          <fieldset>
            <legend>Options</legend>
            {#each ballot.options as option (option.id)}
              <label class="opt"><input type="radio" name="ballot-{id}" value={option.id} bind:group={chosen} /> {option.label}</label>
            {/each}
          </fieldset>
        {/if}
        <button class="primary" disabled={vote.busy || selection.length === 0}>Cast my vote</button>
        <Status action={vote} />
      </form>
    {/if}

    {#if ballot.counting}<p class="help" role="status">Voting has closed. Refresh the ballot shortly to see the published count.</p>{/if}
    {#if ballot.results}<BallotResults {ballot} />{/if}

    {#if ballot.record}
      <h4>Published record</h4>
      <p class="meta">{ballot.record.count} {ballot.record.count === 1 ? "vote" : "votes"} recorded. {anchorText(ballot.record.anchor) || "Not anchored."}</p>
      <p class="help">Record digest: <span class="receipt">{ballot.record.digest}</span></p>
      {#if ballot.record.anchor?.tx}<p class="help">Transaction: <span class="receipt">{ballot.record.anchor.tx}</span></p>{/if}
      <form onsubmit={verify}>
        <label for="receipt-{id}">Receipt to look for (optional)</label>
        <input id="receipt-{id}" class="receipt" bind:value={receipt} maxlength="64" autocomplete="off" />
        <button class="secondary" disabled={check.busy}>Check the record</button>
        <Status action={check} />
      </form>
      {#if verified}
        <ul role="status">
          {#if verified.included !== null}
            <li class={verified.included ? "ok" : "err"}>{verified.included ? "Your receipt is in the published record: your vote was counted." : "That receipt is not in the published record. Check it was copied exactly, then contact the elections officials."}</li>
          {/if}
          <li class={verified.root ? "ok" : "err"}>{verified.root ? "The Merkle root matches the published receipts." : "The Merkle root does not match the published receipts."}</li>
          <li class={verified.digest ? "ok" : "err"}>{verified.digest ? "The digest matches the published record. Compare it with the external anchor to verify anchoring." : "The digest does not match the published record."}</li>
        </ul>
      {/if}
    {/if}
  </section>

  {#if session.adminMode && (ballot.manage || ballot.can_unseal)}
    <BallotOfficial {ballot} onchange={refresh} />
  {/if}
{/if}
