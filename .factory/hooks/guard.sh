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
# The engine and policy are taken from next to this hook. In factory runs the hooks are
# invoked from the trusted pre-agent copy (render-settings --hooks-dir), so nothing in the
# working tree is executed; interactively they resolve to the repository's own copy.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export FACTORY_POLICY="${FACTORY_POLICY:-$HERE/policy.json}"
exec python3 -I "$HERE/scripts/factory.py" hook-guard
