<script>
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "./Status.svelte";

  // An administrator previews the exact notice, confirms it, and the server refuses a repeat.
  let { program, uid } = $props();
  const action = new Action();

  const preview = () => action.run(async () => {
    const base = `/admin/notices/${program}`;
    const draft = await api(base + "/preview", { method: "POST", body: { uid } });
    if (draft.delivery_status) throw new Error("This notice was already sent or its delivery is unresolved.");
    if (!confirm(`Send this notice?\n\nTo: ${draft.to}\nSubject: ${draft.subject}\n\n${draft.body}`)) return;
    await api(base + "/send", { method: "POST", body: { uid, preview_hash: draft.preview_hash, confirm: true } });
    action.ok = "Notice sent.";
  });
</script>

<button class="secondary" onclick={preview} disabled={action.busy}>Preview status notice</button>
<Status {action} />
