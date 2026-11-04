#!/usr/bin/env python3
"""release.py - release automation helpers (spec §7.2, §10.4; Phase D). Standard library only.

Commands
  migration-lint [--dir migrations] [--base REF]   expand/contract rules; append-only history
  watch --url URL [--minutes N] [--max-error-rate R] [--min-requests N]
                                                   metric-driven abort after a deploy (exit 1 = abort)
  slo-check --metrics FILE [--slo docs/factory/SLO.md]
                                                   error-budget state (exit 3 = budget exhausted -> freeze)
  pack --version V --image IMAGE@DIGEST [--sbom F] [--merged-json F] [--ledger-dir D] --out FILE
                                                   release-readiness pack (markdown + JSON)
  freeze-check --open-freeze N [--p0]              release gate under an error-budget freeze
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def emit(obj, code=0):
    print(json.dumps(obj, indent=2, sort_keys=True))
    return code


# ---------------------------------------------------------------------------------------
# Migration lint (expand/contract; LIFE-49, LIFE-50)
# ---------------------------------------------------------------------------------------

CONTRACT_MARK = re.compile(r"^\s*--\s*factory:contract\b", re.M)
RULES = [
    ("drop-table", r"\bDROP\s+TABLE\b", "drops a table"),
    ("drop-column", r"\bALTER\s+TABLE\b[^;]*\bDROP\s+COLUMN\b", "drops a column"),
    ("rename", r"\bALTER\s+TABLE\b[^;]*\bRENAME\b", "renames a table or column"),
    ("alter-type", r"\bALTER\s+TABLE\b[^;]*\bALTER\s+(COLUMN\s+)?\w+\s+(SET\s+DATA\s+)?TYPE\b", "changes a column type"),
    ("set-not-null", r"\bALTER\s+TABLE\b[^;]*\bSET\s+NOT\s+NULL\b", "adds NOT NULL to an existing column"),
    ("truncate", r"\bTRUNCATE\b", "truncates data"),
    ("delete-all", r"\bDELETE\s+FROM\s+\w+\s*;", "deletes every row"),
]
WARN_RULES = [
    ("add-not-null-without-default", r"\bADD\s+COLUMN\b[^;,]*\bNOT\s+NULL\b(?![^;,]*\bDEFAULT\b)",
     "adds a NOT NULL column without a default (fails on non-empty tables)"),
    ("index-without-concurrently", r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY)", "creates an index without CONCURRENTLY (locks writes)"),
]


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def lint_migration(name: str, sql: str) -> dict:
    body = strip_sql_comments(sql)
    contract = bool(CONTRACT_MARK.search(sql))
    errors, warnings = [], []
    destructive = [(rid, desc) for rid, rx, desc in RULES if re.search(rx, body, re.I | re.S)]
    for rid, desc in destructive:
        if not contract:
            errors.append(f"{name}: {desc} ({rid}) in an expand migration; move it to a later migration marked "
                          f"'-- factory:contract' after every reader of the old shape is gone")
    if contract and not destructive:
        warnings.append(f"{name}: marked contract but has no destructive statement")
    for rid, rx, desc in WARN_RULES:
        if re.search(rx, body, re.I | re.S):
            warnings.append(f"{name}: {desc} ({rid})")
    return {"file": name, "contract": contract, "destructive": [d[0] for d in destructive],
            "errors": errors, "warnings": warnings}


def cmd_migration_lint(args) -> int:
    d = Path(args.dir)
    if not d.is_dir():
        return emit({"ok": True, "files": 0, "note": f"{d} not found; nothing to lint"})
    files = sorted(p for p in d.glob("*.sql"))
    results = [lint_migration(p.name, p.read_text(encoding="utf-8")) for p in files]
    errors = [e for r in results for e in r["errors"]]
    names = [p.name for p in files]
    if not all(re.match(r"^\d{3,}[_-]", n) for n in names):
        errors.append("migration files must start with a sequence number (001_name.sql)")
    if args.base:
        # History is append-only: a migration that exists at base must not change.
        listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", args.base, "--", str(d)],
                                capture_output=True, text=True).stdout.split()
        for rel in listed:
            old = subprocess.run(["git", "show", f"{args.base}:{rel}"], capture_output=True, text=True).stdout
            cur = Path(rel)
            if not cur.is_file():
                errors.append(f"{rel}: applied migration deleted")
            elif cur.read_text(encoding="utf-8") != old:
                errors.append(f"{rel}: applied migration modified (write a new migration instead)")
    return emit({"ok": not errors, "files": len(files), "errors": errors,
                 "warnings": [w for r in results for w in r["warnings"]],
                 "contract_migrations": [r["file"] for r in results if r["contract"]]}, 0 if not errors else 1)


# ---------------------------------------------------------------------------------------
# Metric-driven abort after deploy
# ---------------------------------------------------------------------------------------


def fetch_metrics(url: str, timeout: float = 10) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "factory-release/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL is configuration)
        return json.loads(resp.read().decode("utf-8"))


def evaluate_window(first: dict, last: dict, max_error_rate: float, min_requests: int) -> dict:
    reqs = int(last.get("requests_total", 0)) - int(first.get("requests_total", 0))
    errs = int(last.get("errors_total", 0)) - int(first.get("errors_total", 0))
    restarted = reqs < 0 or errs < 0
    rate = (errs / reqs) if reqs > 0 else 0.0
    decision = "continue"
    if restarted:
        decision = "abort"
    elif reqs >= min_requests and rate > max_error_rate:
        decision = "abort"
    elif reqs < min_requests:
        decision = "inconclusive"
    return {"requests": reqs, "errors": errs, "error_rate": round(rate, 5), "restarted": restarted,
            "decision": decision, "release": last.get("release")}


def cmd_watch(args) -> int:
    deadline = time.time() + args.minutes * 60
    try:
        first = fetch_metrics(args.url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return emit({"decision": "abort", "reason": f"metrics unreachable: {exc}"}, 1)
    samples, result = 0, None
    while True:
        time.sleep(min(args.interval, max(0.0, deadline - time.time())))
        try:
            last = fetch_metrics(args.url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return emit({"decision": "abort", "reason": f"metrics unreachable: {exc}", "samples": samples}, 1)
        samples += 1
        result = evaluate_window(first, last, args.max_error_rate, args.min_requests)
        if args.expect_release and last.get("release") != args.expect_release:
            result.update(decision="abort", reason=f"serving release {last.get('release')!r}, expected {args.expect_release!r}")
        if result["decision"] == "abort" or time.time() >= deadline:
            break
    result["samples"] = samples
    if result["decision"] == "inconclusive" and args.require_traffic:
        result["decision"] = "abort"
        result["reason"] = "not enough traffic to judge the release"
    return emit(result, 1 if result["decision"] == "abort" else 0)


# ---------------------------------------------------------------------------------------
# SLO and error budget (LIFE-53, LIFE-54)
# ---------------------------------------------------------------------------------------


def parse_slo(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"```slo\s*(.*?)```", text, re.S)
    if not m:
        raise SystemExit(emit({"error": f"{path}: no ```slo block"}, 2))
    slo = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^\s*([a-z_]+)\s*:\s*([^#\n]+)", line)
        if kv:
            v = kv.group(2).strip()
            slo[kv.group(1)] = float(v) if re.fullmatch(r"[0-9.]+", v) else v
    return slo


def cmd_slo_check(args) -> int:
    slo = parse_slo(Path(args.slo))
    m = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    target = float(slo.get("availability_target", 0.995))
    reqs, errs = int(m.get("requests", 0)), int(m.get("errors", 0))
    allowed = (1 - target) * reqs
    remaining = 1 - (errs / allowed) if allowed > 0 else 1.0
    freeze_below = float(slo.get("freeze_when_budget_below", 0.0))
    state = "exhausted" if remaining <= freeze_below else "ok"
    return emit({"window_days": slo.get("window_days"), "target": target, "requests": reqs, "errors": errs,
                 "error_budget_remaining": round(remaining, 4), "state": state,
                 "action": "open a factory:freeze issue (releases and the factory stop; P0 fixes only)"
                 if state == "exhausted" else "none"}, 3 if state == "exhausted" else 0)


def cmd_freeze_check(args) -> int:
    frozen = args.open_freeze > 0
    ok = not frozen or args.p0
    return emit({"frozen": frozen, "p0_override": args.p0, "release_allowed": ok,
                 "reason": "error-budget freeze in force" if frozen and not args.p0 else "ok"}, 0 if ok else 1)


# ---------------------------------------------------------------------------------------
# Release-readiness pack (spec §7.2)
# ---------------------------------------------------------------------------------------


def sbom_summary(path: Path | None) -> dict:
    if not path or not path.is_file():
        return {"present": False}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"present": True, "parseable": False}
    comps = d.get("components") or []
    lic = {}
    for c in comps:
        for entry in c.get("licenses") or []:
            name = (entry.get("license") or {}).get("id") or (entry.get("license") or {}).get("name") or entry.get("expression")
            if name:
                lic[name] = lic.get(name, 0) + 1
    return {"present": True, "format": d.get("bomFormat"), "spec_version": d.get("specVersion"),
            "components": len(comps), "licenses": dict(sorted(lic.items(), key=lambda kv: -kv[1])[:15])}


def ai_record(ledger_dir: Path | None, merged: list[dict]) -> list[dict]:
    """Models and harness versions behind the factory PRs in this release (spec §6.5 rule 8)."""
    if not ledger_dir or not ledger_dir.is_dir():
        return []
    prs = {str(m.get("number")) for m in merged}
    out = {}
    for f in ledger_dir.rglob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if str(r.get("pr_number")) in prs:
            for role in ("agent", "verify_agent"):
                a = r.get(role) or {}
                key = (role, a.get("model"), a.get("claude_code_version"), r.get("harness_action_sha"))
                out[key] = out.get(key, 0) + 1
    return [{"role": k[0], "model": k[1], "claude_code": k[2], "action_sha": k[3], "prs": n} for k, n in sorted(out.items(), key=str)]


def cmd_pack(args) -> int:
    merged = json.loads(Path(args.merged_json).read_text()) if args.merged_json else []
    sb = sbom_summary(Path(args.sbom) if args.sbom else None)
    mig = json.loads(Path(args.migrations_json).read_text()) if args.migrations_json else None
    slo = json.loads(Path(args.slo_json).read_text()) if args.slo_json else None
    ai = ai_record(Path(args.ledger_dir) if args.ledger_dir else None, merged)
    factory_prs = [m for m in merged if str(m.get("headRefName", "")).startswith("claude/fq-")]
    checks = {
        "image_pinned_by_digest": "@sha256:" in args.image,
        "sbom_present": sb.get("present", False),
        "provenance_attested": bool(args.provenance),
        "migrations_lint_ok": (mig or {}).get("ok", True),
        "error_budget_ok": (slo or {}).get("state", "ok") == "ok",
    }
    pack = {"version": args.version, "image": args.image, "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "changes": len(merged), "factory_prs": len(factory_prs), "checks": checks,
            "ready": all(checks.values()), "sbom": sb, "migrations": mig, "slo": slo, "ai_record": ai,
            "provenance": args.provenance}
    lines = [f"# Release readiness: {args.version}", "",
             f"Image: `{args.image}`  ", f"Created: {pack['created']}  ",
             f"Verdict: **{'READY' if pack['ready'] else 'NOT READY'}** (a human approves the production environment)", "",
             "## Checks", "", "| Check | Result |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {'yes' if v else '**no**'} |" for k, v in checks.items()]
    lines += ["", f"## Changes ({len(merged)} merged pull requests, {len(factory_prs)} from the factory)", ""]
    lines += [f"- #{m.get('number')} {m.get('title', '')}" for m in merged[:200]]
    lines += ["", "## Supply chain", "", f"- SBOM: {sb.get('components', 'n/a')} components ({sb.get('format', 'none')})",
              f"- Provenance: {args.provenance or 'not attested'}"]
    if sb.get("licenses"):
        lines.append("- Licenses: " + ", ".join(f"{k} ({v})" for k, v in sb["licenses"].items()))
    lines += ["", "## AI record (models and harness behind factory changes)", ""]
    lines += [f"- {a['role']}: {a['model']} via Claude Code {a['claude_code']} (action {str(a['action_sha'])[:12]}), {a['prs']} PR(s)" for a in ai] or ["- none"]
    if mig:
        lines += ["", "## Database migrations", "", f"- Lint: {'ok' if mig.get('ok') else 'FAILED'}; contract migrations: {', '.join(mig.get('contract_migrations') or []) or 'none'}"]
        lines += [f"- warning: {w}" for w in mig.get("warnings", [])]
    if slo:
        lines += ["", "## Error budget", "", f"- Remaining: {slo.get('error_budget_remaining')} ({slo.get('state')})"]
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(args.out).with_suffix(".json").write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    return emit({"out": args.out, "ready": pack["ready"], "checks": checks}, 0 if pack["ready"] else 1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="release.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("migration-lint")
    s.add_argument("--dir", default="migrations")
    s.add_argument("--base")
    s.set_defaults(fn=cmd_migration_lint)
    s = sub.add_parser("watch")
    s.add_argument("--url", required=True)
    s.add_argument("--minutes", type=float, default=10)
    s.add_argument("--interval", type=float, default=30)
    s.add_argument("--max-error-rate", type=float, default=0.02)
    s.add_argument("--min-requests", type=int, default=50)
    s.add_argument("--expect-release")
    s.add_argument("--require-traffic", action="store_true")
    s.set_defaults(fn=cmd_watch)
    s = sub.add_parser("slo-check")
    s.add_argument("--metrics", required=True)
    s.add_argument("--slo", default="docs/factory/SLO.md")
    s.set_defaults(fn=cmd_slo_check)
    s = sub.add_parser("freeze-check")
    s.add_argument("--open-freeze", type=int, required=True)
    s.add_argument("--p0", action="store_true")
    s.set_defaults(fn=cmd_freeze_check)
    s = sub.add_parser("pack")
    s.add_argument("--version", required=True)
    s.add_argument("--image", required=True)
    s.add_argument("--sbom")
    s.add_argument("--provenance")
    s.add_argument("--merged-json")
    s.add_argument("--ledger-dir")
    s.add_argument("--migrations-json")
    s.add_argument("--slo-json")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_pack)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
