<script>
  import { onMount } from "svelte";
  import Status from "../../../components/Status.svelte";
  import { Action } from "../../../lib/action.svelte.js";
  import { api } from "../../../lib/api.js";
  import { formatCents, MONTHS, parseDollars } from "../lib/money.js";

  const load = new Action(), save = new Action();
  let overview = $state(null);
  const blank = () => ({ id: null, name: "", description: "", fall: "", summer: "", group: "", refundable: true, active: true });
  let fee = $state(blank()), group = $state({ id: null, name: "", description: "", members: "" });
  let windows = $state({}), headcounts = $state({});

  const refresh = () => load.run(async () => {
    overview = await api("/admin/fees");
    windows = Object.fromEntries(overview.windows.map(w => [w.id, { start_month: w.start_month, length_months: w.length_months }]));
    headcounts = Object.fromEntries(overview.sessions.map(s => [s.session, s.expected_enrolment ?? ""]));
  });
  onMount(refresh);

  const edit = existing => {
    fee = {
      id: existing.id, name: existing.name, description: existing.description || "",
      fall: existing.amounts_cents.fall != null ? (existing.amounts_cents.fall / 100).toFixed(2) : "",
      summer: existing.amounts_cents.summer != null ? (existing.amounts_cents.summer / 100).toFixed(2) : "",
      group: existing.levy_group_id || "", refundable: existing.refundable, active: existing.active,
    };
  };

  const saveFee = () => save.run(async () => {
    const amounts_cents = {};
    for (const key of ["fall", "summer"]) {
      if (String(fee[key]).trim() === "") continue;
      const cents = parseDollars(fee[key]);
      if (cents === null) throw new Error("Amounts are dollars with at most two decimals, for example 25.00.");
      amounts_cents[key] = cents;
    }
    const body = {
      name: fee.name, description: fee.description, amounts_cents, levy_group_id: fee.group || null,
      refundable: fee.refundable, active: fee.active,
    };
    await api(fee.id ? `/admin/fees/${fee.id}` : "/admin/fees", { method: fee.id ? "PUT" : "POST", body });
    fee = blank();
    await refresh();
  }, "Fee saved.");

  const removeFee = existing => save.run(async () => {
    await api(`/admin/fees/${existing.id}`, { method: "DELETE" });
    await refresh();
  }, "Fee deleted.");

  const saveWindow = id => save.run(async () => {
    await api(`/admin/fees/windows/${id}`, { method: "PUT", body: {
      start_month: Number(windows[id].start_month), length_months: Number(windows[id].length_months),
    } });
    await refresh();
  }, "Window saved.");

  const saveHeadcount = session => save.run(async () => {
    const value = String(headcounts[session]).trim();
    await api(`/admin/fees/sessions/${encodeURIComponent(session)}`, { method: "PUT", body: {
      expected_enrolment: value === "" ? null : Number(value),
    } });
    await refresh();
  }, "Headcount saved.");

  const saveGroup = () => save.run(async () => {
    const members = group.members.split(/[\n,]+/).map(entry => entry.trim()).filter(Boolean);
    const body = { name: group.name, description: group.description, members };
    await api(group.id ? `/admin/fees/groups/${group.id}` : "/admin/fees/groups", { method: group.id ? "PUT" : "POST", body });
    group = { id: null, name: "", description: "", members: "" };
    await refresh();
  }, "Levy group saved.");

  const removeGroup = existing => save.run(async () => {
    await api(`/admin/fees/groups/${existing.id}`, { method: "DELETE" });
    await refresh();
  }, "Levy group deleted.");

  const groupName = id => overview.groups.find(g => g.id === id)?.name ?? "No levy group";
</script>

<Status action={load} />
<Status action={save} />
{#if overview}
  <div class="card">
    <h2>Opt-out windows</h2>
    <p>Opt-outs open once for each session and stay open for the chosen number of months.</p>
    {#each overview.windows as window (window.id)}
      <div class="card">
        <h3>{window.label}</h3>
        <p>{window.open ? "Open now" : "Closed"}; applies to the {window.session_label}.</p>
        <label for={`start-${window.id}`}>Opens on the first of</label>
        <select id={`start-${window.id}`} bind:value={windows[window.id].start_month}>
          {#each MONTHS as month, index}<option value={index + 1}>{month}</option>{/each}
        </select>
        <label for={`length-${window.id}`}>Open for (months)</label>
        <input id={`length-${window.id}`} type="number" min="1" max="12" bind:value={windows[window.id].length_months} />
        <button class="primary" disabled={save.busy} onclick={() => saveWindow(window.id)}>Save window</button>
      </div>
    {/each}
    <h3>Expected headcount</h3>
    <p class="help">Used to predict levy payments. Leave blank to use the size of the imported roster.</p>
    {#each overview.sessions as session (session.session)}
      <label for={`count-${session.session}`}>{session.label}{session.roster_count != null ? ` (roster: ${session.roster_count})` : ""}</label>
      <input id={`count-${session.session}`} type="number" min="0" bind:value={headcounts[session.session]} />
      <button class="secondary" disabled={save.busy} onclick={() => saveHeadcount(session.session)}>Save headcount</button>
    {/each}
  </div>

  <div class="card">
    <h2>Fees</h2>
    {#each overview.fees as existing (existing.id)}
      <div class="card">
        <strong>{existing.name}</strong>
        {#if !existing.active}<span class="badge">Inactive</span>{/if}
        {#if !existing.refundable}<span class="badge">Not refundable</span>{/if}
        <p>Fall-Winter: {formatCents(existing.amounts_cents.fall)}; Summer: {formatCents(existing.amounts_cents.summer)}</p>
        <p class="help">{groupName(existing.levy_group_id)}</p>
        <button class="secondary" onclick={() => edit(existing)}>Edit</button>
        <button class="secondary" disabled={save.busy} onclick={() => removeFee(existing)}>Delete</button>
      </div>
    {:else}
      <p>No fees yet.</p>
    {/each}
    <h3>{fee.id ? "Edit fee" : "Add a fee"}</h3>
    <label for="fee-name">Name</label>
    <input id="fee-name" maxlength="120" bind:value={fee.name} />
    <label for="fee-description">Description</label>
    <input id="fee-description" maxlength="1000" bind:value={fee.description} />
    <label for="fee-fall">Fall-Winter session amount (CAD, blank if not charged)</label>
    <input id="fee-fall" inputmode="decimal" bind:value={fee.fall} />
    <label for="fee-summer">Summer session amount (CAD, blank if not charged)</label>
    <input id="fee-summer" inputmode="decimal" bind:value={fee.summer} />
    <label for="fee-group">Levy group</label>
    <select id="fee-group" bind:value={fee.group}>
      <option value="">No levy group</option>
      {#each overview.groups as option (option.id)}<option value={option.id}>{option.name}</option>{/each}
    </select>
    <label class="opt"><input type="checkbox" bind:checked={fee.refundable} /> Refundable once paid</label>
    <label class="opt"><input type="checkbox" bind:checked={fee.active} /> Offered for opt-out</label>
    <button class="primary" disabled={save.busy} onclick={saveFee}>{fee.id ? "Save changes" : "Add fee"}</button>
    {#if fee.id}<button class="secondary" onclick={() => (fee = blank())}>Cancel</button>{/if}
  </div>

  <div class="card">
    <h2>Levy groups</h2>
    <p>Members of a levy group, who also hold the levy_group role, can see who opted out of the group's fees.</p>
    {#each overview.groups as existing (existing.id)}
      <div class="card">
        <strong>{existing.name}</strong>
        <p class="help">{existing.members.map(m => m.name || m.uuid).join(", ") || "No members"}</p>
        <button class="secondary" onclick={() => (group = {
          id: existing.id, name: existing.name, description: existing.description || "",
          members: existing.members.map(m => m.uuid).join("\n"),
        })}>Edit</button>
        <button class="secondary" disabled={save.busy} onclick={() => removeGroup(existing)}>Delete</button>
      </div>
    {/each}
    <h3>{group.id ? "Edit levy group" : "Add a levy group"}</h3>
    <label for="group-name">Name</label>
    <input id="group-name" maxlength="120" bind:value={group.name} />
    <label for="group-description">Description</label>
    <input id="group-description" maxlength="1000" bind:value={group.description} />
    <label for="group-members">Members (emails or usernames, one per line)</label>
    <textarea id="group-members" rows="4" bind:value={group.members}></textarea>
    <button class="primary" disabled={save.busy} onclick={saveGroup}>{group.id ? "Save changes" : "Add levy group"}</button>
    {#if group.id}<button class="secondary" onclick={() => (group = { id: null, name: "", description: "", members: "" })}>Cancel</button>{/if}
  </div>
{/if}
