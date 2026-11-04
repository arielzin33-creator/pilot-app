#!/usr/bin/env bash
# gates.sh - the factory's deterministic verdict.
#
# Adapted from Addy Osmani's `factory` (MIT License, Copyright (c) 2026 Addy Osmani);
# see NOTICE.md. Changes: lives in .factory/scripts; reads .factory/gates.conf (or
# FACTORY_GATES_CONF); tests the working directory's repository (or FACTORY_ROOT).
#
# This is the one part of the pipeline that cannot be talked out of its verdict
# by a confident paragraph. Every factory skill calls it. Nothing merges without it.
#
# Usage:
#   ./.factory/scripts/gates.sh fast    # types + lint            (~seconds, run constantly)
#   ./.factory/scripts/gates.sh full    # + tests + build         (default, run before any PR)
#   ./.factory/scripts/gates.sh ui      # + end-to-end click-through (Tier 2 UI changes)
#   ./.factory/scripts/gates.sh deep    # + audit + architecture  (load-bearing changes)
#
# Exit codes:  0 = all required gates green   1 = at least one gate red
#              2 = invalid level or a required gate could not run
#
# Output ends with a machine-readable line so an agent cannot paraphrase the result:
#   FACTORY_GATES: level=full status=RED passed=3 failed=1 failing=test skipped=build misconfigured=none
#
# Customise .factory/gates.conf (human-reviewed, load-bearing) for your project.

set -uo pipefail

LEVEL="${1:-full}"
# The repository under test is the working directory's repository, not the script's location:
# the workflow runs a trusted copy of this script from outside the checkout.
ROOT="${FACTORY_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT" || exit 2
# Gate requirements come from the trusted copy when the workflow provides one.
GATES_CONF="${FACTORY_GATES_CONF:-$ROOT/.factory/gates.conf}"

case "$LEVEL" in
  fast|full|ui|deep) ;;
  *)
    echo "error: gate level must be fast, full, ui, or deep (got: $LEVEL)" >&2
    printf 'FACTORY_GATES: level=%s status=MISCONFIGURED passed=0 failed=0 failing=none skipped=none misconfigured=invalid-level\n' "$LEVEL"
    exit 2
    ;;
esac

REQUIRED_FAST="types lint"
REQUIRED_FULL="types lint test"
REQUIRED_UI="types lint test e2e"
REQUIRED_DEEP="types lint test audit architecture"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$GATES_CONF" ]; then
  # This file is protected by the factory contract and contains assignments only.
  # shellcheck disable=SC1090
  source "$GATES_CONF"
elif [ -n "${FACTORY_GATES_CONF:-}" ]; then
  echo "error: FACTORY_GATES_CONF=$FACTORY_GATES_CONF does not exist" >&2
  printf 'FACTORY_GATES: level=%s status=MISCONFIGURED passed=0 failed=0 failing=none skipped=none misconfigured=gates-conf-missing\n' "$LEVEL"
  exit 2
fi

case "$LEVEL" in
  fast) REQUIRED="$REQUIRED_FAST" ;;
  full) REQUIRED="$REQUIRED_FULL" ;;
  ui)   REQUIRED="$REQUIRED_UI" ;;
  deep) REQUIRED="$REQUIRED_DEEP" ;;
esac

for gate in $REQUIRED; do
  case "$gate" in
    types|lint|test|build|deps|e2e|holdout|migrations|audit|mutation|architecture) ;;
    *)
      echo "error: unknown required gate in .factory/gates.conf: $gate" >&2
      printf 'FACTORY_GATES: level=%s status=MISCONFIGURED passed=0 failed=0 failing=none skipped=none misconfigured=unknown-required-gate:%s\n' "$LEVEL" "$gate"
      exit 2
      ;;
  esac
done

PASSED=0
FAILED=0
FAILING=""
SKIPPED=""
MISCONFIGURED=""

c_red()   { printf '\033[31m%s\033[0m\n' "$1"; }
c_green() { printf '\033[32m%s\033[0m\n' "$1"; }
c_dim()   { printf '\033[2m%s\033[0m\n' "$1"; }

# run <name> <command...>  - runs a gate, records the verdict, never exits early.
RAN=" "
run() {
  local name="$1"; shift
  RAN="$RAN$name "
  printf '\n=== gate: %s ===\n' "$name"
  if "$@"; then
    c_green "PASS  $name"
    PASSED=$((PASSED + 1))
  else
    c_red   "FAIL  $name"
    FAILED=$((FAILED + 1))
    FAILING="${FAILING}${FAILING:+,}${name}"
  fi
}

skip() {
  RAN="$RAN$1 "
  c_dim "SKIP  $1 ($2)"
  SKIPPED="${SKIPPED}${SKIPPED:+,}$1"
  case " $REQUIRED " in
    *" $1 "*) MISCONFIGURED="${MISCONFIGURED}${MISCONFIGURED:+,}$1" ;;
  esac
}

has()      { command -v "$1" >/dev/null 2>&1; }
pkg_has()  { [ -f package.json ] && node -e "process.exit(require('./package.json').scripts?.['$1']?0:1)" 2>/dev/null; }

# ---------------------------------------------------------------------------
# DETECT - identify the project type. Edit this block for your repo.
# ---------------------------------------------------------------------------
STACK="unknown"
if   [ -f package.json ];      then STACK="node"
elif [ -f pyproject.toml ] || [ -f setup.py ]; then STACK="python"
elif [ -f Cargo.toml ];        then STACK="rust"
elif [ -f go.mod ];            then STACK="go"
fi

# Pick the package manager rather than assuming npm.
PM="npm"
if   [ -f pnpm-lock.yaml ];   then PM="pnpm"
elif [ -f yarn.lock ];        then PM="yarn"
elif [ -f bun.lockb ];        then PM="bun"
fi
PMRUN="$PM run"
[ "$PM" = "npm" ] && PMRUN="npm run --silent"

printf 'factory gates | level=%s stack=%s pm=%s\n' "$LEVEL" "$STACK" "$PM"

# ---------------------------------------------------------------------------
# TIER 1 - fast. Types and lint. Cheap enough to run on every edit.
# ---------------------------------------------------------------------------
case "$STACK" in
  node)
    if pkg_has typecheck;    then run types $PMRUN typecheck
    elif [ -f tsconfig.json ] && has npx; then run types npx --no-install tsc --noEmit
    else skip types "no typecheck script or tsconfig.json"; fi

    if pkg_has lint;         then run lint $PMRUN lint
    elif has npx && [ -f eslint.config.js -o -f eslint.config.mjs -o -f .eslintrc.json -o -f .eslintrc.cjs ]; then
                                  run lint npx --no-install eslint .
    else skip lint "no lint script or eslint config"; fi
    ;;
  python)
    if has ruff;  then run lint  ruff check .;  else skip lint  "ruff not installed"; fi
    if has mypy;  then run types mypy .;        else skip types "mypy not installed"; fi
    ;;
  rust)
    run types cargo check --all-targets
    if has cargo-clippy || cargo clippy --version >/dev/null 2>&1; then
      run lint cargo clippy --all-targets -- -D warnings
    else skip lint "clippy not installed"; fi
    ;;
  go)
    run types go vet ./...
    if has golangci-lint; then run lint golangci-lint run; else skip lint "golangci-lint not installed"; fi
    ;;
  *)
    skip types "unknown stack"; skip lint "unknown stack"
    ;;
esac

# ---------------------------------------------------------------------------
# TIER 2 - full. Tests and build. The default gate before any PR.
# ---------------------------------------------------------------------------
if [ "$LEVEL" = "full" ] || [ "$LEVEL" = "ui" ] || [ "$LEVEL" = "deep" ]; then
  case "$STACK" in
    node)
      if pkg_has test; then run test $PMRUN test; else skip test "no test script"; fi
      if pkg_has build; then run build $PMRUN build; else skip build "no build script"; fi
      # Supply chain (spec §6.5): lockfile resolves only from allowed registries with
      # integrity hashes, no git/file/URL specifiers, install scripts only from an allow-list.
      # Database migrations (Phase D): expand/contract rules, append-only history.
      if [ -d migrations ] && [ -f "$SCRIPT_DIR/release.py" ]; then
        run migrations python3 -I "$SCRIPT_DIR/release.py" migration-lint --dir migrations
      fi
      if [ -f package-lock.json ] && [ -f "$SCRIPT_DIR/factory.py" ]; then
        run deps python3 -I "$SCRIPT_DIR/factory.py" lockfile-check --root "$ROOT"
      else skip deps "no package-lock.json"; fi
      ;;
    python)
      if has pytest; then run test pytest -q; else skip test "pytest not installed"; fi
      ;;
    rust)
      run test cargo test --all
      run build cargo build --release
      ;;
    go)
      run test go test ./...
      run build go build ./...
      ;;
    *) skip test "unknown stack"; skip build "unknown stack" ;;
  esac
fi

# ---------------------------------------------------------------------------
# UI - click-through (rung 5, Phase B): the product is run and driven in a browser, with
# traces and screenshots kept as evidence (test-results/, playwright-report/).
# ---------------------------------------------------------------------------
if [ "$LEVEL" = "ui" ] || [ "$LEVEL" = "deep" ]; then
  if [ "$STACK" = "node" ] && pkg_has test:e2e; then run e2e $PMRUN test:e2e
  else skip e2e "no test:e2e script"; fi
fi

# Holdout scenarios (Phase B): acceptance checks kept outside the repository, so the agent
# never sees them (PRAC-01, VER-13). Run when the workflow provides FACTORY_HOLDOUT_DIR.
if [ -n "${FACTORY_HOLDOUT_DIR:-}" ] && [ "$LEVEL" != "fast" ]; then
  if [ -x "$FACTORY_HOLDOUT_DIR/run.sh" ]; then run holdout "$FACTORY_HOLDOUT_DIR/run.sh" "$ROOT"
  else skip holdout "FACTORY_HOLDOUT_DIR has no executable run.sh"; fi
fi

# ---------------------------------------------------------------------------
# TIER 3 - deep. Security, complexity, mutation, architecture rules.
# Run on anything touching a load-bearing path (see docs/factory/CHARTER.md).
# ---------------------------------------------------------------------------
if [ "$LEVEL" = "deep" ]; then
  case "$STACK" in
    node)
      # In the sandbox there is no network; the workflow runs the audit outside it (the audit
      # reads the lockfile and executes no project code) and passes the result in.
      if [ -n "${FACTORY_AUDIT_STATUS:-}" ]; then run audit test "$FACTORY_AUDIT_STATUS" = pass
      else run audit $PM audit --audit-level=high; fi
      if pkg_has mutation; then run mutation $PMRUN mutation
      else skip mutation "no 'mutation' script (see CHARTER.md - coverage is not a substitute)"; fi
      ;;
    python)
      if has pip-audit; then run audit pip-audit; else skip audit "pip-audit not installed"; fi
      if has mutmut;    then run mutation mutmut run; else skip mutation "mutmut not installed"; fi
      ;;
    rust)
      if has cargo-audit; then run audit cargo audit; else skip audit "cargo-audit not installed"; fi
      ;;
    go)
      if has govulncheck; then run audit govulncheck ./...; else skip audit "govulncheck not installed"; fi
      ;;
    *)
      skip audit "unknown stack"
      skip mutation "unknown stack"
      ;;
  esac

  # Architecture rules: plain grep assertions are enough to start and are
  # impossible for an agent to argue with. Add your own below.
  printf '\n=== gate: architecture ===\n'
  RAN="${RAN}architecture "
  ARCH_FAIL=0
  # Example rule: nothing outside src/db may import the raw database client.
  # if grep -rn "from '.*db/client'" src --include='*.ts' | grep -v '^src/db/'; then
  #   c_red "architecture: db/client imported outside src/db"; ARCH_FAIL=1
  # fi
  if [ "$ARCH_FAIL" -eq 0 ]; then
    c_green "PASS  architecture"; PASSED=$((PASSED + 1))
  else
    c_red   "FAIL  architecture"; FAILED=$((FAILED + 1)); FAILING="${FAILING}${FAILING:+,}architecture"
  fi
fi

# ---------------------------------------------------------------------------
# VERDICT
# ---------------------------------------------------------------------------
# A required gate that neither ran nor was skipped (for example `holdout` without a holdout
# directory, or `deps` on a non-node stack) is a misconfiguration, never a silent pass.
for gate in $REQUIRED; do
  case "$RAN" in *" $gate "*) ;; *) MISCONFIGURED="${MISCONFIGURED}${MISCONFIGURED:+,}$gate" ;; esac
done

STATUS="GREEN"
EXIT_STATUS=0
if [ -n "$MISCONFIGURED" ]; then
  STATUS="MISCONFIGURED"
  EXIT_STATUS=2
elif [ "$FAILED" -gt 0 ]; then
  STATUS="RED"
  EXIT_STATUS=1
fi

printf '\n---------------------------------------------\n'
[ -n "$SKIPPED" ] && c_dim "skipped: $SKIPPED"
case "$STATUS" in
  GREEN) c_green "GATES GREEN ($PASSED passed)" ;;
  RED) c_red "GATES RED ($FAILED failed: $FAILING)" ;;
  MISCONFIGURED) c_red "GATES MISCONFIGURED (required gates skipped: $MISCONFIGURED)" ;;
esac

# The machine-readable line. Skills are instructed to quote this verbatim and
# are forbidden from reporting a result that contradicts it.
printf 'FACTORY_GATES: level=%s status=%s passed=%d failed=%d failing=%s skipped=%s misconfigured=%s\n' \
  "$LEVEL" "$STATUS" "$PASSED" "$FAILED" "${FAILING:-none}" "${SKIPPED:-none}" "${MISCONFIGURED:-none}"

exit "$EXIT_STATUS"
