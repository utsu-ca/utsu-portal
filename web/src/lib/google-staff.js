import { api } from "./api.js";
import { signIn } from "./session.svelte.js";

const KEY = "google-staff-login";

export async function beginGoogleStaffLogin() {
  const { url, state, verifier } = await api("/auth/google/start", { method: "POST" });
  sessionStorage.setItem(KEY, JSON.stringify({ state, verifier }));
  location.assign(url);
}

export async function completeGoogleStaffLogin() {
  const url = new URL(location.href);
  const state = url.searchParams.get("state");
  if (!state?.startsWith("google.")) return false;
  const code = url.searchParams.get("code");
  for (const key of ["code", "state", "error", "error_description", "scope", "authuser", "prompt", "hd", "iss"]) {
    url.searchParams.delete(key);
  }
  history.replaceState(history.state, "", url);
  const pending = JSON.parse(sessionStorage.getItem(KEY) || "null");
  sessionStorage.removeItem(KEY);
  if (!code || pending?.state !== state) {
    throw new Error("Google sign-in did not finish in this tab. Start again from the sign-in page.");
  }
  const result = await api("/auth/google/complete", {
    method: "POST", body: { code, state, verifier: pending.verifier },
  });
  signIn(result.token);
  return true;
}
