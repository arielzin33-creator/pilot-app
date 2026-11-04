#!/usr/bin/env bash
# PreToolUse guard (spec §4.3-4.4). Delegates to factory.py, which fails closed.
# Wired with onFailure "block" so a crash or timeout also blocks (Claude Code v2.1.295+).
# Exit 2 = block (stderr is returned to the agent as feedback).
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
if ! command -v python3 >/dev/null 2>&1; then
  echo "BLOCKED by factory guard: python3 unavailable; failing closed" >&2
  exit 2
fi
exec python3 -I "$ROOT/.factory/scripts/factory.py" hook-guard
