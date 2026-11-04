#!/usr/bin/env bash
# bootstrap-github.sh - one-time repository setup, run by the owner with their own gh login.
# Creates the factory labels and a branch ruleset on the default branch:
#   pull request required, 1 approval, code-owner review, stale approvals dismissed,
#   required status check factory/gates from the GitHub Actions app only (integration 15368,
#   so another app or a user token cannot post it), no force push, no deletion.
#   Bypass: repository admins, through pull requests only (a solo owner can merge their own
#   human PRs). Agents and the Actions bot are not admins and cannot bypass.
# Optional: --seed <issues.json> creates seed issues labeled factory:inbox.
set -euo pipefail
REPO="${REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
DEFAULT_BRANCH="$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)"
SEED=""
[ "${1:-}" = "--seed" ] && SEED="${2:?issues.json}"

echo "== Labels"
while IFS='|' read -r name color desc; do
  [ -n "$name" ] || continue
  gh label create "$name" -R "$REPO" --color "$color" --description "$desc" --force >/dev/null && echo "   $name"
done <<'LABELS'
factory:inbox|C5DEF5|New; waiting for factory triage
factory:ready-to-implement|0E8A16|Triaged; eligible for an unattended run
factory:ready-to-spec|FBCA04|Needs a human-approved spec (Tier 2)
factory:needs-info|D4C5F9|Blocked on a named question
factory:human-only|B60205|Tier 3 or never automated
factory:in-progress|1D76DB|Claimed by one factory run
factory:awaiting-review|5319E7|Draft PR open; a human decides
factory:needs-human|E99695|Stopped by a STOP_IF condition or a failed run
factory:verified|0E8A16|Verifier verdict: verified (advisory)
factory:rejected|B60205|Verifier verdict: rejected (advisory)
factory:tier-0|EDEDED|Tier 0 mechanical
factory:tier-1|EDEDED|Tier 1 bounded change
factory:tier-2|EDEDED|Tier 2 feature
factory:tier-3|EDEDED|Tier 3 load-bearing
LABELS

echo "== Ruleset on $DEFAULT_BRANCH"
cat > /tmp/factory-ruleset.json <<JSON
{
  "name": "factory-default-branch",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [ { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request" } ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [ { "context": "factory/gates", "integration_id": 15368 } ] } }
  ]
}
JSON
existing="$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name=="factory-default-branch") | .id' || true)"
if [ -n "$existing" ]; then
  gh api -X PUT "repos/$REPO/rulesets/$existing" --input /tmp/factory-ruleset.json >/dev/null && echo "   updated ruleset $existing"
else
  gh api -X POST "repos/$REPO/rulesets" --input /tmp/factory-ruleset.json >/dev/null && echo "   created ruleset"
fi

echo "== Repository variables (identifiers, not secrets) - set these yourself:"
for v in ANTHROPIC_FEDERATION_RULE_ID ANTHROPIC_ORGANIZATION_ID ANTHROPIC_SERVICE_ACCOUNT_ID ANTHROPIC_WORKSPACE_ID; do
  if gh variable get "$v" -R "$REPO" >/dev/null 2>&1; then echo "   $v: set"; else echo "   $v: MISSING  (gh variable set $v -R $REPO --body <value>)"; fi
done
gh variable get FACTORY_PAUSED -R "$REPO" >/dev/null 2>&1 || gh variable set FACTORY_PAUSED -R "$REPO" --body false

if [ -n "$SEED" ]; then
  echo "== Seed issues from $SEED"
  python3 -I -c 'import json,sys
for i in json.load(open(sys.argv[1])): print(json.dumps(i))' "$SEED" | while read -r line; do
    title="$(printf '%s' "$line" | python3 -I -c 'import json,sys; print(json.loads(sys.stdin.read())["title"])')"
    printf '%s' "$line" | python3 -I -c 'import json,sys; print(json.loads(sys.stdin.read())["body"])' > /tmp/seed-body.md
    gh issue create -R "$REPO" --title "$title" --body-file /tmp/seed-body.md --label factory:inbox >/dev/null && echo "   $title"
  done
fi
echo "Done. Also enable: Settings > Actions > General > 'Allow GitHub Actions to create and approve pull requests'."
