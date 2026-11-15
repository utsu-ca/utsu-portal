<script>
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import { go } from "../lib/nav.svelte.js";
  import Status from "../components/Status.svelte";

  const fields = [["username", "Username"], ["email", "Email"], ["first_name", "Legal first name"],
    ["last_name", "Legal last name"], ["preferred_name", "Preferred name (optional)"], ["student_number", "Student number"]];
  let values = $state(Object.fromEntries(fields.map(([key]) => [key, ""]))), code = $state(""), sent = $state(false);
  const register = new Action(), verify = new Action();

  const create = () => register.run(async () => {
    await api("/auth/register", { method: "POST", body: { ...values } });
    sent = true;
  }, "Check your email for a code.");

  const confirm = () => verify.run(async () => {
    await api("/auth/verify-email", { method: "POST", body: { username: values.username, code } });
    go("auth");
  });
</script>

<div class="card">
  <h2>Create an account</h2>
  {#each fields as [key, label]}
    <label for={"reg-" + key}>{label}</label>
    <input id={"reg-" + key} bind:value={values[key]} />
  {/each}
  <button class="primary" onclick={create} disabled={register.busy}>Register</button>
  <Status action={register} />
  {#if sent}
    <label for="reg-code">Email code</label>
    <input id="reg-code" bind:value={code} />
    <button class="primary" onclick={confirm} disabled={verify.busy}>Verify email</button>
    <Status action={verify} />
  {/if}
</div>
