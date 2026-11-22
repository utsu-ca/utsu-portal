<script>
  import { onMount } from "svelte";
  import { api, apiAllPages } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { session } from "../../../lib/session.svelte.js";
  import Status from "../../../components/Status.svelte";

  // Register a club, see the registrations you belong to, and (as owner) add a treasurer or executives.
  const load = new Action(), create = new Action(), member = new Action(), decision = new Action(), renewal = new Action();
  let clubs = $state([]), name = $state(""), description = $state(""), category = $state("");
  let invitations = $state([]);
  let memberOf = $state(""), username = $state(""), role = $state("treasurer");

  const refresh = async () => {
    [clubs, invitations] = await Promise.all([apiAllPages("/clubs/mine"), apiAllPages("/clubs/invitations")]);
  };
  onMount(() => load.run(refresh));

  const register = () => create.run(async () => {
    await api("/clubs", { method: "POST", body: { name, description, category } });
    name = description = category = "";
    await refresh();
  }, "Registration submitted. UTSU administrators will review it.");

  const addMember = () => member.run(async () => {
    await api(`/clubs/${memberOf}/members`, { method: "POST", body: { username, role } });
    username = "";
    await refresh();
  }, "Invitation sent. The recipient must accept before the role takes effect.");

  const respond = (invitation, approve) => decision.run(async () => {
    await api(`/clubs/${invitation.club_id}/invitations/${invitation.id}/decision`,
      { method: "POST", body: { approve } });
    await refresh();
  }, approve ? "Invitation accepted." : "Invitation declined.");

  const renew = club => renewal.run(async () => {
    await api(`/clubs/${club.id}/renewal`, { method: "POST" });
    await refresh();
  }, "Renewal submitted for review.");
</script>

<Status action={load} />
<div class="card">
  <h2>Club invitations</h2>
  <Status action={decision} />
  {#if !invitations.length && !load.busy}<p>You have no pending invitations.</p>{/if}
  {#each invitations as invitation (invitation.id)}
    <div>
      <p>{invitation.club_name} invited you to be {invitation.role}.</p>
      <button class="primary" onclick={() => respond(invitation, true)} disabled={decision.busy}>Accept</button>
      <button class="secondary" onclick={() => respond(invitation, false)} disabled={decision.busy}>Decline</button>
    </div>
  {/each}
</div>
<div class="card">
  <h2>Register a club</h2>
  <p class="help">You become the club's owner. Name at least 3 characters; description at least 10.
    The name, category and description appear in the public directory when the club is approved.</p>
  <label for="club-name">Club name</label>
  <input id="club-name" maxlength="100" bind:value={name} />
  <label for="club-category">Category</label>
  <input id="club-category" maxlength="50" bind:value={category} />
  <label for="club-description">Description</label>
  <textarea id="club-description" rows="4" maxlength="2000" bind:value={description}></textarea>
  <button class="primary" onclick={register} disabled={create.busy}>Submit registration</button>
  <Status action={create} />
</div>

<div class="card"><h3>My clubs</h3></div>
{#if !clubs.length && !load.busy}<div class="card"><p>You do not belong to any clubs yet.</p></div>{/if}
{#each clubs as club (club.id)}
  <div class="card">
    <h3>{club.name}</h3>
    <p><span class="badge">{club.status}</span> {club.category}</p>
    {#if club.approval_year != null}<p>Approved for {club.approval_year}–{club.approval_year + 1} (September–August).</p>{/if}
    <p>{club.description}</p>
    <ul>{#each Object.entries(club.members) as [uuid, memberRole]}<li>{memberRole}: <span class="help">{uuid}</span></li>{/each}</ul>
    {#if club.members[session.me?.user?.uuid] === "owner" && (club.status === "expired" || (club.status === "rejected" && club.approval_year != null))}
      <button class="primary" onclick={() => renew(club)} disabled={renewal.busy}>Request renewal</button>
      <Status action={renewal} />
    {/if}
    {#if club.members[session.me?.user?.uuid] === "owner" && memberOf === club.id}
      <label for={"m-" + club.id}>Email or username of a verified account</label>
      <input id={"m-" + club.id} autocomplete="off" bind:value={username} />
      <label for={"r-" + club.id}>Role</label>
      <select id={"r-" + club.id} bind:value={role}><option value="treasurer">treasurer</option><option value="executive">executive</option></select>
      <button class="primary" onclick={addMember} disabled={member.busy}>Send invitation</button>
      <Status action={member} />
    {:else if club.members[session.me?.user?.uuid] === "owner"}
      <button class="secondary" onclick={() => (memberOf = club.id)}>Add a member</button>
    {/if}
  </div>
{/each}
