# AGENTS.md

Map for any coding agent working in this repository. Advisory context: the rules that must hold
are enforced by hooks, permissions, the sandbox and the workflows, not by this file.

## What this repository is

<!-- Replace with five lines about the product. See docs/factory/PRODUCT-LINE.md. -->
A repository on the factory reference stack (TypeScript, Node.js 22, npm, Vitest, ESLint).

## Boot, test, verify (one command each)

| Purpose | Command |
|---|---|
| Install | `npm ci` |
| Verify everything (the gate) | `./.factory/scripts/gates.sh full` |
| Fast check while editing | `./.factory/scripts/gates.sh fast` |
| Tests only | `npm test` |

The final line of `gates.sh` is the verdict: `FACTORY_GATES: level=… status=GREEN|RED|MISCONFIGURED …`.
Quote it verbatim. `MISCONFIGURED` is not green.

## Map

| Topic | File |
|---|---|
| Rules for agents | `docs/factory/CONTRACT.md` |
| Tiers, load-bearing paths, stop conditions | `docs/factory/CHARTER.md` and `.factory/policy.json` |
| Product line and stack | `docs/factory/PRODUCT-LINE.md` |
| Metrics | `docs/factory/METRICS.md` |
| Decisions | `docs/factory/DECISIONS.md` |
| Architecture and conventions | `docs/` (keep current in the same PR as code) |

## Load-bearing (never edited by unattended runs)

`.factory/`, `.claude/`, `.github/`, `AGENTS.md`, `CLAUDE.md`, `docs/factory/CHARTER.md`,
`docs/factory/CONTRACT.md`, test/lint/type configuration, `package.json` scripts, existing test
files, `src/auth/**`, `src/payments/**`, migrations and SQL. Full list: `.factory/policy.json`.

## Definition of done

1. Gates GREEN at the required level.
2. A new test fails without the change and passes with it.
3. The diff does the one thing the issue describes.
4. The hand-back explains why the change is safe.

## When to stop

Stop and return `escalate` when the task needs a load-bearing change, an existing test edit, a
new dependency, more than 5 files or 400 lines, or when the issue is ambiguous. Never weaken a
check to get green.
