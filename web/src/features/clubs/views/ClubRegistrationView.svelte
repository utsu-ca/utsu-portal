<script>
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import Status from "../../../components/Status.svelte";

  // Register a club, see the registrations you belong to, and (as owner) add a treasurer or executives.
  const load = new Action(), create = new Action(), member = new Action();
  let clubs = $state([]), name = $state(""), description = $state(""), category = $state("");
  let memberOf = $state(""), username = $state(""), role = $state("treasurer");

  const refresh = async () => { clubs = await api("/clubs/mine"); };
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
  }, "Member saved.");
</script>

<Status action={load} />
<div class="card">
  <h2>Register a club</h2>
  <p class="help">You become the club's owner. Name at least 3 characters; description at least 10.</p>
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
    <p>{club.description}</p>
    <ul>{#each Object.entries(club.members) as [uuid, memberRole]}<li>{memberRole}: <span class="help">{uuid}</span></li>{/each}</ul>
    {#if memberOf === club.id}
      <label for={"m-" + club.id}>Username of a verified account</label>
      <input id={"m-" + club.id} autocomplete="off" bind:value={username} />
      <label for={"r-" + club.id}>Role</label>
      <select id={"r-" + club.id} bind:value={role}><option value="treasurer">treasurer</option><option value="executive">executive</option></select>
      <button class="primary" onclick={addMember} disabled={member.busy}>Save member</button>
      <Status action={member} />
    {:else}
      <button class="secondary" onclick={() => (memberOf = club.id)}>Add a member</button>
    {/if}
  </div>
{/each}
