# CI hardening checklist: status and enforcement

Spec §6.3 lists 17 controls in order. Each row says where the control lives and what fails if
someone weakens it. "Test" means the factory's own suite (`tests/`) fails; "Owner" means a
repository or organisation setting that only the owner can make (see `MANUAL-CONNECTIONS.md`).

| # | Control | Where | Enforced by |
|---|---|---|---|
| 1 | Default `GITHUB_TOKEN` read-only; explicit per-job permissions | every workflow: top-level `permissions: {}`, per-job grants | Test `test_permissions_are_least_privilege`; Owner: set the org/repo default to read-only |
| 2 | No `pull_request_target`, no `workflow_run` with untrusted checkout | all workflows | Test `test_no_pull_request_target_or_workflow_run_triggers`; `scan-keys` |
| 3 | Never interpolate event text into `run:` | all workflows | Test `test_untrusted_event_text_is_never_interpolated` |
| 4 | Actions pinned to full SHAs; Dependabot | all workflows; `.github/dependabot.yml` | Test `test_actions_are_pinned_to_full_commit_shas` |
| 5 | Human "Approve and run" for fork, agent and automation PRs | repository setting | Owner: Settings → Actions → "Require approval for all external contributors" |
| 6 | Agent runs only for write-access actors; bots denied; no write `gh` tools in issue-triggered runs | triage `select` job (trusted authors), implement preflight, guard hook | Tests `HandoffTests`, `GuardTests` |
| 7 | Base checkout; agent config restored from base | trusted pre-agent copies of `.factory`, `.git`; hooks from the trusted copy; config hashes | Tests `test_post_agent_*`, `test_hooks_run_from_the_trusted_copy_*`, `ConfigHashTests` |
| 8 | No secrets in agent jobs: OIDC for Anthropic, no PATs | `claude-code-action` with workload identity federation | Tests `test_no_secrets_other_than_github_token_*`, `ScanKeyTests` |
| 9 | OS sandbox, deny-by-default egress | Claude Code sandbox (no allowed domains); `sandbox-exec.sh` for agent code elsewhere | Tests `SettingsTests`, `SandboxExecTests` (CI only) |
| 10 | Dependency gate (§6.5) | `factory.py lockfile-check` (gate `deps`), `npm ci --ignore-scripts`, dependency review | Tests `LockfileTests`, `test_no_install_scripts_*` |
| 11 | SAST, secret scanning, advisory checks before human review | `factory-security.yml` (CodeQL, dependency review); `scan-keys` | Owner: enable secret scanning and push protection |
| 12 | Signed commits; SLSA Build L2+ provenance via Sigstore keyless | `attest-build-provenance` in the sandbox-image, release and preview workflows; `admit.sh` verifies before every deploy | **Partial:** factory commits are pushed with `git push` and are not signed, so a required-signatures rule would block them. Commits created through GitHub's API are signed by GitHub; moving `publish.sh` to the API is open work, not built. |
| 13 | Rulesets: human approvals; agent cannot approve or merge; CODEOWNERS on `.github/` and `.claude/` | `bootstrap-github.sh`, `.github/CODEOWNERS` | Test `test_required_status_is_bound_to_the_github_actions_app` |
| 14 | `show_full_output` off; `ACTIONS_STEP_DEBUG` unset | agent steps use `display_report: "false"` | Test `test_no_debug_output_in_agent_jobs` |
| 15 | Ephemeral GitHub-hosted runners only | `runs-on: ubuntu-latest` everywhere | Test `test_runners_are_github_hosted` |
| 16 | Agent runtime pinned at or above fixed releases | `policy.json` runtime pins; action pinned by SHA | Test `test_agent_action_pin_matches_policy` |
| 17 | CI red-team job replaying injection payloads | `factory-redteam.yml` (Phase C) | Tests `RedTeamTests` |
