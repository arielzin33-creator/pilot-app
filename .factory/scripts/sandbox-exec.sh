#!/usr/bin/env bash
# sandbox-exec.sh - run a command that executes agent-written code (tests, builds, lint
# plugins) in a bubblewrap sandbox: no network, a fresh PID namespace (nothing survives the
# command), a cleared environment, and a read-only view of the whole filesystem except one
# writable directory. Trusted copies of the engine, the evidence directory and $RUNNER_TEMP
# are therefore read-only to the code under test (spec §4.3, §4.4; review finding 2026-11-04).
#
# Usage: sandbox-exec.sh <writable-dir> -- <command...>
# Env:   SANDBOX_RO_GIT=1      also make <writable-dir>/.git read-only
#        SANDBOX_KEEP_ENV      space-separated variable names to pass through (e.g. "LEVEL")
# Exit:  the command's status; 125 when bubblewrap is unavailable (callers decide; the
#        workflow treats it as MISCONFIGURED, the Stop hook skips).
set -uo pipefail
DIR="${1:?writable dir}"; shift
[ "${1:-}" = "--" ] || { echo "usage: $0 <writable-dir> -- <command...>" >&2; exit 2; }
shift
[ "$#" -gt 0 ] || { echo "usage: $0 <writable-dir> -- <command...>" >&2; exit 2; }
DIR="$(cd "$DIR" && pwd)"

if ! command -v bwrap >/dev/null 2>&1 || ! bwrap --ro-bind / / --unshare-net --unshare-pid true >/dev/null 2>&1; then
  echo "SANDBOX: status=UNAVAILABLE reason=bubblewrap-missing-or-user-namespaces-disabled" >&2
  exit 125
fi

args=(--ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --bind "$DIR" "$DIR")
# Factory control files stay read-only even inside the writable checkout: code under test
# must not rewrite the hooks, engine or settings that later run outside the sandbox.
for rel in .factory .claude .github .agents .codex .mcp.json AGENTS.md CLAUDE.md; do
  [ -e "$DIR/$rel" ] && args+=(--ro-bind "$DIR/$rel" "$DIR/$rel")
done
if [ "${SANDBOX_RO_GIT:-0}" = "1" ] && [ -e "$DIR/.git" ]; then
  args+=(--ro-bind "$DIR/.git" "$DIR/.git")
fi
args+=(--unshare-net --unshare-pid --unshare-ipc --die-with-parent --new-session --chdir "$DIR"
       --clearenv --setenv PATH "$PATH" --setenv HOME /tmp/home --setenv TMPDIR /tmp
       --setenv LANG C.UTF-8 --setenv CI true --setenv npm_config_cache /tmp/npm-cache
       --setenv npm_config_update_notifier false)
for name in ${SANDBOX_KEEP_ENV:-}; do
  case "$name" in
    *[!A-Za-z0-9_]*|"") continue ;;
    ACTIONS_*|GITHUB_TOKEN|GH_TOKEN|ANTHROPIC_*|CLAUDE_CODE_OAUTH_TOKEN) continue ;;  # never
  esac
  [ -n "${!name+x}" ] && args+=(--setenv "$name" "${!name}")
done
exec bwrap "${args[@]}" -- "$@"
