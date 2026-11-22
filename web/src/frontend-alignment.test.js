import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/svelte";
import App from "./App.svelte";
import { loadPlugins, pluginLoad, plugins } from "./lib/plugins.svelte.js";

vi.mock("virtual:portal-programs", async importOriginal => {
  const original = await importOriginal();
  return {
    ...original,
    compiledPrograms: ["clubs", "outside-program"],
    menuContributions: {
      ...original.menuContributions,
      "outside-program": {
        navigation: () => [{ id: "external", label: "External", view: "external" }],
      },
    },
  };
});
vi.mock("./lib/api.js", async importOriginal => ({
  ...await importOriginal(),
  api: vi.fn(),
}));
import { api } from "./lib/api.js";
import { buildMenu } from "./lib/menu.js";

afterEach(() => {
  cleanup();
  api.mockReset();
  plugins.enabled = [];
  plugins.frontend = null;
});

it("warns about missing enabled contributions and clears the warning when aligned", async () => {
  let enabled = ["clubs", "agm", "agm_verify", "unknown"];
  api.mockImplementation(async path => path === "/plugins" ? { enabled, frontend: enabled } : { pages: {} });

  render(App);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Some enabled programs are missing from this portal build: agm, unknown.",
  );
  enabled = ["clubs"];
  await loadPlugins();
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("includes external navigation only when its backend program is enabled", () => {
  expect(buildMenu().some(node => node.id === "external")).toBe(false);
  expect(buildMenu({ enabled: id => id === "outside-program" }))
    .toContainEqual({ id: "external", label: "External", view: "external" });
});

it("reports invalid backend frontend declarations instead of ignoring them", async () => {
  api.mockResolvedValue({ enabled: ["clubs"], frontend: ["other"] });
  await loadPlugins();
  expect(pluginLoad.error).toContain("invalid frontend program list");
  expect(plugins.enabled).toEqual([]);
});
