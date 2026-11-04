@AGENTS.md

Claude Code specifics:
- Factory runs use `.factory/worker-settings.json` (hooks, sandbox, permissions). Interactive
  sessions use `.claude/settings.json`; the guard hook still blocks merges and force pushes.
- In an interactive session, change load-bearing files only when the human asks, and remind them
  to run `python3 -I .factory/scripts/factory.py config-hashes --update` in the same PR.
