<script>
  import { onMount, tick } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";
  import { loadTurnstile } from "../lib/turnstile.js";

  const fields = [["email", "Email"], ["username", "Username (optional)"], ["first_name", "Legal first name"],
    ["last_name", "Legal last name"], ["preferred_name", "Preferred name (optional)"], ["student_number", "Student number"]];
  let values = $state(Object.fromEntries(fields.map(([key]) => [key, ""]))), code = $state(""), sent = $state(false);
  let challengeConfig = $state(null), challengeToken = $state(""), challengeElement = $state();
  let widget, turnstile, closed = false;
  const register = new Action(), verify = new Action(), challenge = new Action();

  const loadChallenge = () => challenge.run(async () => {
    if (widget !== undefined) turnstile.remove(widget);
    widget = undefined;
    challengeToken = "";
    challengeConfig = null;
    challengeConfig = await api("/auth/registration-challenge");
    if (challengeConfig.local_bypass === true) return;
    if (typeof challengeConfig.site_key !== "string" || !challengeConfig.site_key)
      throw new Error("Registration challenge is not configured.");
    turnstile = await loadTurnstile();
    await tick();
    if (closed) return;
    widget = turnstile.render(challengeElement, {
      sitekey: challengeConfig.site_key,
      action: "register",
      callback: token => { challengeToken = token; },
      "expired-callback": () => { challengeToken = ""; },
      "error-callback": () => { challengeToken = ""; challenge.error = "Registration challenge failed. Try again."; },
    });
  });
  onMount(() => {
    loadChallenge();
    return () => { closed = true; if (widget !== undefined) turnstile.remove(widget); };
  });

  const create = () => register.run(async () => {
    if (!challengeConfig || challenge.error || (!challengeConfig.local_bypass && !challengeToken))
      throw new Error("Complete the registration challenge first.");
    try {
      await api("/auth/register", { method: "POST", body: { ...values, challenge_token: challengeToken } });
      sent = true;
    } finally {
      challengeToken = "";
      if (widget !== undefined) turnstile.reset(widget);
    }
  }, "Check your email for a code.");

  const confirm = () => verify.run(async () => {
    await api("/auth/verify-email", { method: "POST", body: { email: values.email, code } });
    go("auth");
  });
</script>

<div class="card">
  <h2>Create an account</h2>
  {#each fields as [key, label]}
    <label for={"reg-" + key}>{label}</label>
    <input id={"reg-" + key} bind:value={values[key]} disabled={sent} />
  {/each}
  {#if challengeConfig && !challengeConfig.local_bypass}<div bind:this={challengeElement}></div>{/if}
  {#if challengeConfig?.local_bypass}<p class="help">Registration challenge bypassed for local development.</p>{/if}
  <Status action={challenge} />
  {#if challenge.error}<button class="secondary" onclick={loadChallenge} disabled={challenge.busy}>Retry registration challenge</button>{/if}
  <button class="primary" onclick={create} disabled={register.busy || challenge.busy || sent || !challengeConfig || !!challenge.error || (!challengeConfig.local_bypass && !challengeToken)}>Register</button>
  <Status action={register} />
  {#if sent}
    <label for="reg-code">Email code</label>
    <input id="reg-code" bind:value={code} />
    <button class="primary" onclick={confirm} disabled={verify.busy}>Verify email</button>
    <Status action={verify} />
  {/if}
</div>
