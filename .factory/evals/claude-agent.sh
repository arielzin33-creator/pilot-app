#!/usr/bin/env bash
# claude-agent.sh - eval agent: runs Claude Code headless on one eval task, the same way the
# implement workflow does (worker settings, caps, structured result), and writes the cost.
# Usage: evals.py run --agent "cmd:bash $PWD/.factory/evals/claude-agent.sh" --model <id> ...
# Needs Anthropic access in the environment running the evals (owner-run; see EVALS.md).
set -euo pipefail
: "${EVAL_PROMPT_FILE:?}" "${EVAL_RESULT_FILE:?}"
MODEL="${EVAL_MODEL:-claude-sonnet-5-5}"
TURNS="${EVAL_MAX_TURNS:-40}"
BUDGET="${EVAL_MAX_BUDGET_USD:-6}"
SETTINGS="${EVAL_SETTINGS:-.factory/worker-settings.json}"
# Keep the eval suite out of the agent's reach: add it to the sandbox's denyRead list and make
# every sandbox path absolute (render-settings).
RENDERED="$(mktemp)"; trap 'rm -f "$RENDERED"' EXIT
python3 -I - "$SETTINGS" "$RENDERED" "${EVAL_DENY_READ:-}" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
if sys.argv[3]:
    fs = s.setdefault("sandbox", {}).setdefault("filesystem", {})
    fs["denyRead"] = list(fs.get("denyRead", [])) + [sys.argv[3]]
    s.setdefault("permissions", {}).setdefault("deny", []).append(f"Read(/{sys.argv[3]}/**)")
json.dump(s, open(sys.argv[2], "w"))
PY
python3 -I .factory/scripts/factory.py render-settings --in "$RENDERED" --out "$RENDERED" --root "$PWD" >/dev/null
SETTINGS="$RENDERED"
set +e
claude -p "$(cat "$EVAL_PROMPT_FILE")" --model "$MODEL" --settings "$SETTINGS" \
  --max-turns "$TURNS" --max-budget-usd "$BUDGET" --permission-mode dontAsk \
  --output-format json > .eval-claude-result.json 2> .eval-claude-stderr.log
code=$?
set -e
python3 -I - .eval-claude-result.json "$EVAL_RESULT_FILE" "$code" <<'PY'
import json, sys
try:
    r = json.load(open(sys.argv[1]))
except Exception:
    r = {}
status = "done" if sys.argv[3] == "0" and r.get("subtype") == "success" else "error"
text = str(r.get("result", "")).lower()
if "escalate" in text:
    status = "escalate"
json.dump({"status": status, "cost_usd": float(r.get("total_cost_usd") or 0), "turns": r.get("num_turns")},
          open(sys.argv[2], "w"))
PY
rm -f .eval-claude-result.json .eval-claude-stderr.log
