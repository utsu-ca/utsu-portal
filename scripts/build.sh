#!/usr/bin/env bash
# Build the portal container image from config/portal.yaml.
#
#   scripts/build.sh [--config FILE] [--tag NAME] [--version X.Y.Z] [--dev]
#
# Core is always installed. Only programs set to true in the configuration are installed, and the UI is built
# for exactly that selection. The selection is stored on the image so scripts/deploy.sh can check against it.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="$root/config/portal.yaml"
tag="utsu-portal:local"
version="0.2.0"
extra_deps=""

usage() { sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --config) config="$2"; shift 2 ;;
    --tag) tag="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --dev) extra_deps=",container-dev"; shift ;;  # adds tracing dependencies used by docker-compose-dev.yml
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

python="$root/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python3)"
settings="$("$python" "$root/tools/portal_config.py" --config "$config")" || exit $?
eval "$settings"

echo "Building $tag"
echo "  programs:  ${PORTAL_PROGRAMS:-none (core only)}"
echo "  frontend:  $PORTAL_FRONTEND_COMPOSITION"

docker build "$root" \
  --tag "$tag" \
  --build-arg "PORTAL_PROGRAMS=$PORTAL_PROGRAMS" \
  --build-arg "UI_COMPOSITION=$PORTAL_FRONTEND_COMPOSITION" \
  --build-arg "UI_PROGRAMS=$PORTAL_FRONTEND_PROGRAMS" \
  --build-arg "PORTAL_VERSION=$version" \
  --build-arg "PORTAL_EXTRA_DEPS=$extra_deps"

echo "Built $tag. Start it with: scripts/deploy.sh --image $tag"
