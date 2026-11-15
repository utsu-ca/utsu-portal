<script>
  import { api } from "../lib/api.js";
  import { Action } from "../lib/action.svelte.js";
  import Status from "./Status.svelte";

  // Camera capture of a face and an ID card. `scan` receives {challenge, shots, stop} once the camera is running.
  let { scan = $bindable(null), challengePath = "/verification/challenge" } = $props();

  const action = new Action();
  let phrase = $state(""), video, shots = $state({});

  const start = () => action.run(async () => {
    const challenge = await api(challengePath);
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
    video.srcObject = stream;
    phrase = challenge.phrase;
    scan = { challenge: challenge.challenge, shots, stop: () => stream.getTracks().forEach(t => t.stop()) };
  });

  function capture(kind) {
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = Math.round(640 * (video.videoHeight || 480) / (video.videoWidth || 640));
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    shots[kind] = canvas.toDataURL("image/jpeg", 0.8);
  }
</script>

<button class="primary" onclick={start} disabled={action.busy}>Start camera</button>
<Status {action} />
{#if phrase}<p>Hold your face, then your student ID, to the camera. Write or say this phrase in the capture: {phrase}</p>{/if}
<video bind:this={video} autoplay playsinline muted hidden={!phrase}></video>
{#if scan}
  {#each [["face", "face"], ["id_card", "ID card"]] as [kind, label]}
    <button class="primary" onclick={() => capture(kind)}>Capture {label}</button>
    {#if shots[kind]}<img class="shot" alt={"Captured " + label} src={shots[kind]} />{/if}
  {/each}
{/if}
