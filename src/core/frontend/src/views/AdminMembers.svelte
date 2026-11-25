<script>
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";

  let by = $state("email"), query = $state(""), reason = $state(""), division = $state("");
  let member = $state(null);
  const search = new Action(), change = new Action();

  const find = () => search.run(async () => {
    member = null;
    const param = by === "email" ? "email" : "student_number";
    member = await api(`/admin/members/lookup?${param}=${encodeURIComponent(query.trim())}`);
    reason = "";
    division = "";
  });

  const grant = () => change.run(async () => {
    member = await api(`/admin/members/${member.uuid}/honorary`, { method: "PUT", body: { reason, division } });
    reason = "";
    division = "";
  }, "Honorary membership granted for this session.");

  const revoke = () => change.run(async () => {
    if (!confirm("Remove this honorary membership?")) return;
    member = await api(`/admin/members/${member.uuid}/honorary`, { method: "DELETE" });
  }, "Honorary membership removed.");
</script>

<div class="card">
  <h2>Member lookup</h2>
  <p class="help">Find an account by its email or its full student number. Lookups are recorded in the audit log.</p>
  <label for="member-by">Look up by</label>
  <select id="member-by" bind:value={by}>
    <option value="email">Email</option>
    <option value="student_number">Student number</option>
  </select>
  <label for="member-query">{by === "email" ? "Email address" : "Full student number"}</label>
  <input id="member-query" autocomplete="off" inputmode={by === "email" ? "email" : "numeric"} bind:value={query}
    onkeydown={e => e.key === "Enter" && query.trim() && !search.busy && find()} />
  <button class="primary" onclick={find} disabled={search.busy || !query.trim()}>Look up</button>
  <Status action={search} />
</div>

{#if member}
  <div class="card">
    <h3>{member.name || member.uuid}</h3>
    <p>{member.email} · student number {member.student_number ?? "—"} · account {member.status}</p>
    <p class="help">{member.uuid}{member.roles.length ? ` · roles: ${member.roles.join(", ")}` : ""}</p>
    <h4>Membership for {member.membership.session}</h4>
    <ul>
      <li>On the roster: {member.roster.rostered ? `yes${member.roster.division ? ` (${member.roster.division})` : ""}` : `no (${member.roster.reason || "no roster check"})`}</li>
      <li>Member now (any session): {member.membership.live_member ? "yes" : "no"}</li>
      <li>Member of this session: {member.membership.rostered_member ? "yes" : "no"}</li>
      {#if member.honorary}
        <li>Honorary: {member.honorary.session}{member.honorary.current ? "" : " (expired)"} — {member.honorary.reason}</li>
      {/if}
    </ul>
  </div>

  <div class="card">
    <h3>Honorary membership</h3>
    <p class="help">Counts as UTSU membership for {member.membership.session} only, without being on the roster. It ends with the session, and removing it takes effect at once. The reason is recorded in the audit log.</p>
    {#if member.status !== "verified"}
      <p role="alert">Only verified accounts can be granted honorary membership.</p>
    {:else}
      <label for="honorary-reason">Reason</label>
      <input id="honorary-reason" maxlength="200" autocomplete="off" bind:value={reason} />
      <label for="honorary-division">Division (optional, for example UC; needed for Senate seating)</label>
      <input id="honorary-division" maxlength="64" autocomplete="off" bind:value={division} />
      <button class="primary" onclick={grant} disabled={change.busy || reason.trim().length < 3}>
        {member.honorary?.current ? "Update honorary membership" : "Grant honorary membership"}
      </button>
      {#if member.honorary}
        <button class="secondary" onclick={revoke} disabled={change.busy}>Remove honorary membership</button>
      {/if}
    {/if}
    <Status action={change} />
  </div>
{/if}
