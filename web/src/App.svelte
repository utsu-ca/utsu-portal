<script>
  import { onMount } from "svelte";
  import Footer from "./components/Footer.svelte";
  import RelatedPanel from "./components/RelatedPanel.svelte";
  import Sidebar from "./components/Sidebar.svelte";
  import { currentMenu } from "./lib/appmenu.svelte.js";
  import { start } from "./lib/boot.js";
  import { layout } from "./lib/layout.svelte.js";
  import { go, nav } from "./lib/nav.svelte.js";
  import { applyMode, session, setAdminMode, signOut } from "./lib/session.svelte.js";
  import { theme } from "./lib/theme.svelte.js";
  import { views } from "./lib/views.js";

  const menu = $derived(currentMenu());
  const loader = $derived(views[nav.name]);
  const bankingNeedsAction = $derived(session.banking?.status === "reverify_requested" && nav.name !== "banking");

  onMount(() => {
    applyMode();
    start();
  });

  const onaction = action => {
    if (action === "signOut") {
      signOut();
      go("home");
    }
  };
  // Switching mode returns to home so the person is not left on a page the menu no longer lists.
  const onmode = on => {
    setAdminMode(on);
    go("home");
  };
</script>

<svelte:head><title>{theme.name}</title></svelte:head>

<Sidebar {menu} {onaction} {onmode} />
<div class="content" class:collapsed={layout.collapsed}>
  <div class="content-grid">
  <main id="main" class={session.adminMode ? "wide" : ""}>
    {#if session.adminMode}
      <div class="banner-admin" role="status">Admin mode: staff tools are shown. Actions here affect real records.</div>
    {/if}
    {#if bankingNeedsAction}
      <div class="banner-action" role="alert">
        <span>Action needed: we could not verify your banking information.</span>
        <button class="primary" onclick={() => go("banking")}>Verify banking information</button>
      </div>
    {/if}
    {#key nav.seq}
      {#if loader}
        {#await loader() then module}
          {@const View = module.default}
          {#if nav.name === "auth"}<View onsignin={start} />{:else}<View {...nav.props} />{/if}
        {:catch}
          <p class="err" role="alert">This page could not be loaded. Check your connection and try again.</p>
        {/await}
      {/if}
    {/key}
  </main>
  <RelatedPanel />
  </div>
  <Footer />
</div>
