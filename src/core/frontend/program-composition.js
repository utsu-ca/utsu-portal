import { existsSync, readFileSync, realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.dirname(fileURLToPath(import.meta.url));
const builtins = [
  { id: "agm", sourceDirectory: "../src/agm/frontend", menu: ["agmActions"], views: true, standalone: ["checkin"] },
  { id: "clubs", sourceDirectory: "../src/clubs/frontend",
    menu: ["financeActions", "registrationActions"], views: true },
  { id: "discussion", sourceDirectory: "../src/chat/frontend", views: true },
  { id: "sap", sourceDirectory: "../src/sabp/frontend", menu: ["bursaryActions"], views: true, standalone: ["finance"] },
  { id: "senate", sourceDirectory: "../src/senate/frontend", menu: ["navigation"], views: true, publicViews: true },
  { id: "voting", sourceDirectory: "../src/voting/frontend",
    dependencies: ["discussion"], views: true, panels: true },
];

function externalContributions(manifestPath) {
  if (manifestPath === undefined) return [];
  if (!manifestPath.trim()) throw new Error("PORTAL_FRONTEND_MANIFEST must be a non-empty path.");
  const resolved = path.resolve(manifestPath);
  const manifest = JSON.parse(readFileSync(resolved, "utf8"));
  if (!Array.isArray(manifest?.programs)) {
    throw new Error("Frontend contribution manifest must contain a programs array.");
  }
  const unknownFields = Object.keys(manifest).filter(key => key !== "programs");
  if (unknownFields.length) {
    throw new Error(`Unknown frontend manifest fields: ${unknownFields.join(", ")}.`);
  }
  return manifest.programs.map((program, index) => {
    if (!program || typeof program !== "object" || Array.isArray(program)) {
      throw new Error(`Invalid frontend contribution at index ${index}.`);
    }
    const allowed = new Set([
      "id", "directory", "dependencies", "navigation", "menu", "views", "publicViews", "panels", "standalone",
    ]);
    const unknown = Object.keys(program).filter(key => !allowed.has(key));
    if (unknown.length) throw new Error(`Unknown frontend contribution fields: ${unknown.join(", ")}.`);
    if (typeof program.id !== "string" || !/^[a-z][a-z0-9_-]*$/.test(program.id)
        || program.id === "agm_verify") {
      throw new Error(`Invalid frontend contribution ID at index ${index}.`);
    }
    if (typeof program.directory !== "string" || !program.directory.trim()) {
      throw new Error(`Frontend contribution "${program.id}" requires a directory.`);
    }
    const dependencies = program.dependencies ?? [];
    if (!Array.isArray(dependencies) || dependencies.some(id => typeof id !== "string")) {
      throw new Error(`Frontend contribution "${program.id}" dependencies must be a list of IDs.`);
    }
    if (program.menu !== undefined && program.navigation !== undefined) {
      throw new Error(`Frontend contribution "${program.id}" cannot combine menu and navigation.`);
    }
    if (program.menu !== undefined && (!Array.isArray(program.menu) || !program.menu.length
        || program.menu.some(name => typeof name !== "string" || !/^[$A-Z_a-z][$\w]*$/.test(name))
        || new Set(program.menu).size !== program.menu.length)) {
      throw new Error(`Frontend contribution "${program.id}" menu must be a list of unique JavaScript export names.`);
    }
    const standalone = program.standalone ?? [];
    if (!Array.isArray(standalone) || standalone.some(name => typeof name !== "string"
        || !/^[a-z][a-z0-9_-]*$/.test(name) || ["index", "sso"].includes(name))
        || new Set(standalone).size !== standalone.length) {
      throw new Error(`Frontend contribution "${program.id}" standalone must be a list of unique page names.`);
    }
    for (const key of ["navigation", "views", "publicViews", "panels"]) {
      if (program[key] !== undefined && typeof program[key] !== "boolean") {
        throw new Error(`Frontend contribution "${program.id}" ${key} must be a boolean.`);
      }
    }
    return {
      id: program.id,
      directory: path.resolve(path.dirname(resolved), program.directory),
      dependencies,
      menu: program.menu ?? (program.navigation ? ["navigation"] : undefined),
      views: program.views,
      publicViews: program.publicViews,
      panels: program.panels,
      standalone,
      external: true,
    };
  });
}

const external = externalContributions(process.env.PORTAL_FRONTEND_MANIFEST);
const builtinIds = new Set(builtins.map(({ id }) => id));
if (new Set(external.map(({ id }) => id)).size !== external.length) {
  throw new Error("Frontend contribution IDs must be unique.");
}
const externalById = new Map(external.map(program => [program.id, program]));
const contributions = [
  ...builtins.map(program => {
    const installed = externalById.get(program.id);
    if (installed) {
      const menu = [...new Set([...(program.menu ?? []), ...(installed.menu ?? [])])];
      return {
        ...installed,
        dependencies: [...new Set([...(program.dependencies ?? []), ...installed.dependencies])],
        menu: menu.length ? menu : undefined,
        views: Boolean(program.views || installed.views),
        publicViews: Boolean(program.publicViews || installed.publicViews),
        panels: Boolean(program.panels || installed.panels),
        standalone: [...new Set([...(program.standalone ?? []), ...installed.standalone])],
      };
    }
    const { sourceDirectory, ...contribution } = program;
    return {
      ...contribution,
      directory: path.resolve(webRoot, sourceDirectory),
      external: true,
    };
  }),
  ...external.filter(({ id }) => !builtinIds.has(id)),
];

export function externalContributionDirectories(programs) {
  const selected = new Set(programs);
  return contributions.filter(program => program.external && selected.has(program.id))
    .map(program => realpathSync(program.directory));
}
function standaloneEntries(programs) {
  const selected = new Set(programs);
  const entries = {};
  for (const program of contributions) {
    if (!selected.has(program.id)) continue;
    for (const name of program.standalone ?? []) {
      if (Object.hasOwn(entries, name)) {
        throw new Error(`Duplicate standalone frontend page name "${name}".`);
      }
      const html = path.join(webRoot, `${name}.html`);
      const module = path.join(program.directory, `${name}.js`);
      if (!existsSync(html)) throw new Error(`No host HTML template exists for standalone page "${name}".`);
      if (!existsSync(module)) {
        throw new Error(`Frontend contribution "${program.id}" is missing standalone entry "${name}.js".`);
      }
      entries[name] = { html, module };
    }
  }
  return entries;
}
export function standaloneInputEntries(programs) {
  return Object.fromEntries(Object.entries(standaloneEntries(programs)).map(([name, entry]) => [name, entry.html]));
}
export function standaloneModuleEntries(programs) {
  return Object.fromEntries(Object.entries(standaloneEntries(programs)).map(([name, entry]) => [name, entry.module]));
}
export const PROGRAM_IDS = Object.freeze(contributions.map(({ id }) => id));
export const PROGRAM_DEPENDENCIES = Object.freeze(Object.fromEntries(
  contributions.map(({ id, dependencies = [] }) => [id, Object.freeze([...dependencies])]),
));

function validateDependencyGraph() {
  if (new Set(PROGRAM_IDS).size !== PROGRAM_IDS.length) {
    throw new Error("Frontend contribution IDs must be unique.");
  }
  const known = new Set(PROGRAM_IDS);
  for (const [id, dependencies] of Object.entries(PROGRAM_DEPENDENCIES)) {
    if (new Set(dependencies).size !== dependencies.length) {
      throw new Error(`Duplicate frontend dependency for "${id}".`);
    }
    for (const dependency of dependencies) {
      if (!known.has(dependency)) throw new Error(`Unknown frontend dependency "${dependency}" for "${id}".`);
      if (dependency === id) throw new Error(`Frontend contribution "${id}" cannot depend on itself.`);
    }
  }
  const visiting = new Set();
  const visited = new Set();
  const visit = id => {
    if (visited.has(id)) return;
    if (visiting.has(id)) throw new Error(`Cyclic frontend contribution dependency involving "${id}".`);
    visiting.add(id);
    for (const dependency of PROGRAM_DEPENDENCIES[id]) visit(dependency);
    visiting.delete(id);
    visited.add(id);
  };
  for (const id of PROGRAM_IDS) visit(id);
}

validateDependencyGraph();

function resolveSelectedPrograms(selection) {
  if (typeof selection !== "string" || selection.trim() === "") {
    throw new Error('PORTAL_FRONTEND_PROGRAMS is required for the "selected" composition.');
  }
  const requested = selection.split(",").map(id => id.trim());
  if (requested.some(id => !id)) {
    throw new Error("PORTAL_FRONTEND_PROGRAMS must be a comma-separated list of program IDs.");
  }
  const unique = new Set(requested);
  if (unique.size !== requested.length) {
    throw new Error("PORTAL_FRONTEND_PROGRAMS contains duplicate program IDs.");
  }
  const unknown = requested.filter(id => !PROGRAM_IDS.includes(id));
  if (unknown.length) {
    throw new Error(`Unknown frontend program ID(s): ${unknown.join(", ")}. Valid IDs: ${PROGRAM_IDS.join(", ")}.`);
  }
  const selected = new Set(requested);
  const missing = requested.flatMap(id =>
    PROGRAM_DEPENDENCIES[id].filter(dependency => !selected.has(dependency)).map(
      dependency => `${id} requires ${dependency}`,
    ),
  );
  if (missing.length) {
    throw new Error(`Missing frontend program dependencies: ${missing.join("; ")}.`);
  }
  return PROGRAM_IDS.filter(id => selected.has(id));
}

export function resolveComposition(name = "full", selection) {
  if (!["full", "core-only", "selected"].includes(name)) {
    throw new Error(`Unsupported frontend composition: ${String(name)}. Use "full", "core-only", or "selected".`);
  }
  if (name === "selected") return { name, programs: resolveSelectedPrograms(selection) };
  if (selection !== undefined) {
    throw new Error(`PORTAL_FRONTEND_PROGRAMS is only valid for the "selected" composition.`);
  }
  return { name, programs: name === "full" ? [...PROGRAM_IDS] : [] };
}

export function assertCompositionOutput(name, outputDirectory, fullOutputDirectory) {
  if (name === "full") return;
  const resolvedOutput = resolveFilesystemPath(outputDirectory);
  const resolvedFullOutput = resolveFilesystemPath(fullOutputDirectory);
  if (containsPath(resolvedOutput, resolvedFullOutput) || containsPath(resolvedFullOutput, resolvedOutput)) {
    throw new Error(`The "${name}" frontend composition cannot write to or overlap the full static output directory.`);
  }
}

function resolveFilesystemPath(target) {
  const original = path.resolve(target);
  let current = original;
  const missing = [];
  while (true) {
    try {
      return path.join(realpathSync(current), ...missing);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      const parent = path.dirname(current);
      if (parent === current) return original;
      missing.unshift(path.basename(current));
      current = parent;
    }
  }
}

function containsPath(parent, child) {
  const relative = path.relative(parent, child);
  return relative === "" || (
    relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)
  );
}

export function createProgramContributionModule(name = "full", selection) {
  const { programs } = resolveComposition(name, selection);
  const imports = [];
  const menuEntries = [];
  const viewBindings = [];
  const publicViewBindings = [];
  const panelBindings = [];

  const importBindings = (program, file, names) => {
    const prefix = `program_${contributions.indexOf(program)}`;
    const bindings = names.map(name => `${prefix}_${name}`);
    const specifiers = names.map((name, index) => `${name} as ${bindings[index]}`);
    const filePath = path.join(program.directory, file);
    imports.push(`import { ${specifiers.join(", ")} } from ${JSON.stringify(filePath)};`);
    return bindings;
  };

  for (const program of contributions) {
    if (!programs.includes(program.id)) continue;
    if (program.menu) {
      const bindings = importBindings(program, "menu.js", program.menu);
      menuEntries.push(
        `${JSON.stringify(program.id)}: Object.freeze({ ${program.menu.map((name, index) =>
          `${name}: ${bindings[index]}`).join(", ")} })`,
      );
    }
    if (program.views) {
      viewBindings.push(importBindings(program, "views.js", ["views"])[0]);
    }
    if (program.publicViews) {
      publicViewBindings.push(importBindings(program, "public-views.js", ["publicViews"])[0]);
    }
    if (program.panels) {
      panelBindings.push(importBindings(program, "panels.js", ["panels"])[0]);
    }
  }

  return [
    ...imports,
    `export const compiledPrograms = Object.freeze(${JSON.stringify(programs)});`,
    `export const knownProgramContributions = Object.freeze(${JSON.stringify({
      ...Object.fromEntries(PROGRAM_IDS.map(id => [id, id])),
      agm_verify: "agm",
    })});`,
    `export const menuContributions = Object.freeze({ ${menuEntries.join(", ")} });`,
    `export const viewContributions = Object.freeze([${viewBindings.join(", ")}]);`,
    `export const publicViewContributions = Object.freeze([${publicViewBindings.join(", ")}]);`,
    `export const panelContributions = Object.freeze([${panelBindings.join(", ")}]);`,
  ].join("\n");
}
