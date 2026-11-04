# Factory metrics (Phase A schema)

Defined before onboarding, as the spec requires (§10.1 deliverable 9; GAO found DoD factories
had goals without performance measures, GOV-43). Team-scoped: these numbers improve this
factory and are not for comparison with other teams (GOV-20).

## Source of truth

The **run ledger**: one JSON record per run on the `factory/ledger` branch, written by
`factory.py ledger` from the agent's execution file and the workflow's own results. Cost fields
are **client-side estimates at list price** (`total_cost_usd`); they are reconciled to the
Claude Console billing export with `factory.py reconcile` (spec §4.6).

### Run record fields

| Field | Meaning |
|---|---|
| `run_id`, `issue`, `role` | workflow run, issue number, `triage` / `implement` |
| `tier`, `gate_level` | classifier result for the produced diff |
| `outcome` | `published`, `red`, `misconfigured`, `stopped`, `policy-blocked`, `agent-failed`, `no-change` |
| `gates_line`, `proof_line` | verbatim `FACTORY_GATES` and `PROOF` lines |
| `diff_policy` | `ok` and stop reasons from `check-diff` |
| `verdict` | verifier decision (advisory in Phase A) |
| `pr_number` | the draft pull request, when published |
| `agent.*` | model, Claude Code version, turns, duration, estimated cost, token usage by model, failures, warnings |
| `verify_agent.*` | the same for the verifier run |

## Metrics

| Metric | Definition | Use |
|---|---|---|
| Merged factory PRs | PRs from `claude/fq-*` merged to the default branch | Phase A exit: 20 Tier 0–1 merges |
| Cost per merged PR (estimate) | sum of run estimates in the period ÷ merged factory PRs | dashboard; weekly retro |
| Cost per merged PR (billed) | billed amount for the factory workspace ÷ merged factory PRs | Phase A exit: reported and reconciled |
| Reconciliation gap | (billed − estimate) ÷ billed, per day and per period | alert above 10% |
| Publish rate | published runs ÷ implement runs | factory health |
| Merge rate | merged ÷ published PRs | review signal; vendor bands are not targets |
| Red rate | runs with gates RED or MISCONFIGURED ÷ implement runs | gate and spec quality |
| Escalation rate | runs ending `stopped`, `policy-blocked` or `agent-failed` ÷ runs | charter fit |
| Proof failure rate | `PROOF: status=FAILED` ÷ runs with a test | test quality |
| Verifier disagreement | verifier `rejected` on PRs the human merged, and `verified` on PRs the human closed | judge validation input for Phase C |
| Lead time | issue `factory:ready-to-implement` → draft PR; draft PR → merge | DORA-style flow |
| Survival | merged factory changes not reverted within 14 days | unit-cost denominator (merged-and-surviving PRs, ECON-28) |
| Permission denials | runs with non-zero `permission_denials` | policy fit; recurring hits escalate |
| Copy-paste steps | manual steps between ticket and PR (target zero) | Phase A exit criterion |
| Long-lived keys in agent context | `scan-keys` findings plus the workflow's API-key absence check (target zero) | Phase A exit criterion |

## Phase A exit report

Produced by `.factory/scripts/phase-a-report.sh` from the ledger and the GitHub API:
merged Tier 0–1 PRs (target 20), key-scan status, cost per merged PR (estimate and billed),
reconciliation gap, and the STOP_IF test results from `tests/`.
