<script>
  import { onMount } from "svelte";
  import Footer from "./components/Footer.svelte";
  import RelatedPanel from "./components/RelatedPanel.svelte";
  import Sidebar from "./components/Sidebar.svelte";
  import Status from "./components/Status.svelte";
  import BankingStatus from "./features/profile/components/BankingStatus.svelte";
  import { currentMenu } from "./lib/appmenu.svelte.js";
  import { start } from "./lib/boot.js";
  import { layout } from "./lib/layout.svelte.js";
  import { go, nav } from "./lib/nav.svelte.js";
  import { contentLoad, loadContent } from "./lib/content.svelte.js";
  import { loadPlugins, missingFrontendPrograms, pluginLoad, plugins } from "./lib/plugins.svelte.js";
  import { applyMode, session, setAdminMode, signOut } from "./lib/session.svelte.js";
  import { theme } from "./lib/theme.svelte.js";
  import { views } from "./lib/views.js";

  const menu = $derived(currentMenu());
  const loader = $derived(views[nav.name]);
  const missingPrograms = $derived(missingFrontendPrograms(plugins.enabled, undefined, plugins.frontend));

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
    <BankingStatus />
    {#if missingPrograms.length}
      <div class="card" role="alert">
        Some enabled programs are missing from this portal build: {missingPrograms.join(", ")}.
        Contact the portal administrator.
      </div>
    {/if}
    {#if contentLoad.error && ["home", "program"].includes(nav.name)}
      <div class="card">
        <p>The latest page copy could not be loaded.</p>
        <Status action={contentLoad} />
        <button class="secondary" onclick={loadContent}>Retry loading page copy</button>
      </div>
    {/if}
    {#if pluginLoad.error}
      <div class="card">
        <p>Some program links could not be loaded.</p>
        <Status action={pluginLoad} />
        <button class="secondary" disabled={pluginLoad.busy} onclick={loadPlugins}>Retry program links</button>
      </div>
    {/if}
    {#key nav.seq}
      {#if loader}
        {#await loader() then module}
          {@const View = module.default}
          <View {...nav.props} />
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
