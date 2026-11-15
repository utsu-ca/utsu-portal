<script>
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { signIn } from "../lib/session.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";

  let { onsignin } = $props();
  let username = $state(""), code = $state("");
  const send = new Action(), enter = new Action();

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
  <label for="username">Username</label>
  <input id="username" autocomplete="username" bind:value={username} />
  <button class="primary" onclick={sendCode} disabled={send.busy}>Email me a code</button>
  <Status action={send} />
  <label for="code">Email code</label>
  <input id="code" autocomplete="one-time-code" bind:value={code} />
  <button class="primary" onclick={signInWithCode} disabled={enter.busy}>Sign in</button>
  <Status action={enter} />
  <hr />
  <button class="primary" onclick={() => go("register")}>Create an account</button>
  <button class="primary" onclick={() => go("recovery")}>Account recovery</button>
</div>
