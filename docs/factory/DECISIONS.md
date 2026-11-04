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

## 2026-11-04 — Independent review: agent code never shares a writable filesystem with a verdict

- **Context:** a reviewer that had not seen the build process audited the factory adversarially.
  Confirmed findings:
  - agent-written test code (in the Stop hook and the gates job) could overwrite the trusted
    engine copy or the evidence;
  - the diff classifier could be bypassed by renames, non-ASCII file names, type changes,
    `.gitattributes` and embedded test-runner configuration;
  - the trusted `gates.sh` changed into the wrong directory and would have reported
    `MISCONFIGURED` on every run;
  - a duplicated workflow key would probably have made the implement workflow invalid.
- **Decision:**
  1. All agent-written code outside Claude Code's own sandbox runs through
     `sandbox-exec.sh`. This is bubblewrap with no network, a new PID namespace, a cleared
     environment, and a read-only filesystem except the checkout; `.git` is also read-only
     for the Stop hook.
  2. The gates job is authoritative. It checks the patch hash, then re-runs the diff policy
     with the base's engine before any agent code runs.
  3. Post-agent git commands use a copy of `.git` taken before the agent ran.
  4. Artifacts are downloaded by exact name.
  5. Publish runs only scripts from the default branch.
  6. The classifier ignores repository attributes and does not pair renames.
  7. Moving or replacing an existing test counts as modifying it.
  8. `npm ci --ignore-scripts` is used where an OIDC token is available.
  9. The required status is bound to the GitHub Actions app.
- **Not verified here:** the bubblewrap profile could not be executed in the build environment,
  which lacks bubblewrap. Its tests run in the factory's CI on Ubuntu runners and must pass
  before the first live run.

## 2026-11-04 — Second review pass: verdicts from exit codes, hooks from the trusted copy

- **Context:** a second independent pass verified the fixes above and found six more gaps.
  - Code under test could still write `.factory`/`.claude` inside the checkout, and hooks ran
    from there.
  - Verdicts were parsed from log lines the code under test could also print.
  - `.git` was writable in the gates job, and the proof used `HEAD~1`.
  - `factory-gates.yml` ran PR code in the job that posts the status.
  - A failed gates job was reported as `policy-blocked`.
  - Abandoned claims were never released.
- **Decision:**
  1. `sandbox-exec.sh` always mounts the factory control files read-only.
  2. `render-settings --hooks-dir` points every hook at the pre-agent copy.
  3. GREEN and PROVEN are accepted only from the trusted script's exit code, with HEAD
     unchanged.
  4. `.git` is read-only, and the proof uses the explicit base SHA.
  5. `factory-gates.yml` runs base-commit scripts in the sandbox and posts the status from a
     separate `statuses: write` job.
  6. The outcome checks the gates job's result before its outputs.
  7. `select-item` releases claims with no activity for `stale_in_progress_hours`.
  8. `ci_fix_commits` is reserved until a CI-fix loop exists (Phase B).

## 2026-11-04 — Phases B–F built offline; first product is Ink Dimensions web v1

- **Decision (owner):** build factory phases B–F completely, offline, and mark every part that
  needs live validation. Then take Ink Dimensions (web v1) through the Phase E intake as the
  first product. Hosting is a provider-neutral container. No auto-merge: a human merges every PR.
  The product line is confirmed as drafted.

## 2026-11-04 — ADR-04 evaluated at Phase F start: no durable supervisor yet

- **Triggers checked:**
  - multi-hour resumable workflows: no (runs are under 45 minutes);
  - idempotency required: handled by deterministic branch claims and per-run ledger files;
  - multi-repository changes: no;
  - more than one operator needing org identity: no (one owner);
  - scheduled-task caps binding: no (GitHub Actions schedules).
- **Decision:** stay on GitHub Actions. Revisit when a second operator joins, a second
  repository onboards, or runs need to resume across hours.

## 2026-11-04 — Auto-merge excluded

- **Decision (owner):** no Tier 0 class is qualified for automatic merge. Phase F's
  qualification process (spec §9.6) is not built. Every PR is merged by a human.
- **Reason:** there is no second reviewer, and a single owner's review is the only independent check.

## 2026-11-04 — Pooled sandboxes not needed

- **Decision:** GitHub-hosted runners with `sandbox-exec.sh` are the sandbox. E2B or Modal
  pools (Phase F deliverable 3) wait until runner limits bind (concurrency 1 today).
