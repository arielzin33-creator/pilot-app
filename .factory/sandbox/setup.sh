#!/usr/bin/env bash
# setup.sh - prepare a checkout for a factory run: dependencies without install scripts, the
# database schema, and seed data, using the repository's own scripts. Idempotent.
# For local and image use (the workflows install dependencies inline with --ignore-scripts).
# The sandbox image is built and signed by factory-sandbox-image.yml; using it as the job
# container is optional (see docs/factory/RELEASE.md).
set -euo pipefail
ROOT="${1:-$(git rev-parse --show-toplevel)}"
cd "$ROOT"
[ -f package-lock.json ] || { echo "setup: package-lock.json required" >&2; exit 2; }
npm ci --ignore-scripts
has() { node -e "process.exit(require('./package.json').scripts?.['$1']?0:1)"; }
if has db:migrate; then npm run --silent db:migrate; fi
if has db:seed; then npm run --silent db:seed; fi
echo "setup: ready"
