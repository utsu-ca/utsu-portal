<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";

  let file = $state(null), replace = $state(false);
  let page = $state(null), loading = $state(false), loadError = $state("");
  const pageSize = 25;
  const action = new Action();

  async function load(offset = 0) {
    loading = true;
    loadError = "";
    try {
      page = await api(`/admin/roster?offset=${offset}&limit=${pageSize}`);
    } catch (error) {
      loadError = error.message;
    } finally {
      loading = false;
    }
  }

  onMount(() => { load(); });

  const upload = () => action.run(async () => {
    if (!file) throw new Error("Choose one roster CSV file.");
    if (replace && !confirm("Replace the current roster and recheck eligibility?")) return;
    const csv = await file.text();
    const result = await api("/admin/roster", { method: "POST", body: { csv, replace } });
    action.ok = result.imported
      ? `Roster imported: ${result.count} students; ${result.rostered} of ${result.checked} accounts matched.`
      : "A roster already exists. Select replacement to import a new one.";
    if (result.imported) {
      page = null;
      await load();
    }
  });
</script>

<div class="card">
  <h2>Student roster</h2>
  <p class="help">CSV must contain First Name, Last Name, Truncated Student Number, Faculty and Division. Roster data is private.</p>
  <p class="help">Replacing a roster changes eligibility. Do not replace it during seating or reviews.</p>
  <input type="file" accept=".csv,text/csv" aria-label="Roster CSV" onchange={e => (file = e.currentTarget.files?.[0] ?? null)} />
  <label class="opt"><input type="checkbox" bind:checked={replace} /> Replace the existing roster</label>
  <button class="primary" onclick={upload} disabled={action.busy || loading}>Import roster</button>
  <Status {action} />
</div>

<div class="card">
  <h2>Roster entries</h2>
  <p class="help">Email and last login are shown only for a uniquely matched portal account. A dash means information is unavailable.</p>
  {#if loadError}
    <p role="alert">Could not load roster: {loadError}</p>
    <button onclick={() => load(page?.offset ?? 0)} disabled={loading}>Retry</button>
  {/if}
  {#if loading}<p>Loading roster…</p>{/if}
  {#if page}
    {#if page.total === 0}
      <p>No roster entries.</p>
    {:else if page.items.length === 0}
      <p>No entries on this page.</p>
      <button onclick={() => load(0)} disabled={loading}>First page</button>
    {:else}
      <div style="overflow-x: auto">
        <table class="grid">
          <thead><tr><th>Name</th><th>Student Number</th><th>Email</th><th>Last Login</th></tr></thead>
          <tbody>
            {#each page.items as student}
              <tr>
                <td>{student.name}</td>
                <td>{student.student_number ?? "—"}</td>
                <td>{student.email ?? "—"}</td>
                <td>{student.last_login != null ? new Date(student.last_login * 1000).toLocaleString() : "—"}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p>Showing {page.offset + 1}–{Math.min(page.offset + page.items.length, page.total)} of {page.total}</p>
      <button onclick={() => load(Math.max(0, page.offset - pageSize))} disabled={loading || page.offset === 0}>Previous</button>
      <button onclick={() => load(page.offset + pageSize)} disabled={loading || page.offset + page.items.length >= page.total}>Next</button>
    {/if}
  {/if}
</div>
