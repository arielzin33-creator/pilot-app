#!/usr/bin/env bash
# none - dry run: records what would be deployed. The default until a provider is connected.
set -euo pipefail
echo "dry run: would deploy $DEPLOY_IMAGE to $DEPLOY_ENV${DEPLOY_PR:+ (PR $DEPLOY_PR)}${DEPLOY_APP:+ as $DEPLOY_APP}"
