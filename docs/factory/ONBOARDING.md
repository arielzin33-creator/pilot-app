# Onboarding a second repository (Phase F; spec §9.4)

A repository joins only when an agent can build, run and verify it without help. Versions 0.x of the factory accept only the reference stack (`PRODUCT-LINE.md`).

1. In the repository:
   ```bash
   /path/to/software-factory/install.sh . --owner <login>
   ./.factory/scripts/onboard-check.sh --run-gates
   ```
2. Fix every `FAIL`. Each `WARN` needs a written reason in `docs/factory/DECISIONS.md`.
3. Add five eval tasks from the repository's own history (`docs/factory/EVALS.md`).
4. Run `SETUP.md` steps for the new repository. Each repository has its own federation rule, scoped to `repo:<owner>/<repo>:`.
5. Start with Tier 0 only for two weeks (`unattended_max_tier: 0`). Raise the tier only with ledger evidence.

A repository whose onboarding fails stays out; it is not "onboarded with exceptions".
