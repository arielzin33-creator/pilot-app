#!/usr/bin/env bash
# fly - Fly.io Machines. Untested against the live service. Needs flyctl on PATH (installed by
# the workflow) and DEPLOY_TOKEN (a deploy token scoped to the app) as an environment secret.
# Rolling update with health checks from fly.toml; rollback = redeploy the previous digest.
set -euo pipefail
: "${DEPLOY_APP:?DEPLOY_APP_<ENV> must name the Fly app}" "${DEPLOY_TOKEN:?DEPLOY_TOKEN secret missing}"
export FLY_API_TOKEN="$DEPLOY_TOKEN"
if [ "$DEPLOY_ENV" = preview ] && ! flyctl status --app "$DEPLOY_APP" >/dev/null 2>&1; then
  flyctl apps create "$DEPLOY_APP" --org "${FLY_ORG:-personal}"
fi
flyctl deploy --app "$DEPLOY_APP" --image "$DEPLOY_IMAGE" --strategy rolling --wait-timeout 300 \
  --env RELEASE="${RELEASE:-${DEPLOY_IMAGE##*@}}" --yes
