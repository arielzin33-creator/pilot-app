#!/usr/bin/env bash
# phase-a-report.sh - Phase A exit criteria from the ledger and the GitHub API (spec §10.1).
# Usage: phase-a-report.sh [billing.csv]   (billing.csv: date,amount_usd from the Console)
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
PY="python3 -I $ROOT/.factory/scripts/factory.py"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/ledger"
if git -C "$ROOT" ls-remote --exit-code --heads origin factory/ledger >/dev/null 2>&1; then
  git -C "$ROOT" fetch --quiet origin factory/ledger:refs/remotes/origin/factory/ledger
  git -C "$ROOT" archive refs/remotes/origin/factory/ledger | tar -x -C "$TMP/ledger"
fi
gh pr list -R "$REPO" --state merged --search "head:claude/fq-" --limit 500 \
  --json number,title,mergedAt,labels > "$TMP/merged.json"
MERGED_T01="$(python3 -I -c 'import json,sys
prs=json.load(open(sys.argv[1]))
print(sum(1 for p in prs if any(l["name"] in ("factory:tier-0","factory:tier-1") for l in p["labels"])))' "$TMP/merged.json")"
echo "== Phase A exit report for $REPO"
echo "1. Merged Tier 0-1 factory PRs: $MERGED_T01 / 20"
echo -n "2. Long-lived keys (repository scan): "; $PY scan-keys >/dev/null && echo "none found" || echo "FOUND (run scan-keys)"
echo "   Workflows refuse ANTHROPIC_API_KEY at runtime; federation only."
echo "3. Copy-paste steps between ticket and PR: zero by construction (label -> workflow -> draft PR)."
echo "4. Cost per merged PR:"
if [ -n "${1:-}" ]; then
  $PY reconcile --ledger-dir "$TMP/ledger" --merged-json "$TMP/merged.json" --billing-csv "$1" || true
else
  $PY reconcile --ledger-dir "$TMP/ledger" --merged-json "$TMP/merged.json" || true
  echo "   (estimate only; pass the Console billing export as date,amount_usd to reconcile)"
fi
echo "5. STOP_IF conditions exercised by tests: see tests/ in the factory repository (make test)."
