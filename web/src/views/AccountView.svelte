<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import { refreshMe, session } from "../lib/session.svelte.js";
  import Status from "../components/Status.svelte";
  import ScanWidget from "../components/ScanWidget.svelte";

  let preferred = $state(""), rosterNumber = $state(""), divisions = $state(""), first = $state(""), last = $state("");
  let scan = $state(null);
  let backup = $state(""), primaryCode = $state(""), backupCode = $state(""), codeAddress = $state("");
  const save = new Action(), confirmNumber = new Action(), verify = new Action(), load = new Action();
  const sendBackupCodes = new Action(), confirmBackup = new Action();

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

  const requestBackup = () => sendBackupCodes.run(async () => {
    const email = backup.trim();
    await api("/auth/backup/enroll", { method: "POST", body: { email } });
    codeAddress = email;
  }, "Check your primary and backup inboxes for separate codes.");

  const saveBackup = () => confirmBackup.run(async () => {
    await api("/auth/backup/confirm", { method: "POST", body: {
      email: codeAddress, primary_code: primaryCode, backup_code: backupCode
    } });
    backup = primaryCode = backupCode = codeAddress = "";
    await refreshMe();
  }, "Backup email verified.");

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
    <p>{user.first_name} {user.last_name}{#if user.student_number} · ID ending {user.student_number}{/if} · <span class="badge">{session.me.status}</span></p>
    <p>Primary email: {user.email}</p>
    {#if session.me.backup_session}
      <button class="secondary" onclick={() => go("recovery")}>Request a primary email change</button>
    {/if}
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
  {#if "backup_email" in session.me}
    <div class="card">
      <h2>Backup email</h2>
      <p>{session.me.backup_email || "No verified backup email."}</p>
      {#if session.me.backup_session}
        <p>You signed in with your backup email. Sign in with your primary email to change the backup address.</p>
      {:else}
        <p>Use a separate address you can access if you lose access to your primary inbox.</p>
        <label for="backup-email">New backup email</label>
        <input id="backup-email" type="email" autocomplete="email" bind:value={backup} />
        <button class="secondary" onclick={requestBackup} disabled={sendBackupCodes.busy || confirmBackup.busy || !backup.trim()}>Send verification codes</button>
        <Status action={sendBackupCodes} />
        {#if codeAddress && codeAddress === backup.trim()}
          <label for="backup-primary-code">Code sent to your primary email</label>
          <input id="backup-primary-code" inputmode="numeric" maxlength="6" bind:value={primaryCode} />
          <label for="backup-email-code">Code sent to your backup email</label>
          <input id="backup-email-code" inputmode="numeric" maxlength="6" bind:value={backupCode} />
          <button class="primary" onclick={saveBackup} disabled={confirmBackup.busy || sendBackupCodes.busy || primaryCode.length !== 6 || backupCode.length !== 6}>Verify backup email</button>
        {/if}
        <Status action={confirmBackup} />
      {/if}
    </div>
  {/if}
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
