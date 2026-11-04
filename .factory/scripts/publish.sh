#!/usr/bin/env bash
# publish.sh - claim the branch, commit the verified patch, open a DRAFT pull request with the
# evidence pack, set the factory/gates commit status, and move labels. Never merges.
#
# Usage: publish.sh <evidence-dir>
# The evidence dir holds: change.patch, issue.json, handoff.json, classify.json, gates.txt,
#   proof.txt, implement.json, verdict.json (may be {}), record-implement.json,
#   record-verify.json (may be absent), base_sha.txt
# Needs: GH_TOKEN (contents/pull-requests/issues/statuses write), GITHUB_REPOSITORY, RUN_ID.
set -euo pipefail
EV="${1:?evidence dir}"
ROOT="$(git rev-parse --show-toplevel)"
REPO="${GITHUB_REPOSITORY:?}"
RUN_ID="${RUN_ID:?}"
ISSUE="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["number"])' "$EV/issue.json")"
BRANCH="claude/fq-$ISSUE"
BASE_SHA="$(cat "$EV/base_sha.txt")"
TIER="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["tier"])' "$EV/classify.json")"
VERSION="$(python3 -I -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("agent",{}).get("claude_code_version") or "unknown")
except Exception: print("unknown")' "$EV/record-implement.json")"

cd "$ROOT"
git config user.name "factory[bot]"
git config user.email "factory@users.noreply.github.com"
git checkout --quiet --detach "$BASE_SHA"
git apply --index --whitespace=nowarn "$EV/change.patch"

TITLE_RAW="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("title",""))' "$EV/issue.json")"
TITLE="$(printf '%s' "$TITLE_RAW" | tr -d '\000-\037' | cut -c1-72)"
git commit --quiet -F - <<EOF
factory: resolve #$ISSUE ($TITLE)

Produced by an unattended factory run and checked by deterministic gates.
A human reviews and merges this change.

Fixes #$ISSUE
Assisted-by: Claude Code $VERSION
Factory-Run: $RUN_ID
Factory-Tier: $TIER
EOF

# Claim: the first push of the deterministic branch wins (no force).
if ! git push --quiet origin "HEAD:refs/heads/$BRANCH" 2>/dev/null; then
  echo "publish: $BRANCH already exists; another run owns this item" >&2
  echo "claimed-elsewhere" > "$EV/publish-outcome.txt"
  exit 3
fi
HEAD_SHA="$(git rev-parse HEAD)"

python3 -I - "$EV" > "$EV/pr-body.md" <<'EOF'
import json, sys, pathlib
ev = pathlib.Path(sys.argv[1])
def j(name, default=None):
    p = ev / name
    try:
        return json.loads(p.read_text())
    except Exception:
        return default if default is not None else {}
def t(name):
    p = ev / name
    return p.read_text().strip() if p.exists() else "(missing)"
impl = j("implement.json"); verdict = j("verdict.json"); cls = j("classify.json")
rec = j("record-implement.json"); rec["verify_agent"] = j("record-verify.json").get("agent", {})
issue = j("issue.json")
agent = rec.get("agent", {}); vagent = rec.get("verify_agent", {})
cost = (agent.get("total_cost_usd_estimate") or 0) + (vagent.get("total_cost_usd_estimate") or 0)
lines = [
    f"Draft produced by the software factory for #{issue.get('number')}. **A human reviews and merges.**",
    "",
    "## Evidence pack",
    "",
    "| Check | Result |",
    "|---|---|",
    f"| Gates (separate job, no secrets) | `{t('gates.txt')}` |",
    f"| Failing-test proof | `{t('proof.txt')}` |",
    f"| Diff policy | tier {cls.get('tier')}, {cls.get('file_count')} files, {cls.get('changed_lines')} lines, stop reasons: {', '.join(cls.get('stop_reasons') or []) or 'none'} |",
    f"| Verifier (advisory, different model) | {verdict.get('decision', 'not run')} |",
    f"| Trajectory monitor (advisory) | {', '.join(sorted({f['detector'] for f in (j('monitor.json').get('findings') or [])})) or 'no flags'} |",
    f"| Estimated model cost | ${cost:.2f} (client-side estimate, not an invoice) |",
    "",
    "## What changed (implementer's account)",
    "",
    str(impl.get("summary", "(no summary)")),
    "",
    "## Why it is safe (implementer's claim, check it against the diff)",
    "",
    str(impl.get("why_safe", "(not stated)")),
    "",
]
if verdict:
    lines += ["## Verifier findings", ""]
    for f in verdict.get("findings", []) or []:
        lines.append(f"- {f}")
    if verdict.get("risk_notes"):
        lines += ["", f"**Read first:** {verdict['risk_notes']}"]
    lines += [""]
if impl.get("questions"):
    lines += ["## Questions for the reviewer", ""] + [f"- {q}" for q in impl["questions"]] + [""]
lines += [
    "## Reviewer checklist",
    "",
    "- [ ] The new test fails for the reason in the issue, not for an incidental one",
    "- [ ] The diff does one thing; nothing unrelated changed",
    "- [ ] No check was weakened and no input was special-cased",
    "- [ ] I can explain this change to someone else",
    "",
    f"Factory-Run: {rec.get('run_id')} · Tier {cls.get('tier')} · gate level {cls.get('gate_level')}",
]
print("\n".join(lines))
EOF

PR_URL="$(gh pr create -R "$REPO" --draft --base "$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)" \
  --head "$BRANCH" --title "factory: resolve #$ISSUE: $TITLE" --body-file "$EV/pr-body.md")"
PR_NUMBER="${PR_URL##*/}"
echo "$PR_NUMBER" > "$EV/pr-number.txt"

# Only the leading, workflow-written verdict counts (text after "|" is the raw log line).
GATES_STATE="failure"; grep -q '^FACTORY_GATES: level=[a-z]* status=GREEN exit=0 ' "$EV/gates.txt" && GATES_STATE="success"
gh api -X POST "repos/$REPO/statuses/$HEAD_SHA" -f state="$GATES_STATE" -f context="factory/gates" \
  -f description="$(cut -c1-140 "$EV/gates.txt")" >/dev/null

DECISION="$(python3 -I -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("decision",""))
except Exception: print("")' "$EV/verdict.json")"
case "$DECISION" in
  verified) gh pr edit "$PR_NUMBER" -R "$REPO" --add-label factory:verified,"factory:tier-$TIER" >/dev/null ;;
  rejected|escalate) gh pr edit "$PR_NUMBER" -R "$REPO" --add-label factory:rejected,"factory:tier-$TIER" >/dev/null ;;
  *) gh pr edit "$PR_NUMBER" -R "$REPO" --add-label "factory:tier-$TIER" >/dev/null ;;
esac
gh pr edit "$PR_NUMBER" -R "$REPO" --add-label factory:awaiting-review >/dev/null
gh issue edit "$ISSUE" -R "$REPO" --remove-label factory:in-progress --remove-label factory:ready-to-implement \
  --add-label factory:awaiting-review >/dev/null
echo "published" > "$EV/publish-outcome.txt"
echo "publish: opened draft PR #$PR_NUMBER for #$ISSUE"
