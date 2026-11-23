# Program package migration

Status: in progress. Program sources and frontends are separated, the standard
wheel is core-only, and the retained-policy catalogue is implemented.
Remaining: release verification of installed-wheel frontend compositions and
a real multi-process retained-data/rollback check before the production
cutover.

## Target

The source tree will be organized around these top-level owners:

```text
src/
  core/
  agm/
  sabp/
  chat/
  senate/
  voting/
  clubs/
  branding/
```

Core owns shared authentication and identity, authorization, auditing, data protection, storage and file/bucket
mechanisms, forms, email, shared message storage and thread mechanics, and the plugin host. Programs contribute
their own business rules, routes, resources, configuration and UI through core contracts. Chat owns discussion
boards, topics and user-facing discussion features; voting depends on chat through a declared contract. Branding
owns the default theme and assets; core owns generic theme loading and validation.

The existing runtime IDs, such as `sap` and `discussion`, are not automatically renamed by this layout. A separate
compatibility decision is needed before changing public IDs or Python import paths. Plugins are trusted in-process
code, not security sandboxes.

## Invariants

- Preserve existing URLs, permission and role IDs, form IDs and versions, data keys and shapes, encryption, audit
  behavior, atomic updates, and existing full-portal workflows.
- A disabled or uninstalled program must not remove protection for its retained data. Unknown file or thread
  authorizers must deny access.
- Do not combine source extraction with a data rewrite or a switch of file-storage backend.
- Core must eventually work without any program installed. Program packages use public contracts, not a full
  mutable application context or imports of other program implementations.
- Existing branding overrides and the development-environment warning remain functional.
- Existing deployments require an explicit compatibility and rollback path. Installation and enablement are
  separate concerns; changing defaults must not silently disable existing workflows.

## Stages

1. **Baseline and boundary preparation.** Record existing behavior and remove individual reverse dependencies
   where their contracts can be preserved and tested independently. The initial targeted baseline is
   `.venv/bin/python -m pytest -q tests/core/test_file_service.py tests/clubs/test_clubs.py tests/packaging/test_plugin_host.py`:
   50 passed on the current working tree. Receipt storage now receives the club academic-year policy from its
   owner instead of importing the Clubs implementation; the same targeted suite passes with 51 tests.
   The public `FormsService` contract now also exposes the existing owner-scoped keyset paging operation.
   Its ownership and filtering behavior is checked with memory and SQLite stores.
   The full backend baseline passed: 624 tests, with 23 dependency deprecation warnings.
   A subsequent full run after the contract changes had 643 passing tests and one intermittent failure:
   concurrent admin demotions can return 403 when an actor is demoted before authentication, while the existing
   test expected 409. An isolated rerun passed, but repeated reruns reproduced both outcomes. A later full run
   passed 648 tests. The test now accepts either denial while still checking that one admin remains; the migration
   does not change role-demotion behavior.
2. **Core contracts and mechanisms.** Complete scoped identity, storage, files, quotas, financial profiles, audit,
   forms and messaging capabilities as required by the programs. Separate program policy from core mechanisms;
   preserve existing retained-data protection and access audits. Preserve the existing keyset pagination interface
   and owner-scoped submission paging when moving programs; do not replace bounded store queries with full scans.
   The encryption, financial-access audit and quota wrappers now pass safe keyset queries to the underlying store;
   ordering or filtering on encrypted fields uses a scan so plaintext is not put into indexes. Encrypted
   top-level filters are compared after decryption.
   The focused store, financial, file, pagination and plugin-host suite passed 67 tests, including encrypted
   nested filters and audit-preserving delegation.
   The scoped store contract now exposes the existing put and atomic compare-and-swap operations needed by
   programs. Manifest validation reserves shared authentication, identity, forms, audit and other core
   collections rather than allowing a program to claim them as exclusive storage. Existing third-party
   manifests that claim these collections must migrate to core services before upgrading.
   Typed authentication dependencies and thread-authorization callbacks now receive only `uuid`, `status`,
   `role`, `roles`, `email_verified` and `backup_session` when present. Existing typed plugins that relied on
   other account fields must migrate to an appropriate core service; legacy-context modules are unchanged.
   The typed authentication contract also provides the existing signed-in, not-yet-verified gate needed by
   Clubs. Program manifests cannot claim core Google, Turnstile or payment credentials as settings.
   Senate now uses typed services, including minimal person and roster projections, owner-scoped forms and
   exclusive store collections, instead of the legacy application context.
   Existing local and split-auth Senate workflows pass their integration tests.
   AGM and its verification module now use typed services and an owner-scoped verification provider. Zoom SSO
   registration and reviewer identity checks remain in core capabilities so the program does not receive secrets;
   focused AGM, review, SSO and plug-in host tests passed 79 tests.
   SABP's application, review and configuration code now uses typed services. Core retains the financial-profile
   routes and data behind a scoped route service; the legacy banking adapter remains for compatibility. The
   focused SABP, financial-profile and plug-in host suites passed 75 tests.
   Clubs member lookup uses a minimal contact projection after authorizing the caller, rather than full account and user records;
   the same projection is available to typed programs in local and split-auth deployments.
   Typed programs can also request the limited account/role projection by UUID for resource authorizers; it does not
   include private account data. Callers must still authorize each use.
   The full backend suite passed 651 tests after these changes and the banking policy separation below.
   Typed programs can declare package-owned configuration keys and receive only their own configured values
   through the settings service, without adding fields to core `Settings`; existing core settings remain supported.
   Financial access-audit rules are now retained policy metadata rather than SAP/Clubs conditionals in the store
   wrapper. Program declarations must be covered by that catalogue; disabled programs retain the same audit rules.
   The catalogue is currently shipped with core, so a new external program cannot yet introduce protected fields
   or audited collections without a core update. A durable, safely coordinated policy extension mechanism is still
   required before claiming that independently installed programs can define their own retained-data protections.
   Shared financial-profile routes accept a program-provided route policy; SAP owns its application eligibility,
   listing enrichment, permissions and legacy route names. The old shared `legacy=True` entry point remains as a
   lazy compatibility adapter; its SAP policy takes owner-scoped forms rather than the application context.
   Full separation will move that adapter into the eventual compatibility layer.
   File kinds can optionally provide a create operation through the same owner-scoped service as reads and deletes.
   Creation checks the kind's authorizer and records a `file.create` attempt before calling the adapter. Adapters
   remain responsible for content validation and for using quota-backed storage; this contract does not by itself
   provide a bucket or implement upload quotas. Typed plugins may register a file-kind factory receiving their
   scoped services, so adapters can use their own store without a global application context.
   The receipt storage mechanism accepts the narrow atomic store operations it needs; Clubs supplies a scoped
   store while receipt configuration and encryption stay in core.
   Clubs now uses typed services, with core-owned receipt and invitation-link capabilities. Its receipt storage
   remains available to core maintenance; focused Clubs, file-service and plug-in host tests passed 91 tests.
   A file-kind owner can opt in to specific `dependent_actions` and `authorizer_actions` for declared dependents.
   SAP now contributes its cheque-access policy instead of the core profile module naming SAP roles; disabling SAP
   removes its authorizer. The file adapter still checks the actor and unknown or undeclared kinds deny access.
   Typed programs can request quota usage through `quotas` for manifest-declared `quota_domains`. This scopes the
   owner namespace, not the individual resource: programs must authorize each lookup. Reservation and enforcement
   remain in the quota-backed store, not in this read-only service.
   Quota reservation now uses injectable collection policies, with an always-on legacy catalogue for retained
   uploads. A replacement must cover an entire legacy collection group and preserve its quota item prefixes.
   The quota mechanism supports explicitly injected policies, and reconciliation reuses those policies.
   Automatic external providers through `utsu.portal.upload_quota_policies` are rejected at startup: provider
   removal and multi-process rollout cannot yet preserve enforcement for retained records safely. Do not inject
   external policies in production until the retained-policy cutover below is implemented.
   The full backend suite passed 671 tests after the quota and source-layout changes.
3. **Frontend host seams.** Move hard-coded program navigation, views, widgets and deep links behind contributions.
   The compiled host must match installed/enabled plugins, and shared profile features must work without SABP.
   The shared banking status, view, state and cheque reader now live under the profile feature rather than SABP.
   A SABP-owned adapter supplies its endpoint and copy to the shared view; shared-profile and SABP routes and email
   links remain unchanged. Program actions and the Senate results widget now follow the enabled-module list,
   while public informational pages remain available. AGM, Clubs, SABP and Senate own their menu-action definitions;
   program view loaders and the Senate public and voting discussion panels are owned by their features; duplicate
   view IDs fail explicitly. A Vite virtual module selects full, core-only or explicitly listed program contributions
   at build time. Core-only and subset builds omit unselected chunks and standalone pages and use separate outputs;
   an operator-supplied frontend manifest can add installed package contributions to the same build.
   `PORTAL_STATIC_DIR` selects a separately built bundle for serving. The frontend warns when a known enabled
   backend program has no compiled contribution, including AGM verification's shared AGM contribution. External
   backend manifests declare `frontend: true` for the same check even when their frontend was omitted at build time.
   The built-in program frontend sources and contribution manifests now live in their respective program packages.
   AGM check-in and SABP finance keep host-owned HTML shells, with their scripts supplied by those packages.
   The standard bundled wheel includes every program frontend resource. The experimental core wheel uses a core-only
   bundle, while a separate full-frontend wheel supplies the default UI
   for the full portal. A selected frontend build using all six installed wheel contributions emitted both standalone
   pages; SABP's draft WFNI weights remain a host build input. The frontend suite passed 146 tests; full, core-only,
   Clubs-only and discussion/voting builds passed, and a Clubs-only app served its matching bundle.
4. **Program extraction.** Extract and validate each program as an independently installable package, including
   resources, settings, backend, frontend and tests. Senate, chat, voting, Clubs, AGM and SABP implementations now
   live under their own `src/` packages, with identity-preserving legacy module aliases and resource compatibility.
   Voting imports the chat contract rather than its implementation. These packages remain bundled in the standard
   core distribution; the experimental split-wheel builder below is not yet a release cutover. The targeted Senate,
   compatibility and host suite passed 73 tests; chat/voting passed 76; Clubs passed 104; AGM passed 107; and SABP
   passed 191.
   A wheel installed outside the checkout passed full-portal and disabled-program startup checks, as well as
   compatibility-import and schema-resource checks.
   The core implementation lives in `src/core`, a top-level package named `core` like `agm` or `sabp`; the core
   frontend source is `src/core/frontend`. The import name was `utsu_core` and the distribution is still `utsu-core`.
   Development Python paths, Compose watch configuration and the full frontend build output use the new location.
   A wheel installed outside the checkout preserves resources, aliases and full/core-only API startup.
   Legacy schema discovery skips genuinely uninstalled program packages while still reporting errors in installed
   packages; a source-isolated process verifies this behavior without importing the bundled programs.
   Core-only API and UI startup are tested with program and branding imports blocked. A UI deployment without the
   bundled branding package must currently supply `PORTAL_BRANDING_DIR`.
   The full backend suite passed 680 tests after relocation, the historical template links, named manifest discovery,
   separate frontend-bundle serving and configurable historical SABP weights.
   External typed-plugin distributions can provide one `utsu-plugin.json` or, for multiple entry points, one
   `<plugin-id>.utsu-plugin.json` per entry point. Generic and named manifests cannot be mixed in one distribution.
   SABP's historical batch processor accepts an explicit `SABP_WFNI_WEIGHTS_FILE` path and fails on a missing or
   invalid policy, allowing installed deployments to supply their approved weights without bundling a substitute.
5. **Branding and composition.** The default theme and assets now live in `src/branding`; the core still serves
   configured custom directories and loads the default package only when needed. It is still bundled in the
   existing distribution. Provide core-only and full-portal builds, independent package metadata, explicit
   enablement, compatibility and deployment documentation. Branding and related host tests passed 66 tests.
6. **Release verification.** Test clean wheel installs outside the checkout, each program alone and together,
   local and split authentication, disabled-plugin behavior, old protected data, APIs, frontend builds and
   rollback. The full backend suite passed 696 tests; the frontend suite passed 146 tests, and full, core-only
   and discussion/voting builds succeeded. A clean aggregate installation with freshly installed third-party
   dependencies served the full portal UI. A baseline-to-split-to-baseline SQLite rollback check retained encrypted
   financial and receipt records, quota usage and financial-access audits; broader release-cutover checks remain.

At every stage the full portal must remain runnable. Complete each stage's tests before proceeding. The earlier
[plugin architecture plan](07-plugin-architecture-plan.md) contains additional migration detail; current code and
this target take precedence where its historical status descriptions differ.

## Distribution cutover (alpha)

The standard `utsu-core` wheel is now core-only. Program packages, branding,
and the full frontend ship as separate distributions built by
`tools/build_split_distributions.py`; the file-free `utsu-portal` aggregate
installs them all. Alpha: breaking changes are acceptable, but the wheels
must remain non-overlapping — installing split wheels into an environment
containing the older bundled `utsu-core` would give multiple distributions
ownership of the same paths. Build a fresh environment or container image
(the Dockerfile does this), verify against retained data before switching
traffic, and keep the prior image for rollback.

The builder rejects legacy-context programs, generates named program
manifests, and checks for overlapping wheel paths before publishing. Its
core wheel requires a core-only frontend (`src/core/frontend/.core-dist`, built
automatically if missing); a separate full-frontend wheel owns the full
portal UI and is selected automatically when installed. `PORTAL_STATIC_DIR`
still takes precedence. Source-isolated installations started core-only,
each program (with chat for voting), and the full composition in local and
split authentication modes; a fresh aggregate installation resolved its
dependencies, discovered every program, and served the full UI and both
standalone pages. A baseline-to-split-to-baseline SQLite rollback check
retained encrypted financial and receipt records, quota usage, and
financial-access audits.
The builder runs with Python 3.12+ and installed `pip`, `setuptools`, `wheel` and `packaging`. First build the
core-only frontend with `npm --prefix src/core/frontend run build:core`, then run
`python tools/build_split_distributions.py --experimental --version 0.2.0 --out-dir /tmp/utsu-split-wheels --core-static-dir src/core/frontend/.core-dist`.
The explicit experimental flag acknowledges the remaining gaps, and the version must be greater than the bundled
release. Do not install these wheels into an environment that contains the older bundled `utsu-core`; build a
separate environment and retain the old environment for rollback.

## Retained-policy catalogue (implemented)

Core owns a durable retained-policy catalogue in the quota store
(`quota_policy_catalogue/current`): each policy's name, collections, item
prefixes, version, and shape fingerprint, plus a monotonic fencing epoch.
The catalogue lives in the quota store (SQLite/SQL/Firestore via `Store`),
so it survives provider removal and is shared across processes.

- **Provider removal fails closed.** A policy in the catalogue that is not
  installed raises `RetainedPolicyError` at startup instead of treating its
  records as unprotected.
- **Shape changes fail closed.** A fingerprint mismatch for a known policy
  requires an explicit catalogue migration. A sanctioned replacement with
  the same shape under a new name is treated as a rename.
- **Stale processes are fenced.** Every quota write checks the epoch; after
  a catalogue change, older processes refuse writes and must restart.
- **Atomicity is required.** The quota store must override `create` and
  `replace` with atomic versions; non-atomic backends fail closed.
- **Rollout order:** declare the new policy, drain and restart all writers,
  then activate. Rollback targets must read the catalogue — an older binary
  that ignores it cannot be fenced, so drain before rolling back.

External providers are loaded via the `utsu.portal.upload_quota_policies`
entry point; a provider that fails to load or returns a non-policy fails
closed. Tests cover provider loading, load failure, removal, stale-process
fencing, and shape-change rejection.

## Verification and remaining work

Verified on the alpha cutover:

- **Installed-wheel frontends.** A frontend built from the six installed program wheels' `portal-frontend.json`
  manifests emitted `index`, `sso`, `checkin` and `finance`. The WFNI default weights are a host build input
  (`data/wfni_weights.csv`, copied into the Dockerfile's frontend stage), not a program resource.
- **Container image.** The multi-stage `Dockerfile` builds the core-only bundle in a Node stage, builds the ten
  split wheels, and installs the `utsu-portal` aggregate plus `utsu-core` extras (extras belong to `utsu-core`,
  not the file-free aggregate). The running image served the full UI, `checkin.html`, `finance.html`, `sso.html`
  and `/api/plugins` on a persistent SQLite store and quota store.
- **Multi-process retained-policy behaviour.** `test_quota_catalogue_fences_and_retains_across_real_processes`
  runs separate OS processes against a shared SQLite quota file: an older process is fenced when a newer one
  declares a policy, a process missing a retained provider fails closed, and restoring the provider recovers.
  A replica-start race (two replicas booting against an empty catalogue) previously bumped the epoch and fenced
  both; the catalogue is now written by one atomic update that bumps the epoch only when it changes, covered by
  `test_replicas_starting_together_do_not_fence_each_other`.

- **Config-driven build and deploy.** `config/portal.yaml` selects programs; `scripts/build.sh` installs only those and
  builds a matching UI (full, selected or core-only); `scripts/deploy.sh` enables them and refuses a config that asks
  for a program the image lacks. Verified: the full config built and ran the dev stack; an AGM+Senate config produced an
  image with only `utsu-program-agm` and `utsu-program-senate`, served `checkin.html` and not `finance.html`, and
  `deploy.sh` rejected the full config against it.
- **Layout.** `web/` is now `src/core/frontend/` and the `utsu_core` package is flattened to `src/core/` (import name
  `core`), so `core` is a top-level package like `agm` and `sabp`.

Still open:

1. A rolling deployment shape (Kubernetes) rehearsal: the fencing contract means every older replica must be
   restarted after a policy is declared, and rollback to a binary that predates the catalogue cannot be fenced.
2. Publishing: choose the release version and index; the Dockerfile defaults to `PORTAL_VERSION=0.2.0`.
3. Optional: move the retained catalogue to Redis/SQL-backed metadata if a deployment cannot provide an atomic
   `Store` for `PORTAL_QUOTA_STORE`; the atomic-store requirement currently fails closed.

## Current local composition baseline

Captured with an in-memory store and the default local, API-only composition:

- Loaded modules: `sso`, `auth`, `profile`, `payments`, `forms`, `agm`, `agm_verify`, `senate`, `sap`,
  `messaging`, `content`, `notices`, `admin`, `clubs`, `audit`, `roles`.
- Roles: `admin`, `agm_admin`, `agm_id_reviewer`, `auditor`, `checkin`, `clubs`, `finance`, `forms_editor`,
  `payments_finance`, `profile_finance`, `sap_admin`, `sap_finance`, `sap_reviewer`, `senate_admin`, `staff`,
  `verifier`.
- Seeded form versions and hashes:

  | Form | Version | Hash |
  |---|---:|---|
  | `agm_rsvp` | 2 | `007ecdffe087e78c0e5da2cf3ee41a7ef34730dbae0a839498ea0e00f32ebbf7` |
  | `senate_application` | 3 | `eb7b9f5aa2918a74b36144534c8c548fb5ff03154e8473a9ff0933a685638f95` |
  | `student_aid_bursary` | 3 | `b3945ccb921500ea53238038bda448f8c00249e2678f30affd304bad2445468a` |
