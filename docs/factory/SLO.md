# Service level objectives and the error-budget freeze (Phase D)

Fill one block per service. `release.py slo-check` reads the fenced `slo` block.

```slo
service: <service name>
window_days: 28
availability_target: 0.995        # share of requests that succeed (non-5xx) in the window
latency_p95_ms: 800               # recorded for review; not yet enforced automatically
freeze_when_budget_below: 0.0     # freeze when the remaining error budget falls to this share
```

## Policy

- **Error budget.** The budget is `(1 - availability_target) × requests` failed requests per window.
- **Freeze.** When the budget is exhausted, `factory-slo` opens an issue labelled `factory:freeze`. While any such issue is open:
  - the factory stops at preflight (`RELEASE_FREEZE`);
  - `factory-release` refuses non-P0 releases (`release.py freeze-check`).
  - A human closes the issue when the budget recovers.
- **P0 fixes.** These are made by humans and released with the `p0` input of `factory-release`.
- **Review.** SLOs are reviewed with the charter (§9 there). A target nobody would act on is removed, not kept.

The metrics source (`SLO_METRICS_URL`) is a manual connection: an endpoint that returns `{"requests": n, "errors": n}` over the window. It can be served by your monitoring stack, or by a small job that aggregates each instance's `/metrics.json`.
