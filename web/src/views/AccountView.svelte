<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { refreshMe, session } from "../lib/session.svelte.js";
  import Status from "../components/Status.svelte";
  import ScanWidget from "../components/ScanWidget.svelte";

  let preferred = $state(""), rosterNumber = $state(""), divisions = $state(""), first = $state(""), last = $state("");
  let scan = $state(null);
  const save = new Action(), confirmNumber = new Action(), verify = new Action(), load = new Action();

  onMount(() => load.run(async () => {
    await refreshMe();
    const user = session.me.user;
    preferred = user.preferred_name;
    first = user.first_name;
    last = user.last_name;
  }));

  const savePreferred = () => save.run(async () => {
    await api("/me", { method: "PATCH", body: { preferred_name: preferred } });
  }, "Saved");

  const confirmRoster = () => confirmNumber.run(async () => {
    await api("/me/roster-number", { method: "POST", body: { student_number: rosterNumber } });
    rosterNumber = "";
  }, "Student number confirmed for roster matching.");

  const submitVerification = () => verify.run(async () => {
    if (!scan?.shots.face || !scan?.shots.id_card) throw new Error("Capture your face and your student ID first.");
    await api("/verification", { method: "POST", body: {
      first_name: first, last_name: last, division_codes: divisions.split(",").map(s => s.trim()),
      face: scan.shots.face, id_card: scan.shots.id_card, challenge: scan.challenge
    } });
    scan.stop();
  }, "Submitted. An administrator will review it.");
</script>

<Status action={load} />
{#if session.me}
  {@const user = session.me.user}
  <div class="card">
    <h2>Account</h2>
    <p>{user.first_name} {user.last_name} · {user.email}{#if user.student_number} · ID ending {user.student_number}{/if} · <span class="badge">{session.me.status}</span></p>
    <label for="preferred">Preferred name</label>
    <input id="preferred" bind:value={preferred} />
    <button class="primary" onclick={savePreferred} disabled={save.busy}>Save</button>
    <Status action={save} />
    {#if session.me.staff_sso}
      <p>Signed in with a staff work account. This does not verify student status.</p>
    {:else}
      <label for="roster-number">Confirm student number for roster matching</label>
      <input id="roster-number" inputmode="numeric" autocomplete="off" maxlength="10" bind:value={rosterNumber} />
      <button class="secondary" onclick={confirmRoster} disabled={confirmNumber.busy}>Confirm student number</button>
      <Status action={confirmNumber} />
    {/if}
  </div>
  {#if session.me.status === "pending_review" || session.me.status === "pending_email"}
    <div class="card">
      <h2>Verify your identity</h2>
      <label for="ver-div">Division codes (comma separated, e.g. UTSG)</label>
      <input id="ver-div" bind:value={divisions} />
      <label for="ver-first">Legal first name</label>
      <input id="ver-first" bind:value={first} />
      <label for="ver-last">Legal last name</label>
      <input id="ver-last" bind:value={last} />
      <ScanWidget bind:scan />
      <button class="primary" onclick={submitVerification} disabled={verify.busy}>Submit for review</button>
      <Status action={verify} />
    </div>
  {/if}
{/if}
