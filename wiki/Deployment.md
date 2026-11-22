# Deployment (Cloud Run + Firestore, and how to avoid lock-in)

The portal is a stateless container configured only through `PORTAL_*` environment variables. All GCP-specific code lives
in `deploy/gcp/deploy.sh`; the application never imports a cloud SDK except the Firestore adapter, which is loaded only
when `PORTAL_STORE=firestore`.

## Supported entry points

The root [Dockerfile](../Dockerfile) is the one supported image for the portal and authentication service.
`PORTAL_APP` selects `core:create_app` (default) or `core.authsvc:create_auth_app`. Both use the shared
bootstrap, `/healthz` for liveness, and `/readyz` for readiness. `/docs` is deliberately disabled.

The root [Compose configuration](../docker-compose.yml) runs a single portal application with PostgreSQL and Redis:

```sh
export PORTAL_SECRET="$(openssl rand -hex 32)"       # save securely and reuse across restarts
export PORTAL_DB_PASSWORD="$(openssl rand -hex 24)"  # URL-safe; save and reuse for the database volume
export PORTAL_ADMIN_EMAILS="admin@example.com"
# Set PORTAL_SMTP_URL and PORTAL_MAIL_FROM to a working TLS SMTP provider before testing sign-in.
docker compose up --build
```

Only the portal is published, on `127.0.0.1:8000` by default (`PORTAL_PORT` overrides the host port). PostgreSQL data
uses a named volume; Redis is transient. This is a local HTTP stack with HSTS disabled, not a production ingress.
The database password is embedded in a connection URL: use URL-safe characters as generated above.

For a local no-delivery inbox, run the portal natively with `python testing/mail_sink.py` as described in the
[README](../README.md); the SMTP exception for plaintext sinks applies only to loopback hosts. Inside a container,
`127.0.0.1` is that container, not the host. Compose does not pretend an unconfigured mail service can deliver codes.

For split authentication, use the [Kustomization](../deploy/k8s/kustomization.yaml). Superseded multi-service
Docker/Compose/RBAC manifests are retained under [deploy/legacy](../deploy/legacy/) and are not supported entry
points. The optional mail API is not the portal's configured delivery transport and has no durable automatic worker.
Optional observability examples are not automatically initialized by the application.

## Deploy to GCP
In Google Cloud Shell (billing-enabled project): `./deploy/gcp/deploy.sh`. It is safe to re-run and, like
[agm-gcp-runbook](https://github.com/utsu-ca/agm-gcp-runbook), it: enables APIs, creates a native-mode Firestore database
and a least-privilege service account (`datastore.user`), deploys Cloud Run with ingress limited to the load balancer, and
creates Cloud Armor (SQLi/XSS WAF, per-IP throttle), a global HTTPS load balancer, static IP and managed certificate.
Differences: applicants are the public so there is no site-wide IP allowlist (set `ADMIN_IPS` to restrict only
`/api/admin` and the check-in page); secrets (`PORTAL_SECRET`, the SSO signing key, SMTP URL, Zoom client secret) are
generated once into Secret Manager and mounted as env vars. **Never rotate `PORTAL_SECRET`**: it peppers all uuid hashes.

## Configuration
| Variable | Purpose |
|---|---|
| `PORTAL_SECRET` (required) | stable pepper for username/student-id hashes and tokens |
| `PORTAL_STORE` | `memory`, `sqlite:<path>`, `sql:<url>` (or `sql` + `DATABASE_URL`), `firestore`, or `pkg.module:factory` |
| `PORTAL_ADMIN_EMAILS` | emails that become admin once verified by OTP |
| `PORTAL_APPLICANT_EMAIL_DOMAINS` | optional comma-separated exact primary email domains (e.g. `mail.utoronto.ca`); applies to new applicants and primary email changes, not backup emails, existing sign-ins, or new legacy bootstrap admins |
| `PORTAL_COMMIT` | source revision shown with the package version in the site footer (from `GET /api/version`); `deploy.sh` sets it from `git rev-parse HEAD`, and a development checkout falls back to git when unset |
| `PORTAL_TURNSTILE_SITE_KEY`, `PORTAL_TURNSTILE_SECRET_KEY` | Cloudflare Turnstile keys required for registration; register the `PORTAL_BASE_URL` hostname with Cloudflare. The secret supports `_FILE` and must remain private. Registration fails closed without both keys, except in explicitly enabled local dev mode without keys. |
| `PORTAL_DEV_ADMIN` | local development only: `1` seeds the `devadmin` administrator. Startup fails unless `PORTAL_BASE_URL` is localhost (and with Firestore or split auth). While that account exists, the UI is red and named "ACTIVE DEV LOCAL, DO NOT USE". Never set it in a deployment |
| `PORTAL_BASE_URL`, `PORTAL_SSO_KEY` | SSO issuer URL and RSA private key (PEM) |
| `PORTAL_GOOGLE_STAFF_DOMAINS` | comma-separated exact Workspace domains eligible for staff accounts |
| `PORTAL_GOOGLE_CLIENT_ID`, `PORTAL_GOOGLE_CLIENT_SECRET` | optional Google OAuth web credentials; mount the secret or use its `_FILE` variant |
| `PORTAL_GOOGLE_BOOTSTRAP_ADMIN_EMAIL` | optional exact Workspace email eligible for the one-time initial `admin` grant |
| `PORTAL_SMTP_URL`, `PORTAL_MAIL_FROM` | OTP, program notices, and banking re-verification. Without it only recipient and subject are logged, not codes. `smtp://` requires STARTTLS except on a loopback host that doesn't offer it (e.g. `testing/mail_sink.py`) |
| `PORTAL_ZOOM_*` | see the AGM section of [Applicant portal](Applicant-Portal.md); the former `PORTAL_AGM_VERIFY_MODE=auto` setting no longer bypasses human review |

`GET /healthz` is the liveness probe. The container listens on `$PORT`.

Leave `PORTAL_APPLICANT_EMAIL_DOMAINS` unset to allow any valid applicant primary address. In split mode set this
on the authentication service. The backup email uses the same OTP mail transport as primary sign-in and is encrypted
in the auth datastore; it does not replace the primary address for program communications.

### Staff and administrator sign-in

The GCP deploy script asks whether to enable Google Workspace, which domains may sign in, and which **one exact
work email** should receive initial admin access. It prints the OAuth redirect URI and prompts for the web client
credentials (the client secret is stored in Secret Manager). For non-interactive GCP deploys, preset
`GOOGLE_STAFF_SSO=yes` and the variables above, including the initial email or `none` if already set up. Set
`PORTAL_ADMIN_EMAILS=none` to skip legacy bootstrap; this does not remove any existing admins.

Create a Google OAuth **Web application** and register the exact redirect URI `https://<portal-host>/`, including
the trailing slash. `PORTAL_BASE_URL` must be the public portal origin, even when the auth service runs elsewhere.
Set the Google credentials, domain list and initial email on the auth issuer only (the auth Deployment in split
mode); mount the client secret from that platform's secret store. The browser returns to the portal and completes
sign-in through the existing `/api/auth` ingress. Both applicant email codes and outgoing AGM Zoom SSO remain
independent. Google sign-in start and completion are additionally limited to at most 20 POSTs per client IP and
rate window; the shared `PORTAL_RATE_LIMIT_AUTH` limit still applies, and setting it to `0` disables both limits.

The initial address must be in an allowed domain, must sign in with Google, and must not already belong to a
portal account. Once used, the grant is recorded in the auth datastore; changing the configured address cannot
grant a second admin, and later sign-ins do not restore revoked roles. Remove the bootstrap setting after setup.
Everyone else starts with baseline `staff` and no operational or administration privileges. An administrator
can grant additional roles under **Administration → Role Management**. The portal never links accounts by email:
if an existing email-code admin's address already belongs to that account, keep using it to grant roles to a
separately provisioned Workspace account. Removing a domain blocks new sign-ins; for immediate offboarding,
revoke privileged portal roles as well, since existing sessions otherwise remain valid until expiry.

## Database portability
Everything goes through the `Store` interface (`get/put/delete/list/create/replace/page`; `create` and `replace` must be atomic). Built in:
- **Firestore** (wrapped in `ChunkingStore`, which shards values over Firestore's 1 MiB document limit, e.g. receipts and scans).
- **SQL** via SQLAlchemy: the `portal_docs(col, id, body)` table and auxiliary page-index tables are created automatically, so there are no schema migrations.
  - Supabase / any PostgreSQL: `PORTAL_STORE=sql:postgresql+psycopg://USER:PASS@HOST:5432/DB` (psycopg is in the image).
  - Azure SQL / SQL Server: `sql:mssql+pyodbc://...?driver=ODBC+Driver+18+for+SQL+Server` (install `pyodbc` and the ODBC driver in a derived image).
  - MySQL: `sql:mysql+pymysql://...`.
- **Custom**: implement `Store` and point `PORTAL_STORE` at `my_pkg.module:factory`.

`list()` filters by top-level equality. Collection-backed API lists use indexed keyset pages: SQL stores backfill one
sort projection per collection/order/filter shape on first use and keep it synchronized with writes; Firestore uses
native ordered queries and backfills casefold fields once when needed. Subsequent pages seek from the cursor rather
than loading and sorting the collection again. The suite runs the same store contract tests on every backend
(`tests/test_stores.py`).
Firestore may require composite indexes for some equality-filter/order combinations; provision the indexes indicated
by Firestore before enabling those list endpoints in production.

## Upload quotas and cold receipt storage

The portal, not rclone, owns authorization, audit records, file IDs and quota reservations.
Defaults are 15,000,000 decoded bytes per person and 1 GiB per club approved academic year,
capped at 10 GiB. A club has at least the starting allocation while pending. Only distinct approved years
count; elapsed time and rejected renewals do not. Existing clubs retain their known approval year; lost historical
renewals cannot be inferred and must be restored from verified registration records into `registered_years`.
Changing `PORTAL_CLUB_STORAGE_YEAR_BYTES` changes the annual increment, not the fixed 10 GiB ceiling.
`PORTAL_USER_STORAGE_BYTES` changes the personal quota. Base64, encryption overhead and duplicate hot/cold copies
are not charged; archived originals are. Over-quota legacy records remain readable, deletable and migratable,
but cannot increase their usage.

The optional receipt pilot stores encrypted opaque blobs outside the database. Receipt metadata retains owner,
decoded size, content and ciphertext checksums, upload academic year (September boundary, Toronto), storage tier,
and last successful access. It preserves IDs, permissions, audit reads, de-duplication and transaction links.
The old `FILE_STORAGE` adapter is not used by portal uploads.

| Setting | Purpose |
|---|---|
| `PORTAL_QUOTA_STORE` (or `_FILE`) | Persistent shared `Store` URI; required for split auth and portal deployments. Both services must use the **same** URI even when their application databases differ. |
| `PORTAL_RECEIPT_STORAGE_PATH` | Private durable hot directory shared by portal replicas and the worker. Unset keeps existing database storage. Compose supplies a named volume automatically. |
| `PORTAL_RECEIPT_COLD_REMOTE` | rclone `remote:path`, e.g. `archive:/srv/portal-receipts` on a private SFTP node. |
| `PORTAL_RECEIPT_ARCHIVE_ENABLED` | Defaults to `0`: report candidates without copying/deleting. Set to `1` for scheduled archiving. |
| `RCLONE_CONFIG` | rclone config path, mounted read-only into both portal and worker (cold reads also use rclone). |

For a local storage node, use an SSH/SFTP server with a dedicated restricted account and a durable archive disk;
no custom Node server or publicly accessible rclone API is needed. Configure an rclone SFTP remote, including a
verified host key/known-hosts file. Prefer SFTP (or explicitly configured FTPS), never plaintext FTP for receipts.
Keep its credentials and SSH files in `secrets/storage`, not in source control; paths in rclone config must refer
to their container locations under `/run/secrets/storage`. Restrict host/container access so UID 10001 can read
the config and key files; do not make keys world-readable. The storage node needs adequate disk space and backups.

With the Compose deployment:

1. Create `secrets/storage/rclone.conf` with your `archive` remote using `rclone config`; include required key and
   known-hosts files in that directory. Set `PORTAL_RECEIPT_COLD_REMOTE` in your existing environment file.
2. Start the portal with the cold-storage mount and optional nightly worker:

   ```sh
   docker compose --env-file secrets/dev.env -f docker-compose.yml -f docker-compose-storage.yml \
     --profile storage up --build -d
   ```

   Include `-f docker-compose-dev.yml` for the development stack. Migration runs nightly even in report-only
   mode, moving legacy database receipts to encrypted hot files; new uploads already use the hot volume.
3. Inspect migration/archive candidates without enabling deletion:

   ```sh
   docker compose --env-file secrets/dev.env -f docker-compose.yml -f docker-compose-storage.yml \
     run --rm receipt-maintenance python -m core.receipt_maintenance --migrate
   ```

4. After checking the report, testing the remote and backing up the hot directory/database, set
   `PORTAL_RECEIPT_ARCHIVE_ENABLED=1` and recreate **both** portal and worker with the same Compose command.
   For an immediate run, use the previous command with `--migrate --apply`.

Candidates are receipts from any previous upload academic year **OR** receipts with no successful access for
365 days. New/migrated receipts start with a current access timestamp when no historical timestamp exists.
The worker copies, reads back and verifies the ciphertext, then compare-and-swaps the database tier before
removing the hot original. Concurrent record changes retain the hot copy. An unavailable/corrupt remote records
a per-receipt error and retains hot data; other candidates are still attempted and the job exits nonzero.
A crash after committing the cold pointer leaves a redundant hot copy;
the next applied job verifies cold storage again before cleaning it up.
Cold downloads are authorized/audited through the portal and fail explicitly if the node is offline.
This is tiering, **not a backup or a retention/deletion policy**; back up encryption keys, metadata and both tiers.

Personal scans/cheques remain in their existing encrypted records. All upload writes reserve decoded-byte quota
atomically in the shared quota store, so concurrent uploads cannot over-allocate. Lazy backfill accounts for legacy
files at startup and before a new owner's first write. Stop uploads during the initial rollout; start both auth
and portal against the shared quota store before reopening writes so both sets of historical files are counted.
Quota-store outages fail writes/startup rather than granting unmetered storage.

Interrupted writes retain conservative reservations instead of expiring them unsafely. Stop all writers before
rebuilding an affected owner's quota:

```sh
python -m core.receipt_maintenance --reconcile-owner user:UUID --quota-scope all
```

Use `club:ID` for clubs. For split deployments, run with the portal environment and `--quota-scope portal`, then
with the auth-service environment and `--auth-service --quota-scope auth`; both share the same quota URI.
Never reconcile while either service is accepting writes. Direct database edits bypass metering and also require
offline reconciliation. Kubernetes deployments must mount the same persistent hot storage and rclone credentials
in portal and worker pods; `emptyDir` or per-pod disks are not suitable. The provided split manifest expects a
matching `quota-store` secret in both services; the separate quota database must be reachable by both.

## Moving off GCP
Build the image (`docker build .`) and run it anywhere (Azure Container Apps, Fly, Kubernetes): set the env vars above,
supply secrets from that platform's vault, and choose a store. Two things to know when scaling past one instance:
realtime admin sync uses an in-process `EventBus` (swap it for Redis/pub-sub), and the Cloud Armor rules should be replaced
by your platform's WAF. Until then `deploy.sh` pins `--max-instances=1`.

## Scaling out: Redis
Set `PORTAL_REDIS_URL` (e.g. `rediss://:PASSWORD@HOST:6379`) and every replica shares (a) the realtime admin-sync bus
(pub/sub behind the SSE endpoint) and (b) the rate-limit counters (`/api/auth/*`, `/api/recovery*`, `/sso/*` POSTs,
`PORTAL_RATE_LIMIT_AUTH` per minute per client IP, default 30). Without it both are in-process, so run a single replica
(this is why `deploy.sh` defaults to `MAX_INSTANCES=1`; raise it after adding Redis/Memorystore). Redis holds nothing
durable. Realtime publish failures are logged, never fail the write. The rate limiter fails *open* if Redis is down (OTP
attempt limits still apply); `/readyz` reports Redis health.

## Standalone authentication service
The auth API runs independently of the portal: `PORTAL_APP=core.authsvc:create_auth_app` (same image) or
`uvicorn --factory core.authsvc:create_auth_app`. It owns accounts, users, OTPs, identity-verification review, the
session-signing key, its own store and audit log (`/api/admin/auth-audit`).

| | Auth service | Portal (`PORTAL_AUTH_URL` set) |
|---|---|---|
| Holds accounts / users / scans | yes | **no** (modules that mutate them are not even mounted) |
| Session token | signs (Ed25519, `PORTAL_AUTH_SIGNING_KEY`) | verifies offline with `PORTAL_AUTH_PUBLIC_KEY` only; cannot mint sessions |
| Live status/role, name lookups | serves `/internal/*` | calls it (cached 15 s, **fails closed** if unreachable) |
| Secrets | own `PORTAL_SECRET`, data key, DB, SMTP | own `PORTAL_SECRET`, data key, DB, SMTP; plus `PORTAL_SERVICE_TOKEN` |

`/internal/*` requires `PORTAL_SERVICE_TOKEN` (≥32 chars, constant-time compared; returns 404 otherwise or when unset),
exposes a minimal projection (no hashes or lookup keys), and must never be routed publicly (see `deploy/k8s`). In a
single-process deployment nothing changes: the same Ed25519 tokens are signed with a key derived from `PORTAL_SECRET`.
Use a short `PORTAL_TOKEN_TTL` (e.g. 900) in split mode. `PORTAL_AUTH_URL` must be https or a cluster `.svc` address.
Route `/api/auth`, `/api/me`, `/api/verification`, `/api/recovery`, `/api/admin/verifications`, `/api/admin/auth-audit` to
the auth service and everything else to the portal (`deploy/k8s/ingress.yaml`). Rotate signing keys by giving the portal
two concatenated public PEMs, then switching the auth service key.

Both deployments need their own SMTP configuration: auth sends verification codes, while the portal sends
program notices and banking re-verification messages. Mount `smtp-url` in each service's separate secret. The portal
must never receive the auth signing key.

The portal's built-in program plug-ins are enabled by default, except the opt-in `discussion` and `voting`
plug-ins ([Discussions and Voting](Discussions-and-Voting.md)). Set `PORTAL_ENABLED_PLUGINS` to a comma-separated
allowlist of program plug-ins to control backend modules (for example, `clubs` or `agm,senate,sap,clubs`; add
`discussion,voting` to the full list to run them). Core modules are always on and must not be listed. Core authentication,
forms, and audit modules are not optional. AGM verification requires `agm`, and voting requires `discussion`.
Dependencies omitted from the allowlist cause startup to fail. Plug-in discovery reads installed
metadata before importing implementations, so disabled program modules are not loaded. Disabled program roles are
removed from the grantable/effective role catalogue, but existing stored grants are retained; retained-data
encryption policies also remain active independently of the allowlist. The default bundled frontend contains all
program code, while its navigation uses `GET /api/plugins` to hide disabled actions. For a smaller browser build,
use `npm run build:core` or `PORTAL_FRONTEND_PROGRAMS=clubs npm run build:subset` in `src/core/frontend/`, then point
`PORTAL_STATIC_DIR` at the resulting directory. Select frontend contributions that match the backend allowlist;
an empty `PORTAL_ENABLED_PLUGINS` selects no programs, and AGM verification shares the AGM contribution.
The portal warns if an enabled program declaring `"frontend": true` is missing from its frontend build. List external
frontend packages in `PORTAL_FRONTEND_MANIFEST` at build time to include them in the composition.
Core-only UI deployments without the default branding package also
need `PORTAL_BRANDING_DIR`. See [frontend builds](../src/core/frontend/README.md) for available IDs and dependency requirements.
Changing the allowlist or static directory requires an application restart.

Typed program packages may declare their own configuration keys in their manifest. Supply values as a JSON
object keyed by program ID in `PORTAL_PLUGIN_CONFIG`, or mount that JSON in a file and set
`PORTAL_PLUGIN_CONFIG_FILE`. For example, `{"example":{"limit":5}}` supplies `limit` only to `example`.
The package validates its values and defaults; undeclared keys for enabled programs fail startup. Configuration
for disabled programs is retained but not passed to other programs. Existing `PORTAL_*` settings remain valid.

External upload-quota providers are supported through the durable retained-policy catalogue in the quota
store. Core declares each policy (name, collections, item prefixes, version, fingerprint) with a monotonic
fencing epoch. Removing or disabling a provider fails startup closed while its retained records still need
it; a policy that changes shape without a catalogue migration also fails closed. Every writer checks the
epoch before reserving quota, so a stale process refuses writes after a catalogue change and must restart.
The quota store must be a persistent, shared, atomic backend (SQLite, SQL, Firestore, or a custom `Store`
overriding `create`/`replace`); in-memory or non-atomic backends fail closed. Rollout order: declare the new
policy, drain and restart all writers, then activate. Rollback targets must read the catalogue — an older
binary that ignores it cannot be fenced, so drain before rolling back.

Alpha cutover: the standard `utsu-core` wheel is core-only. Program packages
(`utsu-program-agm`, `utsu-program-sabp`, `utsu-program-clubs`,
`utsu-program-senate`, `utsu-program-chat`, `utsu-program-voting`),
`utsu-branding`, and `utsu-portal-frontend` ship as separate distributions
built by `tools/build_split_distributions.py`; the file-free `utsu-portal`
aggregate installs them all. Build a fresh environment or image — do not
install split wheels into an environment containing the older bundled
`utsu-core`, which owns the same paths. The core wheel contains a core-only
frontend; the separate `utsu-portal-frontend` wheel supplies the full UI when
installed. `PORTAL_STATIC_DIR` overrides either. See the
[migration plan](../docs/design/09-program-package-migration.md) for the
fresh-environment and rollback requirements.

## Kubernetes
`kubectl apply -k deploy/k8s` (after creating the secrets from `secret.example.yaml`). Includes: restricted namespace,
auth + portal Deployments (non-root UID 10001, read-only root FS, all capabilities dropped, seccomp, no service-account
token, secrets as read-only files via `*_FILE` variables, `/readyz` readiness and `/healthz` liveness, rolling update with
zero unavailable), HPA, PodDisruptionBudgets, password-protected Redis, default-deny NetworkPolicies (only portal→auth,
→Redis, DNS and database/SMTP egress; narrow those to CIDRs), and an Ingress with TLS, body limit and SSE-safe buffering.
Every secret-bearing variable also accepts `NAME_FILE`, so use External Secrets / a Secret Manager CSI driver instead of
literal Secrets in production.

## Data protection and audit
- Face/ID scans, receipts and audit snapshots are Fernet-encrypted before they reach the database
  (`PORTAL_DATA_KEY`, comma-separated for rotation: first key encrypts, all decrypt; derived from `PORTAL_SECRET` if unset).
  Scans are deleted after the decision. The user record still holds only the five permitted fields.
- `PORTAL_SECRET` must be ≥32 chars. Responses carry HSTS, CSP (`frame-ancestors 'none'`), `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy` and `no-store` for API calls; requests above the 8 MB body limit get 413.
- Audit log: append-only, HMAC hash-chained (`prev`/`hash`, sequence claimed with atomic `create` so replicas can't fork it).
  Every admin request and every state-changing request is logged (actor, route, status, keyed-hash of IP; never bodies,
  query strings, tokens, OTPs or images), and key changes (registration, verification decisions, form draft/publish,
  submissions, club/transaction decisions, check-ins, preferred-name changes) also store compressed (zlib), encrypted
  before/after snapshots with secrets redacted. Admin API: `GET /api/admin/audit` (filter `actor`, `action`, `target`),
  `/{id}/diff` (two-column, word-level, Wikipedia-style revision diff plus a unified diff), `/compare?a=&b=` (diff any two
  entries, e.g. two form revisions), `/verify` (detects edited, removed or reordered entries), `/export` (gzip NDJSON).
  A tamper-evident chain cannot stop someone with both the database and `PORTAL_SECRET`; ship exports to write-once storage.

## Optimistic locking
Concurrent writers (several replicas, two admins, two check-in scanners) are handled with optimistic concurrency, not
locks. Documents written through `create`/`update` carry a `_rev` counter. `Store.replace(col, id, doc, expected_rev)` is an
atomic compare-and-swap (SQL: update guarded on the exact body read, so it works on every dialect with no extra column and no
migration; Firestore: a transaction; memory/SQLite: lock + guard), and `Store.update(col, id, fn)` is the read-modify-write
loop: `fn` is re-run on a fresh read if someone else won (bounded retries with jitter, then `Conflict`), so it must be
side-effect free and may raise `HTTPException` to abort. A new backend only has to implement `replace`.
Used for: one-shot decisions (verification, AGM check, club and expense approvals each count every vote and decide once;
receipts back one transaction), form drafts (on top of the editor's `base_rev` check, so a stale save gets 409),
publish/unpublish, submission edits vs. admin decisions, OTP attempt counting and single redemption, SSO authorization codes
(single redemption), account activation, and check-in. Blind `put` is kept only where the last writer may legitimately win.

## Auth audit ledger: monthly tables, checkpoints, anchoring

The identity service (`core.authsvc`) records everything in a tamper-evident log. Design choices versus a "hash the whole DB nightly / log every query" approach, which is expensive and bloats the DB:

- **Monthly tables** `audit_YYYY_MM`; one HMAC hash chain spans all months. Registry: `audit_months` (hot → archived → dropped).
- **DB access is audited per request**, not per query: with `PORTAL_AUDIT_DB_ACCESS=1` (always on in the auth service) one `db.access` entry lists the requester, collections, ops, and capped ids (never values). All state changes carry redacted, compressed, encrypted before/after snapshots, so the log fully reconstructs history.
- **Nightly checkpoint** (`audit_checkpoints`): an incremental Merkle root over the entries since the last checkpoint, chained to the previous checkpoint digest; skipped when nothing meaningful happened. The checkpoint is also written to the log.
- **Blockchain anchoring** via a provider submodule, off by default (`PORTAL_ANCHOR=none`). Only the 32-byte checkpoint digest leaves the system. Providers:
  - `local` (dev);
  - `ots` (OpenTimestamps/Bitcoin, no keys or fees; proofs need a later upgrade step);
  - `http` (a `PORTAL_ANCHOR_URL`/`PORTAL_ANCHOR_TOKEN` gateway to any chain);
  - `evm` (native Ethereum-compatible JSON-RPC, below);
  - `pkg.module:factory`.

  Failures leave the checkpoint `pending` and it is retried; the service never blocks on the chain. The same provider anchors plug-in digests such as closed ballots' records, submitting in the background and confirming when the anchor's status is read.
- **EVM anchoring** (`PORTAL_ANCHOR=evm`, `pip install 'utsu-core[evm]'`) sends a zero-value EIP-1559 transaction from a dedicated wallet to itself, with calldata `UTSU` followed by the digest; no contract is needed. Settings:
  - `PORTAL_ANCHOR_URL`: the https JSON-RPC endpoint;
  - `PORTAL_ANCHOR_EVM_KEY_FILE`: a file holding the wallet's private key in hex (never put it in the environment directly);
  - `PORTAL_ANCHOR_CHAIN_ID`: required; the provider refuses to send if the endpoint reports a different chain;
  - `PORTAL_ANCHOR_CONFIRMATIONS`: default 12.

  Fund the wallet only with enough for fees. Anchors become `confirmed` after a successful receipt with enough confirmations, and verification re-reads the transaction's sender, recipient and calldata. The EVM provider is tested against a simulated JSON-RPC node, not a live chain.
  The stock Docker image does not install the optional `evm` extra; include it in a derived image before enabling
  this provider. In split authentication, enable the same plug-in role catalogue on the auth service and portal
  so discussion and elections roles can be granted and projected.
- **Archive after ~90 days**: a month is verified, gzipped into `audit_archive` (sha256, Merkle root, boundary hashes). **Drop after ~180 days** only if the archive re-verifies and an anchored checkpoint covers it. The chain stays verifiable after a drop.
- **Weekly verify** walks the hot chain, checkpoint chain/roots, and archive checksums; results go to `audit_checks` and the log.
- **Scheduling**: `python -m core.authsvc maintain [--force]` (k8s `deploy/k8s/maintenance-cronjob.yaml`, Cloud Scheduler, or `POST /api/admin/audit/maintenance/run`). Jobs claim themselves with optimistic locking, so duplicates are safe.
- Admin API: `/api/admin/audit/{verify,checkpoints,checks,tables,archive/{month},maintenance/run}`.
  Maintenance is intentionally admin-only because it archives and drops retained log data; auditor access is read-only.

Limits: the HMAC chain cannot stop an attacker holding both the DB and `PORTAL_SECRET`; the external anchor is what covers that. The OTS/HTTP providers, the CronJob and non-SQLite stores are untested against real services.

## Roles (RBAC)

The auth service is the only issuer of roles (`modules/roles.py`). They are stored on the account (`roles`), exposed to the portal through the service-token-gated `/internal/accounts` projection (cached ~15 s, so a revocation reaches every service within that time), and are **not** embedded in session tokens.

| Role | Grants |
|---|---|
| `admin` | everything, plus granting roles and running audit maintenance |
| `verifier` | review face + ID verifications (portal and AGM) |
| `forms_editor` | form drafts, publish/unpublish, submissions |
| `checkin` | AGM check-in and attendance |
| `agm_id_reviewer` | view the AGM online ID queue and photos, and decide Zoom sign-in; does not grant account-verification access |
| `finance` | club transactions and grants |
| `clubs` | club registration decisions |
| `auditor` | read/verify/export the audit log |
| `discussion_moderator` | discussion boards and moderation (only when the `discussion` plug-in is enabled) |
| `elections_admin` | ballots, voter rolls and breaking the seal (only when the `voting` plug-in is enabled) |

Manage with `GET /api/admin/roles`, `GET /api/admin/roles/holders`, `PUT /api/admin/roles {username, roles}` (or the "Admin: roles" screen). The holders list uses `limit` and `cursor` and returns `items`/`next_cursor`. Rules: admin only; no self-changes; the target must be a verified account; the last admin cannot be removed; each change is audited with before/after. `PORTAL_ADMIN_EMAILS` only bootstraps the first admin. Two admins demoting each other at the same instant could still leave none; recover by editing the account record directly. Legacy `role: admin` accounts keep working.

### Enforcing roles in the database too

The application checks above are the portable layer. Add database-side guards where the engine supports them (defence in depth if a service is compromised):

- **Separate DB identities per service.** The auth service connects as `auth_rw`, the portal as `portal_rw`. Grant `portal_rw` nothing on `accounts`, `users`, `otps`, `student_ids`, `emails`, `verifications`, `media`, `audit_*`; the portal then cannot read or alter identities even with a bug or stolen credentials. Better: a separate database/schema per service.
- **Append-only audit.** For `audit_*` tables grant the service `INSERT, SELECT` only (no `UPDATE/DELETE`), and run maintenance (archive/drop) under a separate `audit_maint` role. PostgreSQL/Supabase: `REVOKE UPDATE, DELETE ON ...`; Azure SQL: `DENY UPDATE, DELETE`.
- **Row-level security.** PostgreSQL/Supabase `CREATE POLICY` (and Azure SQL `SECURITY POLICY`) can restrict applicants to rows they own using a session setting such as `SET app.uid = ...`. The generic key-value `SQLStore` stores JSON documents, so this only fits if you map the high-value collections to real columns. Treat it as an optional hardening step, not something the portable store does.
- **Firestore.** Security rules apply only to client SDKs; server access is governed by IAM. Use one service account per service and a separate Firestore database (or project) for the auth service, since IAM cannot restrict individual collections.

## Sharing secrets between containers

Share as little as possible. What each container actually needs:

| Secret | Auth service | Portal |
|---|---|---|
| `PORTAL_AUTH_SIGNING_KEY` (Ed25519 private) | yes | **never** |
| `PORTAL_AUTH_PUBLIC_KEY` | no | yes (public, not secret) |
| `PORTAL_SERVICE_TOKEN` | accepts | presents (the only shared secret) |
| `PORTAL_SECRET` (pseudonymous ids, OTP hashing) | own value | own, different value |
| `PORTAL_DATA_KEY`, DB credentials | own | own |

Recommendations, strongest first:
1. **Don't share, derive trust.** Replace the shared service token with mTLS (service mesh such as Istio/Linkerd, or cert-manager issued certs) or platform identity (GCP Workload Identity + Cloud Run IAM `run.invoker`, Azure managed identity, Kubernetes projected service-account tokens with `TokenReview`). Then no static secret exists to leak.
2. **Secret manager, injected at start.** GCP Secret Manager, Azure Key Vault or HashiCorp Vault via External Secrets Operator or the Secrets Store CSI driver; mount as files and use the `*_FILE` variables so values never appear in `env`, logs or `docker inspect`. Scope each secret to one workload's identity (IAM/RBAC), enable audit logging on access.
3. **Kubernetes Secrets, if used directly:** one Secret per service (`auth-secrets`, `portal-secrets`), encryption at rest with a KMS provider, RBAC limiting `get secrets`, `automountServiceAccountToken: false`, NetworkPolicy so only the portal reaches `/internal`.
4. **Rotate without downtime.** `PORTAL_SERVICE_TOKEN` on the auth service accepts a comma-separated list: deploy `old,new` to the auth service, switch the portal to `new`, then drop `old`. Rotate `PORTAL_AUTH_SIGNING_KEY` by publishing the new public key beside the old (`PORTAL_AUTH_PUBLIC_KEY` accepts several PEMs) before signing with the new one. `PORTAL_SECRET` must never rotate (it derives identities).
5. Never put secrets in images, ConfigMaps, git, or build args; keep `deploy/k8s/secret.example.yaml` out of the kustomization.
