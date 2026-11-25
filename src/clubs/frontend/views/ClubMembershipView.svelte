<script>
  import { onMount } from "svelte";
  import { Action, api, apiAllPages, Status } from "@utsu/portal";

  // Join a club, or leave one. Membership here is separate from the club's officers and from UTSU membership.
  const load = new Action(), change = new Action();
  let joined = $state([]), directory = $state([]), messages = $state({});

  const refresh = async () => {
    const [mine, clubs] = await Promise.all([api("/clubs/joined"), apiAllPages("/clubs/directory")]);
    joined = mine.items;
    directory = clubs;
  };
  onMount(() => load.run(refresh));

  const isMember = club => joined.some(item => item.id === club.id);
  const ask = club => change.run(async () => {
    await api(`/clubs/${club.id}/join-requests`, { method: "POST", body: { message: messages[club.id] ?? "" } });
    messages[club.id] = "";
  }, `Your request to join ${club.name} was sent. An officer will review it.`);
  const leave = club => change.run(async () => {
    if (!confirm(`Leave ${club.name}?`)) return;
    await api(`/clubs/${club.id}/leave`, { method: "POST" });
    await refresh();
  }, `You left ${club.name}.`);
</script>

<Status action={load} />
<div class="card">
  <h2>My club memberships</h2>
  <p class="help">Clubs you belong to as a member. Officers are listed under Register or manage a club.</p>
  {#if !joined.length && !load.busy}<p>You are not a member of any club yet.</p>{/if}
  {#each joined as club (club.id)}
    <div>
      <strong>{club.name}</strong> <span class="help">{club.category}{club.source === "honorary" ? " · honorary" : ""}</span>
      <button class="secondary" onclick={() => leave(club)} disabled={change.busy}>Leave</button>
    </div>
  {/each}
  <Status action={change} />
</div>
<div class="card">
  <h2>Join a club</h2>
  <p class="help">Membership in a club does not require UTSU membership. An officer of the club approves each request.</p>
  {#each directory.filter(club => !isMember(club)) as club (club.id)}
    <div>
      <h3>{club.name}</h3>
      <p>{club.description}</p>
      <label for={"join-" + club.id}>Message to the club (optional)</label>
      <input id={"join-" + club.id} maxlength="200" autocomplete="off" bind:value={messages[club.id]} />
      <button class="primary" onclick={() => ask(club)} disabled={change.busy}>Ask to join {club.name}</button>
    </div>
  {/each}
</div>
