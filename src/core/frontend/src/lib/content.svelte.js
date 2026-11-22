import { Action } from "./action.svelte.js";
import { api } from "./api.js";
import { CONTENT } from "./content.js";
import { theme } from "./theme.svelte.js";

export const pages = ["home", ...Object.keys(CONTENT)];
export const contentLoad = new Action();
let published = $state({});
let pending;

export function loadContent() {
  if (!pending) {
    pending = contentLoad.run(async () => {
      const data = await api("/content");
      if (!data?.pages || typeof data.pages !== "object" || Array.isArray(data.pages)) {
        throw new Error("The portal returned an invalid page list. Try loading it again.");
      }
      published = data.pages;
      return true;
    }).finally(() => { pending = undefined; });
  }
  return pending;
}

export const revision = id => published[id]?.rev ?? 0;

export function pageCopy(id) {
  const defaults = id === "home" ? {
    title: `Learn more about the ${theme.name}`,
    tagline: "",
    summary: [
      `The ${theme.name} is where UTSU members apply for student aid, register and run clubs, RSVP for the Annual General Meeting and apply for Senate seats. Everything on these pages is public; sign in to apply or manage your own records.`,
      "Select a program below, or open the menu on the right.",
    ],
    eligibility: [], steps: [], deadlines: [],
  } : CONTENT[id];
  return { ...defaults, ...published[id]?.copy };
}

export async function publishCopy(id, rev, copy) {
  published[id] = await api(`/admin/content/${id}`, { method: "PUT", body: { base_rev: rev, copy } });
}
