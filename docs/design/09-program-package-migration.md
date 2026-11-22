# Program package migration

Status: in progress. This note records the agreed target and an incremental migration plan; it does not imply
that packages have already been extracted.

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
   `.venv/bin/python -m pytest -q tests/test_file_service.py tests/test_clubs.py tests/test_plugin_host.py`:
   50 passed on the current working tree. Receipt storage now receives the club academic-year policy from its
   owner instead of importing the Clubs implementation; the same targeted suite passes with 51 tests. This is
   only the first boundary change, not completion of the stage.
2. **Core contracts and mechanisms.** Complete scoped identity, storage, files, quotas, financial profiles, audit,
   forms and messaging capabilities as required by the programs. Separate program policy from core mechanisms;
   preserve existing retained-data protection and access audits.
3. **Frontend host seams.** Move hard-coded program navigation, views, widgets and deep links behind contributions.
   The compiled host must match installed/enabled plugins, and shared profile features must work without SABP.
4. **Program extraction.** Extract and validate each program as an independently installable package, including
   resources, settings, backend, frontend and tests. Start with Clubs, then SABP; migrate AGM, Senate, chat and
   voting without breaking their dependency contracts.
5. **Branding and composition.** Extract the branding provider and its assets. Provide core-only and full-portal
   builds, independent package metadata, explicit enablement, compatibility and deployment documentation.
6. **Release verification.** Test clean wheel installs outside the checkout, each program alone and together,
   local and split authentication, disabled-plugin behavior, old protected data, APIs, frontend builds and
   rollback. Run the existing full suites and document any remaining gaps.

At every stage the full portal must remain runnable. Complete each stage's tests before proceeding. The earlier
[plugin architecture plan](07-plugin-architecture-plan.md) contains additional migration detail; current code and
this target take precedence where its historical status descriptions differ.
