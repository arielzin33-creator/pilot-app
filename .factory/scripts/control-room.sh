#!/usr/bin/env bash
# control-room.sh - one-screen view of the factory (spec §10.1 deliverable 8).
# Needs: gh (authenticated), run from inside the repository.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
PY="python3 -I $ROOT/.factory/scripts/factory.py"
LIMIT="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["limits"]["review_queue_max"])' "$ROOT/.factory/policy.json")"
BUDGET="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["limits"]["daily_budget_usd"])' "$ROOT/.factory/policy.json")"

echo "== Factory control room: $REPO  ($(date -u +%Y-%m-%dT%H:%MZ))"
echo
echo "-- Queue (open issues by state label)"
for label in factory:inbox factory:ready-to-implement factory:in-progress factory:awaiting-review \
             factory:needs-info factory:ready-to-spec factory:needs-human factory:human-only; do
  n="$(gh issue list -R "$REPO" --state open --label "$label" --json number --jq length)"
  printf '   %-28s %s\n' "$label" "$n"
done
echo
AW="$(gh pr list -R "$REPO" --state open --label factory:awaiting-review --json number --jq length)"
echo "-- Pull requests awaiting review: $AW / limit $LIMIT$( [ "$AW" -ge "$LIMIT" ] && echo '   (FULL: the factory is stopped until you review)')"
gh pr list -R "$REPO" --state open --label factory:awaiting-review \
  --json number,title,labels --jq '.[] | "   #\(.number) \(.title)  [\([.labels[].name] | map(select(startswith("factory:"))) | join(", "))]"'
echo
echo "-- Needs a human"
gh issue list -R "$REPO" --state open --label factory:needs-human --json number,title \
  --jq '.[] | "   #\(.number) \(.title)"'
echo
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if git ls-remote --exit-code --heads origin factory/ledger >/dev/null 2>&1; then
  git fetch --quiet --depth 1 origin factory/ledger:refs/remotes/origin/factory/ledger
  git archive refs/remotes/origin/factory/ledger | tar -x -C "$TMP"
fi
SPEND="$($PY spend-today --ledger-dir "$TMP" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["spend_today_usd"])')"
echo "-- Estimated spend today: \$$SPEND / budget \$$BUDGET (estimate; reconcile with the Console)"
echo
echo "-- Last 10 runs"
find "$TMP/runs" -name '*.json' 2>/dev/null | sort | tail -10 | while read -r f; do
  python3 -I -c 'import json,sys
r=json.load(open(sys.argv[1]))
a=r.get("agent",{})
print("   {} #{} {:<14} tier={} cost=${:.2f}".format(r.get("recorded_at","?"), r.get("issue","?"), str(r.get("outcome","?")), r.get("tier","?"), float(a.get("total_cost_usd_estimate") or 0)))' "$f"
done
