import { buildMenu } from "./menu.js";
import { pluginEnabled } from "./plugins.svelte.js";
import { hasRole, session } from "./session.svelte.js";

// The menu for whoever is signed in. Staff tools only appear while admin mode is on.
export const currentMenu = () => buildMenu({
  signedIn: !!session.token && !!session.me,
  verified: session.me?.status === "verified",
  has: role => session.adminMode && hasRole(role),
  enabled: pluginEnabled,
});
