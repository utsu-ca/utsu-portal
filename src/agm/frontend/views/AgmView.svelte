<script>
  import { onMount } from "svelte";
  import { Action, api, go, ScanWidget, Status } from "@utsu/portal";

  // AGM invitation panel: in-person registrants show a QR code; online registrants verify their ID, then sign in to Zoom via SSO.
  const load = new Action(), verify = new Action(), join = new Action();
  const poll = new Action();
  let invitation = $state(null), qr = $state(null), check = $state(null), scan = $state(null), zoom = $state(null);
  let timer, closed = false;

  function schedule(seconds) {
    clearTimeout(timer);
    if (!closed) timer = setTimeout(refreshCheck, seconds * 1000);
  }

  function showCheck(result) {
    check = result;
    if (result.status === "pending") schedule(result.wait_after);
  }

  async function refreshCheck() {
    clearTimeout(timer);
    if (closed || poll.busy) return;
    await poll.run(async () => {
      try {
        const result = await api("/agm/verify");
        if (!closed) showCheck(result);
      } catch (error) {
        if (check?.status === "pending" && ![401, 403, 409].includes(error.status)) schedule(30);
        throw error;
      }
    });
  }

  async function refresh() {
    invitation = await api("/agm/invitation");
    if (!invitation.registered) return;
    if (invitation.mode === "in_person") qr = await api("/agm/qr");
    else if (!invitation.id_verified) await refreshCheck();
  }
  onMount(() => {
    load.run(refresh);
    return () => { closed = true; clearTimeout(timer); };
  });

  const submitCheck = () => verify.run(async () => {
    if (!scan?.shots.face || !scan?.shots.id_card) throw new Error("Capture your face and your student ID first.");
    const capture = scan;
    const result = await api("/agm/verify", { method: "POST", body: { face: scan.shots.face, id_card: scan.shots.id_card, challenge: scan.challenge } });
    if (!closed) {
      capture.stop();
      showCheck(result);
    }
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
      {:else if invitation.mode === "online" && !invitation.id_verified && check?.status !== "approved"}
        <Status action={poll} />
        {#if poll.error}
          <button class="secondary" onclick={refreshCheck}>Retry status check</button>
        {/if}
        {#if check?.status === "pending"}
          <p role="status">Your ID check is awaiting human review. You are {check.position} of {check.queue_length} in the queue.
            {#if !poll.error}This page checks again in about {check.wait_after} seconds.{/if}</p>
        {:else if check}
          {#if check.status === "rejected"}
            <p class="err">Please capture and submit your face and ID again. Reviewer note: {check.note}</p>
          {:else}
            <p>Submit your face and student ID for human review before joining Zoom.</p>
          {/if}
          <ScanWidget bind:scan challengePath="/agm/verify/challenge" />
          <button class="primary" onclick={submitCheck} disabled={verify.busy || !scan?.shots.face || !scan?.shots.id_card}>Submit for review</button>
        {/if}
        <Status action={verify} />
      {:else if invitation.mode === "online"}
        <p class="ok">ID verified.</p>
        <button class="primary" onclick={joinZoom} disabled={join.busy}>Log in to Zoom</button>
        <Status action={join} />
        {#if zoom}<p><a href={zoom.sso_url || zoom.zoom_url} target="_blank" rel="noopener">Open Zoom</a></p>{/if}
      {/if}
    {/if}
  </div>
{/if}
