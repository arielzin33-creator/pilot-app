#!/usr/bin/env bash
# SessionEnd hook: record that the session ended (runs on SIGTERM too, exit 143; TOOL-04).
# SessionEnd hooks share a 1.5-second budget, so this only writes a marker; the
# workflow writes the full run record from the execution file.
set -uo pipefail
INPUT="$(cat || true)"
[ -n "$INPUT" ] || INPUT="{}"
DIR="${FACTORY_RUN_DIR:-}"
[ -n "$DIR" ] || exit 0
mkdir -p "$DIR" 2>/dev/null || exit 0
printf '{"session_end_utc":"%s","input":%s}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$(printf '%s' "$INPUT" | head -c 2000 | python3 -I -c 'import json,sys
raw=sys.stdin.read()
try: d=json.loads(raw); print(json.dumps({k:d.get(k) for k in ("session_id","reason")}))
except Exception: print("{}")')" > "$DIR/session-end.json" 2>/dev/null || true
exit 0
