# Evaluation suite and judge validation (Phase C)

The suite answers one question: can a new model, prompt, skill or harness version be judged
with numbers by tonight (spec §5.4)? Re-run it on every such change.

## Tasks

`evals/` is load-bearing: agents never edit it, and humans change it in reviewed pull requests.

```
evals/tasks/<id>/task.json        id, title, partition (dev | private | heldout), created (YYYY-MM-DD),
                                  issue {title, body}, check (shell, exit 0 = solved),
                                  optional: pass_to_pass, setup_patch, prepare, canary, check_timeout, base
evals/tasks/<id>/reference.patch  a solution that passes every grader
evals/tasks/<id>/setup.patch      optional: turns the base into the task's starting state
evals/tasks/<id>/hidden/          optional: tests copied in after the agent runs (the task worktree
                                  contains no evals/ directory, and the Claude agent wrapper puts
                                  the suite on its sandbox deny-read list)
```

Sourcing rules:
- Start with 20–50 tasks taken from real failures: escalations, verifier rejections, reverted PRs.
- Write each task from its requirements, not from the fix.
- Two people agree on pass or fail for each task.
- Budget up to three reviewer-hours per task.
- Canaries (`"canary": true`) have conflicting requirements, so any pass is cheating.
- Mark the task creation date. Runs on tasks created before the model's release are reported as contaminated.

## Commands

```bash
python3 -I .factory/scripts/evals.py validate                       # reference passes, no-op fails
python3 -I .factory/scripts/evals.py run --agent "cmd:bash $PWD/.factory/evals/claude-agent.sh" \
  --trials 5 --partition dev --model claude-sonnet-5-5 --reasoning default \
  --harness "claude-code 2.1.318 / action c0949c5" --prompt-version "$(git rev-parse --short HEAD)" \
  --cost-cap 6 --turn-cap 40 --model-release 2026-06-09 --sandbox-grade --out evals/results/$(date +%F)-sonnet.json
python3 -I .factory/scripts/evals.py report evals/results/*.json --md report.md --svg pareto.svg
```

Reported per run:
- pass@1 with standard error;
- pass@k;
- pass^k, the metric for unattended work: all k trials succeed;
- cost per correct fix;
- canary cheats;
- contaminated trials;
- the full reporting record: model, reasoning setting, harness, prompt version, task-set hash, caps, trials and date.

The Pareto chart (pass^k against cost per correct fix) is the selection chart.

`factory-evals.yml` re-validates every task, and self-tests the harness with the `reference` and `noop` agents, on every change. Model runs need Anthropic access and are started by the owner. They are deliberately not wired to CI until the suite has at least 20 curated tasks.

## LLM-judge validation

`judge.py` measures a judge against expert labels and implements protocol steps 2–4 and 7 (spec §5.5):

```bash
python3 -I .factory/scripts/judge.py validate --labels evals/judge/labels.jsonl \
  --verdicts evals/judge/verdicts.jsonl --swapped evals/judge/verdicts-swapped.jsonl \
  --verbose evals/judge/verdicts-verbose.jsonl
python3 -I .factory/scripts/judge.py drift --history evals/judge/history.jsonl
```

`ready_to_fail_closed` requires all of the following:
- at least 100 balanced labels;
- agreement at or above the human–human level (0.81 by default);
- block precision ≥ 0.9 and recall ≥ 0.8;
- order and verbosity consistency ≥ 0.9;
- every known cheating transcript blocked.

Until then the verifier stays advisory, as it is in the shipped workflow.

Never use judge scores to select among candidates or as a reward.

## Mutation testing on changed lines (rung 4)

`python3 -I .factory/scripts/observe.py changed-lines --base <sha>` prints `stryker_mutate` entries such as `src/a.ts:10-24`. A product's `mutation` script can pass them to Stryker (`--mutate`). The `deep` gate level runs `mutation` when the script exists; it is required for Tier 3 changes, which humans make.

## Isolation notes

- The agent works in a worktree with no `evals/` directory and no pointer to the main checkout.
- The Claude agent wrapper denies reads of the suite in its sandbox settings.
- A command agent running without any sandbox could still read files anywhere on the machine. For
  held-out tasks, run the suite from a separate checkout the agent account cannot read.
- Graders execute agent-written code. They always run without credential variables;
  `--sandbox-grade` also confines them to the worktree with no network.
