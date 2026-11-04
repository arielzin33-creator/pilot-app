---
name: factory-status
description: Summarize the factory's state in an interactive session - queue by label, pull requests awaiting review, recent run outcomes and estimated spend - using the control-room script.
allowed-tools: Bash(./.factory/scripts/control-room.sh:*)
---
Run `./.factory/scripts/control-room.sh` and summarize: items per queue label, pull requests
awaiting review (and whether the review queue limit is reached), the last ten run outcomes,
estimated spend today against the daily budget, and anything labeled `factory:needs-human`
with its reason. Recommend the single next human action.
