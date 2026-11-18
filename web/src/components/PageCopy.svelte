<script>
  let { content, preview = false, children } = $props();
</script>

<div class="card" id={preview ? undefined : "overview"}>
  <h2>{content.title}</h2>
  {#if content.tagline}<p>{content.tagline}</p>{/if}
  {#if content.comingSoon}<span class="badge">Coming soon</span>{/if}
  {#each content.summary as paragraph}<p>{paragraph}</p>{/each}
  {@render children?.()}
</div>
{#if content.eligibility.length}
  <div class="card" id={preview ? undefined : "eligibility"}>
    <h3>Eligibility</h3>
    <ul>{#each content.eligibility as line}<li>{line}</li>{/each}</ul>
  </div>
{/if}
{#if content.steps.length}
  <div class="card" id={preview ? undefined : "how-to-apply"}>
    <h3>How to apply</h3>
    <ol>{#each content.steps as line}<li>{line}</li>{/each}</ol>
  </div>
{/if}
{#if content.deadlines.length}
  <div class="card" id={preview ? undefined : "key-dates"}>
    <h3>Key dates</h3>
    <ul>{#each content.deadlines as line}<li>{line}</li>{/each}</ul>
  </div>
{/if}
