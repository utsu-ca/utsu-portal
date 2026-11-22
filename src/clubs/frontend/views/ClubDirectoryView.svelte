<script>
  import { onMount } from "svelte";
  import { Action, apiAllPages, Status } from "@utsu/portal";

  const load = new Action();
  let clubs = $state([]), query = $state(""), loaded = $state(false);
  const filtered = $derived(clubs.filter(club =>
    [club.name, club.category, club.description].some(value => value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))));

  const refresh = () => load.run(async () => {
    clubs = await apiAllPages("/clubs/directory");
    loaded = true;
  });
  onMount(refresh);
</script>

<div class="card">
  <h2>Club directory</h2>
  <p>Browse clubs approved for the current academic year.</p>
  <label for="club-search">Search clubs</label>
  <input id="club-search" type="search" bind:value={query} />
  <Status action={load} />
  {#if load.error}<button class="secondary" onclick={refresh}>Retry</button>{/if}
</div>
{#if loaded}
  {#if !filtered.length}
    <div class="card"><p>{query.trim() ? "No clubs match your search." : "No approved clubs are listed yet."}</p></div>
  {/if}
  {#each filtered as club (club.id)}
    <div class="card">
      <h3>{club.name}</h3>
      <p>{club.category}</p>
      <p>{club.description}</p>
    </div>
  {/each}
{/if}
