# Dependency twins and holdout scenarios (Phase B)

## Twins

A twin is a local, deterministic stand-in for an external service (an AI model, an image
generator, payments, email). Spec §10.2 deliverable 3 (PRAC-01, VER-13).

Rules, enforced by the product constitution (Article 3) and by review:

1. Every external service is reached through an interface in `src/providers/**` with at least
   two implementations: the real adapter and the twin.
2. The twin is the default when the service's environment variables are absent, so tests, local
   development and every factory run work with no network and no keys.
3. **One contract suite runs against both.** The same test file exercises the twin on every run
   and the real adapter in a scheduled job that has the service's credentials. That job is a
   manual connection, `MANUAL-CONNECTIONS.md`. Divergence between the two is a bug in the twin.
4. Twins return realistic shapes, errors and latencies, including rate limits and timeouts, so
   failure handling is tested.

Residual risk: twin fidelity is unmeasured until the contract suite runs against the live
service (spec §12).

## Holdout scenarios

Holdouts are acceptance checks the implementer never sees. A visible test can be overfitted
(an agent can special-case it); a hidden scenario measures whether the change does the job.

- They live in a separate private repository, named by the repository variable
  `FACTORY_HOLDOUT_REPO`, with an executable `run.sh` at its root. `run.sh <repo-root>`
  exits 0 when every scenario passes.
- A separate `holdouts` job, which runs no agent or candidate code, mints a read-only
  installation token for that repository from a dedicated GitHub App.
  - The App ID is in the variable `FACTORY_HOLDOUT_APP_ID`.
  - Its private key is in the secret `FACTORY_HOLDOUT_APP_KEY`, allowed only in that job's
    token step (`policy.json` lists every allowed secret).
- The job checks the scenarios out and passes them to the gates job as an artifact.
- In the gates job they sit in `$RUNNER_TEMP/holdouts`, which code under test can read but not
  write. They run through `gates.sh` as the `holdout` gate inside the sandbox.
- When the variable is unset, the holdout gate is skipped. It is optional and is not in the
  required gate list.

Write holdouts from the spec's acceptance scenarios (Given/When/Then), not from the code.
