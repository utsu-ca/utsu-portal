<script>
  import { onMount } from "svelte";
  import Status from "../../../components/Status.svelte";
  import { Action } from "../../../lib/action.svelte.js";
  import { api } from "../../../lib/api.js";

  const load = new Action(), pay = new Action();
  let orders = $state([]);
  const refresh = () => load.run(async () => { orders = await api("/orders"); });
  onMount(refresh);
  const checkout = order => pay.run(async () => {
    const { url } = await api(`/orders/${order.id}/checkout`, { method: "POST" });
    const target = new URL(url);
    if (target.protocol !== "https:" || target.hostname !== "checkout.stripe.com") {
      throw new Error("The payment provider returned an invalid checkout link.");
    }
    location.assign(target.href);
  });
  const amount = order => new Intl.NumberFormat(undefined, {
    style: "currency", currency: order.currency.toUpperCase(),
  }).format(order.amount_cents / 100);
</script>

<div class="card">
  <h2>Orders and payments</h2>
  <p>Card details are entered only on Stripe's secure checkout page. Returning here does not confirm payment;
    refresh to see the status confirmed by Stripe.</p>
  <button class="secondary" disabled={load.busy} onclick={refresh}>Refresh orders</button>
  <Status action={load} />
  <Status action={pay} />
  {#each orders as order (order.id)}
    <div class="card">
      <h3>{order.description}</h3>
      <p>{amount(order)} <span class="badge">{order.status.replaceAll("_", " ")}</span></p>
      <p class="help">Order: {order.id}</p>
      {#if ["awaiting_payment", "checkout"].includes(order.status)}
        <button class="primary" disabled={pay.busy} onclick={() => checkout(order)}>Pay with Stripe</button>
      {/if}
    </div>
  {:else}
    {#if !load.busy && !load.error}<p>No payment orders.</p>{/if}
  {/each}
</div>
