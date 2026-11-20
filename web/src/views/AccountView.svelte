<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import { refreshMe, session } from "../lib/session.svelte.js";
  import Status from "../components/Status.svelte";
  import ScanWidget from "../components/ScanWidget.svelte";

  let preferred = $state(""), rosterNumber = $state(""), divisions = $state(""), first = $state(""), last = $state("");
  let username = $state("");
  let scan = $state(null);
  let backup = $state(""), primaryCode = $state(""), backupCode = $state(""), codeAddress = $state("");
  const save = new Action(), confirmNumber = new Action(), verify = new Action(), load = new Action();
  const sendBackupCodes = new Action(), confirmBackup = new Action();
  const saveUsername = new Action();

  onMount(() => load.run(async () => {
    await refreshMe();
    const user = session.me.user;
    preferred = user.preferred_name;
    username = user.username || "";
    first = user.first_name;
    last = user.last_name;
  }));

  const savePreferred = () => save.run(async () => {
    await api("/me", { method: "PATCH", body: { preferred_name: preferred } });
    await refreshMe();
  }, "Saved");

  const requestAccountChange = () => go("recovery", {
    identifier: session.me.user.email, legalFirst: first, legalLast: last
  });

  const updateUsername = () => saveUsername.run(async () => {
    await api("/me/username", { method: "PATCH", body: { username } });
    await refreshMe();
  }, "Username saved.");

  const confirmRoster = () => confirmNumber.run(async () => {
    await api("/me/roster-number", { method: "POST", body: { student_number: rosterNumber } });
    rosterNumber = "";
  }, "Number confirmed. Roster matching still depends on a matching student record.");

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
    if (!scan?.shots.face || !scan?.shots.id_card) throw new Error("Provide both a face photo and a student ID photo.");
    const user = session.me.user;
    await api("/verification", { method: "POST", body: {
      first_name: user.first_name, last_name: user.last_name, division_codes: divisions.split(",").map(s => s.trim()).filter(Boolean),
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
    <p>Status: <span class="badge">{session.me.status}</span></p>
    <label for="profile-id">Account ID</label>
    <input id="profile-id" value={user.uuid} readonly />
    <label for="profile-email">Primary email</label>
    <input id="profile-email" type="email" value={user.email} readonly />
    <label for="profile-first">Legal first name</label>
    <input id="profile-first" autocomplete="given-name" bind:value={first} readonly={session.me.staff_sso} />
    <label for="profile-last">Legal last name</label>
    <input id="profile-last" autocomplete="family-name" bind:value={last} readonly={session.me.staff_sso} />
    {#if !session.me.staff_sso && user.student_number}
      <p class="help">Legal name and primary email changes require administrator review.</p>
      <button class="secondary" onclick={requestAccountChange} disabled={!first.trim() || !last.trim()}>
        Request a legal name or primary email change
      </button>
    {:else if !session.me.staff_sso}
      <p class="help">Contact an administrator to change your legal name or primary email.</p>
    {/if}
    <label for="preferred">Preferred name</label>
    <input id="preferred" bind:value={preferred} />
    <button class="primary" onclick={savePreferred} disabled={save.busy}>Save preferred name</button>
    <Status action={save} />
    {#if !session.me.backup_session}
      <label for="username">Public username (optional)</label>
      <input id="username" autocomplete="off" bind:value={username} maxlength="64" />
      <span class="help">Leave blank to remove it. Use 3–64 letters, numbers, dots, underscores or hyphens.</span>
      <button class="secondary" onclick={updateUsername} disabled={saveUsername.busy}>Save username</button>
      <Status action={saveUsername} />
    {/if}
    {#if session.me.staff_sso}
      <p>Signed in with a staff work account. This does not verify student status.</p>
    {:else if user.student_number}
      <label for="profile-masked-number">Student number on file (last four digits)</label>
      <input id="profile-masked-number" value={user.student_number} readonly />
      <label for="roster-number">Confirm student number for roster matching</label>
      <input id="roster-number" aria-describedby="roster-number-help" inputmode="numeric" autocomplete="off" maxlength="10" bind:value={rosterNumber} />
      <p id="roster-number-help" class="help">Enter the full number you registered with. The portal compares it against your account, then uses your legal name and four masked roster digits to check student eligibility. This does not change your student number or verify your identity.</p>
      <button class="secondary" onclick={confirmRoster} disabled={confirmNumber.busy || !/^\d{7,10}$/.test(rosterNumber)}>Confirm student number</button>
      <Status action={confirmNumber} />
    {/if}
  </div>
  {#if "backup_email" in session.me}
    <div class="card">
      <h2>Recovery email</h2>
      <label for="verified-recovery-email">Verified recovery email</label>
      <input id="verified-recovery-email" type="email" value={session.me.backup_email || ""} placeholder="Not set" readonly />
      {#if session.me.backup_session}
        <p>You signed in with your backup email. Sign in with your primary email to change the backup address.</p>
      {:else}
        <p>Use a separate address you can access if you lose access to your primary inbox.</p>
        <label for="backup-email">New recovery email</label>
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
      <p>Identity verification uses your registered legal name: {user.first_name} {user.last_name}.</p>
      <ScanWidget bind:scan allowUpload />
      <button class="primary" onclick={submitVerification} disabled={verify.busy}>Submit for review</button>
      <Status action={verify} />
    </div>
  {/if}
{/if}
