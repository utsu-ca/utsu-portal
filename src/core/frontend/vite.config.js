import { fileURLToPath } from "node:url";
import { readFile } from "node:fs/promises";
import { defineConfig, searchForWorkspaceRoot } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { svelteTesting } from "@testing-library/svelte/vite";
import tailwindcss from "@tailwindcss/vite";
import {
  assertCompositionOutput,
  createProgramContributionModule,
  externalContributionDirectories,
  resolveComposition,
  standaloneInputEntries,
  standaloneModuleEntries,
} from "./program-composition.js";

const here = path => fileURLToPath(new URL(path, import.meta.url));
const backend = process.env.PORTAL_DEV_PROXY || "http://localhost:8000";
const weightsFile = here("../../../data/wfni_weights.csv");
const weightsModule = "\0virtual:wfni-weights";
const programModuleId = "virtual:portal-programs";
const resolvedProgramModuleId = `\0${programModuleId}`;

// The build is committed to src/core/static so that `pip install` and the Docker image need no Node.
// Rebuild with `npm run build` in src/core/frontend/ after changing anything under it.
export default defineConfig(({ mode }) => {
  const requestedComposition = process.env.PORTAL_FRONTEND_COMPOSITION;
  if (["core-only", "selected"].includes(mode) && requestedComposition && requestedComposition !== mode) {
    throw new Error(`Vite mode "${mode}" conflicts with PORTAL_FRONTEND_COMPOSITION.`);
  }
  const composition = resolveComposition(
    requestedComposition ?? (["core-only", "selected"].includes(mode) ? mode : "full"),
    process.env.PORTAL_FRONTEND_PROGRAMS,
  );
  const fullOutputDirectory = here("../static");
  const externalDirectories = externalContributionDirectories(composition.programs);
  const standaloneModules = standaloneModuleEntries(composition.programs);
  const input = {
    index: here("index.html"),
  };
  Object.assign(input, standaloneInputEntries(composition.programs));
  input.sso = here("sso.html");

  return {
    base: "/static/",
    // The dev container mounts src/ read-only, so its dependency cache lives elsewhere.
    cacheDir: process.env.PORTAL_VITE_CACHE_DIR || undefined,
    resolve: {
      alias: [{ find: /^@utsu\/portal$/, replacement: here("src/lib/portal-api.js") }],
    },
    plugins: [{
      name: "portal-external-tailwind-sources",
      enforce: "pre",
      transform(code, id) {
        if (!externalDirectories.length || id.split("?")[0] !== here("src/app.css")) return;
        const tailwindImport = '@import "tailwindcss";';
        if (!code.includes(tailwindImport)) throw new Error("Portal Tailwind import is missing.");
        const sources = externalDirectories.map(directory =>
          `@source ${JSON.stringify(directory.replaceAll("\\", "/"))};`).join("\n");
        return code.replace(tailwindImport, `${tailwindImport}\n${sources}`);
      },
    }, {
      name: "portal-program-composition",
      resolveId(id) {
        if (id === programModuleId) return resolvedProgramModuleId;
        const standalonePrefix = "virtual:portal-standalone/";
        if (id.startsWith(standalonePrefix)) return standaloneModules[id.slice(standalonePrefix.length)];
      },
      load(id) {
        if (id === resolvedProgramModuleId) {
          return createProgramContributionModule(composition.name, process.env.PORTAL_FRONTEND_PROGRAMS);
        }
      },
    }, {
      name: "portal-composition-output-safety",
      configResolved(config) {
        assertCompositionOutput(composition.name, config.build.outDir, fullOutputDirectory);
      },
    }, tailwindcss(), svelte(), svelteTesting(), {
      name: "wfni-weights",
      resolveId(id) {
        if (id === "virtual:wfni-weights") return weightsModule;
      },
      async load(id) {
        if (id !== weightsModule) return;
        this.addWatchFile(weightsFile);
        return `export default ${JSON.stringify(await readFile(weightsFile, "utf8"))};`;
      },
    }],
    build: {
      outDir: composition.name === "full"
        ? fullOutputDirectory
        : here(composition.name === "core-only" ? ".core-dist" : ".subset-dist"),
      emptyOutDir: true,
      assetsInlineLimit: 0,  // the CSP only allows same-origin files, so even tiny fonts must not become data: URLs
      rollupOptions: { input },
    },
    server: {
      proxy: { "/api": backend, "/sso": backend, "/branding": backend, "/healthz": backend, "/readyz": backend },
      ...(externalDirectories.length ? {
        fs: { allow: [searchForWorkspaceRoot(here(".")), ...externalDirectories] },
      } : {}),
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test-setup.js"],
      include: ["src/**/*.test.js"],
    },
  };
});
