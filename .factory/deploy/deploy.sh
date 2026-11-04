#!/usr/bin/env bash
# deploy.sh - provider-neutral deployment entry point (Phase D; spec §10.4).
#
# Usage: deploy.sh <environment> <image@sha256:digest> [--pr <number>]
#   environment: preview | staging | production
# Env:   DEPLOY_PROVIDER  none (default) | fly | render | ssh
#        DEPLOY_APP_<ENV> provider app/service name per environment (adapter-specific)
#        DEPLOY_URL_<ENV> public base URL per environment (used for verification)
#        DEPLOY_TOKEN / DEPLOY_SSH_KEY  environment secrets, provided only to deploy jobs
# Output: the last line is machine-readable:
#   FACTORY_DEPLOY: env=<e> provider=<p> image=<ref> url=<url> status=<DEPLOYED|DRY_RUN|FAILED>
#
# Admission rule (spec §6.5 rule 7): only images pinned by digest are deployed. The workflow
# verifies the image's provenance attestation (gh attestation verify) before calling this.
set -euo pipefail
ENV_NAME="${1:?environment}"; IMAGE="${2:?image@digest}"; shift 2
PR=""
while [ $# -gt 0 ]; do case "$1" in --pr) PR="${2:?}"; shift 2 ;; *) echo "unknown arg $1" >&2; exit 2 ;; esac; done
case "$ENV_NAME" in preview|staging|production) ;; *) echo "environment must be preview, staging or production" >&2; exit 2 ;; esac
if ! [[ "$IMAGE" =~ ^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$ ]]; then
  echo "refusing to deploy $IMAGE: not a strict digest reference (registry/name@sha256:<64 hex>)" >&2
  echo "FACTORY_DEPLOY: env=$ENV_NAME provider=${DEPLOY_PROVIDER:-none} image=rejected url= status=FAILED"; exit 2
fi
PROVIDER="${DEPLOY_PROVIDER:-none}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="$HERE/adapters/$PROVIDER.sh"
[ -f "$ADAPTER" ] || { echo "unknown DEPLOY_PROVIDER=$PROVIDER (adapters: $(ls "$HERE/adapters" | sed 's/\.sh//' | tr '\n' ' '))" >&2; exit 2; }
UPPER="$(echo "$ENV_NAME" | tr '[:lower:]' '[:upper:]')"
APP_VAR="DEPLOY_APP_$UPPER"; URL_VAR="DEPLOY_URL_$UPPER"
export DEPLOY_ENV="$ENV_NAME" DEPLOY_IMAGE="$IMAGE" DEPLOY_PR="$PR"
export DEPLOY_APP="${!APP_VAR:-}" DEPLOY_URL="${!URL_VAR:-}"
if [ "$ENV_NAME" = "preview" ] && [ -n "$PR" ]; then
  DEPLOY_APP="${DEPLOY_APP:+$DEPLOY_APP-pr-$PR}"
  DEPLOY_URL="${DEPLOY_URL//\{pr\}/$PR}"
fi
set +e
bash "$ADAPTER"
code=$?
set -e
status=DEPLOYED; [ "$PROVIDER" = none ] && status=DRY_RUN; [ $code -eq 0 ] || status=FAILED
echo "FACTORY_DEPLOY: env=$ENV_NAME provider=$PROVIDER image=$IMAGE url=${DEPLOY_URL:-} status=$status"
exit $code
