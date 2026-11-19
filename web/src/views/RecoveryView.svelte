<script>
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "../components/Status.svelte";
  import ScanWidget from "../components/ScanWidget.svelte";
  import { start as startSession } from "../lib/boot.js";
  import { signIn } from "../lib/session.svelte.js";

  let username = $state(""), trunc = $state(""), newEmail = $state(""), otp = $state(""),
    first = $state(""), last = $state(""), divisions = $state("");
  let scan = $state(null);
  let backup = $state(""), backupCode = $state("");
  const start = new Action(), sendOtp = new Action(), submit = new Action();
  const sendBackup = new Action(), useBackup = new Action();

  const split = text => text.split(",").map(x => x.trim());

  const emailMe = () => start.run(async () => {
    await api("/recovery/start", { method: "POST", body: { username, student_number_trunc: trunc } });
  }, "If the details match, a code was sent. Sign in with it.");

  const emailBackup = () => sendBackup.run(async () => {
    await api("/recovery/backup/start", { method: "POST", body: { username, email: backup } });
  }, "If the details match, a code was sent to your backup email.");

  const signInWithBackup = () => useBackup.run(async () => {
    const result = await api("/recovery/backup/token", { method: "POST", body: { username, code: backupCode } });
    signIn(result.token);
    await startSession();
  });

  const codeToNewEmail = () => sendOtp.run(async () => {
    await api("/recovery/email-otp", { method: "POST", body: { username, student_number_trunc: trunc, new_email: newEmail } });
  }, "Code sent");

  const request = () => submit.run(async () => {
    if (!scan?.shots.face || !scan?.shots.id_card) throw new Error("Capture your face and your student ID first.");
    await api("/recovery/request", { method: "POST", body: {
      username, student_number_trunc: trunc, new_email: newEmail || null, otp: otp || null,
      first_name: first, last_name: last, division_codes: split(divisions),
      face: scan.shots.face, id_card: scan.shots.id_card, challenge: scan.challenge
    } });
    scan.stop();
  }, "Submitted for administrator review.");
</script>

<div class="card">
  <h2>Account recovery</h2>
  <label for="rec-user">Username</label>
  <input id="rec-user" autocomplete="username" bind:value={username} />
  <h3>Sign in with a verified backup email</h3>
  <label for="rec-backup-email">Backup email</label>
  <input id="rec-backup-email" type="email" autocomplete="email" bind:value={backup} />
  <button class="secondary" onclick={emailBackup} disabled={sendBackup.busy || !username.trim() || !backup.trim()}>Send code to backup email</button>
  <Status action={sendBackup} />
  <label for="rec-backup-code">Backup sign-in code</label>
  <input id="rec-backup-code" inputmode="numeric" maxlength="6" autocomplete="one-time-code" bind:value={backupCode} />
  <button class="primary" onclick={signInWithBackup} disabled={useBackup.busy || !username.trim() || backupCode.length !== 6}>Sign in with backup code</button>
  <Status action={useBackup} />
  <hr />
  <h3>Other recovery options</h3>
  <label for="rec-trunc">Last 4 digits of student number</label>
  <input id="rec-trunc" maxlength="4" bind:value={trunc} />
  <button class="primary" onclick={emailMe} disabled={start.busy}>Email me a sign-in code (email on file)</button>
  <Status action={start} />
  <hr />
  <h3>Change primary email or legal name (requires face + ID scan)</h3>
  <label for="rec-email">New primary email (optional)</label>
  <input id="rec-email" type="email" bind:value={newEmail} />
  <button class="primary" onclick={codeToNewEmail} disabled={sendOtp.busy}>Send code to new email</button>
  <Status action={sendOtp} />
  <label for="rec-otp">Code sent to new email</label>
  <input id="rec-otp" bind:value={otp} />
  <label for="rec-first">Legal first name</label>
  <input id="rec-first" bind:value={first} />
  <label for="rec-last">Legal last name</label>
  <input id="rec-last" bind:value={last} />
  <label for="rec-div">Division codes (comma separated)</label>
  <input id="rec-div" bind:value={divisions} />
  <ScanWidget bind:scan />
  <button class="primary" onclick={request} disabled={submit.busy}>Submit request</button>
  <Status action={submit} />
</div>
