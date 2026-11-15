<script>
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";

  let file = $state(null), replace = $state(false);
  const action = new Action();

  const upload = () => action.run(async () => {
    if (!file) throw new Error("Choose one roster CSV file.");
    if (replace && !confirm("Replace the current roster and recheck eligibility?")) return;
    const csv = await file.text();
    const result = await api("/admin/roster", { method: "POST", body: { csv, replace } });
    action.ok = result.imported
      ? `Roster imported: ${result.count} students; ${result.rostered} of ${result.checked} accounts matched.`
      : "A roster already exists. Select replacement to import a new one.";
  });
</script>

<div class="card">
  <h2>Student roster</h2>
  <p class="help">CSV must contain First Name, Last Name, Truncated Student Number, Faculty and Division. Roster data is private.</p>
  <p class="help">Replacing a roster changes eligibility. Do not replace it during seating or reviews.</p>
  <input type="file" accept=".csv,text/csv" aria-label="Roster CSV" onchange={e => (file = e.currentTarget.files?.[0] ?? null)} />
  <label class="opt"><input type="checkbox" bind:checked={replace} /> Replace the existing roster</label>
  <button class="primary" onclick={upload} disabled={action.busy}>Import roster</button>
  <Status {action} />
</div>
