<script>
  import { onDestroy, onMount } from "svelte";
  import { api, apiAllPages } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { openStream } from "../lib/stream.js";
  import Status from "../components/Status.svelte";
  import FormRenderer from "../components/FormRenderer.svelte";

  let { id, isNew = false } = $props();
  const FIELD_TYPES = ["text", "textarea", "email", "number", "date", "select", "radio", "multiselect", "checkbox", "info"];
  const STATUSES = ["submitted", "under_review", "needs_info", "accepted", "rejected"];

  const load = new Action(), preview = new Action(), act = new Action();
  // svelte-ignore state_referenced_locally
  let yaml = $state(`id: ${id}\nversion: 1\ntitle: New form\nfields:\n  - {id: name, type: text, label: Name, required: true}\n`);
  let schema = $state(null), previewValues = $state({}), rev = 0, note = $state(""), newType = $state("text");
  let submissions = $state([]), timer, events;

  async function convert(body) {
    return api("/admin/forms/convert", { method: "POST", body });
  }

  const refreshPreview = () => preview.run(async () => {
    schema = (await convert({ yaml })).schema;
  }, "valid");

  function edited() {
    clearTimeout(timer);
    timer = setTimeout(refreshPreview, 400);
  }

  onMount(() => load.run(async () => {
    if (!isNew) {
      const form = await api("/admin/forms/" + id);
      rev = form.rev;
      yaml = form.draft_yaml;
      submissions = (await apiAllPages(`/admin/forms/${id}/submissions`))
        .map(s => ({ ...s, newStatus: s.status, note: "" }));
      // realtime sync of edits made by other admins
      events = openStream(`/admin/forms/${id}/events`, {
        onEvent: (type, data) => {
          if (type !== "message") return;
          if (data.yaml && data.rev !== rev) {
            rev = data.rev;
            yaml = data.yaml;
            refreshPreview();
            act.ok = "updated by another admin (rev " + data.rev + ")";
          }
        },
      });
    }
    await refreshPreview();
  }));
  onDestroy(() => { clearTimeout(timer); events?.close(); });

  // Add a field to the schema and regenerate the YAML.
  const addField = () => act.run(async () => {
    const current = schema || (await convert({ yaml })).schema;
    const field = { id: "field_" + (current.fields.length + 1), type: newType, label: "New " + newType };
    if (["select", "radio", "multiselect"].includes(newType)) field.options = ["Option 1", "Option 2"];
    current.fields.push(field);
    yaml = (await convert({ schema: current })).yaml;
    await refreshPreview();
  });

  const saveDraft = () => act.run(async () => {
    const result = await api(`/admin/forms/${id}/draft`, { method: "PUT", body: { yaml, base_rev: rev } });
    rev = result.rev;
    act.ok = "saved rev " + rev;
  });
  const publish = () => act.run(async () => { await api(`/admin/forms/${id}/publish`, { method: "POST" }); }, "published");
  const unpublish = () => act.run(async () => { await api(`/admin/forms/${id}/unpublish`, { method: "POST" }); }, "unpublished");

  const setStatus = submission => act.run(async () => {
    await api(`/admin/submissions/${id}/${submission.uuid}/status`, { method: "POST",
      body: { status: submission.newStatus, note: submission.note } });
  }, "updated");
</script>

<Status action={load} />
<div class="card">
  <h2>Edit {id}</h2>
  <div class="row">
    <textarea class="yaml" spellcheck="false" aria-label="Form YAML" bind:value={yaml} oninput={edited}></textarea>
    <div class="card">
      {#if schema}<FormRenderer {schema} bind:values={previewValues} />{/if}
    </div>
  </div>
  <p class={preview.error ? "err" : "ok"}>{preview.error || preview.ok}</p>
  <select aria-label="Field type" bind:value={newType}>
    {#each FIELD_TYPES as type}<option value={type}>{type}</option>{/each}
  </select>
  <button class="primary" onclick={addField}>Add field</button>
  <button class="primary" onclick={saveDraft}>Save draft</button>
  <button class="primary" onclick={publish}>Publish</button>
  <button class="secondary" onclick={unpublish}>Unpublish</button>
  <Status action={act} />
</div>
{#each submissions as submission (submission.uuid)}
  {@const a = submission.applicant}
  <div class="card">
    <strong>{a.first_name} {a.last_name} ({a.preferred_name}) ***{a.student_number}</strong>
    <pre>{JSON.stringify(submission.data, null, 2)}</pre>
    <select aria-label="Status" bind:value={submission.newStatus}>
      {#each STATUSES as status}<option value={status}>{status}</option>{/each}
    </select>
    <input placeholder="note" bind:value={submission.note} />
    <button class="primary" onclick={() => setStatus(submission)}>Set status</button>
  </div>
{/each}
