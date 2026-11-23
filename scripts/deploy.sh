#!/usr/bin/env bash
# Start (or stop) the portal from an image built by scripts/build.sh, enabling what config/portal.yaml enables.
#
#   scripts/deploy.sh [--config FILE] [--env-file FILE] [--image NAME] [--dev] [--down]
#
# --dev adds docker-compose-dev.yml: live source mounts, the Vite UI on :5173, mail sink and observability.
# Programs set to true but missing from the image need a rebuild; the script says so instead of starting.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="$root/config/portal.yaml"
env_file="$root/secrets/dev.env"
image="utsu-portal:local"
dev=0
down=0

usage() { sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --config) config="$2"; shift 2 ;;
    --env-file) env_file="$2"; shift 2 ;;
    --image) image="$2"; shift 2 ;;
    --dev) dev=1; shift ;;
    --down) down=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

python="$root/.venv/bin/python"
[ -x "$python" ] || python="$(command -v python3)"
settings="$("$python" "$root/tools/portal_config.py" --config "$config")" || exit $?
eval "$settings"

files=(-f "$root/docker-compose.yml")
[ "$dev" = 1 ] && files+=(-f "$root/docker-compose-dev.yml")
compose=(docker compose --env-file "$env_file" "${files[@]}")

state="$root/.deploy"
mkdir -p "$state"
override="$state/compose.override.yml"

if [ "$down" = 1 ]; then
  [ -f "$override" ] && files+=(-f "$override")
  docker compose --env-file "$env_file" "${files[@]}" down
  exit 0
fi

built="$(docker image inspect "$image" --format '{{ index .Config.Labels "ca.utsu.portal.programs" }}' 2>/dev/null)" || {
  echo "image $image not found; build it first with scripts/build.sh --tag $image" >&2; exit 1; }
missing=()
for program in ${PORTAL_PROGRAMS//,/ }; do
  case ",$built," in *",$program,"*) ;; *) missing+=("$program") ;; esac
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "config/portal.yaml enables ${missing[*]}, but $image only contains: ${built:-core only}." >&2
  echo "Rebuild with scripts/build.sh --tag $image" >&2
  exit 1
fi

{
  echo "services:"
  echo "  portal:"
  echo "    image: $image"
  echo "    pull_policy: never"
  echo "    environment:"
  echo "      PORTAL_ENABLED_PLUGINS: \"$PORTAL_ENABLED_PLUGINS\""
  echo "      PORTAL_BRANDING_DIR: /app/branding"
  [ -n "$PORTAL_PLUGIN_CONFIG" ] && echo "      PORTAL_PLUGIN_CONFIG: '$PORTAL_PLUGIN_CONFIG'"
  echo "    volumes:"
  echo "      - $PORTAL_BRANDING_PATH:/app/branding:ro"
} > "$override"

echo "Deploying $image"
echo "  enabled:   ${PORTAL_ENABLED_PLUGINS:-none (core only)}"
echo "  branding:  $PORTAL_BRANDING_PATH"
# The portal image comes from scripts/build.sh. The dev-only Vite container is built here, from its own Dockerfile.
[ "$dev" = 1 ] && "${compose[@]}" -f "$override" build web
"${compose[@]}" -f "$override" up -d --no-build
