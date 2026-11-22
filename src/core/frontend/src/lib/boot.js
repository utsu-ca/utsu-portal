// Starts a portal session: who is signed in, any pending SSO redirect or emailed banking link, then the first view.
import { loadPlugins } from "./plugins.svelte.js";
import { loadContent } from "./content.svelte.js";
import { refreshMe, session } from "./session.svelte.js";
import { go } from "./nav.svelte.js";
import { completeGoogleStaffLogin, isGoogleStaffCallback } from "./google-staff.js";

export async function start() {
  // The menu follows the plug-in list as it arrives; the first view must not wait for it.
  loadPlugins();
  loadContent();
  try {
    if (isGoogleStaffCallback()) await completeGoogleStaffLogin();
  } catch (error) {
    return go("auth", { googleError: error.message });
  }
  if (session.token) await refreshMe();
  const next = sessionStorage.getItem("sso_next");
  if (session.token && session.me && next) {
    sessionStorage.removeItem("sso_next");
    location.href = next;
    return;
  }
  if (!session.token || !session.me) return go("home");
  const verified = session.me.status === "verified";
  if (verified && new URLSearchParams(location.hash.slice(1)).has("sap-banking")) return go("banking", { shared: true });
  if (verified && location.hash === "#orders") return go("orders");
  go(verified ? "home" : "account");
}
