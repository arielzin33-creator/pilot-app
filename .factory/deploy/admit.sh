#!/usr/bin/env bash
# admit.sh - admission control before any deploy (spec §6.5 rule 7; review finding 2026-11-04).
# Usage: admit.sh <image@sha256:digest> <signer-workflow-file> [source-ref]
#   Accepts only a strictly formed digest reference, then verifies its provenance attestation:
#   built from this repository, signed by the given workflow, optionally from the given ref.
# Needs GH_TOKEN and a registry login for private images (docker/login-action).
set -euo pipefail
IMAGE="${1:?image}"; SIGNER="${2:?signer workflow, e.g. factory-release.yml}"; REF="${3:-}"
if ! [[ "$IMAGE" =~ ^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$ ]]; then
  echo "FACTORY_ADMIT: image=$IMAGE status=REJECTED reason=not-a-strict-digest-reference"; exit 2
fi
args=(attestation verify "oci://$IMAGE" --repo "${GITHUB_REPOSITORY:?}"
      --signer-workflow "$GITHUB_REPOSITORY/.github/workflows/$SIGNER")
[ -n "$REF" ] && args+=(--source-ref "$REF")
if gh "${args[@]}" >/dev/null; then
  echo "FACTORY_ADMIT: image=$IMAGE status=ADMITTED signer=$SIGNER${REF:+ ref=$REF}"
else
  echo "FACTORY_ADMIT: image=$IMAGE status=REJECTED reason=provenance-verification-failed"; exit 1
fi
