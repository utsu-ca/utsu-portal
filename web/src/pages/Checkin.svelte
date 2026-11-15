<script>
  import { onDestroy } from "svelte";
  import { api, getToken } from "../lib/api.js";

  const signedIn = !!getToken();
  let uuid = $state(""), error = $state(signedIn ? "" : "Sign in to the portal as an admin first."),
    result = $state(null), recent = $state([]), cameraOn = $state(false);
  let busy = false, video, timer;

  const label = d => d.name + (d.pronouns ? " (" + d.pronouns + ")" : "");

  async function checkin(value) {
    if (busy || !value.trim()) return;
    busy = true;
    error = "";
    try {
      const d = await api("/admin/agm/checkin", { method: "POST", body: { uuid: value.trim() } });
      result = d;
      recent.unshift(label(d) + " — " + (d.already_checked_in ? "duplicate" : "checked in"));
      uuid = "";
    } catch (e) {
      result = null;
      error = e.message || "Check-in failed";
    } finally {
      busy = false;
    }
  }

  async function useCamera() {
    if (!("BarcodeDetector" in window)) {
      error = "Camera QR scanning is not supported in this browser; use a scanner or paste the code.";
      return;
    }
    const detector = new BarcodeDetector({ formats: ["qr_code"] });
    video.srcObject = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    cameraOn = true;
    let last = "";
    timer = setInterval(async () => {
      const found = await detector.detect(video).catch(() => []);
      if (found[0] && found[0].rawValue !== last) { last = found[0].rawValue; checkin(last); }
      else if (!found[0]) last = "";
    }, 400);
  }
  onDestroy(() => clearInterval(timer));
</script>

<header><h1>AGM check-in</h1><nav><a href="/">Portal</a></nav></header>
<main>
  <div class="card">
    <video bind:this={video} autoplay playsinline muted hidden={!cameraOn}></video>
    <label for="uuid">Registration UUID (scan the attendee's QR code or paste it)</label>
    <!-- hardware QR scanners type the code then press Enter -->
    <input id="uuid" autocomplete="off" bind:value={uuid} onkeydown={e => e.key === "Enter" && checkin(uuid)} />
    <button class="primary" onclick={() => checkin(uuid)}>Check in</button>
    <button class="secondary" onclick={useCamera}>Use camera</button>
    <div class="err" role="alert">{error}</div>
  </div>
  <div role="status" aria-live="polite">
    {#if result}
      <div class="card">
        <h2 class={result.already_checked_in ? "banner-warn" : "banner-ok"}>{result.already_checked_in ? "⚠ " : "✔ "}{result.message}</h2>
        <p class="big-name">{label(result)}</p>
        <p>Legal name: {result.legal_name}{result.proxy ? " · voting by proxy" : ""} · {new Date(result.checked_in_at * 1000).toLocaleTimeString()}</p>
      </div>
    {/if}
  </div>
  <div class="card">
    <h3>Recent</h3>
    <ul>{#each recent as line}<li>{line}</li>{/each}</ul>
  </div>
</main>
