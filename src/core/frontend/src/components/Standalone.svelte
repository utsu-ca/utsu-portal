<script>
  import Footer from "./Footer.svelte";

  // Wraps a standalone page (check-in desk, finance portal, SSO) so every page carries the footer.
  let { page: Page } = $props();
</script>

<Page />
<Footer />
