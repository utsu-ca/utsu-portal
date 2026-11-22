import { api } from "./api.js";
import { Action } from "./action.svelte.js";
import { compiledPrograms, knownProgramContributions } from "virtual:portal-programs";

// Which plug-ins this deployment runs, so navigation can hide unavailable program actions. The server still
// guards every route; this only keeps the navigation honest.
export const plugins = $state({ enabled: [], frontend: null });
export const pluginLoad = new Action();

export const pluginEnabled = id => plugins.enabled.includes(id);

export function missingFrontendPrograms(enabled, compiled = compiledPrograms, required = null) {
  const available = new Set(compiled);
  return [...new Set((required ?? enabled).flatMap(id => {
    const known = Object.hasOwn(knownProgramContributions, id);
    if (!known && required === null) return [];
    const contribution = known ? knownProgramContributions[id] : id;
    return available.has(contribution) ? [] : [contribution];
  }))];
}

export async function loadPlugins() {
  if (pluginLoad.busy) return;
  await pluginLoad.run(async () => {
    const data = await api("/plugins");
    if (!Array.isArray(data?.enabled) || !data.enabled.every(id => typeof id === "string")) {
      throw new Error("The portal returned an invalid program list. Retry loading the program links.");
    }
    if (data.frontend !== undefined && (!Array.isArray(data.frontend)
        || !data.frontend.every(id => typeof id === "string" && data.enabled.includes(id)))) {
      throw new Error("The portal returned an invalid frontend program list. Retry loading the program links.");
    }
    plugins.enabled = data.enabled;
    plugins.frontend = data.frontend ?? null;
  });
}
