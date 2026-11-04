---
artifact: tasks
id: <NNN-short-name>
plan: specs/<NNN-short-name>/plan.md
status: draft            # draft | approved
approved_by: ""
approved_on: ""
---

# Tasks <NNN>: <feature name>

One task is one pull request. `factory.py backlog` turns each task into a GitHub issue with a
trusted hand-off, and the deterministic classifier predicts its tier from `files`.
Format (parsed by machine; keep the field names):

## T-001: <imperative title>
- files: <comma-separated paths the change is expected to touch>
- done_when: <one observable, testable condition>
- depends_on: <T-ids, or none>
- requirements: <FR-/US- ids from spec.md>

<Optional detail for the implementer: context, edge cases, the test to write first.>
