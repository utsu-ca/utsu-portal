<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";

  const load = new Action(), save = new Action();
  let catalogue = $state({}), holders = $state([]), username = $state(""), chosen = $state({});

  const refresh = async () => {
    catalogue = await api("/admin/roles");
    holders = await api("/admin/roles/holders");
  };
  onMount(() => load.run(refresh));

  const setRoles = () => save.run(async () => {
    await api("/admin/roles", { method: "PUT", body: { username, roles: Object.keys(catalogue).filter(r => chosen[r]) } });
    await refresh();
  });
</script>

<Status action={load} />
<div class="card">
  <h3>Assign roles</h3>
  <label for="role-user">Username of a verified account</label>
  <input id="role-user" autocomplete="off" bind:value={username} />
  {#each Object.entries(catalogue) as [role, description]}
    <label class="opt" title={description}><input type="checkbox" bind:checked={chosen[role]} /> {role} — {description}</label>
  {/each}
  <button class="primary" onclick={setRoles} disabled={save.busy}>Set roles</button>
  <Status action={save} />
</div>
{#each holders as holder (holder.uuid)}
  <div class="card">
    <strong>{holder.preferred_name || holder.uuid}</strong>
    <p>{holder.roles.join(", ")}</p>
    {#if holder.username}
      <p class="help">{holder.email}</p>
      <button class="secondary" disabled={save.busy} onclick={() => {
        username = holder.username;
        chosen = Object.fromEntries(holder.roles.map(role => [role, true]));
      }}>Load roles for {holder.preferred_name || holder.email}</button>
    {/if}
    <p class="help">{holder.uuid}</p>
  </div>
{/each}
