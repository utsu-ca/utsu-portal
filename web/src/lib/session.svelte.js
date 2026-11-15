import { api, getToken, setToken } from "./api.js";

const MODE_KEY = "admin-mode";

// Who is signed in. `banking` is the applicant's banking status, loaded so the portal can ask them to act on it.
// `adminMode` only reveals staff tools in this browser (and recolours it); the server still checks every role.
export const session = $state({ token: getToken(), me: null, banking: null, adminMode: sessionStorage.getItem(MODE_KEY) === "1" });

export const hasRole = role => !!session.me && (session.me.roles || []).some(r => r === "admin" || r === role);
export const isStaff = () => !!session.me && (session.me.roles || []).length > 0;

// Mirrors the mode onto <html data-mode> so the stylesheet can switch accent colours.
export function applyMode() {
  document.documentElement.dataset.mode = session.adminMode ? "admin" : "user";
}

export function setAdminMode(on) {
  session.adminMode = on;
  sessionStorage.setItem(MODE_KEY, on ? "1" : "0");
  applyMode();
}

export function signIn(token) {
  setToken(token);
  session.token = token;
}

export function signOut() {
  setToken(null);
  session.token = null;
  session.me = null;
  session.banking = null;
  setAdminMode(false);
}

export async function refreshMe() {
  try {
    session.me = await api("/me");
    if (session.adminMode && !isStaff()) setAdminMode(false);
  } catch {
    signOut();
  }
}

export async function refreshBanking() {
  session.banking = null;
  if (session.me?.status !== "verified") return;
  try {
    session.banking = await api("/sap/banking");
  } catch {
    session.banking = null;
  }
}
