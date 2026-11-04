#!/usr/bin/env python3
"""intake.py - Phase E intake tooling: from an approved brief to a ready backlog.

Standard library only. Run with `python3 -I .factory/scripts/intake.py <command>`.

Commands
  spec-check <specs/NNN-name> [--gate spec|plan|tasks]   validate a spec folder at a gate
  spec-status <specs/NNN-name>                           approval state as JSON
  prfaq-check <path>                                     validate a PR/FAQ and its kill gate
  backlog <specs/NNN-name> --out issues.json             tasks.md -> issues with hand-offs
  tokens <design/tokens.json> --css out.css [--ts out.ts]  DTCG tokens -> CSS variables
  registry [--ledger-dir DIR]                            validate the core asset registry
  unblock --issues-json F                                blocked issues whose dependencies closed

Four approval gates (spec §10.5, LIFE-15): brief (PR/FAQ), spec, plan, tasks. Each artifact
carries front matter `status: approved`, `approved_by`, `approved_on`, set by a human in a
reviewed pull request. A later gate cannot be approved while an earlier one is not.

Exit codes: 0 ok, 1 validation failed, 2 misconfigured.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLARIFY = re.compile(r"\[NEEDS CLARIFICATION(?::[^\]]*)?\]")


def emit(obj: dict, code: int = 0) -> int:
    print(json.dumps(obj, indent=2, sort_keys=True))
    return code


def load_engine():
    """Load factory.py from next to this file (works under python -I, which skips sys.path[0])."""
    spec = importlib.util.spec_from_file_location("factory_engine", HERE / "factory.py")
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------------------
# Front matter and documents
# ---------------------------------------------------------------------------------------


def front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_]+)\s*:\s*(.*?)\s*$", line)
        if m:
            value = re.sub(r"\s+#.*$", "", m.group(2)).strip().strip('"').strip("'")
            meta[m.group(1)] = value
    return meta, text[end + 4:].lstrip("\n")


def approved(meta: dict) -> bool:
    return meta.get("status") == "approved"


def approval_errors(name: str, meta: dict) -> list[str]:
    errs = []
    if approved(meta):
        if not meta.get("approved_by"):
            errs.append(f"{name}: status approved but approved_by is empty")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", meta.get("approved_on", "")):
            errs.append(f"{name}: status approved but approved_on is not YYYY-MM-DD")
    elif meta.get("status") not in ("draft", "approved", "killed"):
        errs.append(f"{name}: status must be draft or approved (got {meta.get('status')!r})")
    return errs


def read_doc(path: Path) -> tuple[dict, str] | None:
    if not path.is_file():
        return None
    return front_matter(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------
# Spec, plan and tasks checks
# ---------------------------------------------------------------------------------------

SPEC_SECTIONS = ["Problem and users", "User stories", "Functional requirements",
                 "Non-functional requirements", "Out of scope", "Success criteria"]
TASK_FIELDS = ("files", "done_when", "depends_on", "requirements")


def headings(body: str) -> list[str]:
    return [re.sub(r"^\d+\.\s*", "", h.strip()) for h in re.findall(r"^##\s+(.+)$", body, re.M)]


def check_spec(meta: dict, body: str) -> tuple[list[str], list[str], dict]:
    errs, warns = approval_errors("spec.md", meta), []
    hs = headings(body)
    for s in SPEC_SECTIONS:
        if not any(h.lower().startswith(s.lower()) for h in hs):
            errs.append(f"spec.md: missing section '{s}'")
    stories = re.findall(r"^###\s+(US-\d+)\b", body, re.M)
    if not stories:
        errs.append("spec.md: no user stories (### US-n)")
    for us in stories:
        block = body.split(f"### {us}", 1)[1].split("\n### ", 1)[0]
        if not all(k in block for k in ("**Given**", "**When**", "**Then**")):
            errs.append(f"spec.md: {us} lacks a Given/When/Then acceptance scenario")
    frs = re.findall(r"\*\*(FR-\d{3})\*\*", body)
    dupes = sorted({f for f in frs if frs.count(f) > 1})
    if dupes:
        errs.append(f"spec.md: duplicate requirement ids {dupes}")
    if not frs:
        errs.append("spec.md: no functional requirements (**FR-nnn**)")
    markers = CLARIFY.findall(body)
    if markers and approved(meta):
        errs.append(f"spec.md: approved with {len(markers)} [NEEDS CLARIFICATION] marker(s)")
    elif markers:
        warns.append(f"spec.md: {len(markers)} open [NEEDS CLARIFICATION] marker(s)")
    if approved(meta) and re.search(r"^- \[ \]", body, re.M):
        errs.append("spec.md: approved with unchecked review checklist items")
    ids = set(frs) | set(stories) | set(re.findall(r"\*\*((?:NFR|SC)-\d{3})\*\*", body))
    return errs, warns, {"requirements": sorted(ids), "clarifications": len(markers)}


def check_plan(meta: dict, body: str, spec_meta: dict) -> tuple[list[str], list[str]]:
    errs, warns = approval_errors("plan.md", meta), []
    if "Constitution check" not in body:
        errs.append("plan.md: missing 'Constitution check' section")
    else:
        section = body.split("Constitution check", 1)[1].split("\n## ", 1)[0]
        rows = [r for r in section.splitlines() if r.startswith("|") and not re.match(r"^\|\s*-", r)][1:]
        if not rows:
            errs.append("plan.md: constitution check table is empty")
        for r in rows:
            cells = [c.strip() for c in r.strip("|").split("|")]
            if len(cells) < 3 or not cells[1] or not cells[2]:
                errs.append(f"plan.md: constitution row incomplete: {r.strip()}")
            elif cells[1].lower() not in ("yes", "no", "exception"):
                errs.append(f"plan.md: constitution row must say yes, no or exception: {r.strip()}")
            elif cells[1].lower() == "no" and approved(meta):
                errs.append(f"plan.md: approved while violating: {cells[0]}")
    if CLARIFY.search(body) and approved(meta):
        errs.append("plan.md: approved with [NEEDS CLARIFICATION] markers")
    if approved(meta) and not approved(spec_meta):
        errs.append("plan.md: approved before spec.md is approved (gate order)")
    return errs, warns


def parse_tasks(body: str) -> list[dict]:
    tasks = []
    for m in re.finditer(r"^##\s+(T-\d{3}):\s*(.+?)\s*$", body, re.M):
        start = m.end()
        nxt = re.search(r"^##\s+T-\d{3}:", body[start:], re.M)
        block = body[start:start + nxt.start()] if nxt else body[start:]
        fields, detail = {}, []
        for line in block.splitlines():
            f = re.match(r"^-\s+([a-z_]+)\s*:\s*(.*?)\s*$", line)
            if f and f.group(1) in TASK_FIELDS and f.group(1) not in fields:
                fields[f.group(1)] = f.group(2)
            elif line.strip():
                detail.append(line.rstrip())
        def split(v):
            return [x.strip() for x in (v or "").split(",") if x.strip() and x.strip().lower() != "none"]
        tasks.append({"id": m.group(1), "title": m.group(2), "files": split(fields.get("files")),
                      "done_when": fields.get("done_when", ""), "depends_on": split(fields.get("depends_on")),
                      "requirements": split(fields.get("requirements")), "missing": [k for k in TASK_FIELDS if k not in fields],
                      "detail": "\n".join(detail).strip()})
    return tasks


def check_tasks(meta: dict, body: str, plan_meta: dict, req_ids: set[str]) -> tuple[list[str], list[str], list[dict]]:
    errs, warns = approval_errors("tasks.md", meta), []
    tasks = parse_tasks(body)
    if not tasks:
        errs.append("tasks.md: no tasks (## T-nnn: title)")
    ids = [t["id"] for t in tasks]
    for t in tasks:
        if t["missing"]:
            errs.append(f"tasks.md: {t['id']} missing fields {t['missing']}")
        if not t["files"]:
            errs.append(f"tasks.md: {t['id']} lists no files")
        if not t["done_when"]:
            errs.append(f"tasks.md: {t['id']} has no done_when")
        for d in t["depends_on"]:
            if d not in ids:
                errs.append(f"tasks.md: {t['id']} depends on unknown {d}")
        for r in t["requirements"]:
            if req_ids and r not in req_ids:
                errs.append(f"tasks.md: {t['id']} cites unknown requirement {r}")
    if len(ids) != len(set(ids)):
        errs.append("tasks.md: duplicate task ids")
    # dependency cycles
    graph = {t["id"]: t["depends_on"] for t in tasks}
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(d) for d in graph.get(n, []) if d in graph):
            return True
        state[n] = 2
        return False
    if any(visit(n) for n in graph):
        errs.append("tasks.md: dependency cycle")
    covered = {r for t in tasks for r in t["requirements"]}
    uncovered = sorted(r for r in req_ids if r.startswith(("FR-", "US-")) and r not in covered)
    if uncovered:
        warns.append(f"tasks.md: requirements with no task: {uncovered}")
    if approved(meta) and not approved(plan_meta):
        errs.append("tasks.md: approved before plan.md is approved (gate order)")
    return errs, warns, tasks


def spec_folder(path: str) -> Path:
    p = Path(path)
    return p if p.is_dir() else p.parent


def cmd_spec_check(args) -> int:
    folder = spec_folder(args.folder)
    gate = args.gate
    out: dict = {"folder": str(folder), "gate": gate, "errors": [], "warnings": []}
    spec = read_doc(folder / "spec.md")
    if spec is None:
        return emit({**out, "errors": ["spec.md missing"]}, 1)
    s_meta, s_body = spec
    errs, warns, info = check_spec(s_meta, s_body)
    out["errors"] += errs
    out["warnings"] += warns
    out.update(info)
    plan = read_doc(folder / "plan.md")
    tasks_doc = read_doc(folder / "tasks.md")
    if gate in ("plan", "tasks"):
        if plan is None:
            out["errors"].append("plan.md missing")
        else:
            e, w = check_plan(plan[0], plan[1], s_meta)
            out["errors"] += e
            out["warnings"] += w
    if gate == "tasks":
        if tasks_doc is None:
            out["errors"].append("tasks.md missing")
        else:
            e, w, tasks = check_tasks(tasks_doc[0], tasks_doc[1], plan[0] if plan else {}, set(info["requirements"]))
            out["errors"] += e
            out["warnings"] += w
            out["tasks"] = len(tasks)
    out["status"] = {"spec": s_meta.get("status"), "plan": (plan or ({}, ""))[0].get("status"),
                     "tasks": (tasks_doc or ({}, ""))[0].get("status")}
    out["ok"] = not out["errors"]
    return emit(out, 0 if out["ok"] else 1)


def spec_errors(folder: Path) -> list[str]:
    """Every error the tasks gate would report (approvers, dates, gate order, clarifications)."""
    spec, plan, tasks_doc = (read_doc(folder / f"{n}.md") for n in ("spec", "plan", "tasks"))
    if spec is None or plan is None or tasks_doc is None:
        return ["spec, plan or tasks missing"]
    errs, _, info = check_spec(*spec)
    e2, _ = check_plan(plan[0], plan[1], spec[0])
    e3, _, _ = check_tasks(tasks_doc[0], tasks_doc[1], plan[0], set(info["requirements"]))
    return errs + e2 + e3


def spec_status(folder: Path) -> dict:
    status = {}
    for name in ("spec", "plan", "tasks"):
        doc = read_doc(folder / f"{name}.md")
        status[name] = (doc[0].get("status") if doc else None) or "missing"
    errors = spec_errors(folder)
    status["errors"] = errors
    status["implementable"] = all(status[n] == "approved" for n in ("spec", "plan", "tasks")) and not errors
    return status


def cmd_spec_status(args) -> int:
    return emit(spec_status(spec_folder(args.folder)))


# ---------------------------------------------------------------------------------------
# PR/FAQ and kill gate
# ---------------------------------------------------------------------------------------


def cmd_prfaq_check(args) -> int:
    doc = read_doc(Path(args.path))
    if doc is None:
        return emit({"ok": False, "errors": ["file missing"]}, 1)
    meta, body = doc
    errs = approval_errors("PRFAQ.md", meta)
    gate = {}
    block = re.search(r"KILL_GATE:(.*?)DECISION:[ \t]*([^\n]*)", body, re.S)
    if not block:
        errs.append("PRFAQ.md: no KILL_GATE block")
        decision = None
    else:
        for k, v in re.findall(r"^[ \t]*([a-z0-9_]+):[ \t]*([^\n]+?)[ \t]*$", block.group(1), re.M):
            gate[k] = v
        decision = block.group(2).split("#")[0].strip()
        if "|" in decision or decision not in ("proceed", "revise", "kill"):
            errs.append("PRFAQ.md: DECISION not made (still a choice list)")
        nos = [k for k, v in gate.items() if v != "yes"]
        if decision == "proceed" and nos:
            errs.append(f"PRFAQ.md: DECISION proceed with unmet criteria {nos}")
        if approved(meta) and decision != "proceed":
            errs.append("PRFAQ.md: approved but DECISION is not proceed")
    for section in ("Press release", "External FAQ", "Internal FAQ"):
        if f"## {section}" not in body:
            errs.append(f"PRFAQ.md: missing section '{section}'")
    empty = re.findall(r"^\|\s*([^|]+?)\s*\|\s*\|\s*$", body, re.M)
    if empty and approved(meta):
        errs.append(f"PRFAQ.md: approved with {len(empty)} unanswered FAQ row(s)")
    return emit({"ok": not errs, "errors": errs, "decision": decision, "kill_gate": gate,
                 "status": meta.get("status")}, 0 if not errs else 1)


# ---------------------------------------------------------------------------------------
# tasks.md -> backlog
# ---------------------------------------------------------------------------------------


def cmd_backlog(args) -> int:
    folder = spec_folder(args.folder)
    engine = load_engine()
    root = engine.repo_root(str(folder))
    policy = engine.load_policy(root)
    status = spec_status(folder)
    if not status["implementable"] and not args.allow_draft:
        return emit({"ok": False, "error": "spec, plan and tasks must all be approved", "status": status}, 1)
    tasks_doc = read_doc(folder / "tasks.md")
    tasks = parse_tasks(tasks_doc[1]) if tasks_doc else []
    spec_rel = folder.resolve().relative_to(root.resolve()).as_posix()
    issues = []
    for t in tasks:
        pred = engine.predict_tier(t["files"], policy)
        lb = "LOAD_BEARING_TOUCHED" in pred["violations"]
        tier = pred["tier"]
        if lb or tier >= 3:
            disposition = "human-only"
        elif tier == 2 and not policy.get("tier2_unattended_with_approved_spec"):
            disposition = "ready-to-spec"
        else:
            disposition = "ready-to-implement"
        body = "\n".join([
            f"<!-- factory-task: {spec_rel}#{t['id']} -->",
            f"Task **{t['id']}** of spec `{spec_rel}` (generated by `intake.py backlog`).",
            "", f"**Done when:** {t['done_when']}", "",
            f"**Requirements:** {', '.join(t['requirements']) or 'none'}",
            f"**Depends on:** {', '.join(t['depends_on']) or 'none'}",
            f"**Expected files:** {', '.join(t['files'])}", "", t["detail"] or "",
        ]).strip() + "\n"
        handoff = {"disposition": disposition, "done_when": " ".join(t["done_when"].split())[:400],
                   "files_expected": ",".join(t["files"])[:400], "predicted_tier": str(tier),
                   "load_bearing": "true" if lb else "false", "gate_level": "full",
                   "confidence": "high", "spec": spec_rel}
        labels = ["factory:" + disposition, f"factory:tier-{tier}", "factory:from-spec"]
        if t["depends_on"]:
            labels.append("factory:blocked")
        issues.append({"task_id": t["id"], "marker": f"{spec_rel}#{t['id']}", "title": f"{t['id']}: {t['title']}", "body": body,
                       "labels": labels, "depends_on": t["depends_on"], "handoff": handoff,
                       "predicted_tier": tier})
    Path(args.out).write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for i in issues:
        counts[i["handoff"]["disposition"]] = counts.get(i["handoff"]["disposition"], 0) + 1
    return emit({"ok": True, "out": args.out, "issues": len(issues), "by_disposition": counts})


def handoff_comment(h: dict) -> str:
    lines = ["<!-- factory-handoff:v1 -->", "```text"]
    for k in ("disposition", "done_when", "files_expected", "predicted_tier", "load_bearing",
              "gate_level", "confidence", "spec"):
        lines.append(f"{k}: {h.get(k, '')}")
    lines += ["```", "", "Generated from an approved tasks.md (Phase E backlog generator)."]
    return "\n".join(lines)


def cmd_handoff_text(args) -> int:
    issues = json.loads(Path(args.issues_json).read_text(encoding="utf-8"))
    item = next((i for i in issues if i["task_id"] == args.task), None)
    if not item:
        return emit({"error": f"no task {args.task}"}, 1)
    print(handoff_comment(item["handoff"]))
    return 0


# ---------------------------------------------------------------------------------------
# Design tokens (W3C DTCG format) -> CSS custom properties
# ---------------------------------------------------------------------------------------


def flatten_tokens(tree: dict, prefix: tuple = (), inherited_type: str | None = None) -> dict[str, dict]:
    out = {}
    group_type = tree.get("$type", inherited_type)
    for key, val in tree.items():
        if key.startswith("$") or not isinstance(val, dict):
            continue
        path = prefix + (key,)
        if "$value" in val:
            out[".".join(path)] = {"value": val["$value"], "type": val.get("$type", group_type),
                                   "description": val.get("$description", "")}
        else:
            out.update(flatten_tokens(val, path, group_type))
    return out


def resolve_tokens(flat: dict[str, dict]) -> dict[str, dict]:
    def resolve(v, seen):
        if isinstance(v, str):
            m = re.fullmatch(r"\{([^}]+)\}", v.strip())
            if m:
                ref = m.group(1)
                if ref in seen or ref not in flat:
                    raise ValueError(f"unresolvable or circular alias {{{ref}}}")
                return resolve(flat[ref]["value"], seen | {ref})
            return v
        if isinstance(v, dict):
            return {k: resolve(x, seen) for k, x in v.items()}
        if isinstance(v, list):
            return [resolve(x, seen) for x in v]
        return v
    return {k: dict(t, value=resolve(t["value"], {k})) for k, t in flat.items()}


def css_value(t: dict) -> str:
    v = t["value"]
    if t.get("type") == "dimension" and isinstance(v, dict):
        return f"{v.get('value')}{v.get('unit', 'px')}"
    if t.get("type") == "fontFamily" and isinstance(v, list):
        return ", ".join(f'"{x}"' if " " in x else x for x in v)
    if t.get("type") == "shadow" and isinstance(v, dict):
        return " ".join(str(css_value({"type": "dimension", "value": v.get(k)})) if isinstance(v.get(k), dict)
                        else str(v.get(k, "")) for k in ("offsetX", "offsetY", "blur", "spread", "color"))
    if isinstance(v, (int, float)):
        return str(v)
    return str(v)


def cmd_tokens(args) -> int:
    tree = json.loads(Path(args.tokens).read_text(encoding="utf-8"))
    try:
        tokens = resolve_tokens(flatten_tokens(tree))
    except ValueError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)
    untyped = sorted(k for k, t in tokens.items() if not t.get("type"))
    lines = ["/* Generated by .factory/scripts/intake.py tokens from design/tokens.json. Do not edit. */",
             ":root {"]
    for name in sorted(tokens):
        lines.append(f"  --{name.replace('.', '-')}: {css_value(tokens[name])};")
    lines.append("}")
    Path(args.css).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.ts:
        ts = {k: css_value(t) for k, t in sorted(tokens.items())}
        Path(args.ts).write_text("// Generated from design/tokens.json. Do not edit.\n"
                                 f"export const tokens = {json.dumps(ts, indent=2)} as const;\n"
                                 "export type TokenName = keyof typeof tokens;\n", encoding="utf-8")
    return emit({"ok": not untyped, "tokens": len(tokens), "untyped": untyped, "css": args.css},
                0 if not untyped else 1)


# ---------------------------------------------------------------------------------------
# Core asset registry (spec §10.5 deliverable 5, HIST-36)
# ---------------------------------------------------------------------------------------


def cmd_registry(args) -> int:
    engine = load_engine()
    root = engine.repo_root()
    path = root / ".factory/registry.json"
    if not path.is_file():
        return emit({"ok": False, "error": ".factory/registry.json missing"}, 2)
    reg = json.loads(path.read_text(encoding="utf-8"))
    errs, ids = [], set()
    for a in reg.get("assets", []):
        for k in ("id", "kind", "path", "description", "owner", "version"):
            if not a.get(k):
                errs.append(f"{a.get('id', '?')}: missing {k}")
        if a.get("id") in ids:
            errs.append(f"duplicate id {a.get('id')}")
        ids.add(a.get("id"))
        if a.get("path") and not (root / a["path"]).exists():
            errs.append(f"{a.get('id')}: path {a['path']} does not exist")
        if a.get("kind") not in ("template", "skill", "agent", "scaffold", "workflow", "script"):
            errs.append(f"{a.get('id')}: unknown kind {a.get('kind')!r}")
    usage = {i: 0 for i in ids}
    if args.ledger_dir and Path(args.ledger_dir).is_dir():
        for rec in engine.load_ledger(Path(args.ledger_dir)):
            for i in rec.get("assets", []) or []:
                if i in usage:
                    usage[i] += 1
    return emit({"ok": not errs, "errors": errs, "assets": len(ids), "usage": usage}, 0 if not errs else 1)


def cmd_unblock(args) -> int:
    """Given `gh issue list --state all --label factory:from-spec --json number,state,body,labels`,
    list the blocked issues whose dependencies are all closed."""
    issues = json.loads(Path(args.issues_json).read_text(encoding="utf-8"))
    by_marker = {}
    for i in issues:
        m = re.search(r"<!-- factory-task: (\S+) -->", i.get("body") or "")
        if m:
            by_marker[m.group(1)] = i
    ready = []
    for i in issues:
        labels = {(x.get("name") if isinstance(x, dict) else x) for x in i.get("labels", [])}
        if "factory:blocked" not in labels or str(i.get("state", "")).upper() != "OPEN":
            continue
        m = re.search(r"<!-- factory-task: (\S+)#(T-\d{3}) -->", i.get("body") or "")
        dep = re.search(r"\*\*Depends on:\*\* (.+)", i.get("body") or "")
        if not m or not dep:
            continue
        deps = [d.strip() for d in dep.group(1).split(",") if d.strip() and d.strip() != "none"]
        states = [str((by_marker.get(f"{m.group(1)}#{d}") or {}).get("state", "MISSING")).upper() for d in deps]
        if all(s == "CLOSED" for s in states):
            ready.append(i["number"])
    return emit({"unblock": sorted(ready)})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="intake.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("spec-check")
    s.add_argument("folder")
    s.add_argument("--gate", choices=("spec", "plan", "tasks"), default="tasks")
    s.set_defaults(fn=cmd_spec_check)
    s = sub.add_parser("spec-status")
    s.add_argument("folder")
    s.set_defaults(fn=cmd_spec_status)
    s = sub.add_parser("prfaq-check")
    s.add_argument("path")
    s.set_defaults(fn=cmd_prfaq_check)
    s = sub.add_parser("backlog")
    s.add_argument("folder")
    s.add_argument("--out", required=True)
    s.add_argument("--allow-draft", action="store_true", help="preview only; never for publishing")
    s.set_defaults(fn=cmd_backlog)
    s = sub.add_parser("handoff-text")
    s.add_argument("--issues-json", required=True)
    s.add_argument("--task", required=True)
    s.set_defaults(fn=cmd_handoff_text)
    s = sub.add_parser("unblock")
    s.add_argument("--issues-json", required=True)
    s.set_defaults(fn=cmd_unblock)
    s = sub.add_parser("tokens")
    s.add_argument("tokens")
    s.add_argument("--css", required=True)
    s.add_argument("--ts")
    s.set_defaults(fn=cmd_tokens)
    s = sub.add_parser("registry")
    s.add_argument("--ledger-dir")
    s.set_defaults(fn=cmd_registry)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
