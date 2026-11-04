#!/usr/bin/env bash
# collect-state.sh - gather everything STOP_IF needs into JSON files (spec §5.6).
#
# Usage: collect-state.sh <issue-number|auto> <out-dir>
# Needs: GH_TOKEN (read access), GITHUB_REPOSITORY, gh, python3, git.
# Writes: <out-dir>/state.json, issue.json, comments.json, handoff.json, selected.txt
# Optional env: FACTORY_PAUSED=true, LEDGER_DIR (defaults to <out-dir>/ledger)
set -euo pipefail
ISSUE_IN="${1:?issue number or auto}"
OUT="${2:?output directory}"
ROOT="$(git rev-parse --show-toplevel)"
PY="python3 -I $ROOT/.factory/scripts/factory.py"
mkdir -p "$OUT"
REPO="${GITHUB_REPOSITORY:?}"
LEDGER_DIR="${LEDGER_DIR:-$OUT/ledger}"

# Ledger branch (may not exist yet on a fresh repository).
rm -rf "$LEDGER_DIR"; mkdir -p "$LEDGER_DIR"
if git -C "$ROOT" ls-remote --exit-code --heads origin factory/ledger >/dev/null 2>&1; then
  git -C "$ROOT" fetch --quiet --depth 1 origin factory/ledger:refs/remotes/origin/factory/ledger
  git -C "$ROOT" archive refs/remotes/origin/factory/ledger | tar -x -C "$LEDGER_DIR"
fi

# Select the item.
if [ "$ISSUE_IN" = "auto" ]; then
  gh issue list -R "$REPO" --state open --label factory:ready-to-implement \
    --json number,labels,createdAt,updatedAt --limit 100 > "$OUT/candidates.json"
  ISSUE="$($PY select-item --issues-json "$OUT/candidates.json" | python3 -I -c 'import json,sys; n=json.load(sys.stdin)["number"]; print(n if n else "")')"
else
  ISSUE="$ISSUE_IN"
fi
echo "${ISSUE}" > "$OUT/selected.txt"

AWAITING="$(gh pr list -R "$REPO" --state open --label factory:awaiting-review --json number --jq 'length')"
DEFAULT_BRANCH="$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)"
HEAD_SHA="$(gh api "repos/$REPO/commits/$DEFAULT_BRANCH" --jq .sha)"
BASE_STATE="$(gh api "repos/$REPO/commits/$HEAD_SHA/status" --jq '[.statuses[] | select(.context=="factory/gates")][0].state // ""')"
case "$BASE_STATE" in
  success) BASE_GATES="GREEN" ;;
  failure|error) BASE_GATES="RED" ;;
  *) BASE_GATES="" ;;   # unknown: do not block on missing history
esac

if [ -n "$ISSUE" ]; then
  gh issue view "$ISSUE" -R "$REPO" --json number,title,body,labels,createdAt,updatedAt,author > "$OUT/issue.json"
  gh api --paginate "repos/$REPO/issues/$ISSUE/comments" > "$OUT/comments.json"
  $PY parse-handoff --comments-json "$OUT/comments.json" > "$OUT/handoff.json"
  # When did the item enter factory:in-progress (for staleness)?
  gh api --paginate "repos/$REPO/issues/$ISSUE/events" \
    --jq '[.[] | select(.event=="labeled" and .label.name=="factory:in-progress") | .created_at] | last // ""' \
    > "$OUT/in_progress_since.txt" || echo "" > "$OUT/in_progress_since.txt"
else
  echo '{}' > "$OUT/issue.json"; echo '[]' > "$OUT/comments.json"; echo '{"handoff": null}' > "$OUT/handoff.json"
  echo "" > "$OUT/in_progress_since.txt"
fi

$PY spend-today --ledger-dir "$LEDGER_DIR" ${ISSUE:+--issue "$ISSUE"} > "$OUT/spend.json"

# Tier 2 spec gate: the hand-off may cite a spec folder; its approval state is read from the
# default branch checkout (trusted), never from the issue.
SPEC="$(python3 -I -c 'import json,sys
h=json.load(open(sys.argv[1])).get("handoff") or {}
print(h.get("spec",""))' "$OUT/handoff.json")"
# The approval is bound to the issue: only an issue the backlog workflow created from that same
# spec (author github-actions[bot], label factory:from-spec, matching task marker) qualifies.
BOUND="$(python3 -I - "$OUT/issue.json" "$SPEC" "$ROOT/.factory/policy.json" <<'PY'
import json, re, sys
issue = json.load(open(sys.argv[1])); spec = sys.argv[2]
trusted = set(json.load(open(sys.argv[3])).get("trusted_handoff_authors", []))
login = (issue.get("author") or {}).get("login", "")
name = login[4:] if login.startswith("app/") else login      # gh shows apps as "app/<slug>"
bot = login in trusted or f"{name}[bot]" in trusted
labels = {l.get("name") for l in issue.get("labels", [])}
m = re.search(r"<!-- factory-task: (specs/[0-9]{3}-[a-z0-9-]+)#T-\d{3} -->", issue.get("body") or "")
print("true" if bot and "factory:from-spec" in labels and m and m.group(1) == spec else "false")
PY
)"
if [ "$BOUND" = "true" ] && [[ "$SPEC" =~ ^specs/[0-9]{3}-[a-z0-9-]+$ ]] && [ -d "$ROOT/$SPEC" ]; then
  python3 -I "$ROOT/.factory/scripts/intake.py" spec-status "$ROOT/$SPEC" > "$OUT/spec-status.json"
else
  echo '{"implementable": false, "reason": "issue not generated from this spec by the backlog workflow"}' > "$OUT/spec-status.json"
fi

# Release freeze (Phase D SLO policy): an open issue labelled factory:freeze stops the factory.
# Fail closed: if the freeze state cannot be read, behave as if frozen.
FREEZE="$(gh issue list -R "$REPO" --state open --label factory:freeze --json number --jq 'length' 2>/dev/null || echo 1)"

OUT="$OUT" AWAITING="$AWAITING" BASE_GATES="$BASE_GATES" ISSUE="$ISSUE" FREEZE="${FREEZE:-0}" \
PAUSED="${FACTORY_PAUSED:-false}" python3 -I - <<'EOF'
import json, os, datetime as dt
out = os.environ["OUT"]
issue = json.load(open(f"{out}/issue.json"))
handoff = json.load(open(f"{out}/handoff.json")).get("handoff")
spend = json.load(open(f"{out}/spend.json"))
since = open(f"{out}/in_progress_since.txt").read().strip()
hours = None
if since:
    t = dt.datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    hours = (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600
state = {
    "paused": os.environ["PAUSED"].lower() == "true",
    "awaiting_review_count": int(os.environ["AWAITING"] or 0),
    "spend_today_usd": spend["spend_today_usd"],
    "base_gates_status": os.environ["BASE_GATES"] or None,
    "freeze": int(os.environ.get("FREEZE") or 0) > 0,
    "item": None,
}
if os.environ["ISSUE"]:
    state["item"] = {
        "number": issue.get("number"),
        "labels": [l["name"] for l in issue.get("labels", [])],
        "handoff": handoff,
        "runs": spend.get("item_runs", []),
        "ci_fix_commits": 0,  # reserved: Phase A has no CI-fix loop (CHARTER §7, TOO_MANY_CI_FIX)
        "in_progress_since_hours": hours,
        "spec_implementable": bool(json.load(open(f"{out}/spec-status.json")).get("implementable")),
    }
json.dump(state, open(f"{out}/state.json", "w"), indent=2)
print(json.dumps(state, indent=2))
EOF
