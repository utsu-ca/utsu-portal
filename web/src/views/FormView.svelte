<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { validate } from "../lib/forms.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";
  import FormRenderer from "../components/FormRenderer.svelte";

  let { id } = $props();
  const load = new Action(), submit = new Action();
  let form = $state(null), values = $state({}), errors = $state({}), newSap = $state(false);

  // The whole form is reconstructed client side from the static JSON; the server is only asked to store the submission.
  onMount(() => load.run(async () => {
    form = await api(`/forms/${id}/static`);
    const previous = (await api("/me/submissions")).find(s => s.form_id === id);
    newSap = id === "student_aid_bursary" && !!previous && ["accepted", "rejected"].includes(previous.status);
    values = previous && !newSap ? previous.data : {};
  }));

  const send = () => submit.run(async () => {
    const result = validate(form.schema, values);
    errors = result.errors;
    if (Object.keys(errors).length) throw new Error("Fix the highlighted fields, then submit again.");
    try {
      await api(`/forms/${id}/submission`, { method: "PUT", body: { version: form.version, hash: form.hash, data: result.data } });
    } catch (e) {
      // the server's own checks come back per field so the person can re-enter just those
      if (e.status === 422 && e.detail && typeof e.detail === "object" && !Array.isArray(e.detail)) {
        errors = e.detail;
        throw new Error("Fix the highlighted fields, then submit again.");
      }
      throw e;
    }
    go("status");
  });
</script>

<Status action={load} />
{#if form}
  <div class="card">
    <h2>{form.schema.title}</h2>
    <p>{form.schema.description || ""}</p>
    {#if newSap}<p>A new application may only cover terms not included in an earlier decision.</p>{/if}
    <FormRenderer schema={form.schema} bind:values {errors} />
    <button class="primary" onclick={send} disabled={submit.busy}>Submit</button>
    <Status action={submit} />
  </div>
{/if}
