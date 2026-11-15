// Starts a portal session: who is signed in, any pending SSO redirect or emailed banking link, then the first view.
import { refreshBanking, refreshMe, session } from "./session.svelte.js";
import { go } from "./nav.svelte.js";

export async function start() {
  if (session.token) await refreshMe();
  if (session.token && session.me) await refreshBanking();
  const next = sessionStorage.getItem("sso_next");
  if (session.token && session.me && next) {
    sessionStorage.removeItem("sso_next");
    location.href = next;
    return;
  }
  if (!session.token || !session.me) return go("home");
  const verified = session.me.status === "verified";
  if (verified && new URLSearchParams(location.hash.slice(1)).has("sap-banking")) return go("banking");
  go(verified ? "home" : "account");
}
