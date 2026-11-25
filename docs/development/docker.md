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

## Load sample records

`scripts/seed.sh` fills a running dev portal with sample applicants, form submissions and bursary applications. It is
an external client: it makes the same HTTP calls a browser does, signs in as `admin@dev-local.test`, and reads
sign-in codes from the dev mail inbox. It never touches the database, so it works on whatever instance is running and
runs the real validation. Nothing is seeded at start-up.

```sh
scripts/deploy.sh --dev              # start the stack first
scripts/seed.sh                      # roster + 30 submissions each for AGM, Senate and SABP
scripts/seed.sh --only senate sabp --per-program 10
scripts/seed.sh --skip-roster        # run it again: nothing is duplicated
```

It generates a fake roster and form exports (`testing/generate_fake_*.py`), imports the roster, then for each
program that is enabled on the instance registers and verifies a student account (`student+NNNN@dev-local.test`,
where `NNNN` is the roster's four-digit identifier) for applicants the portal verifies, and files their application.
Half of the SABP applicants also get synthetic banking details. Programs that are not enabled are skipped, so it works
on a subset deployment. The data is deterministic, so a second run uses the same people.

- **Roster.** It refuses to replace a roster that is already loaded. Use `--skip-roster` when an earlier seed loaded it,
  or `--replace-roster` to overwrite it.
- **Speed.** Auth endpoints allow 30 requests a minute per client and the seeder waits when told to, which makes a
  full seed slow. Redeploy with `PORTAL_RATE_LIMIT_AUTH=0 scripts/deploy.sh --dev` and it takes about a minute.
- **Safety.** It needs `PORTAL_DEV_ADMIN=1` on a localhost `PORTAL_BASE_URL`, and refuses a non-local `--url`
  unless `--allow-remote` is given for a test environment you own.
- **Other instances.** Point it elsewhere with `--url` and `--mail-url` (or `PORTAL_URL` and `PORTAL_MAIL_URL`);
  `scripts/seed.sh` reads `PORTAL_PORT` and `PORTAL_MAIL_INBOX_PORT` when you run the stack on other ports.

To start from nothing, remove the database volume: `docker compose ... down -v`.

## Stop and deploy

To stop containers without deleting the database and monitoring volumes:

```sh
docker compose --env-file secrets/dev.env -p utsu-portal-dev \
  -f docker-compose.yml -f docker-compose-dev.yml down
```

For the generated stack, use `-p utsu-portal-generated`. `down -v` deletes that project's data; do not use it
unless you want to reset the project.

Deploy the standard [portal image](../../Dockerfile), not the dev override, to Cloud Run or Azure Container Apps.
Rebuild the production frontend bundle with `npm run build` in `src/core/frontend/` before building the image if frontend
sources changed. Set the public `PORTAL_BASE_URL`, inject secrets, and use managed persistence via `PORTAL_STORE`
and `DATABASE_URL` (and `PORTAL_REDIS_URL` if using Redis). Keep dev sign-in accounts disabled in deployments.
Neither managed platform nor this Compose workflow requires a local Knative installation.
[Knative Serving](https://knative.dev/docs/install/) is optional for testing revisions or scale-to-zero on Kubernetes.
