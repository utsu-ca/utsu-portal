<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";

  const load = new Action();
  let forms = $state([]);
  onMount(() => load.run(async () => { forms = await api("/admin/forms"); }));

  const create = () => {
    const id = prompt("New form id (a-z, 0-9, _)");
    if (id) go("formEditor", { id, isNew: true });
  };
</script>

<Status action={load} />
{#each forms as form (form.id)}
  <div class="card">
    <h3>{form.id}</h3>
    <span class="badge">{form.published_version ? "published v" + form.published_version : "unpublished"}</span><br />
    <button class="primary" onclick={() => go("formEditor", { id: form.id })}>Edit</button>
  </div>
{/each}
<button class="primary" onclick={create}>New form from template</button>
