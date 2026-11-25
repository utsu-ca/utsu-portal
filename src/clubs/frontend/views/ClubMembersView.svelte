<script>
  import { onMount } from "svelte";
  import { Action, api, apiAllPages, session, Status } from "@utsu/portal";

  // For the owner, treasurer and executives: requests to join, members, finding people and honorary membership.
  const load = new Action(), act = new Action(), find = new Action();
  let clubs = $state([]), cid = $state(""), members = $state([]), requests = $state([]), person = $state(null);
  let by = $state("email"), query = $state(""), reason = $state("");

  const officerOf = club => ["owner", "treasurer", "executive"].includes(club.members[session.me?.user?.uuid]);
  const club = $derived(clubs.find(item => item.id === cid));

  const refreshClub = async () => {
    person = null;
    if (!cid) return;
    [members, requests] = await Promise.all([api(`/clubs/${cid}/members`), api(`/clubs/${cid}/join-requests`)])
      .then(([m, r]) => [m.items, r.items]);
  };
  onMount(() => load.run(async () => {
    clubs = (await apiAllPages("/clubs/mine")).filter(officerOf);
    cid = clubs[0]?.id ?? "";
    await refreshClub();
  }));

  const decide = (request, approve) => act.run(async () => {
    await api(`/clubs/${cid}/join-requests/${request.uuid}/decision`, { method: "POST", body: { approve } });
    await refreshClub();
  }, approve ? "Request approved." : "Request declined.");
  const remove = member => act.run(async () => {
    if (!confirm(`Remove ${member.name} from the club?`)) return;
    await api(`/clubs/${cid}/members/${member.uuid}`, { method: "DELETE" });
    await refreshClub();
  }, "Member removed.");
  const lookup = () => find.run(async () => {
    person = null;
    person = await api(`/clubs/${cid}/people/lookup?${by === "email" ? "email" : "student_number"}=${encodeURIComponent(query.trim())}`);
  });
  const honorary = () => act.run(async () => {
    await api(`/clubs/${cid}/members/${person.uuid}/honorary`, { method: "PUT", body: { reason } });
    reason = "";
    await refreshClub();
    person = await api(`/clubs/${cid}/people/lookup?${by === "email" ? "email" : "student_number"}=${encodeURIComponent(query.trim())}`);
  }, "Honorary membership granted for this membership session.");
</script>

<Status action={load} />
{#if !clubs.length && !load.busy}
  <div class="card"><p>You are not an officer of any club.</p></div>
{:else if clubs.length}
  <div class="card">
    <h2>Club members</h2>
    <label for="members-club">Club</label>
    <select id="members-club" bind:value={cid} onchange={() => load.run(refreshClub)}>
      {#each clubs as item (item.id)}<option value={item.id}>{item.name}</option>{/each}
    </select>
    <Status action={act} />
  </div>

  <div class="card">
    <h3>Requests to join</h3>
    {#if !requests.length}<p>No requests are waiting.</p>{/if}
    {#each requests as request (request.uuid)}
      <div>
        <strong>{request.name}</strong>{request.message ? ` — ${request.message}` : ""}
        <button class="primary" onclick={() => decide(request, true)} disabled={act.busy}>Approve</button>
        <button class="secondary" onclick={() => decide(request, false)} disabled={act.busy}>Decline</button>
      </div>
    {/each}
  </div>

  <div class="card">
    <h3>Members</h3>
    <ul>
      {#each members as member (member.uuid + member.role)}
        <li>{member.name} — {member.role}{member.source === "honorary" ? " (honorary)" : ""}
          {#if member.role === "member"}<button class="secondary" onclick={() => remove(member)} disabled={act.busy}>Remove</button>{/if}
        </li>
      {/each}
    </ul>
  </div>

  <div class="card">
    <h3>Find a person</h3>
    <p class="help">Find an account by email or full student number to see how they relate to this club and to grant honorary membership for the current membership session. Only their name is shown.</p>
    <label for="people-by">Look up by</label>
    <select id="people-by" bind:value={by}><option value="email">Email</option><option value="student_number">Student number</option></select>
    <label for="people-query">{by === "email" ? "Email address" : "Full student number"}</label>
    <input id="people-query" autocomplete="off" bind:value={query} />
    <button class="primary" onclick={lookup} disabled={find.busy || !query.trim()}>Look up</button>
    <Status action={find} />
    {#if person}
      <p><strong>{person.name}</strong> — {person.verified ? "verified account" : "unverified account"}; relation to this club: {person.relation}</p>
      {#if person.verified && person.relation === "none"}
        <label for="honorary-reason">Reason for honorary membership</label>
        <input id="honorary-reason" maxlength="200" autocomplete="off" bind:value={reason} />
        <button class="primary" onclick={honorary} disabled={act.busy || reason.trim().length < 3}>Grant honorary membership</button>
      {/if}
    {/if}
  </div>
{/if}
