#!/usr/bin/env python3
"""automerge.py - qualified auto-merge for narrow Tier 0 classes (spec §9.6, Phase F
deliverable 4; ADR-05). Standard library only.

Merging is a human act by default. A class of change may be merged by the factory only after it
has qualified on repository-specific evidence, and only while that evidence holds:

  1. the class is defined narrowly in policy.json "auto_merge.classes" (one change type, one
     path pattern, one producer);
  2. qualification: at least `window_prs` consecutive merged PRs in the class, each approved by
     a human, with zero reverts and zero escapes at the `audit_days` audit;
  3. eval evidence: pass^k on the class's eval tasks at or above the human acceptance rate,
     recorded with the same model and harness that run today;
  4. budgets: money (mean cost per PR), reliability (publish success) and attention (open
     sampled reviews) within the class limits;
  5. a human records the decision: status "qualified", qualified_on, decision_ref and the
     pinned runtime in policy.json, in a reviewed PR, plus an entry in DECISIONS.md;
  6. sampled review continues: one in `sample_every` auto-merged PRs gets a post-merge review;
  7. automatic revocation: an escape, a revert, a model or harness change, or a red-team
     failure since qualification makes every decision "human review" until requalified.

Commands
  classify --classify-json F --pr-json F                  which class a PR belongs to, if any
  decide   --classify-json F --pr-json F --evidence-json F  may this PR be auto-merged now?
  qualify  --class C --history-json F [--eval-json F] [--ledger-dir D] [--today YYYY-MM-DD]
  validate                                                policy sanity (used by the test suite)

Exit codes: 0 = yes / ok, 1 = no, 2 = misconfigured.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def emit(obj, code=0):
    print(json.dumps(obj, indent=2, sort_keys=True))
    return code


def load_engine():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("factory_engine", HERE / "factory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


E = load_engine()


def policy() -> dict:
    path = os.environ.get("FACTORY_POLICY") or str(HERE.parent / "policy.json")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def am(pol: dict) -> dict:
    return pol.get("auto_merge") or {"enabled": False, "classes": {}}


def read(path: str | None, default=None):
    if not path:
        return default
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default


# ---------------------------------------------------------------------------------------
# Class membership (deterministic; computed from the trusted diff policy, never from the PR)
# ---------------------------------------------------------------------------------------

DEPENDABOT_COMMITTERS = {"web-flow", "dependabot[bot]"}   # verify on the first live Dependabot PR
EXACT_VERSION = re.compile(r"^[~^]?(\d+)\.(\d+)\.(\d+)$")
BLOCKING_LABELS = {"factory:needs-human", "factory:human-only", "factory:no-auto-merge", "factory:rejected"}


def bump_kind(old: str, new: str) -> str:
    """Exact versions only (optionally ^ or ~): ranges such as "1.2.3 || >=0" are 'unknown'."""
    o, n = str(old or "").strip(), str(new or "").strip()
    a, b = EXACT_VERSION.fullmatch(o), EXACT_VERSION.fullmatch(n)
    prefix = lambda v: v[0] if v[:1] in ("^", "~") else ""
    if not a or not b or prefix(o) != prefix(n):   # a changed range operator is not a version bump
        return "unknown"
    a, b = tuple(map(int, a.groups())), tuple(map(int, b.groups()))
    if b <= a:
        return "downgrade"
    if b[0] != a[0]:
        return "major"
    return "minor" if b[1] != a[1] else "patch"


def author_login(pr: dict) -> str:
    login = (pr.get("author") or {}).get("login") or ""
    return login[4:] + "[bot]" if login.startswith("app/") else login


def producer_of(pr: dict, pol: dict) -> str:
    head = pr.get("headRefName") or ""
    author = author_login(pr)
    factory_authors = set(am(pol).get("factory_authors") or ["github-actions[bot]"])
    if head.startswith("claude/fq-") and author in factory_authors:
        return "factory"
    if head.startswith("dependabot/") and author == "dependabot[bot]":
        return "dependabot"
    return "human"


# Files coding agents load as instructions (Claude Code, Codex and others), at any depth. A change
# to one steers every later unattended run, so it always needs a human, whatever the class says.
AGENT_INSTRUCTION_NAME = re.compile(
    r"(^|/)(?:(?:CLAUDE|AGENTS|AGENT|GEMINI|COPILOT|CONVENTIONS)(?:\.[^/]*)?\.md"
    r"|copilot-instructions\.md|\.cursorrules|\.windsurfrules|\.clinerules[^/]*)$", re.IGNORECASE)
AGENT_INSTRUCTION_DIR = re.compile(r"(^|/)(?:\.claude|\.codex|\.cursor|\.github/instructions|\.github/prompts|\.factory)/",
                                   re.IGNORECASE)


def agent_instruction_files(files: list[str]) -> list[str]:
    return [f for f in files if AGENT_INSTRUCTION_NAME.search(f) or AGENT_INSTRUCTION_DIR.search(f)]


def canonical_tarball(name: str, version: str) -> str:
    base = name.split("/")[-1]
    return f"https://registry.npmjs.org/{name}/-/{base}-{version}.tgz"


def bare_version(v) -> str:
    return str(v or "").strip().lstrip("^~")


def lock_delta_problems(delta: list[dict], bumps: list[dict]) -> list[str]:
    """Only the bumped packages may change in the lockfile, each from exactly the old version to
    exactly the new one (every copy, nested ones included), at its canonical registry URL."""
    out = []
    by_name = {b.get("name"): b for b in bumps}
    for d in delta:
        name = d.get("name") or ""
        b = by_name.get(name)
        if d.get("change") != "changed" or b is None:
            out.append(f"lockfile {d.get('change')} entry {d.get('path')} is not one of the bumped packages")
            continue
        if str(d.get("old")) != bare_version(b.get("from")) or str(d.get("new")) != bare_version(b.get("to")):
            out.append(f"lockfile entry {d.get('path')} moves {d.get('old')} -> {d.get('new')}, "
                       f"not the bumped {bare_version(b.get('from'))} -> {bare_version(b.get('to'))}")
            continue
        if d.get("resolved") != canonical_tarball(name, str(d.get("new") or "")):
            out.append(f"lockfile entry {name} resolves to {d.get('resolved')!r}, not its canonical registry tarball")
    return out


def match_class(cls_json: dict, pr: dict, pol: dict) -> dict:
    """Return {"class": name | None, "reasons": [...]} for one PR."""
    classes = am(pol).get("classes") or {}
    files = cls_json.get("files") or []
    raw = pr.get("raw_files")
    out = {"class": None, "candidates": [], "reasons": []}
    if cls_json.get("tier") != 0:
        out["reasons"].append(f"tier {cls_json.get('tier')} (only Tier 0 classes qualify in 0.x)")
        return out
    if cls_json.get("violations") or cls_json.get("stop_reasons"):
        out["reasons"].append(f"diff policy violations: {cls_json.get('stop_reasons') or cls_json.get('violations')}")
        return out
    if raw is None or sorted(raw) != sorted(files):
        # ignore_in_diff hides paths from the tier classifier; auto-merge sees every file.
        out["reasons"].append(f"raw diff differs from the classified files: {sorted(set(raw or []) - set(files))[:5]}")
        return out
    agent_files = agent_instruction_files(raw)
    if agent_files:
        out["reasons"].append(f"changes agent instruction files, which always need a human: {agent_files[:5]}")
        return out
    producer = producer_of(pr, pol)
    for name, c in classes.items():
        if name.startswith("_"):
            continue
        why = []
        if producer != c.get("producer"):
            why.append(f"producer {producer} != {c.get('producer')}")
        if not files or not all(E.matches_any(f, c.get("paths", [])) for f in files):
            why.append("files outside the class paths")
        if any(E.matches_any(f, c.get("excluded", [])) for f in files):
            why.append("touches an excluded path")
        if cls_json.get("changed_lines", 0) > c.get("max_changed_lines", 0):
            why.append(f"{cls_json.get('changed_lines')} lines > {c.get('max_changed_lines')}")
        if c.get("kind") == "docs" and not cls_json.get("docs_only"):
            why.append("not documentation-only")
        if c.get("kind") == "dependency-bump":
            pkg = cls_json.get("package_json") or {}
            if pkg.get("deps_added") or pkg.get("deps_removed") or pkg.get("other_fields") or pkg.get("load_bearing_fields"):
                why.append("package.json changes beyond version bumps")
            bumps = pr.get("dependency_bumps") or []
            if not bumps:
                why.append("no dependency bumps recorded")
            allowed = set(c.get("bump_kinds", ["patch"]))
            bad = [b for b in bumps if bump_kind(b.get("from"), b.get("to")) not in allowed]
            if bad:
                why.append(f"bumps outside {sorted(allowed)} or not exact versions: {[b.get('name') for b in bad]}")
            if pr.get("other_dependency_changes"):
                why.append(f"dependency fields changed beyond version bumps: {pr['other_dependency_changes']}")
            why += lock_delta_problems(pr.get("lock_delta") or [], bumps)
            # GitHub's "verified" reflects the committer's signature, so the committer must be
            # GitHub or Dependabot as well as the author (finding 4 of the second review).
            commits = pr.get("commits") or []
            if not commits or not all(c2.get("author_login") == "dependabot[bot]" and c2.get("verified")
                                      and c2.get("committer_login") in DEPENDABOT_COMMITTERS for c2 in commits):
                why.append("not every commit is a signature-verified Dependabot commit committed by GitHub or Dependabot")
        if why:
            out["reasons"].append(f"{name}: " + "; ".join(why))
        else:
            out["candidates"].append(name)
    if len(out["candidates"]) == 1:
        out["class"] = out["candidates"][0]
    elif len(out["candidates"]) > 1:
        out["reasons"].append(f"ambiguous: matches {out['candidates']} (classes must not overlap)")
    return out


def cmd_classify(args) -> int:
    r = match_class(read(args.classify_json, {}), read(args.pr_json, {}), policy())
    return emit(r, 0 if r["class"] else 1)


# ---------------------------------------------------------------------------------------
# Revocation and the per-PR decision
# ---------------------------------------------------------------------------------------

RUNTIME_KEYS = ("claude_code_version", "claude_code_action_sha")


def revocation(c: dict, evidence: dict, pol: dict) -> list[str]:
    """Automatic revocation triggers (§9.6 rule 7). Requalification sets a new qualified_on."""
    since = c.get("qualified_on") or "9999-12-31"
    out = []
    # Only incidents about this class count: its qualification window and its auto-merged PRs.
    # An escape issue that names no PR is counted conservatively once it postdates qualification.
    class_prs = {int_or_none(x) for x in (c.get("window_prs") or [])}
    class_prs |= {int_or_none(x) for x in ((evidence.get("class_prs") or {}).get(c.get("_name", "")) or [])}
    class_prs.discard(None)
    for kind, label in (("escapes", "escape"), ("reverts", "revert")):
        hits = [x for x in evidence.get(kind, []) or [] if int_or_none(x.get("pr")) in class_prs
                or (kind == "escapes" and not int_or_none(x.get("pr")) and str(x.get("date", ""))[:10] >= since)]
        if hits:
            out.append(f"{len(hits)} {label}(s) of this class (qualified on {since})")
    pinned = c.get("runtime") or {}
    current = pol.get("runtime") or {}
    for key in RUNTIME_KEYS:
        if pinned.get(key) != current.get(key):
            out.append(f"harness changed: {key} {pinned.get(key)} -> {current.get(key)}")
    for role in ("triage", "implementer", "verifier"):
        if (pinned.get("models") or {}).get(role) != (pol.get("models") or {}).get(role):
            out.append(f"model changed: {role} {(pinned.get('models') or {}).get(role)} -> {(pol.get('models') or {}).get(role)}")
    # What actually ran for this PR (from the ledger), not only what policy says.
    led = evidence.get("ledger") or {}
    if c.get("producer") == "factory" and led:
        pm = pinned.get("models") or {}
        for field, want, what in (("model", pm.get("implementer"), "implementer model"),
                                  ("verifier_model", pm.get("verifier"), "verifier model"),
                                  ("claude_code_version", pinned.get("claude_code_version"), "Claude Code version"),
                                  ("harness_action_sha", pinned.get("claude_code_action_sha"), "claude-code-action SHA")):
            if not led.get(field):   # unknown is not the qualified runtime: fail closed
                out.append(f"the ledger record does not show the {what} that ran")
            elif led.get(field) != want:
                out.append(f"the run used {what} {led.get(field)}, not the qualified {want}")
    rt = evidence.get("redteam")
    if rt is None:
        out.append("red-team status unknown (fail closed)")
    elif rt.get("failed"):
        out.append(f"red-team failures: {[f.get('id') for f in rt['failed']][:5]}")
    return out


def int_or_none(x):
    try:
        return int(str(x).strip().lstrip("#")) or None
    except (TypeError, ValueError):
        return None


def sampled(pr_number: int, every: int, salt: str = "") -> bool:
    """Deterministic 1-in-N choice of PRs for post-merge review. Reproducible and auditable, not
    secret: the salt (the repository id) is public, so the sample is predictable."""
    if every <= 1:
        return True
    h = hashlib.sha256(f"{salt}:{pr_number}".encode()).digest()
    return int.from_bytes(h[:4], "big") % every == 0


def factory_evidence_problems(evidence: dict, sha: str, default_branch: str) -> list[str]:
    out = []
    led = evidence.get("ledger") or {}
    if not led:
        return ["no ledger record for this pull request"]
    if led.get("head_sha") != sha:
        out.append("the ledger record is for a different commit (commits were pushed after verification)")
    if led.get("verdict") != "verified":
        out.append(f"verifier verdict is {led.get('verdict')!r}, not 'verified'")
    gl = str(led.get("gates_line") or "")
    if not re.match(r"^FACTORY_GATES: level=[a-z]+ status=GREEN exit=0 ", gl):
        out.append("gates were not GREEN (exit 0) in the trusted gates job")
    if not re.match(r"^PROOF: status=(PROVEN|NOT_REQUIRED)\b", str(led.get("proof_line") or "")):
        out.append("failing-test proof missing or not proven")
    run = evidence.get("run") or {}
    if not str(run.get("path", "")).endswith(".github/workflows/factory-implement.yml") or run.get("head_branch") != default_branch:
        out.append("the ledger record was not produced by the default branch's factory-implement workflow")
    if (led.get("monitor") or {}).get("highest") == "high":
        out.append("trajectory monitor raised a high-severity flag")
    return out


def dependabot_evidence_problems(evidence: dict, sha: str) -> list[str]:
    g = evidence.get("gates_run") or {}
    if not g:
        return ["no factory-gates run found for this commit"]
    out = []
    if not str(g.get("path", "")).endswith(".github/workflows/factory-gates.yml") or g.get("head_sha") != sha:
        out.append("factory-gates run does not match this commit")
    if g.get("status_job") != "success":
        out.append(f"factory-gates verdict job concluded {g.get('status_job')!r}")
    r = evidence.get("review_run") or {}
    if not str(r.get("path", "")).endswith(".github/workflows/factory-security.yml") or r.get("head_sha") != sha:
        out.append("no factory-security run (dependency review) for this commit")
    elif r.get("dependency_review_job") != "success":
        out.append(f"dependency review concluded {r.get('dependency_review_job')!r} (vulnerability or licence)")
    return out


def decide(cls_json: dict, pr: dict, evidence: dict, pol: dict) -> dict:
    cfg = am(pol)
    reasons = []
    m = match_class(cls_json, pr, pol)
    name = m["class"]
    c = (cfg.get("classes") or {}).get(name or "", {})
    sha = evidence.get("classified_sha")
    default_branch = evidence.get("default_branch") or ""
    if not cfg.get("enabled"):
        reasons.append("auto-merge is disabled (policy.auto_merge.enabled is false)")
    if pol.get("charter_status") != "ready":
        reasons.append("charter not ready")
    if not name:
        reasons.append("no auto-merge class matches: " + "; ".join(m["reasons"]))
    elif c.get("status") != "qualified":
        reasons.append(f"class {name} is {c.get('status')!r}, not qualified")
    else:
        reasons += [f"class {name}: {e}" for e in validate_class(name, c)]
        reasons += revocation({**c, "_name": name}, evidence, pol)
        if c.get("producer") == "factory":
            reasons += factory_evidence_problems(evidence, sha, default_branch)
        else:
            reasons += dependabot_evidence_problems(evidence, sha)
    if not sha or sha != pr.get("headRefOid"):
        reasons.append("the classified commit is not the pull request head")
    if not default_branch or pr.get("baseRefName") != default_branch:
        reasons.append(f"base branch {pr.get('baseRefName')!r} is not the default branch")
    if evidence.get("freeze"):
        reasons.append("release freeze in force")
    if pr.get("isCrossRepository"):
        reasons.append("pull request from a fork")
    if (pr.get("mergeable") or "").upper() not in ("MERGEABLE", ""):
        reasons.append(f"not mergeable ({pr.get('mergeable')})")
    labels = {(l.get("name") if isinstance(l, dict) else l) for l in pr.get("labels", [])}
    if labels & BLOCKING_LABELS:
        reasons.append(f"labelled for human handling: {sorted(labels & BLOCKING_LABELS)}")
    open_reviews = int(evidence.get("open_sampled_reviews") or 0)
    if name and open_reviews >= int(c.get("max_open_sampled_reviews", 3)):
        reasons.append(f"{open_reviews} sampled post-merge reviews are still open (attention budget)")
    every = int(cfg.get("qualification", {}).get("sample_every", 5))
    return {"pr": pr.get("number"), "class": name, "merge": not reasons, "reasons": reasons,
            "post_merge_review": bool(name) and sampled(int(pr.get("number") or 0), every, str(evidence.get("salt", ""))),
            "head_sha": sha, "sample_every": every}


def cmd_decide(args) -> int:
    try:
        d = decide(read(args.classify_json, {}) or {}, read(args.pr_json, {}) or {}, read(args.evidence_json, {}) or {}, policy())
    except Exception as exc:  # one malformed input means human review, never a crash or a merge
        d = {"merge": False, "reasons": [f"evaluation error: {exc}"], "pr": None, "class": None,
             "post_merge_review": False, "head_sha": None}
    return emit(d, 0 if d["merge"] else 1)


# ---------------------------------------------------------------------------------------
# Qualification report (the human decides)
# ---------------------------------------------------------------------------------------


def qualify(name: str, history: dict, eval_rec: dict | None, ledger: list[dict], pol: dict, today: dt.date) -> dict:
    cfg = am(pol)
    q = cfg.get("qualification") or {}
    c = (cfg.get("classes") or {}).get(name)
    if c is None:
        return {"class": name, "qualified": False, "reasons": [f"no class {name!r} in policy"]}
    window, audit_days = int(q.get("window_prs", 50)), int(q.get("audit_days", 14))
    prs = [p for p in history.get("prs", []) if p.get("class") == name]
    class_ids = {p["number"] for p in prs}
    incidents = [x for x in history.get("escapes", []) + history.get("reverts", []) if x.get("pr") in class_ids]
    # A new window starts after the latest escape or revert of any PR in the class.
    reset = max((str(x.get("date", ""))[:10] for x in incidents), default="")
    decided = sorted((p for p in prs if (p.get("mergedAt") or p.get("closedAt") or "")[:10] > reset),
                     key=lambda p: p.get("mergedAt") or p.get("closedAt") or "")
    cutoff = (today - dt.timedelta(days=audit_days)).isoformat()
    audited = [p for p in decided if (p.get("mergedAt") or p.get("closedAt") or "")[:10] <= cutoff]
    # consecutive merged run at the end of the audited history; a PR closed unmerged breaks it
    tail = []
    for p in reversed(audited):
        if not p.get("mergedAt"):
            break
        tail.insert(0, p)
    tail = tail[-window:]
    reasons, facts = [], {"merged_in_class": sum(1 for p in prs if p.get("mergedAt")),
                          "window_reset_after": reset or None, "audited_consecutive": len(tail)}
    if incidents:
        facts["incidents"] = len(incidents)
    if len(tail) < window:
        reasons.append(f"{len(tail)} consecutive audited merged PRs (merged at least {audit_days} days ago, after the "
                       f"latest escape or revert); {window} required")
    unreviewed = [p["number"] for p in tail if not p.get("human_approved")]
    if unreviewed:
        reasons.append(f"PRs merged without a human approval: {unreviewed[:10]}")
    merged_all = [p for p in decided if p.get("mergedAt")]
    acceptance = len(merged_all) / len(decided) if decided else 0.0
    facts["human_acceptance_rate"] = round(acceptance, 4)
    if c.get("producer") == "factory":
        if not eval_rec:
            reasons.append("no eval results for the class (evals.py run on the class's tasks)")
        else:
            m = eval_rec.get("metrics") or {}
            k = m.get("k") or 5
            ph = m.get(f"pass_hat_{k}", 0.0)
            facts["eval"] = {"pass_hat_k": ph, "k": k, "model": eval_rec.get("model"), "harness": eval_rec.get("harness"),
                             "task_set_version": eval_rec.get("task_set_version"), "date": eval_rec.get("date")}
            if ph < acceptance:
                reasons.append(f"pass^{k} {ph} below the human acceptance rate {round(acceptance, 4)}")
            if eval_rec.get("model") != pol.get("models", {}).get("implementer"):
                reasons.append(f"eval model {eval_rec.get('model')} is not the current implementer model")
            if (pol.get("runtime") or {}).get("claude_code_version", "") not in str(eval_rec.get("harness") or ""):
                reasons.append("eval harness does not name the current Claude Code version")
            if m.get("canary_cheats"):
                reasons.append(f"{m['canary_cheats']} canary cheat(s) in the eval run")
    window_ids = {str(p["number"]) for p in tail}
    costs = [float((r.get("agent") or {}).get("total_cost_usd_estimate") or 0) + float((r.get("verify_agent") or {}).get("total_cost_usd_estimate") or 0)
             for r in ledger if str(r.get("pr_number")) in window_ids]
    if costs:
        mean = sum(costs) / len(costs)
        facts["mean_cost_usd"] = round(mean, 4)
        if mean > float(c.get("max_mean_cost_usd", 1e9)):
            reasons.append(f"mean cost ${mean:.2f} above the class budget ${c['max_mean_cost_usd']}")
    if c.get("producer") == "factory" and tail:
        start = (tail[0].get("mergedAt") or "")[:10]
        runs = [r for r in ledger if r.get("role") == "implement" or r.get("outcome") in
                ("published", "red", "misconfigured", "escalated", "no-change", "policy-blocked", "agent-failed")]
        runs = [r for r in runs if str(r.get("recorded_at", ""))[:10] >= start]
        if runs:   # all implement runs in the window period count, not only those that made a PR
            ok = sum(1 for r in runs if r.get("outcome") == "published")
            facts["publish_success_rate"] = round(ok / len(runs), 4)
            if ok / len(runs) < float(c.get("min_publish_success", 0.9)):
                reasons.append(f"publish success {ok}/{len(runs)} below {c.get('min_publish_success', 0.9)}")
    return {"class": name, "qualified": not reasons, "reasons": reasons, "facts": facts,
            "window": [p["number"] for p in tail],
            "next_step": ("A human may now record the decision: set status 'qualified', qualified_on, "
                          "decision_ref, window_prs and runtime in policy.json in a reviewed PR, and add the "
                          "evidence to DECISIONS.md. Then set auto_merge.enabled true.") if not reasons else
                         "Not qualified. Keep merging this class by hand and re-run when the window fills."}


def cmd_qualify(args) -> int:
    pol = policy()
    ledger = E.load_ledger(Path(args.ledger_dir)) if args.ledger_dir and Path(args.ledger_dir).is_dir() else []
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()
    r = qualify(args.cls, read(args.history_json, {}), read(args.eval_json), ledger, pol, today)
    return emit(r, 0 if r["qualified"] else 1)


def validate_class(name: str, c: dict) -> list[str]:
    errs = []
    if c.get("status") == "qualified":
        if not (c.get("qualified_on") and c.get("decision_ref")):
            errs.append("qualified without qualified_on and decision_ref")
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in c.get("window_prs") or []):
            errs.append("window_prs must be a list of PR numbers (integers)")
        rt = c.get("runtime") or {}
        models = rt.get("models") or {}
        if not all(models.get(r) for r in ("triage", "implementer", "verifier")) or not all(rt.get(k) for k in RUNTIME_KEYS):
            errs.append("qualified without a full runtime pin (models.triage/implementer/verifier, "
                        "claude_code_version, claude_code_action_sha)")
    return errs


def validate(pol: dict) -> list[str]:
    errs = []
    cfg = am(pol)
    for name, c in (cfg.get("classes") or {}).items():
        if name.startswith("_"):
            continue
        for k in ("kind", "producer", "paths", "max_changed_lines", "status"):
            if k not in c:
                errs.append(f"{name}: missing {k}")
        if c.get("status") not in ("candidate", "qualified", "revoked"):
            errs.append(f"{name}: status must be candidate, qualified or revoked")
        if c.get("producer") not in ("factory", "dependabot"):
            errs.append(f"{name}: producer must be factory or dependabot")
        for p in c.get("paths", []):
            if E.matches_any(p.replace("**", "x").replace("*", "x"), pol.get("load_bearing", [])) and c.get("kind") != "dependency-bump":
                errs.append(f"{name}: path pattern {p} overlaps load-bearing paths")
        errs += [f"{name}: {e}" for e in validate_class(name, c)]
    return errs


def cmd_validate(args) -> int:
    errs = validate(policy())
    return emit({"ok": not errs, "errors": errs}, 0 if not errs else 1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="automerge.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("classify")
    s.add_argument("--classify-json", required=True)
    s.add_argument("--pr-json", required=True)
    s.set_defaults(fn=cmd_classify)
    s = sub.add_parser("decide")
    s.add_argument("--classify-json", required=True)
    s.add_argument("--pr-json", required=True)
    s.add_argument("--evidence-json", required=True)
    s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("qualify")
    s.add_argument("--class", dest="cls", required=True)
    s.add_argument("--history-json", required=True)
    s.add_argument("--eval-json")
    s.add_argument("--ledger-dir")
    s.add_argument("--today")
    s.set_defaults(fn=cmd_qualify)
    s = sub.add_parser("validate")
    s.set_defaults(fn=cmd_validate)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
