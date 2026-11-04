#!/usr/bin/env bash
# Stop hook: keep the agent working while gates are red, up to a retry cap
# (Spotify's stop-hook verifiers, PRAC-47; spec §5.6). Exit 2 "prevents Claude from
# stopping". The authoritative verdict is the separate, secret-less gates job; this hook
# only shortens the loop.
#
# Gates run agent-written code. When bubblewrap is available they run with no network
# (--unshare-net) because hooks execute outside Claude Code's Bash sandbox (TOOL-14).
# Without bubblewrap the hook skips (exit 0) rather than run agent code with network.
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
LEVEL="${FACTORY_GATE_LEVEL:-full}"
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

if command -v bwrap >/dev/null 2>&1 && bwrap --dev-bind / / --unshare-net true >/dev/null 2>&1; then
  OUT="$(cd "$ROOT" && bwrap --dev-bind / / --unshare-net --die-with-parent -- ./.factory/scripts/gates.sh "$LEVEL" 2>&1)"
  STATUS=$?
else
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
