#!/usr/bin/env bash
# platform-readiness.sh - collect platform-readiness evidence for the factory itself (spec §7.5,
# adapted from cATO): reference design and configuration items, gate dry runs, monitoring,
# boundary, people and revocation. Writes a markdown report; a human reviews it quarterly and on
# every factory change. Usage: platform-readiness.sh [out.md]
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
OUT="${1:-platform-readiness-$(date -u +%F).md}"
PY="python3 -I .factory/scripts/factory.py"
{
  echo "# Platform readiness: $(basename "$ROOT") ($(date -u +%F))"
  echo
  echo "## 1. Reference design and configuration items"
  echo
  python3 -I - <<'PY'
import json, re, pathlib
p = json.load(open(".factory/policy.json"))
print(f"- Charter status: `{p.get('charter_status')}`; phase `{p.get('factory_phase')}`; unattended max tier {p.get('unattended_max_tier')}; Tier 2 with approved spec: {p.get('tier2_unattended_with_approved_spec')}")
print(f"- Models: " + ", ".join(f"{k} `{v}`" for k, v in p["models"].items() if not k.startswith("_")))
print(f"- Runtime: Claude Code `{p['runtime']['claude_code_version']}` (floor `{p['runtime']['claude_code_version_floor']}`), action `{p['runtime']['claude_code_action_sha'][:12]}`")
pins = set()
for wf in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
    for m in re.finditer(r"uses:\s*([\w./-]+)@([0-9a-f]{40})", wf.read_text()):
        pins.add((m.group(1), m.group(2)))
print(f"- Pinned actions ({len(pins)}):")
for name, sha in sorted(pins):
    print(f"  - `{name}@{sha[:12]}`")
reg = json.load(open(".factory/registry.json")) if pathlib.Path(".factory/registry.json").exists() else {"assets": []}
print(f"- Registered assets: {len(reg['assets'])} (`.factory/registry.json`)")
PY
  echo
  echo "## 2. Gates and controls (dry runs)"
  echo
  echo '```'
  $PY config-hashes 2>&1 | grep '"status"' | sed 's/^ */config-hashes /'
  $PY scan-keys 2>&1 | grep '"status"' | sed 's/^ */scan-keys /'
  python3 -I .factory/scripts/redteam.py 2>/dev/null | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print("redteam blocked %d/%d" % (d["blocked"], d["cases"]))'
  python3 -I .factory/scripts/intake.py registry 2>/dev/null | grep '"ok"' | sed 's/^ */registry /'
  [ -f package-lock.json ] && $PY lockfile-check 2>/dev/null | grep '"ok"' | sed 's/^ */lockfile /'
  echo '```'
  echo
  echo "## 3. Monitoring"
  echo
  echo "- Health dashboard: \`factory-retro\` weekly artifact (\`observe.py dashboard\`)."
  echo "- Alerts: \`factory-feed\` (ready-queue floor, escalation age), \`factory-slo\` (error budget), \`factory-incident\` (production)."
  echo
  echo "## 4. Boundary"
  echo
  echo "- See \`docs/factory/THREAT-MODEL.md\` (data flow and trust boundaries). The factory boundary (GitHub Actions, Anthropic API) is separate from production (deploy jobs behind GitHub Environments)."
  echo
  echo "## 5. People and separation of duties"
  echo
  echo "- CODEOWNERS: $(grep -v '^#' .github/CODEOWNERS | grep -c '@') rules. No human holds a signing key: provenance is keyless (Sigstore via OIDC)."
  echo "- Agent identities: workload identity federation (no key); optional GitHub Apps for publishing and holdouts, revocable in one place."
  echo
  echo "## 6. Revocation triggers (review now)"
  echo
  echo "- [ ] Escaped defects this quarter above the charter threshold?"
  echo "- [ ] Any red-team case not blocked? (section 2)"
  echo "- [ ] Ledger unreconciled with billing for more than four weeks?"
  echo "If any is ticked, the affected ticket class returns to full review (spec §9.6); record it in DECISIONS.md."
} > "$OUT"
echo "wrote $OUT"
