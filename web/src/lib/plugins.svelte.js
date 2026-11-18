import { api } from "./api.js";
import { Action } from "./action.svelte.js";

// Which plug-ins this deployment runs, so the menu can hide opt-in programs that are not there. The server still
// guards every route; this only keeps the navigation honest.
export const plugins = $state({ enabled: [] });
export const pluginLoad = new Action();

export const pluginEnabled = id => plugins.enabled.includes(id);

export async function loadPlugins() {
  if (pluginLoad.busy) return;
  await pluginLoad.run(async () => {
    const data = await api("/plugins");
    if (!Array.isArray(data?.enabled) || !data.enabled.every(id => typeof id === "string")) {
      throw new Error("The portal returned an invalid program list. Retry loading the program links.");
    }
    plugins.enabled = data.enabled;
  });
}
