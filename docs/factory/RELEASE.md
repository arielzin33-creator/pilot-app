# Release automation and production feedback (Phase D)

```
merge to main
  → factory-release: build      image (digest-pinned), CycloneDX SBOM, SLSA provenance
                                 signed keyless with the workflow's OIDC identity
  → gate                         error-budget freeze (factory:freeze), migration lint
  → staging     (environment)    admission: gh attestation verify → deploy → smoke → watch
  → production  (environment,    release-readiness pack must be READY → deploy → smoke →
                 human approval)  watch (metric-driven abort) → rollback on failure → GitHub
                                  Release with the pack and SBOM attached
pull request → factory-preview   image per PR → preview environment → smoke → URL comment
production alert → factory-incident → issue (factory:incident + inbox) → triage
hourly → factory-slo             error budget → factory:freeze issue when exhausted
```

## Provider-neutral deploys

`.factory/deploy/deploy.sh <preview|staging|production> <image@sha256:…>` dispatches to
`adapters/$DEPLOY_PROVIDER.sh`:

| Provider | Adapter | Needs |
|---|---|---|
| `none` (default) | dry run, records what would deploy | nothing |
| `fly` | `flyctl deploy --image`, rolling strategy | `DEPLOY_TOKEN` (Fly deploy token), `DEPLOY_APP_<ENV>` |
| `render` | image-backed service deploy hook | `DEPLOY_TOKEN` (deploy hook URL) |
| `ssh` | one Docker VM: pull, run new, swap | `DEPLOY_SSH_KEY`, `DEPLOY_SSH_TARGET_<ENV>`, `DEPLOY_SSH_KNOWN_HOSTS`. A reverse proxy routes to the container. Previews would need one env file per PR, so use ssh for staging and production only. |

All real adapters are **untested against the live services**. Each is small enough to read in one sitting. A new provider is one more adapter script with the same contract: deploy the digest, exit 0 on success.

Images are deployed only by a strict digest reference, and only after `.factory/deploy/admit.sh` passes. This is the admission rule, spec §6.5 rule 7. `admit.sh` runs `gh attestation verify` with the expected signer workflow, and for releases with source ref `refs/heads/main`. It covers staging, production, rollback and previews.

I believe `--signer-workflow` and `--source-ref` are current `gh attestation verify` flags. If your `gh` version rejects them, admission fails closed; update `admit.sh`.

Previews of factory branches (`claude/*`) run only after a human adds the label `factory:preview`.

## Rollout, abort and rollback

- `release.py watch` polls `/metrics.json` after a deploy and aborts in any of these cases:
  - the error rate exceeds the threshold, 2% by default, with at least 50 requests;
  - the instance restarted (its counters went backwards);
  - it serves the wrong release;
  - the metrics endpoint becomes unreachable.
- On abort, the production job redeploys the previous release's digest, which is recorded in the previous GitHub Release's body.
- Feature-level rollout uses OpenFeature flags (`flags/flags.json` in the product). Risky capabilities ship behind a flag and are switched off without a deploy. Clean up a flag two weeks after it reaches 100% (spec §9.5).

## Migrations: expand and contract

`release.py migration-lint` (also the `migrations` gate) enforces three rules:
- Destructive statements are allowed only in a migration marked `-- factory:contract`:
  - DROP TABLE and DROP COLUMN;
  - RENAME;
  - changing a column's type;
  - SET NOT NULL;
  - TRUNCATE;
  - DELETE without WHERE.
  Ship that migration in a later release than the code that stops using the old shape.
- Applied migrations never change.
- Warnings are raised for:
  - NOT NULL columns added without a default;
  - indexes created without CONCURRENTLY.

Migrations are load-bearing: humans write and merge them.

## Evidence

Each GitHub Release carries `RELEASE-READINESS.md` and `.json`. They contain:
- checks: digest pin, SBOM, provenance, migration lint, error budget;
- the merged pull requests;
- an SBOM summary with licences;
- the **AI record**: models and Claude Code versions behind the factory PRs, from the ledger;
- the migrations.

SLSA level: the provenance is generated and signed in the build job with GitHub's attestation service. That is Build L2. Build L3 requires provenance generated outside the build steps, for example by a reusable signing workflow. That is the next step and is not claimed here.
