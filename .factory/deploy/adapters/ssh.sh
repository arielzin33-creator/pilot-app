#!/usr/bin/env bash
# ssh - a single VM running Docker. Untested against a live host. Needs DEPLOY_SSH_KEY
# (environment secret), DEPLOY_SSH_TARGET (user@host, a variable) and DEPLOY_SSH_KNOWN_HOSTS
# (the host's public key line, a variable: never disable host key checking).
set -euo pipefail
: "${DEPLOY_SSH_KEY:?}" "${DEPLOY_SSH_TARGET:?}" "${DEPLOY_SSH_KNOWN_HOSTS:?}"
d="$(mktemp -d)"; trap 'rm -rf "$d"' EXIT
printf '%s\n' "$DEPLOY_SSH_KEY" > "$d/key"; chmod 600 "$d/key"
printf '%s\n' "$DEPLOY_SSH_KNOWN_HOSTS" > "$d/known_hosts"
name="${DEPLOY_APP:-app}-${DEPLOY_ENV}"
ssh -i "$d/key" -o UserKnownHostsFile="$d/known_hosts" -o StrictHostKeyChecking=yes "$DEPLOY_SSH_TARGET" \
  "set -e; docker pull '$DEPLOY_IMAGE'; docker rm -f '$name-new' 2>/dev/null || true;
   docker run -d --name '$name-new' --env-file '/etc/$name.env' -e RELEASE='${RELEASE:-${DEPLOY_IMAGE##*@}}' --restart unless-stopped '$DEPLOY_IMAGE';
   sleep 5; docker inspect -f '{{.State.Running}}' '$name-new' | grep -q true;
   docker rm -f '$name' 2>/dev/null || true; docker rename '$name-new' '$name'"
