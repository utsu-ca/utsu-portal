<script>
  // The counted result of a closed ballot: totals for yes/no and plurality methods, round-by-round transfers for
  // ranked methods. Fractions from transfers ("15/7") are shown to two decimals.
  let { ballot } = $props();

  const results = $derived(ballot.results);
  const label = id => ballot.options.find(option => option.id === id)?.label ?? id;
  const winners = $derived(["ranked", "stv"].includes(ballot.method) ? "Elected" : "Chosen");
  const number = value => {
    const [top, bottom] = String(value).split("/").map(Number);
    return bottom ? (top / bottom).toFixed(2) : String(top);
  };
  const outcome = $derived({
    carried: "Carried",
    defeated: "Defeated",
    decided: `${winners}: ${(results.elected || []).map(label).join(", ")}`,
    tie: "Tied. The elections officials must resolve the tie.",
    no_votes: "No votes were cast.",
    no_quorum: `Quorum not met: ${results.ballots} of ${ballot.quorum} ballots needed.`,
  }[results.outcome] ?? results.outcome);
  const rows = $derived(results.counts
    ? Object.entries(results.counts).sort(([a, x], [b, y]) => y - x || ballot.options.findIndex(o => o.id === a) - ballot.options.findIndex(o => o.id === b))
    : []);
  const step = round => [
    round.elected?.length ? `Elected ${round.elected.map(label).join(", ")}` : "",
    round.excluded ? `Excluded ${label(round.excluded)}` : "",
    round.tied?.length ? `Tied: ${round.tied.map(label).join(", ")}` : "",
  ].filter(Boolean).join("; ");
</script>

<h4>Result</h4>
<p class="ok" role="status">{outcome}</p>
{#if results.tied?.length}<p class="help">Tied: {results.tied.map(label).join(", ")}</p>{/if}
<p class="meta">{results.ballots} {results.ballots === 1 ? "ballot" : "ballots"} counted{ballot.quorum ? `; quorum ${ballot.quorum}` : ""}.</p>

{#if rows.length}
  <table class="grid">
    <thead><tr><th scope="col">Option</th><th scope="col">Votes</th></tr></thead>
    <tbody>
      {#each rows as [id, count] (id)}
        <tr><td>{ballot.method === "yes_no" ? { yes: "Yes", no: "No", abstain: "Abstain" }[id] : label(id)}{results.elected?.includes(id) ? ` (${winners.toLowerCase()})` : ""}</td><td>{count}</td></tr>
      {/each}
    </tbody>
  </table>
{/if}

{#if results.rounds?.length}
  <p class="meta">Quota {number(results.quota)} of {results.valid} valid ballots.</p>
  <table class="grid">
    <thead>
      <tr><th scope="col">Round</th>{#each ballot.options as option (option.id)}<th scope="col">{option.label}</th>{/each}<th scope="col">Exhausted</th><th scope="col">Outcome</th></tr>
    </thead>
    <tbody>
      {#each results.rounds as round, index (index)}
        <tr>
          <th scope="row">{index + 1}</th>
          {#each ballot.options as option (option.id)}<td>{round.tally && option.id in round.tally ? number(round.tally[option.id]) : "–"}</td>{/each}
          <td>{round.exhausted ? number(round.exhausted) : "–"}</td>
          <td>{round.remaining ? `Remaining candidates fill the seats: ${round.elected.map(label).join(", ")}` : step(round)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}
