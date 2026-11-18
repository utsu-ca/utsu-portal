# Architecture implementation plan

Status: approved; all five structural implementation steps completed and verified.

Follow-on: [plug-in architecture plan](07-plugin-architecture-plan.md) proposes independently installable programs
and a core limited to auth, audit, files, forms, notifications, messaging and email. Its expanded scope is not part
of this completed structural pass.

This plan follows the November 2026 architecture audit. It supersedes conflicting structural proposals in
00-05, which describe earlier migration designs rather than the current application.

## Objectives and invariants

Keep the modular FastAPI application and lazy-loaded Svelte client. Separate platform infrastructure, program
workflows, and historical processing without a rewrite or additional required services.

Structural work must preserve API URLs, role checks, collection names, stored identifiers, form versions,
encryption, audit events, optimistic-lock checks, and existing user workflows. Do not mix file moves with data
migrations. Existing historical processors remain available until replacement and parity are established.

## 1. Neutral bootstrap and canonical deployment

Status: implemented and verified.

- Extract common application construction, middleware, health checks, and resource serving from portal assembly.
- Keep portal, authentication, and optional mail factories as separate composition roots. Importing auth must not
  import the program routers.
- Configure module dependencies before assembling routers, avoiding SSO/program registration-order dependence.
- Use the supported factory-based Docker image and configuration contract for local Compose and Kubernetes.
- Remove or clearly retire conflicting deployment examples and the overwritten alternative auth-router assembly.

Acceptance: isolated auth imports; module-order/SSO regression coverage; existing authentication, audit, rate-limit,
static-site and configuration tests pass; Compose renders with canonical settings and working health endpoints.
Container execution is validated when the local daemon and required images are available.

## 2. Program-owned application policies and SABP responsibilities

Status: implemented and verified.

- Split SABP application, banking, and committee-review responsibilities behind a small router assembler.
- Move SABP request schemas and pure workflow helpers to their owning modules where useful.
- Make generic forms invoke a program-owned submission policy rather than importing SABP or inspecting its
  internal record structure. Policy checks remain inside the optimistic update callback.
- Keep finance/admin banking endpoints on shared banking logic and preserve their distinct role gates.
- Group clubs with the other program modules without changing its API or approval rules.

Acceptance: SABP lifecycle, completed-term overlap, review claims/conflicts, banking re-verification, encryption,
finance access, club approval, and historical-import tests retain existing behavior. Generic forms have no SABP
imports or record-field knowledge.

## 3. One mail transport and explicit template ownership

Status: implemented and verified.

- Retain the public Mailer interface and test doubles, backed by the existing shared SMTP transport.
- Keep program-specific messaging intent in the program; share rendering and delivery mechanisms.
- Package bundled email templates so installed wheels do not depend on the repository working directory.
- Distinguish historical reply-based correspondence from live portal notifications.
- Do not describe the optional mail API as a durable automatic queue/retry worker when it is not one.
- Keep the local SMTP sink in the testing harness, not the production dependency graph.

Acceptance: mail transport/TLS behavior, SMTP sink integration, message rendering, preview-confirm-send and
uncertain-delivery protections pass. Bundled templates load outside the repository. Optional mail API boundaries
are tested without introducing automatic retries of deliveries whose outcome is unknown.

## 4. Canonical pure logic and historical processing boundaries

Status: implemented and verified for live dependency extraction and canonical helpers.

- Extract live redaction and seating constants from historical processors into program-owned pure modules.
- Consolidate equivalent text/identifier helpers behind one implementation, preserving historical import paths
  where compatibility is needed.
- Clearly mark historical SQLite/CSV orchestration and prevent live routers from importing it.
- Retain algorithms, exports, schemas, and historical adapters still needed for parity and migration.

Acceptance: historical and live tests pass; deterministic identifiers, matching semantics and seating results stay
unchanged; live router imports no longer load the old program processors. No historical data is rewritten.

## 5. Frontend feature ownership

Status: implemented and verified.

- Group SABP, Senate, AGM, and clubs views/components/state/tests by feature.
- Keep authentication/session, navigation, branding, layout, and generic form rendering shared.
- Move banking status out of global session state and avoid blocking general application startup on its request.
- Preserve banking action prompts and emailed links, staff mode, standalone pages, and lazy view loading.
- Rebuild and commit-ready the generated static assets; deployment must still require no Node runtime.

Acceptance: existing Vitest tests pass, focused banking startup/session tests cover the changed ownership, Vite
builds, and Python static-site tests serve every entry point and referenced asset.

## Integration and documentation

Run focused suites after each coherent extraction, then the full existing Python and frontend suites. Update
current developer/deployment documentation and link this plan from the README and older design overview.
Record exact verification results and remaining work here. Do not claim production deployment, capacity, or
durable queue guarantees from an in-process test suite.

## Deferred unless required by the extractions

- Additional microservices, microfrontends, plugin frameworks, and a dependency-injection container.
- Database schema/collection migrations, payment reconciliation, and completing the experimental aid workflow.
- Wholesale removal of historical processors or rewriting algorithms during relocation.
- Moving sensitive uploads into the currently separate file-storage library merely for naming consistency.
- Independent frontend-release guarantees beyond the existing lazy, hashed asset build.

## Implementation record

- Plan saved before implementation. Baseline worktree was clean.
- Audit verification: 28 focused Python tests passed; the old Compose configuration ignored its auth/Redis settings
  and defaulted storage to memory; auth imports loaded all three program routers.
- Shared bootstrap now configures all modules before registering routers. Authentication imports no portal
  assembly or program routers; an import-boundary regression test covers this.
- Clubs now lives under `programs/clubs`; the former module path remains a compatibility import.
- Canonical Compose uses the root Python 3.12 Dockerfile, explicit SQL storage, the supported Redis variable, and
  `/readyz`. Conflicting Docker/Compose and auth/mail/ingress manifests are archived under `deploy/legacy`.
  The supported split-auth portal now receives SMTP configuration for its own program messages.
- Live SABP redaction and Senate preference constants no longer come from historical processors. AST-equivalent
  text, identifier, truncated-number, and fuzzy-name helpers now have one implementation with historical re-exports.
  Historical SQLite/CSV adapters remain at their documented import paths; no data was migrated.
- Focused canonical-helper/historical regression suite: 104 passed. Bootstrap/auth/SSO/roles/clubs/audit suite:
  41 passed, including reversed SSO/AGM router order and import-boundary regressions.
- Compose renders with synthetic settings; the rendered application settings use SQL storage and the supported Redis
  URL, with no unintended split-auth configuration. Eight referenced Kubernetes files (18 YAML documents) parse;
  factories, probes and separate SMTP secret mounts are consistent. No cluster deployment was performed.
- SABP now assembles application, banking and review routers. Generic forms use neutral submission policies;
  SABP owns rollover/term-overlap/review rules, while AGM and Senate register roster requirements during configuration.
  The combined program/forms/historical regression suite passed all 65 tests.
- Portal `SMTPMailer` delegates to the shared transport. Verified TLS and the loopback-only plaintext exception are
  retained; disconnected sends are not automatically replayed. Historical correspondence is bundled under
  `mail/templates/historical`, with source-tree compatibility symlinks under `templates/email`.
- The optional mail API atomically claims one-shot dispatch using its existing persisted enum states. Failed or
  interrupted delivery requires provider investigation, not automatic retry. No enum migration or worker was added.
  Mail-focused validation passed 67 tests, including original-enum constraint compatibility.
- Integrated Python regression suite: 468 passed in 42.81 seconds; 14 deprecation warnings, no failures or skips.
- Frontend views, components, state and tests are grouped by program. Shared session startup no longer waits for
  banking status. Banking request failures offer retry/navigation, and cache ownership and stale-response guards
  prevent a replacement session from inheriting another account's result. Vitest: 64 passed across 16 files;
  Vite production build succeeded and generated static assets were rebuilt.
- Final static-site, branding and package-layout suite after the last frontend build: 14 passed. Historical
  templates also loaded successfully outside the repository from the built wheel and installed container package.
- The canonical Python 3.12 Docker image built successfully. Separate temporary portal/auth containers passed
  liveness/readiness checks, disabled-docs checks, bundled-form/auth route isolation and portal/standalone-page asset
  checks. The installed package loaded all eight historical templates, static resources, SQLAlchemy and psycopg;
  auth imports did not load portal/program assembly.
- Docker smoke tests used synthetic secrets, memory stores and loopback ephemeral ports. Task-specific containers
  and image tags were removed; unrelated shared Docker objects were preserved. No production database, SMTP
  provider or Kubernetes cluster was exercised. PostgreSQL enum compatibility is covered by the original-state
  constraint regression, not a live PostgreSQL migration test.
- Final whitespace checks passed; no changes were staged or committed. Existing portal and mail-sink processes
  remain responsive and were not restarted, preserving their in-memory state. New backend code takes effect on
  the next deliberate restart. The deferred product/migration work above remains outside this structural pass.
