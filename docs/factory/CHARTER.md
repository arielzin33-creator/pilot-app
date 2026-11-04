# Factory charter

**This file is the human judgment, upstream.** Every factory run reads it. It is load-bearing:
agents never edit it, and a change to it is a human pull request reviewed under CODEOWNERS.
The machine-readable copy is `.factory/policy.json`; the two must agree. After changing either,
run `python3 -I .factory/scripts/factory.py config-hashes --update` in the same pull request.

Specification: *Software Factory Build Specification v0.1* (§1.5 tiers, §5 verification, §5.6
stop conditions, §7 governance). Structure adapted from Addy Osmani's MIT-licensed `factory`
(see `NOTICE.md`).

```
CHARTER_STATUS: draft      # policy.json "charter_status"; the factory refuses to run until "ready"
FACTORY_PHASE: A           # minimum viable factory: one loop, Tier 0 and Tier 1 only
```

Set `charter_status` to `"ready"` in `.factory/policy.json` only after reviewing every section.
Until then every run stops with `CHARTER_NOT_READY`.

---

## 1. Risk tiers (per work item, not per repository)

The tier is assigned by the deterministic classifier in `factory.py` from the files a change
touches. A human label `factory:tier-N` can raise a tier; nothing can lower it.

| Tier | Definition | Human gate | Gate level | Unattended in Phase A |
|---|---|---|---|---|
| 0 Mechanical | Documentation only; dependency version bumps with no new dependency | One human merges on evidence | `fast` (docs) or `full` | Yes |
| 1 Bounded change | Bug fix or small feature in one module; at most 5 files and 400 changed lines; no load-bearing path; acceptance criteria present | One human reads the evidence pack and merges | `full` | Yes |
| 2 Feature | UI files, public API, more than 5 files or 400 lines (up to 15 files and 1,200 lines), new or removed dependency | Spec, plan and tasks approved by a human first; one human reviewer merges | `ui`: `full` plus end-to-end click-through with traces and screenshots | **Only with an approved spec** (Phase B; `SPEC_NOT_APPROVED` otherwise) |
| 3 Load-bearing | Any path in `load_bearing`, `package.json` scripts or engines, test and lint configuration, existing test files | Two independent human approvals | `deep` | **Never** |

**No second reviewer is available (decision of 2026-11-04).** Tier 3 changes therefore never
run unattended, and the factory never produces them. A Tier 3 change is made by a human, in an
interactive session if Claude helps, and merged under CODEOWNERS. See `DECISIONS.md`.

## 2. Load-bearing paths

Listed in `.factory/policy.json` under `load_bearing`. They include authentication, payments,
migrations and SQL, all factory configuration (`.factory/**`, `.claude/**`, `.github/**`,
`AGENTS.md`, `CLAUDE.md`, this charter, the contract), environment files, infrastructure, and
the test, lint, type-check, coverage and transpiler configuration that the gates execute or
read (for example `tsconfig*.json`, `eslint.config.*`, `vitest.config.*`, `vite.config.*`,
`jest.config.*`, `.mocharc*`, `babel.config.*`, test setup files, `pyproject.toml`,
`conftest.py`), Git attribute and hook files (`.gitattributes`, `.gitmodules`, `.husky/**`),
and the `package.json` fields `scripts`, `engines`, `packageManager`, `overrides`,
`workspaces`, `jest`, `mocha`, `ava`, `c8`, `nyc`, `eslintConfig`, `babel` and `vitest`.

Moving or deleting a load-bearing file is a change to it: the classifier does not pair
renames, so both sides of a move are classified.

An agent that can rewrite its own gates has no constraints. Gate configuration is on this list
because agents in one observed 2026 factory authored 40 of 58 CI-changing pull requests and
loosened 7 checks (spec §12, HIST-44).

## 3. Test-file rule

```
TESTS_ARE_LOAD_BEARING: true
```

An unattended run never modifies, renames, deletes or replaces (for example with a symlink)
an existing test file. Adding a new test
file is required for Tier 1 bug fixes and allowed elsewhere. Agents rewrite assertions to match
broken behavior; an unexplained green suite after an agent edited tests is weak evidence.

## 4. What is automatable here

```
AUTOMATABLE:
  - Documentation and comment corrections
  - Dependency version bumps that pass full gates
  - Lint, format and type-only fixes
  - Test coverage added to existing untested functions (new test files)
  - Single-module bug fixes with a reproducible failing test
  - Small, specified behavior changes inside one module (Tier 1 limits)

NEEDS_SPEC:
  - Any user-visible feature or UI change
  - Any public API or exported type change
  - Anything touching more than 5 files or 400 lines

NEVER_AUTOMATE:
  - Merge decisions
  - Changes to load-bearing paths
  - Architectural direction and dependency choices
  - Product intent: whether a feature should exist at all
  - Anything this charter does not cover (default deny)
```

Silence in this file means stop, not proceed.

## 5. Definition of done

A pull request may be marked ready for review only when all hold. The workflow checks each one
independently and does not accept the implementer's word.

```
DONE:
  - gates.sh reports FACTORY_GATES status=GREEN at the required level (separate job, no secrets)
  - prove-test reports PROOF: status=PROVEN (the new test fails without the change)
  - check-diff reports ok: no load-bearing path, no existing test modified, tier <= 1
  - no long-lived key pattern in the diff
  - the diff does the one thing the issue describes (verifier judge; advisory in Phase A)
  - the PR body explains why the change is safe, in words a reviewer can check
```

The failing-test proof is not required, and the evidence pack says so (`PROOF:
status=NOT_REQUIRED` with the reason), for:

- Tier 0 changes: documentation only, or dependency version bumps with no other change. There
  is no behaviour change for a test to pin down.
- Changes that only add new test files. There is no non-test change to revert; the reviewer
  judges whether the new tests are meaningful.

A proof that cannot run is `MISCONFIGURED`, never a pass.

## 6. Gate level by change type

```
GATES:
  docs_only: fast
  tier0: full   (types lint test build deps)
  tier1: full
  tier2: ui     (full plus e2e click-through, rung 5)
  tier3: deep   (full plus audit and architecture; human-run)
```

## 7. Stop conditions

The factory halts and hands the item to a human when any of these is true. Codes are what the
workflow reports; every code is exercised by `tests/`.

| Code | Condition |
|---|---|
| `CHARTER_NOT_READY` | `charter_status` is not `ready` |
| `FACTORY_PAUSED` | repository variable `FACTORY_PAUSED=true` or a `.factory/PAUSED` file |
| `REVIEW_QUEUE_FULL` | 3 or more factory pull requests await human review |
| `DAILY_BUDGET_EXHAUSTED` | estimated spend today reaches `daily_budget_usd` |
| `BASE_NOT_GREEN` | gates on the default branch are not green |
| `NO_ITEM` | nothing is ready to implement |
| `NEEDS_HUMAN_LABEL` | the item carries `factory:needs-human` or `factory:human-only` |
| `CLAIMED_ELSEWHERE` | the item carries `factory:in-progress` (set by the run that claimed it) and the claim is less than `stale_in_progress_hours` old |
| `HANDOFF_MISSING` | no trusted triage handoff on the issue |
| `AMBIGUOUS` | the handoff lacks `done_when` or expected files, or confidence is low |
| `LOAD_BEARING_EXPECTED` | the expected files include a load-bearing path |
| `TIER_NOT_UNATTENDED` | the predicted or labeled tier exceeds the unattended maximum |
| `SPEC_NOT_APPROVED` | a Tier 2 item whose hand-off does not cite a spec folder with spec, plan and tasks all approved (Phase B spec gate) |
| `RELEASE_FREEZE` | an open issue carries `factory:freeze` (error budget exhausted, Phase D SLO policy); humans ship P0 fixes only |
| `RED_GATES_TWICE` | gates were red twice on this item |
| `TOO_MANY_RUNS` | three runs on this item |
| `TOO_MANY_CI_FIX` | three CI-fix commits on this item. *Reserved:* Phase A has no CI-fix loop (the factory does not push follow-up commits to its PRs), so the count is always 0; the code is tested and takes effect when Phase B adds the loop. |

After a run, the diff policy can also stop publication: `LOAD_BEARING_TOUCHED`,
`EXISTING_TEST_MODIFIED`, `TIER_ABOVE_UNATTENDED`, `DIFF_TOO_LARGE`, `EMPTY_DIFF`,
`SECRET_IN_DIFF`.

The review-queue limit is the back-pressure valve: the constraint on a factory is how many
decisions can wait on a human at once, not how many agents can run (PRAC-71, PRAC-13).

## 8. Budgets and caps

| Cap | Value | Enforcement |
|---|---|---|
| Per implement run | $6 estimate, 40 turns | `--max-budget-usd`, `--max-turns` (client-side estimate; spend can pass the cap) |
| Per verify run | $2 estimate, 20 turns | same |
| Per triage run | $1 estimate, 12 turns | same |
| Wall clock | 45 minutes per agent job | Actions `timeout-minutes` |
| Daily | $25 estimate | `DAILY_BUDGET_EXHAUSTED` stop condition |
| Hard ceiling | set in the Claude Console workspace spend limit | billing-side; the only cap that cannot be overshot |

Values are starting points chosen for Phase A (spec §4.6 sets per-run caps at 70–80% of the real
ceiling); revise them from ledger data in the weekly retro.

## 9. Constraint review

Review this charter deliberately, not continuously.

- **Loosen** a class only after a run of evidence (for example 50 merged pull requests with no
  escaped defect), recorded in `DECISIONS.md`.
- **Tighten** immediately when an escaped defect traces back to a gate you trusted, and record
  what the gate missed.

```
LAST_REVIEWED: <YYYY-MM-DD>
NEXT_REVIEW: <YYYY-MM-DD>
```

## 10. The five questions (LIFE-23), answered before any unattended run

| Question | Answer in this factory |
|---|---|
| What does done look like? | Section 5 (`DONE`): green gates at the required level, the failing-test proof, the diff policy, no keys in the diff, and a reviewable PR body. |
| Can the agent check its own work? | Yes, but its word is not accepted. `gates.sh` runs in the Stop hook while it works. The authoritative verdict comes from a separate job, sandboxed, using the trusted engine. That job holds no secrets: optional holdouts arrive as an artifact from a job that runs no candidate code. |
| What may it touch? | Tier 0–1 paths; Tier 2 only with an approved spec. Never load-bearing paths (section 2) or existing tests (section 3). Enforced by the guard hook, the sandbox and the diff policy. |
| What stops it? | The STOP_IF conditions in section 7, the per-run and daily budgets in section 8, the wall-clock timeout, and the pause switch (`FACTORY_PAUSED`). |
| Who signs off on what cannot be taken back? | The owner merges every pull request. Production deployments wait for the owner's approval on the `production` environment. Migrations, authentication, payments and CI changes are made by humans only. |
