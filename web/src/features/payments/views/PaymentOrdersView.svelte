<script>
  import { onMount } from "svelte";
  import Status from "../../../components/Status.svelte";
  import { Action } from "../../../lib/action.svelte.js";
  import { api, apiAllPages } from "../../../lib/api.js";

  const load = new Action(), save = new Action();
  let orders = $state([]), uuid = $state(""), description = $state(""), cents = $state(100);
  let pending = $state(null);
  const refresh = () => load.run(async () => { orders = await apiAllPages("/finance/orders"); });
  onMount(refresh);
  const create = () => save.run(async () => {
    if (!pending) {
      pending = { request_id: crypto.randomUUID(), uuid, description, amount_cents: cents, currency: "cad" };
    }
    try {
      await api("/finance/orders", { method: "POST", body: pending });
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && error.status !== 409) pending = null;
      throw error;
    }
    pending = null;
    uuid = description = "";
    await refresh();
  }, "Order created.");
</script>

<div class="card">
  <h2>Payment orders</h2>
  <p>Create an order for a verified profile. Prices are fixed in Canadian cents; applicants cannot change them.
    A retry submits the same order, not a new charge.</p>
  <form onsubmit={event => { event.preventDefault(); create(); }}>
    <label for="order-profile">Profile UUID</label>
    <input id="order-profile" bind:value={uuid} required disabled={save.busy || !!pending} />
    <label for="order-description">Description</label>
    <input id="order-description" bind:value={description} required maxlength="200" disabled={save.busy || !!pending} />
    <label for="order-cents">Amount (CAD cents, minimum 50)</label>
    <input id="order-cents" type="number" min="50" max="99999999" step="1" bind:value={cents} required disabled={save.busy || !!pending} />
    <button class="primary" type="submit" disabled={save.busy}>{pending ? "Retry same order" : "Create order"}</button>
  </form>
  <Status action={save} />
  <button class="secondary" onclick={refresh} disabled={load.busy}>Refresh orders</button>
  <Status action={load} />
  {#each orders as order (order.id)}
    <div class="card">
      <h3>{order.description}</h3>
      <p>{(order.amount_cents / 100).toFixed(2)} CAD - {order.status.replaceAll("_", " ")}</p>
      <p class="help">Profile: {order.uuid}<br />Order: {order.id}</p>
    </div>
  {/each}
</div>
