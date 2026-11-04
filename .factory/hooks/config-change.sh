#!/usr/bin/env bash
# ConfigChange hook (spec §4.4 rule 6): block configuration changes during factory runs.
# Exit 2 "blocks the configuration change from taking effect (except policy_settings)".
set -uo pipefail
cat >/dev/null || true
if [ "${FACTORY_RUN:-0}" = "1" ]; then
  echo "BLOCKED by factory: configuration changes are not allowed during unattended runs." >&2
  exit 2
fi
exit 0
