#!/usr/bin/env bash
# Load sample applicants, form submissions and bursary applications into a running dev portal.
#
#   scripts/seed.sh [--only agm senate sabp] [--per-program N] [--skip-roster | --replace-roster] [--url URL]
#
# An external client: it only calls the portal's HTTP API and reads sign-in codes from the dev mail inbox, so it
# works on whatever is running (start it first with scripts/deploy.sh --dev). It signs in as the development admin
# and refuses a non-local address. Safe to re-run with --skip-roster. See `testing/seed_portal.py --help`.
#
# Auth endpoints are limited to 30 requests a minute and the seeder waits for that; for a quick run redeploy with
#   PORTAL_RATE_LIMIT_AUTH=0 scripts/deploy.sh --dev
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PORTAL_URL="${PORTAL_URL:-http://127.0.0.1:${PORTAL_PORT:-8000}}"
export PORTAL_MAIL_URL="${PORTAL_MAIL_URL:-http://127.0.0.1:${PORTAL_MAIL_INBOX_PORT:-8025}}"

python="$root/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python3)"
exec "$python" "$root/testing/seed_portal.py" "$@"
