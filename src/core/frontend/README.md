# Frontend builds

The default `npm run build` includes all program contributions and writes to the configured full static output (`src/core/static` in the current layout).

## Compositions

- `npm run build`: full build (default).
- `npm run build:core`: core-only build, written to the ignored `src/core/frontend/.core-dist/`.
- `PORTAL_FRONTEND_PROGRAMS=clubs npm run build:subset`: selected program build, written to the ignored `src/core/frontend/.subset-dist/`.

`PORTAL_FRONTEND_PROGRAMS` is a comma-separated list of frontend contribution IDs: `agm`, `clubs`, `discussion`, `sap`, `senate`, and `voting`. IDs are case-sensitive; duplicates and unknown IDs are errors. Selection order does not change contribution order. Program dependencies must be selected explicitly: `voting` requires `discussion`, so `PORTAL_FRONTEND_PROGRAMS=discussion,voting` is valid and `PORTAL_FRONTEND_PROGRAMS=voting` is rejected. The `agm` contribution also contains the optional AGM-verification UI. Core services and views are always included and are not selectable program contributions.

The equivalent explicit modes are `PORTAL_FRONTEND_COMPOSITION=full`, `core-only`, or `selected`. The selected mode requires `PORTAL_FRONTEND_PROGRAMS`; `npm run build:subset` selects that mode. Non-full builds use isolated output directories, and Vite rejects a non-full composition if its resolved output directory targets the full static output.

To serve a subset without replacing the full bundle, point the backend's `PORTAL_STATIC_DIR` at `src/core/frontend/.subset-dist/` (or `src/core/frontend/.core-dist/`). Match the selected frontend contributions, including their dependencies, to the backend's enabled program configuration. The portal warns when an enabled program that declares a frontend contribution is missing from the build. With older backends that do not report frontend declarations, only known contributions can be checked. This alignment is operational, not an authorization boundary: the backend must continue enforcing access independently.

Composition controls which frontend code is present at build time. Existing server-reported plug-in enablement still gates navigation actions and runtime behavior. The full build includes the AGM check-in and SAP finance standalone pages; core-only includes the portal and SSO pages, while selected builds include standalone program pages only for their selected programs.

## External frontend contributions

Set `PORTAL_FRONTEND_MANIFEST` to a JSON file listing frontend contributions supplied by installed packages. Resolve the file path from the `src/core/frontend/` working directory; each contribution's directory is relative to the manifest. For example:

```json
{
  "programs": [{
    "id": "example-program",
    "directory": "./node_modules/example-program/portal",
    "dependencies": ["clubs"],
    "menu": ["navigation"],
    "views": true
  }]
}
```

The ID must match the backend plug-in ID; its backend manifest should also declare `"frontend": true` so an omitted frontend can be detected. IDs and dependencies must be unique and valid, and dependencies must be explicitly selected for a subset build. External contributions are included in full builds; to select this example, use `PORTAL_FRONTEND_PROGRAMS=clubs,example-program npm run build:subset`.

`menu` lists named exports from `menu.js`; the legacy `navigation: true` option remains supported as shorthand for `menu: ["navigation"]`. Menu functions receive the host menu context and return menu nodes. `views.js` exports a `views` object mapping names to lazy Svelte component loaders. Optional `publicViews` and `panels` booleans select `public-views.js` and `panels.js`, which export matching objects. `standalone` lists names of package-owned `<name>.js` modules; each needs a matching host HTML shell, and selected builds emit that shell as `<name>.html`. View and panel names must not conflict with existing contributions. Tailwind scans selected external directories as well as the host. External code is trusted build input, not a sandbox; backend authorization remains mandatory.

An external manifest entry may replace any built-in contribution with the same ID. It occupies the built-in contribution's original position; built-in dependencies and capabilities remain enabled, so the `voting` to `discussion` dependency and standalone pages cannot be accidentally dropped by an incomplete override.

The program-owned frontend source and its manifest are located at:

- AGM: `src/agm/frontend/` (`portal-frontend.json`, views and the `checkin.js` entry).
- Clubs: `src/clubs/frontend/` (`portal-frontend.json`, views, menu and receipt/money helpers).
- Discussion/chat: `src/chat/frontend/` (`portal-frontend.json`, views; contribution ID `discussion`).
- SABP: `src/sabp/frontend/` (`portal-frontend.json`, views and the `finance.js` entry).
- Senate: `src/senate/frontend/` (`portal-frontend.json`, views and public views).
- Voting: `src/voting/frontend/` (`portal-frontend.json`, views, panels and ballot helpers).

`PORTAL_FRONTEND_MANIFEST` can point to an operator-composed JSON manifest that combines these package manifests. Each package manifest uses `directory: "."`, so its directory resolves to the package's `frontend/` resource directory. Standalone page names select package-owned `<name>.js` modules; the host-owned HTML shells preserve the existing `/static/checkin.html` and `/static/finance.html` routes.

The SABP administration view gets its default WFNI weights through the host's `defaultWeights` export. The host Vite build provides that value from `data/wfni_weights.csv`; this host data input remains separate from the SABP frontend resources.

## Senate program-owned frontend

Senate's contribution source lives with the program at `../src/senate/frontend/`: `menu.js`, `views.js`,
`public-views.js`, and the Svelte files under `views/`. The repository full build uses these sources by default;
the core-only composition still omits them, and selecting `senate` includes its member/admin actions and public
results view. The Senate package manifest is `portal-frontend.json` in that directory:

```json
{
  "programs": [{
    "id": "senate",
    "directory": ".",
    "navigation": true,
    "views": true,
    "publicViews": true
  }]
}
```

For an installed Senate package, set `PORTAL_FRONTEND_MANIFEST` to its packaged `senate/frontend/portal-frontend.json`.
An external `senate` entry replaces the repository source at the same contribution position. The Senate distribution
must include `frontend/portal-frontend.json`, `frontend/*.js`, and `frontend/views/*.svelte` as package resources.

The manifest is the same external-contribution format described above: `programs` is an array, `directory` is
relative to the manifest file, and the optional `navigation`, `menu`, `views`, and `publicViews` fields select
`menu.js`, `views.js`, and `public-views.js` exports. This allows an installed contribution to replace the in-tree
Senate source without changing program order.

Program-owned Svelte and JavaScript files should use the stable `@utsu/portal` host entry point instead of reaching
into the host's relative source tree. Its current exports, maintained in `src/lib/portal-api.js`, are:

- Functions: `api`, `apiAllPages`, `downloadReport`, `getToken`, `go`, `isBlank`, `mountPage`, `openStream`,
  `readFileAsDataUrl`.
- Values: `defaultWeights`, `nav`, `panels`, `session`.
- Class: `Action`.
- Svelte components: `BankingView`, `NoticeButton`, `ScanWidget`, `Status`, `ThreadPanel`.
