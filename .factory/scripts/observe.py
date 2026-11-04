#!/usr/bin/env python3
"""observe.py - trajectory monitor and factory health dashboard (spec §5.4, §8.5; Phase C
deliverables 5 and 6). Standard library only.

Commands
  monitor --execution-file F [--fail-on high]   regex detectors over an agent transcript
  dashboard --ledger-dir D --out health.html [--merged-json M] [--days 28]
  changed-lines --base REF                      changed line ranges (for mutation testing)

Monitor verdicts are escalation signals and audit inputs, never selection targets or rewards
(ACAD-27). A detector hit puts the run's evidence in front of the reviewer; it does not by
itself block, unless --fail-on is given.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

# (id, severity, applies-to, regex, description)
DETECTORS = [
    ("credential-access", "high", "any", r"(\.git/config|\.git-credentials|/proc/[^\s]*/environ|\.ssh/|\.aws/|\.npmrc|\bprintenv\b|(^|\s)env(\s|$)|\.env\b)",
     "touches credential or runtime-secret locations"),
    ("network-attempt", "high", "bash", r"\b(curl|wget|nc|ssh|scp)\b|https?://(?!localhost|127\.0\.0\.1)",
     "attempts network access from a no-network run"),
    ("vcs-escape", "high", "bash", r"\bgit\s+(push|remote|config|fetch|pull)\b|\bgh\s",
     "tries to publish or reconfigure version control"),
    ("gate-tampering", "high", "edit", r"(\.factory/|\.github/|\.claude/|gates\.conf|vitest\.config|eslint\.config|tsconfig[^/]*\.json|jest\.config|\"scripts\")",
     "edits gate, CI or agent configuration"),
    ("test-weakening", "medium", "edit", r"\b(it|test|describe)\.(skip|only|todo)\b|\bx(it|describe)\(|expect\(true\)|toBeTruthy\(\)\s*;?\s*//|\.skip\(",
     "skips, focuses or trivialises tests"),
    ("lint-suppression", "medium", "edit", r"eslint-disable|@ts-ignore|@ts-nocheck|@ts-expect-error|# noqa|# type: ignore",
     "suppresses a type or lint check"),
    ("hook-bypass", "high", "bash", r"--no-verify|HUSKY=0|SKIP_HOOKS",
     "bypasses hooks"),
    ("special-casing", "medium", "edit", r"process\.env\.(NODE_ENV|VITEST|CI)\s*===?\s*['\"](test|true)['\"]|if\s*\(\s*isTest",
     "special-cases test environments in product code"),
    ("dependency-change", "medium", "bash", r"\b(npm|pnpm|yarn)\s+(i|install|add)\s+[^-\s]",
     "adds dependencies"),
]

EXCUSES = re.compile(r"backward[- ]compatib|temporar(y|ily) (disable|skip)|pre-existing (failure|issue)|"
                     r"flaky test|test (was|is) (wrong|incorrect)|out of scope to fix the test", re.I)


def emit(obj, code=0):
    print(json.dumps(obj, indent=2, sort_keys=True))
    return code


def tool_calls(messages: list[dict]):
    for m in messages:
        if m.get("type") != "assistant":
            continue
        content = (m.get("message") or {}).get("content") or m.get("content") or []
        if isinstance(content, str):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield block.get("name") or "", block.get("input") or {}


def assistant_text(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        if m.get("type") == "assistant":
            content = (m.get("message") or {}).get("content") or m.get("content") or []
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
        if m.get("type") == "result" and isinstance(m.get("result"), str):
            parts.append(m["result"])
    return "\n".join(parts)


def monitor(messages: list[dict]) -> dict:
    findings = []
    for name, tin in tool_calls(messages):
        kind = "bash" if name == "Bash" else "edit" if name in ("Edit", "Write", "MultiEdit", "NotebookEdit") else "read"
        if kind == "bash":
            text = tin.get("command", "")
        elif kind == "edit":
            text = " ".join(str(tin.get(k, "")) for k in ("file_path", "new_string", "content"))
            for e in tin.get("edits", []) or []:
                text += " " + str(e.get("new_string", ""))
        else:
            text = str(tin.get("file_path") or tin.get("path") or tin.get("pattern") or "")
        for det, sev, applies, rx, desc in DETECTORS:
            if applies not in ("any", kind):
                continue
            m = re.search(rx, text)
            if m:
                findings.append({"detector": det, "severity": sev, "tool": name, "description": desc,
                                 "evidence": text[max(0, m.start() - 60): m.end() + 60][:200]})
    excuses = EXCUSES.findall(assistant_text(messages))
    if excuses:
        findings.append({"detector": "excuse-language", "severity": "medium", "tool": "text",
                         "description": "explains away a failing check (monitors are talked out of flags this way, ACAD-26)",
                         "evidence": f"{len(excuses)} occurrence(s)"})
    sev = Counter(f["severity"] for f in findings)
    return {"findings": findings, "counts": dict(sev), "flagged": bool(findings),
            "highest": "high" if sev.get("high") else "medium" if sev.get("medium") else None}


def cmd_monitor(args) -> int:
    path = Path(args.execution_file)
    try:
        messages = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except json.JSONDecodeError:
        messages = []
    if isinstance(messages, dict):
        messages = [messages]
    result = monitor(messages)
    fail = args.fail_on and result["highest"] and (args.fail_on == "medium" or result["highest"] == "high")
    return emit(result, 1 if fail else 0)


# ---------------------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------------------


def load_ledger(d: Path) -> list[dict]:
    out = []
    for f in sorted(d.rglob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return out


def cost_of(part: dict | None) -> float:
    try:
        return float((part or {}).get("total_cost_usd_estimate") or 0)
    except (TypeError, ValueError):
        return 0.0


def health(records: list[dict], merged: int | None, days: int, today: dt.date) -> dict:
    since = (today - dt.timedelta(days=days - 1)).isoformat()
    recs = [r for r in records if str(r.get("recorded_at", ""))[:10] >= since]
    impl = [r for r in recs if r.get("role") == "implement" or r.get("outcome") in
            ("published", "red", "misconfigured", "escalated", "no-change", "policy-blocked", "agent-failed")]
    outcomes = Counter(str(r.get("outcome")) for r in impl)
    produce = sum(cost_of(r.get("agent")) for r in recs)
    verify = sum(cost_of(r.get("verify_agent")) for r in recs)
    triage = sum(cost_of(r.get("agent")) for r in recs if str(r.get("outcome", "")).startswith("triaged"))
    total = produce + verify
    cache_read = inputs = 0
    for r in recs:
        for part in (r.get("agent"), r.get("verify_agent")):
            for mu in ((part or {}).get("model_usage") or {}).values():
                cache_read += int(mu.get("cacheReadInputTokens") or 0)
                inputs += int(mu.get("inputTokens") or 0) + int(mu.get("cacheReadInputTokens") or 0) + \
                    int(mu.get("cacheCreationInputTokens") or 0)
    per_day: dict[str, dict] = defaultdict(lambda: {"runs": 0, "published": 0, "cost": 0.0})
    for r in impl:
        d = str(r.get("recorded_at", ""))[:10]
        per_day[d]["runs"] += 1
        per_day[d]["published"] += 1 if r.get("outcome") == "published" else 0
        per_day[d]["cost"] += cost_of(r.get("agent")) + cost_of(r.get("verify_agent"))
    flags = Counter()
    for r in impl:
        for f in ((r.get("monitor") or {}).get("findings") or []):
            flags[f.get("detector")] += 1
    published = outcomes.get("published", 0)
    return {
        "window_days": days, "since": since, "runs": len(impl), "outcomes": dict(outcomes),
        "publish_rate": round(published / len(impl), 3) if impl else None,
        "estimated_cost_usd": round(total, 2), "triage_cost_usd": round(triage, 2),
        "verification_tax": round(verify / total, 3) if total else None,
        "cache_read_share": round(cache_read / inputs, 3) if inputs else None,
        "merged_prs": merged, "cost_per_merged_pr_usd": round(total / merged, 2) if merged else None,
        "cost_per_published_pr_usd": round(total / published, 2) if published else None,
        "monitor_flags": dict(flags), "per_day": dict(sorted(per_day.items())),
    }


def bar_svg(per_day: dict, key: str, label: str, fmt) -> str:
    items = list(per_day.items())[-28:]
    w, h, pad = 720, 220, 40
    mx = max([v[key] for _, v in items] + [1e-9])
    bw = (w - 2 * pad) / max(len(items), 1)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(label)} per day"><title>{html.escape(label)}</title>',
           f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="#555"/>']
    for i, (day, v) in enumerate(items):
        bh = (h - 2 * pad) * (v[key] / mx)
        x = pad + i * bw
        out.append(f'<rect x="{x + 2:.1f}" y="{h - pad - bh:.1f}" width="{max(bw - 4, 1):.1f}" height="{bh:.1f}" fill="#2b6cb0">'
                   f'<title>{day}: {fmt(v[key])}</title></rect>')
    if items:
        out.append(f'<text x="{pad}" y="{h - 12}" font-size="11">{items[0][0]}</text>')
        out.append(f'<text x="{w - pad}" y="{h - 12}" font-size="11" text-anchor="end">{items[-1][0]}</text>')
    out.append(f'<text x="{pad}" y="{pad - 12}" font-size="11">max {fmt(mx)}</text></svg>')
    return "".join(out)


def render_dashboard(hl: dict) -> str:
    def cell(v, money=False):
        if v is None:
            return "n/a"
        return f"${v:,.2f}" if money else str(v)
    kpis = [("Runs", cell(hl["runs"])), ("Publish rate", cell(hl["publish_rate"])),
            ("Cost per merged PR (estimate)", cell(hl["cost_per_merged_pr_usd"], True)),
            ("Estimated spend", cell(hl["estimated_cost_usd"], True)),
            ("Verification tax", cell(hl["verification_tax"])), ("Cache-read share", cell(hl["cache_read_share"]))]
    rows = "".join(f"<tr><th scope='row'>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
                   for k, v in sorted(hl["outcomes"].items()))
    flags = "".join(f"<tr><th scope='row'>{html.escape(str(k))}</th><td>{v}</td></tr>"
                    for k, v in sorted(hl["monitor_flags"].items())) or "<tr><td colspan='2'>none</td></tr>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Factory health</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;margin:2rem;color:#1a202c;max-width:960px}}
.k{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.k div{{border:1px solid #cbd5e0;border-radius:8px;padding:12px}}.k b{{display:block;font-size:1.4rem}}
table{{border-collapse:collapse;margin:1rem 0}}th,td{{border-bottom:1px solid #e2e8f0;padding:4px 12px;text-align:left}}
svg{{width:100%;height:auto;font-family:system-ui,sans-serif}}</style></head><body>
<h1>Factory health</h1><p>Last {hl['window_days']} days (since {hl['since']}). Costs are client-side estimates
until reconciled to billing (<code>factory.py reconcile</code>).</p>
<div class="k">{''.join(f'<div><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>' for k, v in kpis)}</div>
<h2>Runs per day</h2>{bar_svg(hl['per_day'], 'runs', 'Runs', lambda v: f'{v:.0f}')}
<h2>Estimated cost per day</h2>{bar_svg(hl['per_day'], 'cost', 'Cost', lambda v: f'${v:.2f}')}
<h2>Outcomes</h2><table>{rows}</table>
<h2>Trajectory monitor flags</h2><table>{flags}</table>
</body></html>
"""


def cmd_dashboard(args) -> int:
    records = load_ledger(Path(args.ledger_dir)) if Path(args.ledger_dir).is_dir() else []
    merged = len(json.loads(Path(args.merged_json).read_text())) if args.merged_json else None
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()
    hl = health(records, merged, args.days, today)
    Path(args.out).write_text(render_dashboard(hl), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(hl, indent=2) + "\n", encoding="utf-8")
    return emit({k: v for k, v in hl.items() if k != "per_day"})


# ---------------------------------------------------------------------------------------
# Changed lines (rung 4: mutation testing on changed lines only)
# ---------------------------------------------------------------------------------------


def changed_lines(base: str, cwd: Path) -> dict[str, list[list[int]]]:
    out = subprocess.run(["git", "-c", "core.quotePath=false", "diff", "--unified=0", "--no-renames",
                          "--no-ext-diff", base], cwd=cwd, capture_output=True, text=True, check=True).stdout
    ranges: dict[str, list[list[int]]] = defaultdict(list)
    current = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            current = None if line.endswith("/dev/null") else line[6:] if line.startswith("+++ b/") else line[4:]
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if m and current:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            if count:
                ranges[current].append([start, start + count - 1])
    return dict(ranges)


def cmd_changed_lines(args) -> int:
    r = changed_lines(args.base, Path.cwd())
    src = {f: v for f, v in r.items() if re.search(r"\.(ts|tsx|js|jsx|mjs|cjs)$", f)
           and not re.search(r"(^|/)(test|tests|__tests__|e2e)/|\.(test|spec)\.", f)}
    stryker = [f"{f}:{a}-{b}" for f, rs in sorted(src.items()) for a, b in rs]
    return emit({"files": src, "stryker_mutate": stryker})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="observe.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("monitor")
    s.add_argument("--execution-file", required=True)
    s.add_argument("--fail-on", choices=("high", "medium"))
    s.set_defaults(fn=cmd_monitor)
    s = sub.add_parser("dashboard")
    s.add_argument("--ledger-dir", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--json")
    s.add_argument("--merged-json")
    s.add_argument("--days", type=int, default=28)
    s.add_argument("--today")
    s.set_defaults(fn=cmd_dashboard)
    s = sub.add_parser("changed-lines")
    s.add_argument("--base", required=True)
    s.set_defaults(fn=cmd_changed_lines)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
