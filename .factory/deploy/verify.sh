#!/usr/bin/env bash
# verify.sh - deploy verification smoke checks (Phase D deliverable 6).
# Usage: verify.sh <base-url> [expected-release]
# Checks /healthz and /readyz return 200 and /metrics.json reports the expected release.
set -euo pipefail
URL="${1:?base url}"; EXPECT="${2:-}"
fail() { echo "FACTORY_VERIFY: url=$URL status=FAILED reason=$1"; exit 1; }
for path in /healthz /readyz; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --retry 5 --retry-delay 5 --retry-all-errors "$URL$path" || echo 000)"
  [ "$code" = "200" ] || fail "$path returned $code"
done
if [ -n "$EXPECT" ]; then
  got="$(curl -s --max-time 15 "$URL/metrics.json" | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("release",""))' || true)"
  [ "$got" = "$EXPECT" ] || fail "serving release '$got', expected '$EXPECT'"
fi
echo "FACTORY_VERIFY: url=$URL status=OK"
