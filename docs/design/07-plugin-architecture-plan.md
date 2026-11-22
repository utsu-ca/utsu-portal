# Plug-in architecture implementation plan

Status: approved; stages 0–3 are complete; stage 4 (frontend host seams) is next. The core of stage 9 (record
messaging) was pulled forward by [the live chat, discussion and voting plan](08-messaging-discussion-voting-plan.md),
which also added the first typed built-in plug-ins (`discussion` and `voting`, both opt-in).

This is the follow-on to [the completed structural reorganization](06-architecture-implementation.md).
It brings plug-in packaging into scope; it does not reopen the deferred production workflow work.

## 1. Decisions and intended outcome

User-confirmed choices:

- Independently installable Python packages, developed together in this repository.
- One compiled Svelte host, with plug-in frontend contributions included at build time.
- Adding frontend implementation requires a build. Enabling or disabling an already-built plug-in does not require
  a rebuild, but does require validated configuration and an application restart. No hot-unloading.

Revision — core scope and communications, also user-confirmed:

- Forms, notifications, chat messages and email are core services, not optional plug-ins. Programs contribute
  form resources, notification types and message-thread types; core never names a program.
- Notifications: an in-app feed with unread state plus email delivery, with per-user channel preferences.
- Chat: asynchronous applicant <-> staff threads attached to program-owned records, plus staff-only internal notes.
  Live updates use server-sent events (SSE) while a thread is open; no new required services.
- Chat contracts are defined during this migration; chat itself is built as a separate stage after program extraction.

The core must run without any program installed. Its product responsibilities are login/authentication, account
identity and authorization, audit, file handling, forms, notifications, record-linked messaging and email.
Storage, encryption, configuration, rate limiting, the event bus, health checks, static serving and the plug-in host
remain supporting platform infrastructure.
There must be no program lists, program business rules or program-specific settings in core implementation.

Each program owns its backend, frontend contributions, form resources, settings, business rules and tests.
Installing a package does not enable it. Core must not import disabled program implementations.

Plug-ins are trusted, reviewed Python code running in the host process, not sandboxed third-party code or separate
microservices. Scoped contracts prevent accidental coupling; they are not isolation against malicious Python.

## Authorization transition: roles to permissions

Route authorization is moving from named-role checks to named permissions. Users continue to receive the existing
roles, and role-to-permission grants are declared by the owning core module or plug-in manifest. This preserves the
stored grants and admin workflow while route handlers migrate to explicit permissions. Permission identifiers are
namespaced OAuth scope tokens (`utsu:resource:action`); a denied permission check uses the Bearer
`insufficient_scope` challenge. Permission requirements are all-of, matching OAuth scope semantics.
The portal's existing session token remains unchanged; permissions are resolved from the active identity's roles and
are not embedded as token scopes in this increment.

The existing OIDC authorization-code flow retains `openid profile email` as the default for registered clients and
supports per-client-allowed OIDC scopes with claims filtered to the granted scopes. API permission scopes are not yet
exposed to those clients. Any future exposure must preserve audience/resource separation; ID tokens remain identity
assertions, not API authorization tokens. Legacy `require_role` remains as a compatibility adapter while
third-party plug-ins migrate. The separate `authsvc/rbac.py` implementation is unmounted legacy code and is not an
authorization source; it must not be re-enabled in parallel with the manifest-backed permission catalogue.

## 2. Current-source findings

The previous extraction provides useful seams, but these still prevent genuine plug-in independence:

| Current location | Coupling to remove |
|---|---|
| [app.py](../../src/utsu_core/app.py) | Imports every program and constructs fixed local/remote module tuples. |
| [bootstrap.py](../../src/utsu_core/bootstrap.py) | Seeds all bundled forms before module configuration; serves the combined program asset bundle from core. |
| [context.py](../../src/utsu_core/context.py) | Hard-coded program role catalogue; unrestricted settings/store/service dictionary handed to modules. |
| [roles.py](../../src/utsu_core/modules/roles.py) | Request limits, validation and effective role handling depend on that global catalogue. |
| [crypto.py](../../src/utsu_core/crypto.py) | Encryption metadata explicitly names AGM media, club receipts and SABP submission/banking fields. |
| [settings.py](../../src/utsu_core/settings.py) | Contains AGM/Zoom, clubs and SABP configuration. |
| [forms.py](../../src/utsu_core/modules/forms.py), [formdef.py](../../src/utsu_core/formdef.py), [submission policies](../../src/utsu_core/services/submissions.py) | Generic forms already live in core, but all bundled program form resources are seeded globally and programs read/write the shared submissions collection directly. |
| [notices.py](../../src/utsu_core/modules/notices.py) | Fixed AGM/Senate/SABP form/role table. Its generic preview-hash/confirm/uncertain-delivery ledger is the starting point for core notifications. |
| [mailer.py](../../src/utsu_core/mailer.py), [mail/](../../src/utsu_core/mail/), [mailsvc](../../src/utsu_core/mailsvc/__init__.py) | One SMTP transport, but no single core email contract: live mailers, the optional HTTP mail API and historical CSV/SQLite campaign/recipient tooling sit side by side, and live message text is inline in feature code. |
| [events.py](../../src/utsu_core/events.py), [forms editor stream](../../src/utsu_core/modules/forms.py) | In-process or Redis event bus with best-effort publish. The forms editor SSE stream authenticates with the session token in the query string (`allow_query_token`), which must not be copied to applicant-facing streams. |
| Backend and frontend source | No chat, message-thread or in-app notification feature exists yet. |
| [filesvc](../../src/utsu_core/filesvc/__init__.py) | Separate storage abstraction, not the service currently used by live program uploads. Pylance references found only its own implementation/example usage. |
| [App.svelte](../../web/src/App.svelte), [boot.js](../../web/src/lib/boot.js) | Shell mounts SABP banking status and interprets the SABP emailed-link hash. |
| [menu.js](../../web/src/lib/menu.js), [views.js](../../web/src/lib/views.js), [content.js](../../web/src/lib/content.js) | Shared code names every program, action, view and public page. |
| [ProgramPage.svelte](../../web/src/views/ProgramPage.svelte), [StatusView.svelte](../../web/src/views/StatusView.svelte), [AdminVerifications.svelte](../../web/src/views/AdminVerifications.svelte) | Generic-looking views contain Senate, SABP-history and AGM-specific branches. |
| [Vite config](../../web/vite.config.js), [package manifest](../../pyproject.toml) | One backend distribution owns all programs, their resources and their generated frontend assets. |

### Stage 0 baseline (before runtime implementation)

Captured at repository HEAD `8fa2a8016ec6fa4f4bdd020cfec4511ae2db04d1`. The worktree had only the earlier plan edits:
`docs/design/06-architecture-implementation.md` modified and this plan untracked; nothing staged.

- `.venv/bin/python -m pytest -q`: **468 passed**.
- `cd web && npm test -- --run`: **64 passed in 16 test files**.
- No frontend build was run, so generated static assets were not rewritten.
- Default local composition: SSO, local auth, AGM verification, AGM, Senate, SAP, forms, notices, local admin,
  clubs, audit and role administration. Split-auth intentionally omits local auth, admin and role administration.
- Route owners: bootstrap (health/readiness/index); shared auth, forms, notices, admin, audit, roles and SSO modules;
  AGM verification/operations, Senate, SAP applications/banking/review and Clubs program modules. Separate authsvc
  and mailsvc expose their own internal/RBAC/service-auth and mail APIs.
- Portal role IDs: `admin`, `verifier`, `forms_editor`, `checkin`, `agm_admin`, `senate_admin`, `sap_admin`,
  `sap_finance`, `sap_reviewer`, `finance`, `clubs`, `auditor`. The `admin` role is a superset; the role API
  currently caps grants at 12.
- Encryption declarations: `media.face`, `media.id_card`; `agm_media.face`, `agm_media.id_card`;
  `receipts.data_url`; `submissions.data`, `submissions.sap_review`, `submissions.sap_review_hardship`,
  `submissions.sap_previous`; `sap_banking.institution_number`, `transit_number`, `account_number`, `pending`,
  `void_cheque`; `roster_identifiers.digits`; every `audit_*` collection's `blob`.
- Seeded form versions/hashes: `agm_rsvp` v2
  `007ecdffe087e78c0e5da2cf3ee41a7ef34730dbae0a839498ea0e00f32ebbf7`; `senate_application` v3
  `eb7b9f5aa2918a74b36144534c8c548fb5ff03154e8473a9ff0933a685638f95`; `student_aid_bursary` v3
  `b3945ccb921500ea53238038bda448f8c00249e2678f30affd304bad2445468a`.
- Static entry pages: `index.html`, `sso.html`, `checkin.html`, `finance.html`.

These are observed baseline facts, not claims that the architecture stages below have passed.

## 3. Package and ownership boundaries

Proposed layout; package names below are distribution names, with underscore equivalents for Python imports:

```text
packages/
  core/                         # utsu-core; src/utsu_core/ — auth, identity, audit, files, forms,
                                #   notifications, messaging, email
  plugins/
    agm/                        # utsu-plugin-agm
    senate/                     # utsu-plugin-senate
    sabp/                       # utsu-plugin-sabp; runtime id remains "sap"
    clubs/                      # utsu-plugin-clubs
  portal/                       # utsu-portal: full-site composition and compiled web artifact
  legacy/                       # optional retained processing and temporary import compatibility
web/                            # shared Svelte host/build tooling; not a program registry
tests/                          # cross-package and deployment integration tests
```

Each plug-in directory contains its own manifest, Python project metadata, source, resource files, frontend source
where applicable, and focused tests. Reuse the current Python/Svelte toolchains; do not introduce a package manager
or build orchestrator unless the actual multi-package build requires it.

### Core owns

- Login, OTP, recovery, account activation/identity review, account profile and central roster/eligibility APIs.
- Roles/authorization administration and SSO protocol handling, not program-specific SSO eligibility.
- Audit ledger, snapshots/redaction mechanisms, maintenance and anchoring.
- Validated, authorized, encrypted file ingestion/read/delete mechanisms and optional storage adapters.
- **Forms:** YAML/schema validation, immutable versions/hashes, renderer/editor UI, submissions, generic status
  views, the atomic submission-policy mechanism and seeding of resources contributed by enabled plug-ins.
  Core ships no program form resources.
- **Notifications:** typed notification registry, per-user in-app feed and unread state, per-user channel
  preferences, email delivery through the core email service, and the staff preview/confirm/send ledger
  generalized from the current notices module.
- **Messaging:** record-linked threads, participant-visible messages and staff-only internal notes, read state,
  attachments through the file service and SSE live updates.
- **Email:** the single delivery service (current SMTP transport and test/outbox mailers), live message templates,
  a delivery ledger with explicit sent/failed/unknown semantics, and the optional HTTP mail API.
- Generic infrastructure and a small, public, typed `utsu_core.contracts` surface.
- Shared frontend session/API/navigation/layout/theme primitives and core account/auth/audit/file UI, plus the
  forms, notification feed/preferences, thread panel and notice preview/send UI.
  Preserve existing surfaces; this extraction does not require inventing new audit or file-management screens.

### Program plug-ins own

| Plug-in | Ownership |
|---|---|
| AGM | RSVP resources/policy, invitations, attendance/check-in, QR functionality, online-attendance identity review and grants, Zoom configuration/eligibility callback, public/admin/standalone views. |
| Senate | Application resources/policy, seats, preference handling, selection/publication, public results and applicant/admin UI. |
| SABP | Application resources/policy/history, WFNI, review/decisions, hardship redaction workflow, banking/re-verification, finance standalone UI, and program-specific historical/export helpers. |
| Clubs | Registration, membership, approvals, ledger/tax/grants/receipts and all club-facing/admin UI. |

Program-specific historical algorithms/resources travel with their owning optional package, outside its live
entry-point import graph. Shared historical CSV/SQLite orchestration, historical mail campaign/recipient tooling and
historical templates, and compatibility adapters belong in the optional legacy package, never core; the legacy
campaign tooling sends through the core email transport. Inventory all current helpers, including export tools,
before moving them.

### Core forms and communications rules

Programs contribute; core decides nothing program-specific:

- **Forms:** form resources plus `SubmissionPolicy` callbacks. A disabled owner's forms disappear from lists,
  direct form-ID routes and status views; its retained submissions stay protected.
- **Notification types:** stable ID, audience, title/summary template with safe parameters, deep-link target,
  email eligibility and whether email is mandatory. Disabled owners' types and feed items are hidden, not deleted.
- **Thread types:** the record kind, an authorizer that resolves the record's participant(s) and staff role,
  whether the participant may start or reply to a thread, and whether internal notes are enabled.

Notification behavior follows the portal's no-alert direction: no pop-ups, toasts or modal alerts. The feed is
passive, with an unread count. Actionable items link to the exact field or step to fix, such as re-entering banking
details; failure and re-input prompts stay inline in the owning view.

- Email is on by default. Preferences cannot disable account/security email (OTP, recovery, activation) or types
  a program marks mandatory, such as a banking re-verification link.
- Email bodies never contain chat text or sensitive submission content; they follow the current
  "sign in to view" notice pattern.
- Every notification has a dedupe key. Delivery recorded as unknown is never retried automatically.
- The optional HTTP mail API stays optional. This plan adds no required mail service or durable retry worker;
  live email keeps the current send-in-request behavior with recorded outcomes.
- Staff-initiated status emails keep the current explicit preview-hash/confirm rule. Any automatic trigger is
  declared per notification type by its owning plug-in and reviewed individually.

Messaging rules:

- Participants see only participant-visible messages. Internal notes are excluded from participant APIs, streams,
  exports and email at the server, not just hidden in the UI.
- Messages are append-only. Staff redaction is explicit and audited; there is no silent edit or delete.
- Size and rate limits apply per account and thread. Attachments are file-service kinds authorized by the
  thread's owning plug-in.
- Stored messages are authoritative. Event-bus publishes are best-effort hints; clients resynchronize from a cursor
  on reconnect, so live updates work with or without Redis.

Dependency direction: programs -> published core contracts -> core.
Programs must not import one another's implementations. The deployment composition package may name all
selected plug-ins; core may not.

The current scholarship placeholder remains public content, not an invented operational scholarship system.
Its contribution belongs to site configuration or a small content-only optional plug-in, not core.

## 4. Predefined contracts

Contract names are proposed; freeze their initial signatures and versioning rules in stage 1 before moving code.
Use protocols, dataclasses/TypedDicts and existing FastAPI types rather than a new dependency-injection framework.

| Contract | Required behavior |
|---|---|
| `PluginManifest` | Stable ID, package version, supported core API version, dependencies/capability versions, configuration schema, resource ownership, collection/protection declarations and frontend requirements. Serializable metadata, readable without importing program code. |
| `Plugin` | Static descriptor plus `configure(registry)`, router construction from `PluginServices`, and optional explicit lifespan hooks. Configuration registers contributions; router construction cannot mutate frozen registries. |
| `RegistrationContext` | Typed registration for capabilities, roles, collection/protection policies, file kinds, form resources/submission policies, notification types, thread types, SSO clients, UI metadata and extension contributions. Reject duplicate ownership and unresolved dependencies. |
| `PluginServices` | Typed access to auth/identity projections, authorization dependencies, scoped storage, audit, files, forms, notifications, messaging, email, integrity/token operations, events, clock and this plug-in's validated settings. No raw global settings or signing/encryption keys. |
| `AuthService` | Existing local/remote identity semantics, verified/rostered checks and role dependencies. Auth is the only role issuer; navigation visibility never authorizes an API call. |
| `StoreService` | Existing create/replace/update/CAS semantics through declared collections or scoped capabilities; preserve identifiers and bounded retry behavior. Shared collections require an explicit owner/API, not blanket access. |
| `AuditService` | Existing action/target formats, protected snapshots and request attribution. Plug-ins cannot turn off host middleware or weaken audit redaction. |
| `FileService` | Registered file kinds, size/type validation, opaque references, explicit actor/resource authorization, encryption, audited reads and lifecycle operations. No unrestricted authenticated file browser. |
| `FormsService` / `FormContribution` / `SubmissionPolicy` | Contributed resources with immutable version/hash checks, owner-scoped submission reads and atomic updates, policy callbacks inside the CAS update. |
| `NotificationService` / `NotificationType` | Register types during configuration. `notify(type, recipients, params, dedupe_key)` writes feed items idempotently and requests email according to type and preferences. The staff notice ledger provides preview hash, confirm and send with sent/unknown outcomes. |
| `MessagingService` / `ThreadType` / `ThreadAuthorizer` | Open or get a thread for (plug-in, record kind, record ID); post with participant or internal visibility; list by cursor; mark read; redact with audit. The owning authorizer decides access; a missing or disabled authorizer denies. |
| `EmailService` / `IntegrityService` | One delivery port with explicit sent/failed/unknown outcomes and no automatic replay after uncertainty; purpose-scoped token/hash operations without revealing platform secrets. Compatibility adapters preserve existing hashes/tokens. |
| `FrontendContribution` | Versioned IDs for views, navigation/content, shell widgets, deep links, public-page extensions and standalone entry pages. Executable loaders are build-time imports, never server-supplied JavaScript. |

Forms, notification, messaging and email contracts are part of the versioned core API because they are
cross-cutting platform services. Core is still closed to program business features: a new program capability
starts in its plug-in, and only a clearly shared platform service is considered for core.

`FormsService` must support owner-scoped submission reads and atomic updates, including program-owned extension
fields in the existing submission document. Replace direct program access to the shared submissions collection
with that API, without splitting one atomic update across stores. Preserve `SubmissionPolicy` checks inside the
CAS callback. Forms lists, generic submission views and direct form-ID routes must exclude disabled owners.

Version the manifest schema and core contract API explicitly. Initial contracts use API major 1; incompatible
major versions fail before startup. Distribution dependency versions alone do not prove runtime contract or
compiled-frontend compatibility.

The existing untyped `ctx.services` dictionary is an internal migration adapter only, not the new public contract.

The stage-2 typed runtime currently supports declared auth, audit, files, integrity, events, clock and settings
services. Typed storage requires an exclusive collection list in the static manifest; settings expose only declared
values, and file access is owner-bound. Forms, notifications, messaging and email remain typed contracts but are
not yet runtime capabilities: requesting them fails before importing the plug-in, and accidental direct use raises
`CapabilityUnavailable`. The built-in modules still use the temporary legacy-context adapter. The current file
service adapts read/delete operations over the existing encrypted store; upload validation remains in the owning
routes, and no storage or ciphertext format changed.
Do not pass the full mutable application context to newly extracted program code.

## 5. Discovery and lifecycle

Use Python package entry points under a project-owned group such as `utsu.portal.plugins`, with a static manifest
shipped as package data. Locate/read it through distribution file metadata without importing the package or its
Python factory. Do not use an inactive package's import-time resource lookup as a discovery mechanism.

1. Read the host's explicit enabled allowlist and discover installed descriptors.
2. Validate IDs, API/dependency versions, the dependency graph, settings and the selected frontend bundle.
   Installation alone enables nothing; missing/disabled required dependencies are errors, not silently activated.
3. Load only enabled implementation factories. Call every `configure(registry)` in dependency order to collect
   typed providers and contributions. Registration must not send email or perform application writes.
4. Validate/freeze registrations and construct protected storage/services. Protection and role metadata must be
   ready before any provider startup or seeding. The core forms service seeds only resources contributed by
   enabled owners, retaining immutable-version/hash checks.
5. Construct routers and explicitly owned page routes. Reject duplicate method/path ownership, conflicting mounts
   and attempts to take core-owned auth/audit/file/internal routes before accepting traffic.
6. Run declared, dependency-ordered lifespan startup; expose readiness only after successful initialization.
   Fail with the plug-in ID and actionable cause, without secret values. Close started resources on failure/shutdown.

Keep the configure-all-before-router invariant. Neither dependency handling nor SSO eligibility may depend on
accidental discovery order. No runtime pip installation, directory scanning for arbitrary executable files,
automatic migrations, silent partial startup or hot-unload mechanism.

Provide two explicit compositions:

- **Core-only:** auth, audit, files, forms with no program resources, notifications, email, and messaging once
  built; no program routes, notification/thread types or program frontend requests.
- **Full portal:** explicit enabled list, initially AGM/Senate/SABP/clubs, with the current public URLs.

The split-auth factory loads core and the deployment's authorization metadata, not program implementation code.
It sends account/security email through the core email service; feeds, threads and program forms stay in the
portal process.
Keep current factory names as compatibility entry points where possible; document the explicit full-portal profile
so changing the default composition does not silently remove workflows from existing deployments.

## 6. Role, data-protection and file boundaries

### Roles must work in split-auth mode

Programs declare their role metadata instead of editing a core constant. Build a validated, non-secret role catalogue
for the deployment which both auth and portal can consume without importing programs into auth. Check compatibility
at deployment/readiness. Preserve existing role IDs, legacy admin behavior and accounts' stored grants.

Disabling a program removes its routes and grantable actions, not historical account grants. Do not silently drop
stored grants when serializing accounts or editing unrelated roles. Unknown grants are not effective permissions.
Move the current role-catalogue-dependent request bounds/validation away from import-time constants.

### Disabling must not disable encryption

Program manifests declare collection ownership and field/JSON protection policies, including protections for their
extension fields in shared submission documents. Core declares the protections for its own forms, notification,
messaging and email collections and enforces all of them through generic machinery. Shared submission documents
retain the union of required protections.

Maintain a deployment-owned protection catalogue for retained data independently of the active plug-in list.
Disabling or uninstalling code must not remove policies for stored banking details, scans, receipts or historical
submission fields. Reject policy weakening/missing required metadata; retain compatible descriptors until an
explicitly reviewed data-decommission step. Audit protection remains mandatory.

Preserve current collection names, keys, form IDs/versions/hashes, ciphertext format/key derivation, JSON encoding,
audit events, role IDs and atomic update boundaries. Do not combine source relocation with data rewrites or an
object-storage migration. Any new metadata persistence needs a separate, additive and rollback-safe decision.

### File handling becomes a real core service

Introduce an encrypted-document adapter over the current live storage layout first. Wrap the existing file-storage
abstraction behind the same contract only after its authorization, validation and error semantics are compatible.
Do not switch live uploads to raw external storage merely because the abstraction exists.

Programs own resource authorization and business lifecycle: for example, who can review a void cheque or club
receipt and when an AGM scan must be purged. Core owns the safe mechanism. A disabled/missing authorization provider
must deny a read, not fall back to "any signed-in user." Keep the existing upload/read URLs and audited behavior.
Provider moves, retention-policy changes and direct signed-URL delivery are separate work.

### Communications data

- Keep `notice_delivery` unchanged: same collection, HMAC preview digest keys and audit actions
  `notice.sent`/`notice.delivery_unknown`. Existing notice routes keep working through registered types.
- Proposed new core collections: `notifications`, `notification_prefs`, `message_threads`, `messages`, and an
  email delivery ledger for core-sent mail. All are additive; no existing data is rewritten.
- Message bodies, internal notes and notification parameters use core field protection. Message attachments use
  file-service encryption. These protections sit in the retained-data catalogue, so they survive a disabled owner.
- Feed items store a type ID and safe parameters, not copies of sensitive submission values.
- Message posts, redactions, preference changes and notice sends are audited. Read markers and preference
  updates use CAS updates.

## 7. Compiled frontend plug-ins

Move each program's frontend implementation alongside its backend package. The shared Vite host consumes selected
workspace contributions and generates an explicit lazy-loader registry and versioned build manifest.
Keep current JavaScript/Svelte patterns; a TypeScript migration is not required.

Replace hard-coded shell behavior with contribution slots:

- Navigation groups/actions, public content and related links.
- Lazy views and role-sensitive actions.
- Shell widgets such as the SABP banking-status prompt, with cleanup on session change/unmount.
- Deep-link handlers such as `sap-banking`, processed at the existing post-auth point without logging token values.
- Public-page sections such as Senate results.
- Shared-form status extensions such as SABP history.
- Reusable identity-review presentation with program-owned data/endpoint adapters, not an AGM branch in core UI.
- Owned standalone pages: AGM check-in and SABP finance; SSO remains core.
- Notification deep-link targets and per-type display, routed through the owning plug-in's view.
- Thread panels mounted on program record views, using the core thread component.

Core UI owns the forms renderer/editor/status views, the notification bell/feed and preferences page, the thread
component and the notice preview/send screen. The bell is a passive unread count, not an alert surface.

SSE streams for applicants never put the long-lived session token in a URL. Stage 1 chooses one mechanism:
a short-lived, purpose-scoped stream ticket from `IntegrityService`, or fetch-based streaming with an
`Authorization` header. The existing forms editor stream may move to the same mechanism with a compatibility window.
**Chosen:** fetch-based streaming with the `Authorization` header (`web/src/lib/stream.js`), used by the core
message stream. The forms editor stream still uses its query token and has not moved yet.

The backend exposes a non-secret active plug-in/capability manifest. The host enables only matching contributions
from its compiled registry. An enabled plug-in requiring missing/incompatible frontend code is a deployment error,
not a silently incomplete menu. Backend-only plug-ins may explicitly have no UI requirement.

Keep one Svelte runtime, same-origin assets, CSP and the current lazy/hash-based build. Disabled plug-ins must not
mount widgets, fetch APIs or expose standalone HTML entry points, even when their chunks remain in the compiled
bundle. Public static JavaScript is not an authorization boundary; the APIs remain authoritative.

The core Python wheel must not contain a full program bundle. Build the same host in a core-only profile for core
UI (auth, forms, notifications, messaging) and a selected full-portal profile whose generated assets belong to the
deployment package. Deploy one
matching profile, not separate runtime microfrontends. Commit the required generated assets, preserving installation
and deployment without Node. Adding/changing frontend code requires a build; compatible enable/disable changes
within that built profile require only configuration/restart.

An explicit API-only host mode may omit the web bundle; it does not pretend to serve a complete browser portal.
Clean-wheel per-program backend tests use that mode. Browser deployments must pass the frontend-manifest checks.

## 8. Implementation stages and acceptance gates

Implement one coherent stage at a time; each stage must leave a runnable, tested full-portal profile.

| Stage | Work | Gate before proceeding |
|---|---|---|
| 0. Baseline | Snapshot current routes, role IDs, protection policies, form hashes, factory behavior and standalone assets. Inventory legacy helpers and external imports. Add boundary-focused tests, not speculative new product tests. | Record the actual current suites; no assumption that plan 06 counts still describe HEAD. |
| 1. Contracts and host | Add versioned contracts, including forms, notification, messaging and email contracts, static descriptors, entry-point discovery, explicit allowlist, dependency validation and typed registration/lifecycle. Choose the SSE stream-authentication mechanism. Initially adapt existing modules. | Core-only startup; inactive factories not imported; missing deps/cycles/duplicate IDs/routes fail explicitly; ordering and startup-cleanup tests. **Implemented and validated:** static host metadata, legacy module loading, allowlist, API-only mode, form ownership and lifecycle. The typed runtime adapter and capability gates were added in stage 2. |
| 2. Core boundaries | Register roles/settings/protection metadata; introduce typed core services and the compatible file adapter; remove program conditionals from platform implementation. | Split-auth recognizes declared roles without importing programs; old data decrypts and stays protected with owners disabled; file authorization/encryption/read-audit parity. **Complete:** static role, setting and protection declarations; active role grants filtered per composition without deleting stored grants; retained protection policies remain independent of enabled plug-ins; typed plug-ins get declared, scoped services, exclusive store collections, owner-bound file kinds, and explicit pre-import rejection for unimplemented future services. Legacy file reads/deletes now pass through authorization and audit adapters while retaining encrypted store shapes and existing routes. Validation: **487 backend tests passed**, `git diff --check` passed, and the Python wheel built successfully. |
| 3. Core forms and email | Make forms owner-scoped and contribution-seeded; move submission contracts into `utsu_core.contracts`; move program form resources to their owners. Put live email behind one `EmailService` and delivery ledger; move live message text into templates. Replace the notices program table with registered notification types, preserving routes, preview hashes, ledger records and uncertainty handling. | **Complete and validated:** active plug-ins contribute their own form resources; all program submission reads and CAS updates use owner-scoped `FormsService`; direct form routes and notice types disappear when owners are disabled. Core routes no longer contain a fixed program-notice table. OTP, SABP banking re-verification and staff notices use templated `EmailService` delivery with sent/failed/unknown ledger outcomes; uncertain sends are not retried. Historical campaigns remain separate. Existing form IDs, submission shapes and SABP approval/review data remain unchanged; no general data migration was added. Validation: **490 backend tests passed**, wheel contents verified, and `git diff --check` passed. |
| 4. Frontend host seams | Add contribution registry/build manifest and core-only/full-portal profiles; adapt current views behind it; remove program imports/branches from shell code. | Disabled widgets/pages/API calls absent; deep links, session cleanup, banking failure UX, menus/sidecar/staff mode and standalone URLs preserved. |
| 5. Program extraction | Move Senate first as an end-to-end reference, then clubs, AGM, and SABP. Move each one's Python, resources, frontend, config and tests together. Preserve algorithms, URLs and persisted IDs. | For each: wheel install outside checkout, run alone with its declared dependencies and in the full profile; prove enable/disable isolation and existing behavior. |
| 6. Packaging/cutover | Move historical orchestration and mail campaign/recipient tooling out of core; package compiled profiles; update factory/profile config, Docker/Compose/Kubernetes, test setup and developer docs. | Clean core-only and full-portal installs work without source-path leakage or Node. Do not add general data migrations or compatibility import adapters; preserve existing SABP approval/review data and record shapes. |
| 7. Integrated release check | Run contract tests, complete existing Python/frontend suites, full build/resource/container checks and the supported local/split-auth compositions. Record limitations and rollback instructions. | No core/program reverse imports, no hidden program role/settings/resource/asset dependency, all existing behavior retained in the full profile. |
| 8. In-app notifications | Add feed, unread count and preferences APIs/UI; add the email channel through `EmailService`. Programs register their types, such as SABP banking re-verification and application status. Keep staff status emails on preview/confirm. | Dedupe/idempotency; preferences respected except mandatory types; no sensitive content in email; no toast/modal alerts; disabled owners' items hidden but retained; unknown delivery never replayed; keyboard/screen-reader access. |
| 9. Record messaging | Add threads/messages APIs, SSE live updates with cursor resync, the thread component and opt-in thread panels for program records. Add internal notes, file-service attachments, read state and new-message notifications without message text in email. | Participants never receive internal notes through API, stream, export or email; a missing/disabled authorizer denies; works with the in-process bus and with Redis across replicas; size/rate limits; audited redaction; CAS read markers; full suites and builds pass. **Core done early (plan 08):** threads, messages, internal notes, read markers, audited redaction, limits, the header-authenticated SSE stream and `ThreadPanel.svelte`, used by discussion topics. **Still to do:** thread panels on program records, file attachments and new-message notifications. |

Stages 8 and 9 are new product features with their own gates. They start only after stage 7 passes, so the
platform extraction can be released and rolled back independently of them.

Do not introduce a general data migration or legacy data compatibility path. Existing SABP approval decisions,
review history and `sap_previous` snapshots are the explicit preservation exception: keep their stored shape and
CAS semantics intact. Do not leave a second permanent implementation of the same role, encryption, forms, email
or file service.

Rollback is package/configuration rollback, not a database restore after an implicit migration. Retain existing data
formats, especially SABP approval records. Never reset unrelated changes or restart the existing in-memory
development service implicitly.

## 9. Definition of done

- A clean install of core starts login/auth/audit/files/forms/notifications/email, and messaging after stage 9,
  with zero program dependencies or program assets.
- Each of the four program wheels depends only on published core contracts.
- A new minimal test plug-in can add a protected route, a form resource, a notification type, a thread type and an
  optional UI contribution without editing core code.
- Full-portal deployment preserves current routes, roles, form resources, encrypted data and application workflows,
  including SABP approval decisions and review history without conversion.
- Disabled programs have no live routes, form entry points, notification/thread types, frontend hooks or background
  activity; retained data, notifications and messages remain protected and no roles or records are destroyed.
- Split-auth stays program-import-free while authorization metadata remains consistent.
- Both compiled profiles load installed resources outside the checkout; supported deployments need no Node runtime.
- Tests cover lifecycle errors, API/build version mismatch, auth/file denial, concurrency, disabled-data protection,
  notification dedupe/preferences and internal-note isolation, alongside the existing program regressions.

## 10. Explicitly outside this implementation

- Completing SABP production workflows or payment reconciliation. General historical data migration is out of scope;
  existing SABP approval and review data must remain readable and unchanged.
- New payment behavior, changed approval rules, identifier/schema rewrites or new retention rules.
- A plug-in marketplace, untrusted-code sandbox, runtime frontend federation or extra required microservices.
- A durable mail worker, hot installation/unloading or arbitrary administrator-supplied executable code.
- WebSocket chat, presence/typing indicators, direct member-to-member chat or a general support inbox not tied to a
  record. (Plan 08 narrows this: discussion topics are records owned by the discussion plug-in, so their
  member-visible threads are record-linked and in scope.)
- SMS/push notifications, email digests, or automatic status emails that bypass the per-type review.

Next action: begin stage 4 by adding the frontend contribution registry and build manifest while preserving the
existing compiled client and standalone entry points. Stage 3 did not rewrite persisted submissions, change
SABP approval records, alter dependencies or regenerate frontend assets.
