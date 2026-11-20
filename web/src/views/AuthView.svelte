<script>
  import { onMount, tick } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { signIn } from "../lib/session.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import { start } from "../lib/boot.js";
  import Status from "../components/Status.svelte";
  import { beginGoogleStaffLogin } from "../lib/google-staff.js";

  // Rendered both as its own view and inside Profile, so it starts the session itself rather than relying on a callback.
  let { googleError = "" } = $props();
  let email = $state(""), code = $state("");
  let codeSent = $state(false);
  let googleEnabled = $state(false);
  let codeInput = $state();
  const send = new Action(), enter = new Action();
  const google = new Action(), check = new Action();
  const loadGoogle = () => check.run(async () => { googleEnabled = (await api("/auth/google")).enabled === true; });
  onMount(() => { loadGoogle(); });

  const sendCode = async event => {
    event?.preventDefault();
    if (!email.trim()) {
      send.error = "Enter the primary email on your account.";
      return;
    }
    let sent = false;
    await send.run(async () => {
      await api("/auth/login-email", { method: "POST", body: { email } });
      sent = true;
    }, "If an account uses that email, a code was sent.");
    if (!sent) return;
    codeSent = true;
    await tick();
    codeInput?.focus();
  };

  const changeEmail = () => {
    codeSent = false;
    code = "";
    send.ok = "";
    send.error = "";
    enter.error = "";
  };

  const signInWithCode = event => {
    event?.preventDefault();
    return enter.run(async () => {
      const result = await api("/auth/token-email", { method: "POST", body: { email, code } });
      signIn(result.token);
      await start();
    });
  };
</script>

<div class="card">
  <h2>Sign in</h2>
  {#if googleError}<p class="err" role="alert">{googleError}</p>{/if}

  <h3>Sign in with an email code</h3>
  <p>For applicants and existing accounts that use email codes.</p>
  <form onsubmit={sendCode}>
    <label for="login-email">Primary email</label>
    <input id="login-email" type="email" autocomplete="email" bind:value={email} readonly={codeSent} />
    {#if codeSent}
      <button type="button" class="secondary" onclick={changeEmail}>Use a different email</button>
      <button type="submit" class="secondary" disabled={send.busy}>Resend code</button>
    {:else}
      <button type="submit" class="secondary" disabled={send.busy}>Email me a code</button>
    {/if}
    <Status action={send} />
  </form>

  {#if codeSent}
    <form class="code-pop" onsubmit={signInWithCode}>
      <label for="code">Email code</label>
      <input id="code" autocomplete="one-time-code" inputmode="numeric" bind:value={code} bind:this={codeInput} />
      <button type="submit" class="primary" disabled={enter.busy || !code.trim()}>Sign in with code</button>
      <Status action={enter} />
    </form>
  {/if}

  <hr />
  <button class="primary" onclick={() => go("register")}>Create an account</button>
  <button class="primary" onclick={() => go("recovery")}>Account recovery</button>

  <section class="sso-box" aria-labelledby="sso-heading">
    <h3 id="sso-heading">Staff? Log in here!</h3>
    <p>Union staff and administrators sign in with single sign-on using their approved work account.</p>
    <button class="primary" onclick={() => google.run(beginGoogleStaffLogin)} disabled={!googleEnabled || google.busy || check.busy}>Continue with Google</button>
    {#if check.busy}
      <p class="help">Checking single sign-on…</p>
    {:else if check.error}
      <Status action={check} />
      <button class="secondary" onclick={loadGoogle}>Retry loading staff sign-in</button>
    {:else if !googleEnabled}
      <p class="help">Single sign-on is not set up for this portal yet. An administrator can turn it on in the deployment settings.</p>
    {/if}
    <Status action={google} />
  </section>
</div>

<style>
  .sso-box {
    margin-top: 1.25rem;
    padding: 1rem;
    border-radius: 0.5rem;
    border: 1px solid var(--color-accent);
    background: color-mix(in srgb, var(--color-accent) 6%, white);
  }
  .code-pop {
    margin-top: 0.75rem;
    transform-origin: top center;
    animation: code-pop 320ms cubic-bezier(0.34, 1.56, 0.64, 1) both;
  }
  @keyframes code-pop {
    from { opacity: 0; transform: translateY(-6px) scale(0.92); }
    to { opacity: 1; transform: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    .code-pop { animation: none; }
  }
</style>
