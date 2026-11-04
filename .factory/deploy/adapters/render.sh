#!/usr/bin/env bash
# render - Render web service (image-backed). Untested against the live service. Triggers a
# deploy of a specific image through the service's deploy hook URL (secret DEPLOY_TOKEN holds
# the hook URL, which embeds its key).
set -euo pipefail
: "${DEPLOY_TOKEN:?DEPLOY_TOKEN secret (deploy hook URL) missing}"
enc="$(python3 -I -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$DEPLOY_IMAGE")"
curl --fail --silent --show-error --max-time 60 -X POST "${DEPLOY_TOKEN}&imgURL=${enc}" >/dev/null
echo "render: deploy triggered for $DEPLOY_IMAGE"
