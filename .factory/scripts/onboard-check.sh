#!/usr/bin/env bash
# onboard-check.sh - can an agent build, run and verify this repository without help?
# The automatable part of the repository onboarding checklist (spec §9.4). Run in the target
# repository after install.sh. Exit 0 when every required item passes.
# Usage: onboard-check.sh [--run-gates]
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not a git repository" >&2; exit 2; }
cd "$ROOT"
RUN_GATES=0; [ "${1:-}" = "--run-gates" ] && RUN_GATES=1
pass=0; fail=0; warn=0
ok()   { printf 'PASS  %-4s %s\n' "$1" "$2"; pass=$((pass+1)); }
bad()  { printf 'FAIL  %-4s %s\n' "$1" "$2"; fail=$((fail+1)); }
note() { printf 'WARN  %-4s %s\n' "$1" "$2"; warn=$((warn+1)); }
script() { node -e "process.exit(require('./package.json').scripts?.['$1']?0:1)" 2>/dev/null; }

# Reference stack (product line, spec §1.2)
[ -f package.json ] && [ -f package-lock.json ] && ok "1.2" "npm project with a committed lockfile" || bad "1.2" "reference stack needs package.json and package-lock.json"
[ -f tsconfig.json ] && grep -q '"strict": *true' tsconfig.json && ok "1.2" "TypeScript strict mode" || bad "1.2" "tsconfig.json with \"strict\": true"
# 1 one-command boot
script start && ok "1" "start script" || bad "1" "no 'start' script (one-command boot)"
script db:migrate && ok "1" "db:migrate script" || note "1" "no 'db:migrate' script (fine if the product has no database)"
[ -f .factory/sandbox/setup.sh ] && ok "12" "sandbox setup script present" || bad "12" ".factory/sandbox/setup.sh missing"
# 2-4 gates
for s in typecheck lint test build; do script "$s" && ok "3" "'$s' script" || bad "3" "no '$s' script (gates.sh would report MISCONFIGURED)"; done
if grep -qE '\.(tsx|jsx)' <(git ls-files) ; then
  script test:e2e && ok "7" "UI repository has an end-to-end suite (test:e2e)" || bad "7" "UI repository without 'test:e2e' (Playwright click-through, Tier 2 gate)"
fi
[ -x .factory/scripts/gates.sh ] && ok "4" "verify entry point (.factory/scripts/gates.sh)" || bad "4" "gates.sh missing"
# 5 agent docs
if [ -f AGENTS.md ]; then
  n="$(wc -l < AGENTS.md)"; [ "$n" -le 200 ] && ok "5" "AGENTS.md ($n lines)" || note "5" "AGENTS.md is $n lines (aim for about 100)"
else bad "5" "AGENTS.md missing"; fi
grep -q '@AGENTS.md' CLAUDE.md 2>/dev/null && ok "5" "CLAUDE.md imports AGENTS.md" || bad "5" "CLAUDE.md must import @AGENTS.md"
# 6 docs
[ -d docs ] && ok "6" "docs/ present" || note "6" "no docs/ (architecture and conventions notes)"
# 8 twins
if [ -d src/providers ] || grep -rqs "twin" docs/ README.md 2>/dev/null; then ok "8" "dependency twins referenced"; else note "8" "no twins found (src/providers/** or docs mention); required for every external SaaS"; fi
# 9 holdouts
[ -n "${FACTORY_HOLDOUT_REPO:-}" ] && ok "9" "holdout repository configured" || note "9" "holdout scenarios not configured (optional; see TWINS-AND-HOLDOUTS.md)"
# 10 charter
python3 -I -c 'import json,sys; p=json.load(open(".factory/policy.json")); sys.exit(0 if p.get("charter_status")=="ready" else 1)' 2>/dev/null \
  && ok "10" "charter approved (charter_status: ready)" || bad "10" "charter not approved: review docs/factory/CHARTER.md, set charter_status ready"
# 11 governance files
grep -q '/.github/' .github/CODEOWNERS 2>/dev/null && grep -q '/.claude/' .github/CODEOWNERS && ok "11" "CODEOWNERS covers .github/ and .claude/" || bad "11" "CODEOWNERS must cover /.github/ and /.claude/"
python3 -I .factory/scripts/factory.py config-hashes >/dev/null 2>&1 && ok "11" "configuration hashes match" || bad "11" "configuration hashes do not match (factory.py config-hashes --update in a reviewed PR)"
python3 -I .factory/scripts/factory.py scan-keys >/dev/null 2>&1 && ok "8.s" "no long-lived keys in the repository" || bad "8.s" "long-lived key pattern found (factory.py scan-keys)"
# 13 ledger hooks
grep -q "ledger-commit.sh" .github/workflows/factory-implement.yml 2>/dev/null && ok "13" "ledger wired into the implement workflow" || bad "13" "ledger not wired"
# 14 evals
n="$(ls evals/tasks/*/task.json 2>/dev/null | wc -l)"
[ "$n" -ge 5 ] && ok "14" "$n eval tasks" || note "14" "$n eval tasks (five from the repository's own history are required before scale-up)"
# 15 the five questions answered in the charter
grep -q "LIFE-23\|five questions" docs/factory/CHARTER.md 2>/dev/null && ok "15" "charter answers the five LIFE-23 questions" || note "15" "charter does not record the five LIFE-23 questions"
if [ "$RUN_GATES" -eq 1 ]; then
  ./.factory/scripts/gates.sh full >/tmp/onboard-gates.log 2>&1 && ok "2" "gates full GREEN" || bad "2" "gates full not green ($(grep '^FACTORY_GATES:' /tmp/onboard-gates.log | tail -1))"
fi
printf '\nONBOARD: pass=%d fail=%d warn=%d status=%s\n' "$pass" "$fail" "$warn" "$([ $fail -eq 0 ] && echo READY || echo NOT_READY)"
[ "$fail" -eq 0 ]
