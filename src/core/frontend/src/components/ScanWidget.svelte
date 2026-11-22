<script>
  import { onDestroy } from "svelte";
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "./Status.svelte";

  let { scan = $bindable(null), challengePath = "/verification/challenge", allowUpload = false } = $props();

  const action = new Action(), upload = new Action();
  let phrase = $state(""), mode = $state(""), video, shots = $state({});
  let stream, destroyed = false;

  function stopCamera() {
    stream?.getTracks().forEach(track => track.stop());
    stream = undefined;
    if (video) video.srcObject = null;
  }
  onDestroy(() => {
    destroyed = true;
    stopCamera();
  });

  const start = nextMode => action.run(async () => {
    stopCamera();
    phrase = mode = "";
    scan = null;
    shots = {};
    const challenge = await api(challengePath);
    if (destroyed) return;
    if (nextMode === "camera") {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera access is not available in this browser.");
      const camera = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
      stream = camera;
      if (destroyed) return stopCamera();
      video.srcObject = stream;
    }
    upload.error = "";
    mode = nextMode;
    phrase = challenge.phrase;
    scan = { challenge: challenge.challenge, shots, stop: stopCamera };
  });

  function selectPhoto(kind, file) {
    delete shots[kind];
    if (!file) return;
    upload.run(async () => {
      if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
        throw new Error("Choose a JPEG, PNG, or WebP image.");
      }
      shots[kind] = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("Could not read the image. Choose a file again."));
        reader.readAsDataURL(file);
      });
    });
  }

  function capture(kind) {
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = Math.round(640 * (video.videoHeight || 480) / (video.videoWidth || 640));
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    shots[kind] = canvas.toDataURL("image/jpeg", 0.8);
  }
</script>

<button type="button" class="primary" onclick={() => start("camera")} disabled={action.busy || upload.busy}>Start camera</button>
{#if allowUpload}
  <button type="button" class="secondary" onclick={() => start("upload")} disabled={action.busy || upload.busy}>Upload photos instead</button>
{/if}
<Status {action} />
{#if mode === "camera"}
  <p>Hold your face, then your student ID, to the camera. {allowUpload ? "Show the written phrase in your face photo:" : "Write or say this phrase in the capture:"} <strong>{phrase}</strong></p>
{:else if mode === "upload"}
  <p>Take a new face photo with the phrase <strong>{phrase}</strong> written next to you, then upload it and a clear photo of your student ID. If the phrase expires, select “Upload photos instead” again to get a new one.</p>
  <Status action={upload} />
{/if}
<video bind:this={video} autoplay playsinline muted hidden={mode !== "camera"}></video>
{#if scan}
  {#each [["face", "face"], ["id_card", "ID card"]] as [kind, label] (kind)}
    {#if mode === "upload"}
      <label>
        Upload {label} photo
        <input type="file" accept="image/jpeg,image/png,image/webp" disabled={upload.busy}
          onchange={event => selectPhoto(kind, event.currentTarget.files[0])} />
      </label>
    {:else}
      <button type="button" class="primary" onclick={() => capture(kind)}>Capture {label}</button>
    {/if}
    {#if shots[kind]}<img class="shot" alt={`${label} photo preview`} src={shots[kind]} />{/if}
  {/each}
{/if}
