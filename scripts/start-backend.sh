#!/usr/bin/env bash
# Start the deepsplat backend on this machine: API, web viewer and capture uploads from the iOS app.
#
#   scripts/start-backend.sh             viewer + API on this machine and the local network (http)
#   scripts/start-backend.sh --tunnel    also publish an https address, which the iOS app needs
#
# Options: --port <n> (default 8000), --no-sync (skip `uv sync`).
# Data lives in ./data (database, splats, photos); settings in ./.env (see backend/.env.example).
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=8000 TUNNEL=0 SYNC=1
while [ $# -gt 0 ]; do
  case "$1" in
    --tunnel) TUNNEL=1 ;;
    --port) PORT="$2"; shift ;;
    --no-sync) SYNC=0 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

command -v uv >/dev/null || { echo "uv is missing. Install it first: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }
[ "$SYNC" = 1 ] && uv sync --quiet

if [ ! -f .env ]; then
  cp backend/.env.example .env
  echo "Created .env from backend/.env.example."
fi

# value of KEY from the environment, else from .env (comments and spaces after the value dropped)
setting() {
  local v
  eval "v=\${$1-}"
  [ -n "$v" ] || v=$(sed -n "s/^$1=\([^#]*\).*/\1/p" .env | tail -n 1 | sed 's/[[:space:]]*$//')
  printf '%s' "$v"
}

# The app always sends a bearer token, and with --tunnel the server is reachable from the internet:
# make sure one is set. It is generated once and kept in .env.
TOKEN=$(setting API_TOKEN)
if [ -z "$TOKEN" ]; then
  TOKEN=$(uv run --no-sync python -c "import secrets; print(secrets.token_urlsafe(18))")
  if grep -q '^API_TOKEN=' .env; then
    sed -i.bak "s|^API_TOKEN=.*|API_TOKEN=$TOKEN|" .env && rm -f .env.bak
  else
    printf '\nAPI_TOKEN=%s\n' "$TOKEN" >> .env
  fi
  echo "Generated an API token and saved it in .env."
fi
export API_TOKEN="$TOKEN"

if [ "$(setting DETECTOR)" != "fake" ] && [ -z "$(setting ROBOFLOW_API_KEY)" ]; then
  echo "Warning: ROBOFLOW_API_KEY is empty in .env, so SAM 3 detection will fail. Set it, or DETECTOR=fake for a dry run." >&2
fi

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)
BASE="http://${LAN_IP:-localhost}:$PORT"

TUNNEL_PID=""
cleanup() { [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true; }
trap cleanup EXIT

if [ "$TUNNEL" = 1 ]; then
  command -v cloudflared >/dev/null || {
    echo "cloudflared is missing. Install it (macOS: brew install cloudflared), or run without --tunnel." >&2; exit 1; }
  mkdir -p data
  LOG=data/tunnel.log
  : > "$LOG"
  cloudflared tunnel --url "http://localhost:$PORT" >"$LOG" 2>&1 &
  TUNNEL_PID=$!
  echo "Opening an https tunnel to this machine…"
  URL=""
  for _ in $(seq 1 60); do
    URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -n 1 || true)
    [ -n "$URL" ] && break
    kill -0 "$TUNNEL_PID" 2>/dev/null || break
    sleep 0.5
  done
  [ -n "$URL" ] || { echo "The tunnel did not come up. See $LOG." >&2; exit 1; }
  BASE="$URL"
fi
# Upload and file links handed to clients start with this address.
export PUBLIC_BASE_URL="$BASE"

# Once the server answers, print what to open and what to enter in the app.
(
  UP=0
  for _ in $(seq 1 120); do
    if curl -fs "http://localhost:$PORT/health" >/dev/null 2>&1; then UP=1; break; fi
    sleep 0.5
  done
  [ "$UP" = 1 ] || exit 0
  MAPS=$(curl -fs "http://localhost:$PORT/maps" 2>/dev/null | uv run --no-sync python -c "
import json, sys
try:
    maps = json.load(sys.stdin)
except Exception:
    maps = []
for m in maps:
    print(f\"    {m['id']}   ({m['name']}, map {m['status']}, localization {m['vps_status']})\")
" 2>/dev/null || true)
  echo
  echo "────────────────────────────────────────────────────────────────────"
  echo "  deepsplat backend is running"
  echo
  echo "  Web viewer (this machine):  http://localhost:$PORT/"
  [ "$TUNNEL" = 1 ] && echo "  Web viewer (anywhere):      $BASE/"
  echo "  API docs:                   http://localhost:$PORT/docs"
  echo
  echo "  iOS app → Settings (switch Mock backend off):"
  if [ "$TUNNEL" = 1 ]; then
    echo "    Backend URL:  $BASE"
  else
    echo "    Backend URL:  needs https — restart with --tunnel to get one"
  fi
  echo "    Token:        $TOKEN"
  if [ -n "$MAPS" ]; then
    echo "    Map ID, one of:"
    echo "$MAPS"
  else
    echo "    Map ID:       no map registered yet. In a second terminal:"
    echo "                  scripts/register-map.sh <map-id> <splat.ply> [views.zip]"
  fi
  echo "────────────────────────────────────────────────────────────────────"
  echo
) &

uv run --no-sync uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
