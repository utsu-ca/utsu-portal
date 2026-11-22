import { mount } from "svelte";
import "./app.css";
import Standalone from "./components/Standalone.svelte";
import { applyMode } from "./lib/session.svelte.js";
import { loadTheme } from "./lib/theme.svelte.js";

// Staff pages (check-in, finance) use the admin colours; the SSO page uses the member colours.
export function mountPage(page, { staff = false } = {}) {
  loadTheme();
  document.documentElement.dataset.mode = staff ? "admin" : "user";
  if (!staff) applyMode();
  mount(Standalone, { target: document.getElementById("app"), props: { page } });
}
