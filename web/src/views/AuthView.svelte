<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { signIn } from "../lib/session.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";
  import { beginGoogleStaffLogin } from "../lib/google-staff.js";

  let { onsignin, googleError = "" } = $props();
  let username = $state(""), code = $state("");
  let googleEnabled = $state(false);
  const send = new Action(), enter = new Action();
  const google = new Action(), check = new Action();
  const loadGoogle = () => check.run(async () => { googleEnabled = (await api("/auth/google")).enabled === true; });
  onMount(() => { loadGoogle(); });

  const sendCode = () => send.run(async () => {
    await api("/auth/login", { method: "POST", body: { username } });
  }, "If the account exists, a code was sent.");

  const signInWithCode = () => enter.run(async () => {
    const result = await api("/auth/token", { method: "POST", body: { username, code } });
    signIn(result.token);
    await onsignin();
  });
</script>

<div class="card">
  <h2>Sign in</h2>
  {#if googleError}<p class="err" role="alert">{googleError}</p>{/if}
  <Status action={check} />
  {#if check.error}<button class="secondary" onclick={loadGoogle}>Retry loading staff sign-in</button>{/if}
  {#if googleEnabled}
    <h3>Staff and administrators</h3>
    <p>Use your approved work account to sign in.</p>
    <button class="primary" onclick={() => google.run(beginGoogleStaffLogin)} disabled={google.busy}>Continue with Google</button>
    <Status action={google} />
    <hr />
    <h3>Sign in with an email code</h3>
    <p>For applicants and existing accounts that use email codes.</p>
  {/if}
  <label for="username">Username</label>
  <input id="username" autocomplete="username" bind:value={username} />
  <button class="secondary" onclick={sendCode} disabled={send.busy}>Email me a code</button>
  <Status action={send} />
  <label for="code">Email code</label>
  <input id="code" autocomplete="one-time-code" bind:value={code} />
  <button class="secondary" onclick={signInWithCode} disabled={enter.busy}>Sign in with code</button>
  <Status action={enter} />
  <hr />
  <button class="primary" onclick={() => go("register")}>Create an account</button>
  <button class="primary" onclick={() => go("recovery")}>Account recovery</button>
</div>
