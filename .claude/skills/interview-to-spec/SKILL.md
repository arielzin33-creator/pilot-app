---
name: interview-to-spec
description: Turn an approved PR/FAQ or a feature request into a spec, plan and tasks through a structured interview (Spec Kit flow with clarification markers and four approval gates). Use when a Tier 2 feature needs a spec, or a new product's brief has been approved.
---

# Interview to spec (Phase E, gates 2–4)

Inputs: an approved `PRFAQ.md` (or an issue labelled `factory:ready-to-spec`) and
`docs/product/CONSTITUTION.md`.

## Gate 2 — spec (what and why)
1. `mkdir specs/<NNN-short-name>` and copy `.factory/templates/spec/spec.md`.
2. Interview: users and their current workaround; each user story with priority; for every
   story, acceptance scenarios in Given/When/Then; edge cases; non-functional limits with
   numbers; what is out of scope.
3. Write no technology choices. Every uncertainty becomes `[NEEDS CLARIFICATION: question]`
   in place; never guess. Ask the owner to resolve markers before requesting approval.
4. `python3 -I .factory/scripts/intake.py spec-check specs/<id> --gate spec`.

## Gate 3 — plan (how)
1. Copy `.factory/templates/spec/plan.md`. Fill the constitution check first; a `no` needs an
   approved exception in `docs/factory/DECISIONS.md`.
2. Mark load-bearing parts (auth, payments, migrations, CI) as Tier 3, human only.
3. `intake.py spec-check specs/<id> --gate plan`.

## Gate 4 — tasks (one task = one PR)
1. Copy `.factory/templates/spec/tasks.md`. Keep each task inside Tier 1 limits where possible
   (≤ 5 files, ≤ 400 lines); list the exact files; one testable `done_when`; requirement ids.
2. Put load-bearing work in separate tasks so the rest can run unattended.
3. `intake.py spec-check specs/<id> --gate tasks`, then preview the backlog:
   `intake.py backlog specs/<id> --out /tmp/issues.json --allow-draft`.

The owner approves each gate in a reviewed PR by setting `status: approved`, `approved_by` and
`approved_on`. After gate 4 the `factory-backlog` workflow creates the issues.
