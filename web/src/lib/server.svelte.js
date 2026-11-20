// What the running server reports about itself: its version and commit, and whether it is a local development copy.
import { api } from "./api.js";
import { markDevLocal } from "./theme.svelte.js";

export const server = $state({ version: "", commit: "", devLocal: false });

export function loadServer() {
  return api("/version").then(info => {
    server.version = typeof info?.version === "string" ? info.version : "";
    server.commit = typeof info?.commit === "string" ? info.commit.slice(0, 7) : "";
    server.devLocal = info?.dev_local === true;
    if (server.devLocal) markDevLocal();
  }).catch(() => {
    /* the footer still shows the copyright */
  });
}

export function buildLabel({ version, commit }) {
  if (!version) return "";
  return commit ? `v${version} (${commit})` : `v${version}`;
}
