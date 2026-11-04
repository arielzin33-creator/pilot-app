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
factory:blocked|C2E0C6|Waits for dependent tasks (released by factory-backlog)
factory:from-spec|BFD4F2|Generated from an approved tasks.md
factory:freeze|B60205|Error budget exhausted: release and factory freeze (P0 only)
factory:brief|FEF2C0|PR/FAQ brief under review (Phase E gate 1)
factory:incident|D93F0B|Production signal turned into an issue (Phase D)
factory:retro|C5DEF5|Weekly factory retrospective
factory:preview|BFDADC|A human allows a preview deployment of a factory PR
factory:auto-merged|0E8A16|Merged by the factory under a qualified Tier 0 class
factory:audit-opened|C2E0C6|Auto-merged PR listed in an escape-audit checklist issue
factory:sampled-review|FBCA04|Post-merge review of a sampled auto-merged PR
factory:escape|B60205|A defect escaped review; body says Escaped from #N (revokes auto-merge)
factory:no-auto-merge|E99695|Never auto-merge this PR
factory:class-docs|EDEDED|Auto-merge class: docs
factory:class-dependency-patch|EDEDED|Auto-merge class: dependency-patch
LABELS

echo "== Ruleset on $DEFAULT_BRANCH"
# Qualified auto-merge (spec §9.6): the dedicated merge App, if configured, may bypass the review
# rules through pull requests only. It is used solely by factory-automerge's merge job.
MERGE_APP_ID="$(gh variable get FACTORY_MERGE_APP_ID -R "$REPO" 2>/dev/null || true)"
EXTRA_BYPASS=""
if [[ "$MERGE_APP_ID" =~ ^[0-9]+$ ]]; then
  EXTRA_BYPASS=", { \"actor_id\": $MERGE_APP_ID, \"actor_type\": \"Integration\", \"bypass_mode\": \"pull_request\" }"
  echo "   merge App $MERGE_APP_ID added as a pull-request bypass actor"
fi
cat > /tmp/factory-ruleset.json <<JSON
{
  "name": "factory-default-branch",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [ { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request" }$EXTRA_BYPASS ],
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
  gh api -X PUT "repos/$REPO/rulesets/$existing" --input /tmp/factory-ruleset.json >/dev/null
  echo "   updated ruleset $existing"
else
  gh api -X POST "repos/$REPO/rulesets" --input /tmp/factory-ruleset.json >/dev/null
  echo "   created ruleset"
fi

echo "== Deployment environments (Phase D)"
OWNER_LOGIN="${OWNER_LOGIN:-$(gh api user --jq .login)}"
OWNER_ID="$(gh api "users/$OWNER_LOGIN" --jq .id)"
for envname in preview staging production factory-merge; do
  if [ "$envname" = production ]; then
    # A human (the owner) approves every production deployment. prevent_self_review stays false
    # because a solo owner both merges and approves; with a second operator, set it to true.
    body="{\"wait_timer\":0,\"prevent_self_review\":false,\"reviewers\":[{\"type\":\"User\",\"id\":$OWNER_ID}],\"deployment_branch_policy\":{\"protected_branches\":false,\"custom_branch_policies\":true}}"
  elif [ "$envname" = staging ] || [ "$envname" = factory-merge ]; then
    # factory-merge holds FACTORY_MERGE_APP_KEY; only the default branch's workflow can use it.
    body='{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}'
  else
    body='{}'
  fi
  # No "&& echo" here or on the ruleset calls: set -e ignores a failure inside an && list, which
  # would report success while leaving the branch or production unprotected.
  printf '%s' "$body" | gh api -X PUT "repos/$REPO/environments/$envname" --input - >/dev/null
  echo "   $envname"
  # Only the default branch may deploy to staging and production (explicit policy; rulesets
  # may not count as "protected branches" for environments).
  if [ "$envname" != preview ]; then
    gh api -X POST "repos/$REPO/environments/$envname/deployment-branch-policies" -f name="$DEFAULT_BRANCH" -f type=branch >/dev/null 2>&1 || true
  fi
done
# Required reviewers need a public repository below GitHub Enterprise. Without the rule, production
# deploys with nobody's approval, so stop rather than carry on.
reviewers="$(gh api "repos/$REPO/environments/production" --jq '[.protection_rules[]? | select(.type == "required_reviewers")] | length')"
if [ "$reviewers" -eq 0 ]; then
  echo "ERROR: production has no required reviewer. On a private repository this needs GitHub Enterprise; make the repository public or provide another production gate." >&2
  exit 1
fi
echo "   Add environment secrets yourself (DEPLOY_TOKEN or DEPLOY_SSH_KEY) for the provider you choose."
echo "   If you use auto-merge, put FACTORY_MERGE_APP_KEY in the factory-merge environment, not the repository."

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
