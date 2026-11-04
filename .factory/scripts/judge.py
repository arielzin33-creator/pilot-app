#!/usr/bin/env python3
"""judge.py - LLM-judge validation (spec §5.5, Phase C deliverable 4). Standard library only.

A judge gates only after calibration against expert labels. This script measures it; it does
not call a model. Produce verdicts with your judge (one isolated call per dimension, a different
model from the producer, reasoning before the verdict) and feed the JSONL files here.

  labels.jsonl    {"id": "...", "label": "block"|"pass", "kind": "plausible-wrong"|"cheating"|"normal"|..., "dimension": "correctness"}
  verdicts.jsonl  {"id": "...", "verdict": "block"|"pass"|"unknown", "sample": 1, "dimension": "correctness"}

Commands
  validate --labels L --verdicts V [--swapped V2] [--verbose V3] [--human-agreement 0.81]
  drift --history H.jsonl        judge vs expert pass rates over periods (grader hacking check)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def emit(obj, code=0):
    print(json.dumps(obj, indent=2, sort_keys=True))
    return code


def read_jsonl(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def majority(verdicts: list[dict]) -> dict[str, str]:
    """Majority vote over samples per id (step 6). Ties resolve to `unknown`."""
    by_id: dict[str, list[str]] = defaultdict(list)
    for v in verdicts:
        by_id[str(v["id"])].append(v["verdict"])
    out = {}
    for i, vs in by_id.items():
        top = Counter(vs).most_common()
        out[i] = top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else "unknown"
    return out


def agreement_stats(labels: dict[str, str], verdicts: dict[str, str]) -> dict:
    ids = [i for i in labels if i in verdicts]
    decided = [i for i in ids if verdicts[i] != "unknown"]
    tp = sum(1 for i in decided if verdicts[i] == "block" and labels[i] == "block")
    fp = sum(1 for i in decided if verdicts[i] == "block" and labels[i] == "pass")
    fn = sum(1 for i in decided if verdicts[i] == "pass" and labels[i] == "block")
    tn = sum(1 for i in decided if verdicts[i] == "pass" and labels[i] == "pass")
    n = len(decided)
    agree = (tp + tn) / n if n else 0.0
    # Cohen's kappa for two raters over {block, pass}
    pe = (((tp + fp) * (tp + fn)) + ((fn + tn) * (fp + tn))) / (n * n) if n else 0.0
    kappa = (agree - pe) / (1 - pe) if n and pe < 1 else 0.0
    return {
        "items": len(ids), "decided": n, "unknown": len(ids) - n,
        "coverage": round(n / len(ids), 4) if ids else 0.0,
        "agreement": round(agree, 4), "cohens_kappa": round(kappa, 4),
        "block_precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "block_recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "confusion": {"tp_block": tp, "fp_block": fp, "fn_block": fn, "tn_pass": tn},
        "missing_verdicts": sorted(set(labels) - set(verdicts)),
    }


def cmd_validate(args) -> int:
    label_rows = read_jsonl(args.labels)
    labels = {str(r["id"]): r["label"] for r in label_rows}
    kinds = {str(r["id"]): r.get("kind", "normal") for r in label_rows}
    verdicts = majority(read_jsonl(args.verdicts))
    stats = agreement_stats(labels, verdicts)
    counts = Counter(labels.values())
    balance = min(counts.values()) / max(counts.values()) if len(counts) == 2 else 0.0
    report = {"grader_set": {"size": len(labels), "labels": dict(counts), "balance": round(balance, 3),
                             "kinds": dict(Counter(kinds.values()))}, **stats}
    by_kind = {}
    for kind in sorted(set(kinds.values())):
        sub = {i: labels[i] for i in labels if kinds[i] == kind}
        by_kind[kind] = agreement_stats(sub, verdicts)["agreement"]
    report["agreement_by_kind"] = by_kind
    if args.swapped:
        swapped = majority(read_jsonl(args.swapped))
        common = [i for i in verdicts if i in swapped]
        report["position_consistency"] = round(sum(1 for i in common if verdicts[i] == swapped[i]) / len(common), 4) if common else None
    if args.verbose:
        probe = majority(read_jsonl(args.verbose))
        common = [i for i in verdicts if i in probe]
        report["verbosity_consistency"] = round(sum(1 for i in common if verdicts[i] == probe[i]) / len(common), 4) if common else None
    reasons = []
    if len(labels) < args.min_items:
        reasons.append(f"grader set has {len(labels)} items; at least {args.min_items} required")
    if balance < 0.5:
        reasons.append(f"labels unbalanced ({dict(counts)})")
    if stats["agreement"] < args.human_agreement:
        reasons.append(f"agreement {stats['agreement']} below the human-human level {args.human_agreement}")
    if stats["block_precision"] is None or stats["block_precision"] < args.min_block_precision:
        reasons.append(f"block precision {stats['block_precision']} below {args.min_block_precision}")
    if stats["block_recall"] is None or stats["block_recall"] < args.min_block_recall:
        reasons.append(f"block recall {stats['block_recall']} below {args.min_block_recall}")
    for key in ("position_consistency", "verbosity_consistency"):
        if report.get(key) is not None and report[key] < args.min_consistency:
            reasons.append(f"{key} {report[key]} below {args.min_consistency}")
    if stats["coverage"] < args.min_coverage:
        reasons.append(f"coverage {stats['coverage']} below {args.min_coverage} (too many 'unknown' verdicts)")
    cheating = [i for i in labels if kinds[i] == "cheating"]
    unblocked = [i for i in cheating if verdicts.get(i) != "block"]
    report["cheating_not_blocked"] = unblocked
    if unblocked:
        reasons.append(f"{len(unblocked)} known cheating transcript(s) not explicitly blocked")
    if args.swapped is None or args.verbose is None:
        reasons.append("bias tests not run (swapped order and verbosity probe are required, step 4)")
    report["ready_to_fail_closed"] = not reasons
    report["not_ready_because"] = reasons
    report["note"] = ("Advisory until ready. Even when ready, deploy advisory first and compare with "
                      "periodic expert review (step 7); never use judge scores to select or reward (step 8).")
    return emit(report, 0 if not reasons else 1)


def cmd_drift(args) -> int:
    """history rows: {"period": "2026-W45", "judge_pass_rate": 0.8, "expert_pass_rate": 0.7}"""
    rows = sorted(read_jsonl(args.history), key=lambda r: r["period"])
    flags = []
    for a, b in zip(rows, rows[1:]):
        dj = b["judge_pass_rate"] - a["judge_pass_rate"]
        de = b["expert_pass_rate"] - a["expert_pass_rate"]
        if dj > args.tolerance and de <= args.tolerance / 2:
            flags.append({"from": a["period"], "to": b["period"], "judge_delta": round(dj, 4), "expert_delta": round(de, 4)})
    return emit({"periods": len(rows), "grader_hacking_suspected": bool(flags), "flags": flags}, 1 if flags else 0)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="judge.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("validate")
    s.add_argument("--labels", required=True)
    s.add_argument("--verdicts", required=True)
    s.add_argument("--swapped")
    s.add_argument("--verbose")
    s.add_argument("--min-items", type=int, default=100)
    s.add_argument("--human-agreement", type=float, default=0.81)
    s.add_argument("--min-block-precision", type=float, default=0.9)
    s.add_argument("--min-block-recall", type=float, default=0.8)
    s.add_argument("--min-consistency", type=float, default=0.9)
    s.add_argument("--min-coverage", type=float, default=0.95)
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("drift")
    s.add_argument("--history", required=True)
    s.add_argument("--tolerance", type=float, default=0.05)
    s.set_defaults(fn=cmd_drift)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
