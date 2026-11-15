#!/usr/bin/env bash
# Register a trained Gaussian splat as a map in the running backend (scripts/start-backend.sh).
#
#   scripts/register-map.sh <map-id> <splat.ply> [views.zip] [name]
#
#   map-id     what the iOS app sends as "Map ID", e.g. auditorium
#   splat.ply  trained, metric 3DGS .ply
#   views.zip  training images + poses (nerfstudio transforms.json or a COLMAP text model).
#              Without it the backend cannot localize captures from the app.
#   name       shown in the viewer (default: the map id)
#
# Environment: BACKEND (default http://localhost:8000), METADATA (JSON, see backend/README.md,
# e.g. '{"up_axis": "z", "property": "Campus", "floor_label": "Ground floor"}').
set -euo pipefail
cd "$(dirname "$0")/.."

[ $# -ge 2 ] || { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
MAP_ID="$1" SPLAT="$2" VIEWS="${3-}" NAME="${4-$1}"
BACKEND="${BACKEND:-http://localhost:8000}"
[ -f "$SPLAT" ] || { echo "Splat not found: $SPLAT" >&2; exit 1; }
[ -z "$VIEWS" ] || [ -f "$VIEWS" ] || { echo "Views zip not found: $VIEWS" >&2; exit 1; }

TOKEN="${API_TOKEN-}"
[ -n "$TOKEN" ] || [ ! -f .env ] || TOKEN=$(sed -n 's/^API_TOKEN=\([^#]*\).*/\1/p' .env | tail -n 1 | sed 's/[[:space:]]*$//')

ARGS=(-F "name=$NAME" -F "map_id=$MAP_ID" -F "splat=@$SPLAT;filename=splat.ply")
[ -z "$VIEWS" ] || ARGS+=(-F "views=@$VIEWS;filename=views.zip")
[ -z "${METADATA-}" ] || ARGS+=(-F "metadata=$METADATA")
[ -z "$TOKEN" ] || ARGS+=(-H "Authorization: Bearer $TOKEN")

echo "Uploading $SPLAT to $BACKEND as map \"$MAP_ID\"…"
curl --fail-with-body -sS -X POST "$BACKEND/maps" "${ARGS[@]}"
echo
echo
echo "Registered. The backend now prepares the viewer copy, the floor plan and the localization index"
echo "(a few minutes for a large splat). Progress: $BACKEND/maps/$MAP_ID  →  status, vps_status"
[ -n "$VIEWS" ] || echo "No views.zip given: captures from the app cannot be localized on this map."
