import { expect, it } from "vitest";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import {
  assertCompositionOutput,
  createProgramContributionModule,
  PROGRAM_DEPENDENCIES,
  PROGRAM_IDS,
  resolveComposition,
  standaloneInputEntries,
  standaloneModuleEntries,
} from "../../program-composition.js";
import { missingFrontendPrograms } from "./plugins.svelte.js";

it("keeps all frontend contributions in the default full composition", () => {
  expect(resolveComposition()).toEqual({
    name: "full",
    programs: ["agm", "clubs", "discussion", "sap", "senate", "voting"],
  });
  const source = createProgramContributionModule();
  expect(source).toContain("src/agm/frontend/views.js");
  expect(source).toContain("src/sabp/frontend/menu.js");
  expect(source).toContain("src/senate/frontend/menu.js");
  expect(source).toContain("src/senate/frontend/public-views.js");
  expect(source).toContain("src/voting/frontend/panels.js");
});

it("uses the installed program manifests as same-ID source overrides", () => {
  const packageManifests = [
    "../src/agm/frontend/portal-frontend.json",
    "../src/clubs/frontend/portal-frontend.json",
    "../src/chat/frontend/portal-frontend.json",
    "../src/sabp/frontend/portal-frontend.json",
    "../src/senate/frontend/portal-frontend.json",
    "../src/voting/frontend/portal-frontend.json",
  ].flatMap(file => {
    const manifestPath = path.resolve(process.cwd(), file);
    const { programs } = JSON.parse(readFileSync(manifestPath, "utf8"));
    return programs.map(program => ({
      ...program,
      directory: path.resolve(path.dirname(manifestPath), program.directory),
    }));
  });
  const root = mkdtempSync(path.join(tmpdir(), "portal-program-manifests-"));
  const manifest = path.join(root, "contributions.json");
  const output = path.join(root, "dist");
  writeFileSync(manifest, JSON.stringify({ programs: packageManifests }));
  const script = `
    import {
      createProgramContributionModule,
      externalContributionDirectories,
      resolveComposition,
      standaloneInputEntries,
      standaloneModuleEntries,
    } from "./program-composition.js";
    console.log(JSON.stringify({
      full: resolveComposition().programs,
      selected: resolveComposition("selected", "voting,discussion").programs,
      source: createProgramContributionModule("selected", "agm,clubs,discussion,sap,senate,voting"),
      directories: externalContributionDirectories(["agm", "clubs", "discussion", "sap", "senate", "voting"]),
      standalone: standaloneInputEntries(["agm", "sap"]),
      standaloneModules: standaloneModuleEntries(["agm", "sap"]),
    }));
  `;
  try {
    const result = execFileSync(process.execPath, ["--input-type=module", "-e", script], {
      cwd: process.cwd(),
      env: { ...process.env, PORTAL_FRONTEND_MANIFEST: manifest },
      encoding: "utf8",
    });
    const composition = JSON.parse(result);

    expect(composition.full).toEqual(["agm", "clubs", "discussion", "sap", "senate", "voting"]);
    expect(composition.selected).toEqual(["discussion", "voting"]);
    for (const program of ["agm", "clubs", "chat", "sabp", "senate", "voting"]) {
      expect(composition.source).toContain(`/src/${program}/frontend/`);
    }
    expect(composition.source).toContain("src/senate/frontend/public-views.js");
    expect(composition.directories).toHaveLength(6);
    expect(composition.standalone).toEqual({
      checkin: path.resolve(process.cwd(), "checkin.html"),
      finance: path.resolve(process.cwd(), "finance.html"),
    });
    expect(composition.standaloneModules).toEqual({
      checkin: path.resolve(process.cwd(), "../src/agm/frontend/checkin.js"),
      finance: path.resolve(process.cwd(), "../src/sabp/frontend/finance.js"),
    });
    execFileSync(process.execPath, ["node_modules/vite/bin/vite.js", "build", "--mode", "selected",
      "--outDir", output], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        PORTAL_FRONTEND_MANIFEST: manifest,
        PORTAL_FRONTEND_PROGRAMS: "agm,clubs,discussion,sap,senate,voting",
      },
      stdio: "pipe",
      timeout: 90_000,
    });
    expect(readdirSync(output)).toEqual(expect.arrayContaining(["index.html", "sso.html", "checkin.html", "finance.html"]));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}, 120_000);

it("emits no program imports for the core-only composition", () => {
  const source = createProgramContributionModule("core-only");
  expect(resolveComposition("core-only")).toEqual({ name: "core-only", programs: [] });
  expect(source).not.toContain("features/");
  expect(source).toContain("export const compiledPrograms = Object.freeze([]);");
});

it("reports enabled known programs missing from a frontend build", () => {
  expect(missingFrontendPrograms(["clubs", "agm", "agm_verify", "auth", "external"], ["clubs"]))
    .toEqual(["agm"]);
  expect(missingFrontendPrograms(["clubs", "agm_verify"], ["clubs", "agm"])).toEqual([]);
  expect(missingFrontendPrograms(["sap", "discussion"], [])).toEqual(["sap", "discussion"]);
  expect(missingFrontendPrograms(["unknown"], [], ["unknown"])).toEqual(["unknown"]);
  expect(missingFrontendPrograms(["unknown"], [], [])).toEqual([]);
});

it("rejects unknown frontend compositions", () => {
  expect(() => resolveComposition("senate-only")).toThrow("Unsupported frontend composition: senate-only");
});

it("selects one independent program without importing other program sources", () => {
  const composition = resolveComposition("selected", "clubs");
  expect(composition).toEqual({ name: "selected", programs: ["clubs"] });
  const source = createProgramContributionModule("selected", "clubs");
  expect(source).toContain("src/clubs/frontend/views.js");
  expect(source).not.toContain("src/agm/frontend/");
  expect(source).not.toContain("src/sabp/frontend/");
});

it("requires and orders program dependencies for selected contributions", () => {
  expect(PROGRAM_DEPENDENCIES.voting).toEqual(["discussion"]);
  expect(resolveComposition("selected", "voting, discussion")).toEqual({
    name: "selected",
    programs: ["discussion", "voting"],
  });
  const source = createProgramContributionModule("selected", "voting,discussion");
  expect(source).toContain("src/chat/frontend/views.js");
  expect(source).toContain("src/voting/frontend/panels.js");
  expect(source).not.toContain("src/clubs/frontend/");
});

it("rejects invalid IDs, duplicates, and missing dependencies", () => {
  expect(() => resolveComposition("selected")).toThrow("PORTAL_FRONTEND_PROGRAMS is required");
  expect(() => resolveComposition("selected", "unknown")).toThrow("Unknown frontend program ID(s): unknown");
  expect(() => resolveComposition("selected", "clubs,clubs")).toThrow("duplicate program IDs");
  expect(() => resolveComposition("selected", "voting")).toThrow("voting requires discussion");
  expect(() => resolveComposition("selected", "clubs,")).toThrow("comma-separated list");
  expect(PROGRAM_IDS).toContain("senate");
});

it("keeps every non-full composition away from the full static output", () => {
  expect(() => assertCompositionOutput("core-only", "/tmp/static", "/tmp/static"))
    .toThrow("cannot write to or overlap the full static output directory");
  expect(() => assertCompositionOutput("selected", "/tmp/static/subset", "/tmp/static"))
    .toThrow("cannot write to or overlap the full static output directory");
  expect(() => assertCompositionOutput("selected", "/tmp", "/tmp/static"))
    .toThrow("cannot write to or overlap the full static output directory");
  expect(() => assertCompositionOutput("full", "/tmp/static", "/tmp/static")).not.toThrow();
  expect(() => assertCompositionOutput("selected", "/tmp/subset", "/tmp/static")).not.toThrow();
});

it("builds an external contribution and rejects invalid manifests", () => {
  const root = mkdtempSync(path.join(tmpdir(), "portal-frontend-test-"));
  const webRoot = process.cwd();
  const manifest = path.join(root, "contributions.json");
  const feature = path.join(root, "feature");
  const output = path.join(root, "dist");
  try {
    mkdirSync(feature);
    writeFileSync(path.join(feature, "menu.js"),
      'export const navigation = () => [{ id: "external", label: "External fixture", view: "external" }];');
    writeFileSync(path.join(feature, "views.js"), 'export const views = { external: () => import("./view.svelte") };');
    writeFileSync(path.join(feature, "view.svelte"),
      '<h1 class="' + "bg-[" + "#123456" + ']">External view fixture</h1>');
    writeFileSync(manifest, JSON.stringify({
      programs: [{ id: "outside-program", directory: "feature", navigation: true, views: true }],
    }));
    const env = {
      ...process.env,
      PORTAL_FRONTEND_MANIFEST: manifest,
      PORTAL_FRONTEND_PROGRAMS: "outside-program",
    };
    execFileSync(process.execPath, ["node_modules/vite/bin/vite.js", "build", "--mode", "selected",
      "--outDir", output], { cwd: webRoot, env, stdio: "pipe", timeout: 45_000 });
    const assets = readdirSync(path.join(output, "assets")).filter(file => file.endsWith(".js"));
    const bundled = assets.map(file => readFileSync(path.join(output, "assets", file), "utf8")).join("\n");
    expect(bundled).toContain("External fixture");
    expect(bundled).toContain("External view fixture");
    const styles = readdirSync(path.join(output, "assets")).filter(file => file.endsWith(".css"));
    expect(styles.map(file => readFileSync(path.join(output, "assets", file), "utf8")).join("\n"))
      .toContain("#123456");

    const coreOutput = path.join(root, "core");
    const coreEnv = { ...env };
    delete coreEnv.PORTAL_FRONTEND_PROGRAMS;
    execFileSync(process.execPath, ["node_modules/vite/bin/vite.js", "build", "--mode", "core-only",
      "--outDir", coreOutput], { cwd: webRoot, env: coreEnv, stdio: "pipe", timeout: 45_000 });
    const coreStyles = readdirSync(path.join(coreOutput, "assets")).filter(file => file.endsWith(".css"));
    expect(coreStyles.map(file => readFileSync(path.join(coreOutput, "assets", file), "utf8")).join("\n"))
      .not.toContain("#123456");

    const serverCheck = `
      import { createServer } from "vite";
      const server = await createServer({ mode: "selected", server: { host: "127.0.0.1", port: 0 } });
      try {
        await server.listen();
        const address = server.httpServer.address();
        const response = await fetch("http://127.0.0.1:" + address.port + "/static/@fs" + ${JSON.stringify(feature)} + "/menu.js");
        if (!response.ok || !(await response.text()).includes("External fixture")) {
          throw new Error("external frontend contribution was not served: " + response.status);
        }
      } finally {
        await server.close();
      }
    `;
    execFileSync(process.execPath, ["--input-type=module", "-e", serverCheck],
      { cwd: webRoot, env, stdio: "pipe", timeout: 30_000 });

    for (const [invalid, message] of [
      [{ programs: [{ id: "outside-program", directory: "feature", dependencies: ["unknown"] }] },
        'Unknown frontend dependency "unknown"'],
      [{ programs: [
        { id: "clubs", directory: "feature" },
        { id: "clubs", directory: "feature" },
      ] }, "Frontend contribution IDs must be unique"],
      [{ programs: [{ id: "agm_verify", directory: "feature" }] }, "Invalid frontend contribution ID"],
      [{ programs: [
        { id: "one", directory: "feature", dependencies: ["two"] },
        { id: "two", directory: "feature", dependencies: ["one"] },
      ] }, "Cyclic frontend contribution dependency"],
      [{ programs: [], typo: true }, "Unknown frontend manifest fields: typo"],
    ]) {
      writeFileSync(manifest, JSON.stringify(invalid));
      const rejected = spawnSync(process.execPath, ["--input-type=module", "-e",
        'import "./program-composition.js"'], { cwd: webRoot, env, encoding: "utf8", timeout: 5_000 });
      expect(rejected.status).not.toBe(0);
      expect(rejected.stderr).toContain(message);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}, 150_000);
