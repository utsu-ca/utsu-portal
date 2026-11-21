# Docker development

Docker Compose 2.32 or newer is the supported local development environment. The standalone SQLite launcher
is retired. The [base Compose file](../../docker-compose.yml) runs the deployment-style services, and the
[dev override](../../docker-compose-dev.yml) adds hot reload, local sign-in accounts, and observability.

## Start

From the repository root, create a local environment file once and reuse it on subsequent starts:

```sh
mkdir -p secrets
(umask 077; set -C; printf 'PORTAL_SECRET=%s\nPORTAL_DB_PASSWORD=%s\n' \
  "$(openssl rand -hex 32)" "$(openssl rand -hex 24)" > secrets/dev.env)
```

Changing the portal secret can make existing encrypted records unreadable. The database password should be
URL-safe. Neither value should be reused from production.

```sh
docker compose --env-file secrets/dev.env -p utsu-portal-dev \
  -f docker-compose.yml -f docker-compose-dev.yml up --build --watch
```

Open http://127.0.0.1:5173/static/ and sign in as `admin@dev-local.test` or another `<role>@dev-local.test` account.
The dev override disables Turnstile and routes email to the [local inbox](http://127.0.0.1:8025/) instead of
sending it externally. Request a one-time sign-in code in the portal, then open the inbox to read it.
Club receipt blobs persist encrypted in the `portal-receipt-files` volume. The optional `storage` profile adds a
nightly migration/archive worker; archive transfers are disabled by default. Use the
[cold-storage overlay and setup](../../wiki/Deployment.md#upload-quotas-and-cold-receipt-storage) when connecting
an SFTP archive node. Never remove that volume independently of the database.
It also enables the experimental student aid review queue for testing; it is not a complete committee process.
`applicant+1@dev-local.test` through `applicant+3@dev-local.test` have no staff roles.
Restarting the portal keeps existing dev accounts and their IDs; no database reset is needed.

## What starts automatically

| Service | Purpose | Default host address |
|---|---|---|
| `web` | Vite serves Svelte with hot reload and proxies API, SSO, and branding requests on the same origin. | `127.0.0.1:5173/static/` (`PORTAL_WEB_PORT`) |
| `portal` | FastAPI/Uvicorn, ready checks, and OpenTelemetry for HTTP traces and metrics. | `127.0.0.1:8000` (`PORTAL_PORT`) |
| `postgres` | Application and audit records in the project's named volume. | Internal only |
| `redis` | Shared events and rate limits across portal processes. | Internal only |
| `mail-sink` | Captures sign-in codes and other mail without relaying. SMTP is internal only; messages are kept in memory. | `127.0.0.1:8025` (`PORTAL_MAIL_INBOX_PORT`) |
| `jaeger` | Receives OpenTelemetry traces; search for `utsu-portal` after making a portal API request. Trace storage is in memory. | `127.0.0.1:16686` (`PORTAL_JAEGER_PORT`) |
| `prometheus` | Receives OpenTelemetry HTTP metrics and retains them for seven days in a named volume. | `127.0.0.1:9090` (`PORTAL_PROMETHEUS_PORT`) |
| `grafana` | Includes provisioned Prometheus and Jaeger data sources for Explore; first-use credentials are `admin` / `admin`. | `127.0.0.1:3000` (`PORTAL_GRAFANA_PORT`) |

Compose Watch syncs frontend edits into Vite without touching the committed static bundle. Backend source
changes restart the API container, and dependency-manifest changes rebuild the corresponding image.
Use **Compose logs** for application and container logs; ELK, Alertmanager, and the experimental separate
audit database are not started.

```sh
docker compose --env-file secrets/dev.env -p utsu-portal-dev \
  -f docker-compose.yml -f docker-compose-dev.yml logs -f portal
```

## Opt in to generated fixtures

Use a separate Compose project so generated records do not mix with ordinary development. On the first run,
start without Watch: a source edit can restart the portal before its initial seed is complete. This example uses
different host ports so both projects can run at once:

```sh
PORTAL_DEV_SEED=1 PORTAL_WEB_PORT=5174 PORTAL_PORT=8001 PORTAL_MAIL_INBOX_PORT=8026 \
PORTAL_JAEGER_PORT=16687 PORTAL_PROMETHEUS_PORT=9091 PORTAL_GRAFANA_PORT=3001 \
docker compose --env-file secrets/dev.env -p utsu-portal-generated \
  -f docker-compose.yml -f docker-compose-dev.yml up --build -d --wait --wait-timeout 960
```

Wait for this command to succeed before opening http://127.0.0.1:5174/static/. To enable live reload afterward,
rerun the same command with `up --watch` in place of `up --build -d --wait --wait-timeout 960`.
With the ports above, the seeded stack's mail inbox is at http://127.0.0.1:8026/.
Before the API starts, the first run imports the full 1,200-student roster and registers exactly 1,000 students
sampled with a fixed seed (`UTSU-482`), in addition to the standard dev accounts. Each sampled student is
verified and has a synthetic student number (`12345NNNN0`) linked to the roster automatically. Sign-in uses
`student+NNNN@dev-local.test`, where `NNNN` is the four-digit roster identifier.

The seeder checks every AGM, Senate, and SABP row through the API and saves exactly 30 submissions per
program, sampled reproducibly from exact roster matches among the registered students. For repeated
submissions, the latest matching row is the candidate for that student. On the first seed, 15 of the SABP applicants
receive synthetic banking details (institution `000`, transit `12345`, account `12345NNNN`). No legacy contact email
or banking details are imported. Subsequent portal starts rerun the checks in memory without overwriting local edits,
and do not add banking details to an existing generated database.
Use a fresh Compose project to get these counts if you already seeded an earlier version.

If startup reports `Refusing to seed a populated database`, that project's PostgreSQL volume already has
accounts or a roster but no completed fixture seed. Starting with seeding disabled can populate a volume
with local role accounts alone. Stop the blocked project with `down` (without `-v`), then rerun the command
above with a project name you have not used before. The new project gets a fresh volume; the original volume
and its data are preserved. Use the same recovery if startup reports `Previous dev seed did not complete`.
`audit_months` duplicate-key lines from an older portal process do not require a volume reset. Restart
`portal` to load the fix; PostgreSQL retains the old log lines until its container is replaced.

## Stop and deploy

To stop containers without deleting the database and monitoring volumes:

```sh
docker compose --env-file secrets/dev.env -p utsu-portal-dev \
  -f docker-compose.yml -f docker-compose-dev.yml down
```

For the generated stack, use `-p utsu-portal-generated`. `down -v` deletes that project's data; do not use it
unless you want to reset the project.

Deploy the standard [portal image](../../Dockerfile), not the dev override, to Cloud Run or Azure Container Apps.
Rebuild the production frontend bundle with `npm run build` in `web/` before building the image if frontend
sources changed. Set the public `PORTAL_BASE_URL`, inject secrets, and use managed persistence via `PORTAL_STORE`
and `DATABASE_URL` (and `PORTAL_REDIS_URL` if using Redis). Keep dev sign-in accounts disabled in deployments.
Neither managed platform nor this Compose workflow requires a local Knative installation.
[Knative Serving](https://knative.dev/docs/install/) is optional for testing revisions or scale-to-zero on Kubernetes.
