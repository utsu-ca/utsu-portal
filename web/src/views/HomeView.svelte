<script>
  import { go } from "../lib/nav.svelte.js";
  import { CONTENT } from "../lib/content.js";
  import { theme } from "../lib/theme.svelte.js";

  const programs = ["bursaries", "scholarships", "club-registration", "club-finances", "agm", "senate"];
</script>

<div class="card">
  <h2>Learn more about the {theme.name}</h2>
  <p>
    The {theme.name} is where UTSU members apply for student aid, register and run clubs, RSVP for the Annual General
    Meeting and apply for Senate seats. Everything on these pages is public; sign in to apply or manage your own
    records.
  </p>
  <p>Select a program below, or open the menu on the left.</p>
  <button class="primary" onclick={() => go("profile")}>Sign in or create an account</button>
</div>
{#each programs as key (key)}
  <div class="card">
    <h3>{CONTENT[key].title}</h3>
    <p>{CONTENT[key].tagline}</p>
    <button class="secondary" onclick={() => go("program", { program: key })}>Learn more</button>
  </div>
{/each}
