# Factory decisions and constraint log

Append-only. One entry per decision that changes what the factory may do. Loosening entries
must cite their run of evidence (charter §9).

## 2026-11-04 — Phase A approved without a second reviewer

- **Decision:** Proceed to Phase A with a single human reviewer (the owner).
- **Consequence:** Tier 3 (load-bearing) work never runs unattended and is never produced by
  the factory. `policy.json`: `tier3_unattended: false`, `second_reviewer_available: false`,
  `unattended_max_tier: 1`.
- **Revisit when:** a second trusted reviewer is available (spec §3.7, §9.1).

## 2026-11-04 — Anthropic access through workload identity federation

- **Decision:** Workflows authenticate to the Claude API by exchanging the GitHub OIDC token
  (federation rule `fdrl_…`, service account `svac_…`). No `ANTHROPIC_API_KEY` secret exists.
- **Evidence:** claude-code-action `docs/setup.md` at commit
  `c0949c50a78c1ec8ce2647db36629b870e715d0c` documents the federation inputs; release tag
  `v1.0.99` does not contain them, so the action is pinned to that commit, not the tag.

## 2026-11-04 — Agents edit files only; workflows publish

- **Decision:** The implementer agent has no network, no GitHub CLI and no push permission.
  Deterministic workflow steps check the diff, run gates in a job without secrets, and publish.
- **Reason:** the action leaves a short-lived GitHub App token in `.git/config`; agents must not
  be able to carry it into a published diff (guard hook, permission deny rule, and a key scan of
  the diff before publishing).

## 2026-11-04 — Run ledger on a dedicated branch

- **Decision:** Run records are committed to the `factory/ledger` branch by the workflow.
- **Reason:** failed runs leave no pull request; their records must persist for STOP_IF
  (`RED_GATES_TWICE`, `TOO_MANY_RUNS`) and for cost reconciliation.

## 2026-11-04 — OpenTelemetry export deferred

- **Decision:** OTel export is optional in Phase A (enabled only when a collector endpoint
  variable is set). The ledger is the metrics source of truth.
- **Reason:** most hosted collectors require an authentication token, which would be a
  long-lived secret; the Phase A exit criterion is zero long-lived keys.
