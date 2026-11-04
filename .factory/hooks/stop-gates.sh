#!/usr/bin/env bash
# Stop hook: keep the agent working while gates are red, up to a retry cap
# (Spotify's stop-hook verifiers, PRAC-47; spec §5.6). Exit 2 "prevents Claude from
# stopping". The authoritative verdict is the separate, secret-less gates job; this hook
# only shortens the loop.
#
# Gates run agent-written code, and hooks execute outside Claude Code's Bash sandbox
# (TOOL-14). They therefore run through sandbox-exec.sh: no network, new PID namespace,
# cleared environment, filesystem read-only except the working tree. Without bubblewrap the
# hook skips (exit 0) rather than run agent code unconfined.
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
LEVEL="${FACTORY_GATE_LEVEL:-full}"
[ "$LEVEL" = "ui" ] && LEVEL=full   # browsers are provisioned in the gates job, not the agent job
MAX="${FACTORY_MAX_STOP_RETRIES:-2}"
INPUT="$(cat || true)"
SESSION="$(printf '%s' "$INPUT" | python3 -I -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id") or "nosession")
except Exception: print("nosession")' 2>/dev/null || echo nosession)"
COUNTER="${RUNNER_TEMP:-/tmp}/factory-stop-${SESSION//[^A-Za-z0-9_-]/_}"

[ "${FACTORY_RUN:-0}" = "1" ] || exit 0          # only enforce inside factory runs

count=0; [ -f "$COUNTER" ] && count="$(cat "$COUNTER" 2>/dev/null || echo 0)"
if [ "$count" -ge "$MAX" ]; then
  echo "factory stop-gates: retry cap ($MAX) reached; stopping. Return status 'escalate' if gates are not green." >&2
  exit 0
fi

# Read-only everywhere except the working tree; .git read-only too, so code under test
# cannot plant git configuration (core.fsmonitor, hooks) that later trusted steps would run.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # trusted copy in factory runs
OUT="$(cd "$ROOT" && SANDBOX_RO_GIT=1 FACTORY_GATES_CONF="$HERE/gates.conf" SANDBOX_KEEP_ENV=FACTORY_GATES_CONF \
        bash "$HERE/scripts/sandbox-exec.sh" "$ROOT" -- bash "$HERE/scripts/gates.sh" "$LEVEL" 2>&1)"
STATUS=$?
if [ "$STATUS" -eq 125 ]; then
  echo "factory stop-gates: bubblewrap unavailable; skipping in-loop gates (the gates job still decides)." >&2
  exit 0
fi

LINE="$(printf '%s\n' "$OUT" | grep '^FACTORY_GATES:' | tail -1)"
if [ "$STATUS" -eq 0 ]; then
  rm -f "$COUNTER"
  exit 0
fi
echo $((count + 1)) > "$COUNTER"
{
  echo "Gates are not green. Fix the cause (do not edit tests, gate config or load-bearing files):"
  printf '%s\n' "$OUT" | tail -40
  echo "${LINE:-FACTORY_GATES: missing}"
} >&2
exit 2
