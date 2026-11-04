#!/usr/bin/env bash
# ledger-commit.sh - append one run record to the factory/ledger branch (one file per run;
# never a shared log). Retries on a concurrent push. Needs a token with contents: write.
#
# Usage: ledger-commit.sh <record.json> <run-id>
set -euo pipefail
RECORD="${1:?record file}"
RUN_ID="${2:?run id}"
ROOT="$(git rev-parse --show-toplevel)"
DAY="$(date -u +%Y-%m-%d)"
WT="$(mktemp -d)"
trap 'git -C "$ROOT" worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$WT"' EXIT

git -C "$ROOT" config user.name "factory-ledger[bot]"
git -C "$ROOT" config user.email "factory-ledger@users.noreply.github.com"

for attempt in 1 2 3 4 5; do
  if git -C "$ROOT" ls-remote --exit-code --heads origin factory/ledger >/dev/null 2>&1; then
    git -C "$ROOT" fetch --quiet origin factory/ledger:refs/remotes/origin/factory/ledger
    git -C "$ROOT" worktree add --force --detach "$WT" refs/remotes/origin/factory/ledger >/dev/null
  else
    git -C "$ROOT" worktree add --force --detach "$WT" HEAD >/dev/null
    git -C "$WT" checkout --orphan factory-ledger-new >/dev/null
    git -C "$WT" rm -rf --quiet . >/dev/null 2>&1 || true
    printf '# Factory run ledger\n\nOne JSON record per run under runs/YYYY-MM-DD/. Written by workflows only.\n' > "$WT/README.md"
    git -C "$WT" add README.md
  fi
  mkdir -p "$WT/runs/$DAY"
  cp "$RECORD" "$WT/runs/$DAY/$RUN_ID.json"
  git -C "$WT" add "runs/$DAY/$RUN_ID.json"
  git -C "$WT" commit --quiet -m "ledger: run $RUN_ID" -m "Factory-Run: $RUN_ID"
  if git -C "$WT" push --quiet origin HEAD:refs/heads/factory/ledger; then
    echo "ledger: committed runs/$DAY/$RUN_ID.json"
    exit 0
  fi
  echo "ledger: push raced (attempt $attempt); retrying" >&2
  git -C "$ROOT" worktree remove --force "$WT" >/dev/null 2>&1 || true
  rm -rf "$WT"; WT="$(mktemp -d)"
  sleep $((attempt * 3))
done
echo "ledger: failed to commit after 5 attempts" >&2
exit 1
