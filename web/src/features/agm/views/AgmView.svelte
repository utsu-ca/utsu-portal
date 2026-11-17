<script>
  import { onMount } from "svelte";
  import { api } from "../../../lib/api.js";
  import { Action } from "../../../lib/action.svelte.js";
  import { go } from "../../../lib/nav.svelte.js";
  import Status from "../../../components/Status.svelte";
  import ScanWidget from "../../../components/ScanWidget.svelte";

  // AGM invitation panel: in-person registrants show a QR code; online registrants verify their ID, then sign in to Zoom via SSO.
  const load = new Action(), verify = new Action(), join = new Action();
  let invitation = $state(null), qr = $state(null), pendingCheck = $state(false), scan = $state(null), zoom = $state(null);

  async function refresh() {
    invitation = await api("/agm/invitation");
    if (!invitation.registered) return;
    if (invitation.mode === "in_person") qr = await api("/agm/qr");
    else if (!invitation.id_verified) pendingCheck = (await api("/agm/verify")).status === "pending";
  }
  onMount(() => load.run(refresh));

  const submitCheck = () => verify.run(async () => {
    if (!scan?.shots.face || !scan?.shots.id_card) throw new Error("Capture your face and your student ID first.");
    await api("/agm/verify", { method: "POST", body: { face: scan.shots.face, id_card: scan.shots.id_card, challenge: scan.challenge } });
    scan.stop();
    await refresh();
  });

  const joinZoom = () => join.run(async () => {
    zoom = await api("/agm/zoom/join", { method: "POST" });
    window.open(zoom.sso_url || zoom.zoom_url, "_blank", "noopener");
  });
</script>

<Status action={load} />
{#if invitation}
  <div class="card">
    <h2>AGM</h2>
    {#if !invitation.registered}
      <p>You are not registered yet.</p>
      <button class="primary" onclick={() => go("form", { id: "agm_rsvp" })}>Register for the AGM</button>
    {:else}
      <p>Registration: <span class="badge">{invitation.mode === "online" ? "Online" : "In person"}</span></p>
      {#if invitation.checked_in}<p class="ok">You are checked in.</p>{/if}
      {#if invitation.mode === "in_person" && qr}
        <p>Show this code at the door:</p>
        {#if qr.svg}
          <img class="shot" alt="Check-in QR code" width="220" height="220"
            src={"data:image/svg+xml;charset=utf-8," + encodeURIComponent(qr.svg)} />
        {/if}
        <p class="help">Code: {qr.payload}</p>
      {:else if invitation.mode === "online" && !invitation.id_verified}
        {#if pendingCheck}
          <p>Your ID check is awaiting review.</p>
        {:else}
          <p>Verify your identity to receive your Zoom sign-on.</p>
          <ScanWidget bind:scan challengePath="/agm/verify/challenge" />
          <button class="primary" onclick={submitCheck} disabled={verify.busy}>Submit</button>
          <Status action={verify} />
        {/if}
      {:else if invitation.mode === "online"}
        <p class="ok">ID verified.</p>
        <button class="primary" onclick={joinZoom} disabled={join.busy}>Log in to Zoom</button>
        <Status action={join} />
        {#if zoom}<p><a href={zoom.sso_url || zoom.zoom_url} target="_blank" rel="noopener">Open Zoom</a></p>{/if}
      {/if}
    {/if}
  </div>
{/if}
