#!/usr/bin/env python3
"""factory.py - the software factory's deterministic policy engine.

Standard library only. Every decision that must not be talked out of its verdict lives
here: tier classification, STOP_IF evaluation, diff policy, prompt rendering with
untrusted-input handling, the PreToolUse guard hook, configuration hashes, long-lived
key scanning, the run ledger and billing reconciliation.

Run with `python3 -I .factory/scripts/factory.py <command> ...`. Every command prints
JSON on stdout (except the hook, which follows the hook protocol) so workflows can
parse results without trusting an agent's paraphrase.

Exit codes: 0 = ok / allowed, 1 = policy says no, 2 = misconfigured or blocked hook.

Specification: Software Factory Build Specification v0.1, sections 1.5 (risk tiers),
4.2 (run failure signals), 4.4 (configuration trust), 5 (verification), 7 (evidence),
8 (ledger). Patterns adapted from Addy Osmani's MIT-licensed `factory` (see NOTICE.md).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

POLICY_REL = ".factory/policy.json"
HASHES_REL = ".factory/config-hashes.txt"

# ---------------------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------------------


def repo_root(start: str | None = None) -> Path:
    """Return the git top-level directory (or the start directory if not a repo)."""
    start = start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        out = subprocess.run(
            ["git", "-C", start, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(start).resolve()


def load_policy(root: Path) -> dict:
    """Load policy.json. FACTORY_POLICY overrides the path so that post-agent workflow steps
    use a copy taken before the agent ran (spec §4.4: configuration from the trusted base)."""
    override = os.environ.get("FACTORY_POLICY")
    path = Path(override) if override else root / POLICY_REL
    if not path.is_file():
        raise SystemExit(emit_error(f"policy file missing: {POLICY_REL}", code=2))
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def emit(obj: dict, code: int = 0) -> int:
    print(json.dumps(obj, indent=2, sort_keys=True))
    return code


def emit_error(msg: str, code: int = 2) -> int:
    print(json.dumps({"status": "MISCONFIGURED", "error": msg}), file=sys.stdout)
    return code


_GLOB_CACHE: dict[str, re.Pattern] = {}


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a root-anchored glob with `**` support into a regex.

    `**/` matches zero or more directories, `**` matches anything, `*` matches within one
    path segment, `?` matches one character. Patterns are anchored at the repository root.
    """
    if pattern in _GLOB_CACHE:
        return _GLOB_CACHE[pattern]
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    rx = re.compile("^" + "".join(out) + "$")
    _GLOB_CACHE[pattern] = rx
    return rx


def norm_path(path: str) -> str:
    """Remove leading './' segments only (never strip the dot of '.github')."""
    while path.startswith("./"):
        path = path[2:]
    return path


def matches_any(path: str, patterns: list[str]) -> bool:
    path = norm_path(path)
    return any(glob_to_regex(p).match(path) for p in patterns)


def git(root: Path, *args: str, check: bool = True) -> str:
    # surrogateescape: a file name that is not valid UTF-8 must not crash the policy engine
    res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                         encoding="utf-8", errors="surrogateescape")
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


def exists_in_ref(root: Path, ref: str, path: str) -> bool:
    res = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{ref}:{path}"],
                         capture_output=True, text=True)
    return res.returncode == 0


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------
# Diff collection and tier classification (spec §1.5; deterministic first, cannot be
# lowered by an LLM)
# ---------------------------------------------------------------------------------------


# Options that make the diff describe content, whatever the repository's attributes or the
# user's configuration say: no rename pairing (both sides of a move are classified), every
# file counted as text (a `-diff` attribute cannot hide a rewrite), NUL-separated output (no
# C-quoting of unusual file names), no external diff or textconv drivers.
_DIFF_OPTS = ("--no-renames", "--text", "--no-ext-diff", "--no-textconv", "-z")
# Attributes are read from the empty tree, so the repository's .gitattributes (for example
# `*.ts -diff`, which makes --numstat report "-" even with --text) cannot shape the diff.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_GIT_DIFF = ("-c", "core.quotePath=false", f"--attr-source={EMPTY_TREE}", "diff")


def collect_diff(root: Path, base: str) -> list[dict]:
    """Return changed files between `base` and the working tree, including untracked."""
    files: dict[str, dict] = {}
    numstat = git(root, *_GIT_DIFF, "--numstat", *_DIFF_OPTS, base)
    for rec in numstat.split("\0"):
        parts = rec.split("\t", 2)
        if len(parts) < 3 or not parts[2]:
            continue
        added, deleted, path = parts
        binary = added == "-"
        lines = 1 if binary else int(added) + int(deleted)
        files[path] = {"path": path, "lines": lines, "binary": binary}
    status = git(root, *_GIT_DIFF, "--name-status", *_DIFF_OPTS, base).split("\0")
    for code, path in zip(status[0::2], status[1::2]):
        if not code or not path:
            continue
        files.setdefault(path, {"path": path, "lines": 0, "binary": False})["status"] = code[0]
    untracked = git(root, "ls-files", "-z", "--others", "--exclude-standard")
    for path in untracked.split("\0"):
        if not path or path in files:
            continue
        try:
            with (root / path).open("rb") as fh:
                data = fh.read()
            lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        except OSError:
            lines = 1
        files[path] = {"path": path, "lines": lines, "binary": False, "status": "A"}
    for f in files.values():
        f.setdefault("status", "M")
    return sorted(files.values(), key=lambda f: f["path"])


def package_json_changes(root: Path, base: str, policy: dict) -> dict:
    """Compare package.json at base and in the working tree."""
    result = {"changed": False, "load_bearing_fields": [], "deps_added": [],
              "deps_removed": [], "deps_bumped": [], "other_fields": []}
    path = root / "package.json"
    if not path.is_file() or not exists_in_ref(root, base, "package.json"):
        return result
    try:
        old = json.loads(git(root, "show", f"{base}:package.json"))
        new = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, RuntimeError):
        result["changed"] = True
        result["load_bearing_fields"] = ["<unparseable>"]
        return result
    dep_sections = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
    for key in sorted(set(old) | set(new)):
        if old.get(key) == new.get(key):
            continue
        result["changed"] = True
        if key in policy.get("load_bearing_package_json_fields", []):
            result["load_bearing_fields"].append(key)
        elif key in dep_sections:
            o, n = old.get(key) or {}, new.get(key) or {}
            result["deps_added"] += sorted(set(n) - set(o))
            result["deps_removed"] += sorted(set(o) - set(n))
            result["deps_bumped"] += sorted(k for k in set(o) & set(n) if o[k] != n[k])
        elif key != "version":
            result["other_fields"].append(key)
    return result


def classify_files(files: list[dict], policy: dict, pkg: dict | None = None,
                   existing_tests: set[str] | None = None) -> dict:
    """Assign a risk tier (0-3) to a change set. Pure function; unit-tested."""
    pkg = pkg or {}
    existing_tests = existing_tests or set()
    limits = policy["limits"]
    considered = [f for f in files if not matches_any(f["path"], policy.get("ignore_in_diff", []))]
    reasons: list[str] = []
    violations: list[str] = []
    tier = 1

    paths = [f["path"] for f in considered]
    total_lines = sum(f.get("lines", 0) for f in considered)
    lockfiles = policy.get("lockfiles", [])
    manifests = policy.get("dependency_manifests", [])

    lb = [p for p in paths if matches_any(p, policy["load_bearing"])]
    if lb:
        tier = 3
        reasons.append("load-bearing paths: " + ", ".join(lb))
        violations.append("LOAD_BEARING_TOUCHED")
    if pkg.get("load_bearing_fields"):
        tier = 3
        reasons.append("package.json load-bearing fields changed: " + ", ".join(pkg["load_bearing_fields"]))
        violations.append("LOAD_BEARING_TOUCHED")

    # Any change to a path that held a test at base counts: modification, deletion, a move
    # (seen as a deletion because renames are not paired) or a type change such as a symlink.
    modified_tests = [f["path"] for f in considered
                      if f["path"] in existing_tests or f.get("renamed_from") in existing_tests]
    if modified_tests:
        tier = max(tier, 3)
        reasons.append("existing test files modified: " + ", ".join(modified_tests))
        violations.append("EXISTING_TEST_MODIFIED")

    tier2_hits = [p for p in paths if matches_any(p, policy.get("tier2_globs", []))]
    if tier2_hits:
        tier = max(tier, 2)
        reasons.append("tier-2 paths (UI or public API): " + ", ".join(tier2_hits))
    if pkg.get("deps_added") or pkg.get("deps_removed"):
        tier = max(tier, 2)
        reasons.append("dependency set changed: added=%s removed=%s"
                       % (pkg.get("deps_added"), pkg.get("deps_removed")))
    if len(paths) > limits["tier1_max_files"]:
        tier = max(tier, 2)
        reasons.append(f"{len(paths)} files exceeds tier-1 limit {limits['tier1_max_files']}")
    if total_lines > limits["tier1_max_changed_lines"]:
        tier = max(tier, 2)
        reasons.append(f"{total_lines} changed lines exceeds tier-1 limit {limits['tier1_max_changed_lines']}")

    docs_only = bool(paths) and all(matches_any(p, policy.get("docs_globs", [])) for p in paths)
    dep_bump_only = bool(paths) and bool(pkg.get("deps_bumped")) and not pkg.get("other_fields") \
        and all(p in manifests or p in lockfiles for p in paths)
    if tier == 1 and docs_only:
        tier = 0
        reasons.append("documentation only")
    elif tier == 1 and dep_bump_only:
        tier = 0
        reasons.append("dependency version bump only: " + ", ".join(pkg["deps_bumped"]))
    if tier == 1:
        reasons.append("bounded change within tier-1 limits")
    if not paths:
        violations.append("EMPTY_DIFF")

    tests_only = bool(paths) and all(matches_any(p, policy["test_globs"]) for p in paths)
    if tests_only and tier == 1:
        reasons.append("new tests only (failing-test proof not applicable)")
    gates = policy["gates"]
    gate_level = gates["docs_only"] if docs_only and tier == 0 else gates[f"tier{tier}"]
    return {
        "tier": tier,
        "gate_level": gate_level,
        "docs_only": docs_only,
        "tests_only": tests_only,
        "files": paths,
        "file_count": len(paths),
        "changed_lines": total_lines,
        "reasons": reasons,
        "violations": sorted(set(violations)),
    }


def existing_test_files(root: Path, base: str, policy: dict) -> set[str]:
    tracked = git(root, "ls-tree", "-r", "-z", "--name-only", base).split("\0")
    return {p for p in tracked if matches_any(p, policy["test_globs"])}


def cmd_classify(args) -> int:
    root = repo_root()
    policy = load_policy(root)
    files = collect_diff(root, args.base)
    pkg = package_json_changes(root, args.base, policy)
    result = classify_files(files, policy, pkg, existing_test_files(root, args.base, policy))
    result["package_json"] = pkg
    return emit(result)


def cmd_check_diff(args) -> int:
    """Post-run diff policy for unattended runs. Exit 1 when the diff must not be published."""
    root = repo_root()
    result = check_diff(root, args.base, load_policy(root), args.max_tier)
    return emit(result, 0 if result["ok"] else 1)


def check_diff(root: Path, base: str, policy: dict, requested_tier: int | None = None) -> dict:
    """The diff policy as a function (used by check-diff and the red-team suite)."""
    files = collect_diff(root, base)
    pkg = package_json_changes(root, base, policy)
    result = classify_files(files, policy, pkg, existing_test_files(root, base, policy))
    stop = list(result["violations"])
    max_tier = allowed_unattended_tier(policy, requested_tier)
    if result["tier"] > max_tier:
        stop.append("TIER_ABOVE_UNATTENDED")
    limits = policy["limits"]
    line_cap = limits["tier1_max_changed_lines"] if max_tier <= 1 else limits.get("tier2_max_changed_lines", 0)
    file_cap = limits["tier1_max_files"] if max_tier <= 1 else limits.get("tier2_max_files", 0)
    if result["changed_lines"] > line_cap or result["file_count"] > file_cap:
        stop.append("DIFF_TOO_LARGE")
    result["max_tier"] = max_tier
    secrets = []
    for f in files:
        fp = root / f["path"]
        if fp.is_file() and fp.stat().st_size < 2_000_000:
            secrets += scan_text_for_keys(f["path"], fp.read_text(encoding="utf-8", errors="ignore"))
    patch = git(root, *_GIT_DIFF, "--text", "--no-ext-diff", "--no-textconv", base, check=False)
    secrets += [dict(s, path=s["path"] + " (diff)") for s in scan_text_for_keys("<patch>", patch)]
    if secrets:
        stop.append("SECRET_IN_DIFF")
        result["secret_findings"] = secrets
    # Supply chain before anything is installed: a changed lockfile must satisfy the dependency
    # policy here, because the gates job runs `npm ci` from it (review finding 2026-11-04).
    if (root / "package-lock.json").is_file() and any(f["path"] in ("package-lock.json", "package.json") for f in files):
        lock = lockfile_check(root, policy)
        if not lock["ok"]:
            stop.append("LOCKFILE_POLICY")
            result["lockfile_violations"] = lock["violations"]
    result["stop_reasons"] = sorted(set(stop))
    result["ok"] = not result["stop_reasons"]
    result["package_json"] = pkg
    return result


def allowed_unattended_tier(policy: dict, requested: int | None = None) -> int:
    """The highest tier a run may publish. Tier 2 only when the policy allows it with an
    approved spec (the caller, preflight, has verified the spec); Tier 3 never."""
    base = int(policy["unattended_max_tier"])
    if requested is None:
        return base
    ceiling = 2 if policy.get("tier2_unattended_with_approved_spec") else base
    return max(base, min(int(requested), ceiling, 2))


def predict_tier(expected_files: list[str], policy: dict) -> dict:
    files = [{"path": p.strip(), "lines": 0, "status": "M"} for p in expected_files if p.strip()]
    result = classify_files(files, policy)
    result["violations"] = [v for v in result["violations"] if v != "EMPTY_DIFF"]
    return result


# ---------------------------------------------------------------------------------------
# Handoff parsing (untrusted input: only fields, never instructions)
# ---------------------------------------------------------------------------------------

HANDOFF_MARKER = "<!-- factory-handoff:v1 -->"
HANDOFF_FIELDS = {"disposition", "done_when", "files_expected", "load_bearing",
                  "gate_level", "confidence", "triaged_at", "predicted_tier", "spec"}


def parse_handoff_body(body: str) -> dict | None:
    if HANDOFF_MARKER not in body:
        return None
    fields: dict[str, str] = {}
    block = body.split(HANDOFF_MARKER, 1)[1]
    block = block.replace("```text", "").replace("```", "")
    for line in block.splitlines():
        m = re.match(r"^\s*([a-z_]+)\s*:\s*(.*?)\s*$", line)
        if m and m.group(1) in HANDOFF_FIELDS:
            if m.group(1) in fields:
                # A repeated field means a value smuggled a newline (e.g. model output in
                # files_expected). Refuse the whole handoff rather than guess which value wins.
                return None
            fields[m.group(1)] = sanitize_untrusted(m.group(2), 500)
    return fields or None


def latest_trusted_handoff(comments: list[dict], policy: dict) -> dict | None:
    """Pick the latest handoff comment written by a trusted author."""
    trusted_logins = set(policy.get("trusted_handoff_authors", []))
    trusted_assoc = set(policy.get("trusted_author_associations", []))
    best = None
    for c in comments:
        login = ((c.get("user") or {}).get("login") or (c.get("author") or {}).get("login") or "")
        assoc = c.get("author_association") or c.get("authorAssociation") or ""
        if login not in trusted_logins and assoc not in trusted_assoc:
            continue
        parsed = parse_handoff_body(c.get("body") or "")
        if parsed:
            ts = c.get("updated_at") or c.get("updatedAt") or c.get("created_at") or c.get("createdAt") or ""
            if best is None or ts >= best[0]:
                best = (ts, parsed)
    return best[1] if best else None


def cmd_predict_tier(args) -> int:
    root = repo_root()
    policy = load_policy(root)
    files = [f for f in (args.files or "").split(",") if f.strip()]
    if args.files_json:
        files = json.loads(Path(args.files_json).read_text(encoding="utf-8"))
    result = predict_tier(files, policy)
    result["unattended_ok"] = result["tier"] <= policy["unattended_max_tier"] and not result["violations"]
    return emit(result)


def cmd_parse_handoff(args) -> int:
    root = repo_root()
    policy = load_policy(root)
    comments = json.loads(Path(args.comments_json).read_text(encoding="utf-8"))
    if isinstance(comments, dict):
        comments = comments.get("comments", [])
    handoff = latest_trusted_handoff(comments, policy)
    return emit({"handoff": handoff})


# ---------------------------------------------------------------------------------------
# STOP_IF (spec §5.6, §9.5; charter section 7). Pure function; every condition is tested.
# ---------------------------------------------------------------------------------------


def evaluate_stop_if(state: dict, policy: dict) -> dict:
    limits = policy["limits"]
    reasons: list[str] = []
    detail: dict[str, str] = {}

    def stop(code: str, why: str) -> None:
        reasons.append(code)
        detail[code] = why

    if policy.get("charter_status") != "ready":
        stop("CHARTER_NOT_READY", f"charter_status is {policy.get('charter_status')!r}; a human must review and set it to 'ready'")
    if state.get("paused"):
        stop("FACTORY_PAUSED", "factory paused by FACTORY_PAUSED variable or .factory/PAUSED file")
    if state.get("freeze"):
        stop("RELEASE_FREEZE", "error budget exhausted: factory:freeze is set (SLO policy); only P0 fixes by humans")
    if state.get("awaiting_review_count", 0) >= limits["review_queue_max"]:
        stop("REVIEW_QUEUE_FULL", f"{state.get('awaiting_review_count')} items awaiting review; limit {limits['review_queue_max']}")
    if state.get("spend_today_usd", 0.0) >= limits["daily_budget_usd"]:
        stop("DAILY_BUDGET_EXHAUSTED", f"estimated spend today ${state.get('spend_today_usd'):.2f} >= ${limits['daily_budget_usd']}")
    base = state.get("base_gates_status")
    if base is not None and base != "GREEN":
        stop("BASE_NOT_GREEN", f"default branch gates are {base}; fix main before producing more work")

    item = state.get("item")
    if not item:
        stop("NO_ITEM", "no eligible queue item")
        return {"stop": True, "reasons": reasons, "detail": detail, "allowed_tier": policy["unattended_max_tier"]}

    labels = set(item.get("labels", []))
    if "factory:needs-human" in labels or "factory:human-only" in labels:
        stop("NEEDS_HUMAN_LABEL", "item is labeled for human handling")
    if "factory:in-progress" in labels:
        hours = item.get("in_progress_since_hours")
        if hours is None or hours < limits["stale_in_progress_hours"]:
            stop("CLAIMED_ELSEWHERE", "item is already in progress")
    handoff = item.get("handoff")
    if not handoff:
        stop("HANDOFF_MISSING", "no trusted factory-handoff:v1 comment; triage first")
    else:
        if not handoff.get("done_when") or handoff.get("confidence") == "low" \
                or handoff.get("disposition") not in (None, "ready-to-implement"):
            stop("AMBIGUOUS", "handoff lacks done_when, has low confidence, or is not ready-to-implement")
        expected = [p for p in (handoff.get("files_expected") or "").split(",") if p.strip()]
        if not expected:
            stop("AMBIGUOUS", "handoff lists no expected files")
        else:
            pred = predict_tier(expected, policy)
            item["predicted_tier"] = pred["tier"]
            if "LOAD_BEARING_TOUCHED" in pred["violations"]:
                stop("LOAD_BEARING_EXPECTED", "expected files include load-bearing paths: " + "; ".join(pred["reasons"]))
            allowed = policy["unattended_max_tier"]
            if pred["tier"] == 2 and policy.get("tier2_unattended_with_approved_spec"):
                if item.get("spec_implementable"):
                    allowed = 2
                else:
                    stop("SPEC_NOT_APPROVED", "tier 2 needs a hand-off citing a spec whose spec, plan and tasks are approved")
            item["allowed_tier"] = allowed
            if pred["tier"] > allowed and "SPEC_NOT_APPROVED" not in reasons:
                stop("TIER_NOT_UNATTENDED", f"predicted tier {pred['tier']} > unattended max {allowed}")
        if str(handoff.get("load_bearing", "false")).lower() == "true":
            stop("LOAD_BEARING_EXPECTED", "handoff declares load_bearing: true")
    raised = [lab for lab in labels if lab.startswith("factory:tier-")]
    for lab in raised:
        try:
            t = int(lab.rsplit("-", 1)[1])
        except ValueError:
            continue
        if t > item.get("allowed_tier", policy["unattended_max_tier"]):
            stop("TIER_NOT_UNATTENDED", f"label {lab} raises the tier above the unattended maximum")
    runs = item.get("runs", [])
    red = sum(1 for r in runs if r.get("outcome") in ("red", "misconfigured"))
    if red >= limits["max_red_gate_runs_per_item"]:
        stop("RED_GATES_TWICE", f"{red} red gate runs on this item")
    if len(runs) >= limits["max_runs_per_item"]:
        stop("TOO_MANY_RUNS", f"{len(runs)} runs on this item; limit {limits['max_runs_per_item']}")
    if item.get("ci_fix_commits", 0) >= limits["max_ci_fix_commits"]:
        stop("TOO_MANY_CI_FIX", f"{item.get('ci_fix_commits')} ci-fix commits on this item")

    # de-duplicate while keeping order
    seen, ordered = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            ordered.append(r)
    return {"stop": bool(ordered), "reasons": ordered, "detail": detail,
            "predicted_tier": item.get("predicted_tier"),
            "allowed_tier": item.get("allowed_tier", policy["unattended_max_tier"])}


def cmd_stop_if(args) -> int:
    root = repo_root()
    policy = load_policy(root)
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    if (root / ".factory" / "PAUSED").exists():
        state["paused"] = True
    result = evaluate_stop_if(state, policy)
    return emit(result, 1 if result["stop"] else 0)


def cmd_select_item(args) -> int:
    """Choose the oldest eligible issue from `gh issue list --json` output."""
    root = repo_root()
    policy = load_policy(root)
    issues = json.loads(Path(args.issues_json).read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    stale_h = policy["limits"]["stale_in_progress_hours"]
    eligible = []
    for issue in issues:
        labels = {(lab.get("name") if isinstance(lab, dict) else lab) for lab in issue.get("labels", [])}
        if "factory:ready-to-implement" not in labels:
            continue
        if labels & {"factory:needs-human", "factory:human-only", "factory:blocked", "factory:freeze"}:
            continue
        if "factory:in-progress" in labels:
            # A claim with no activity for stale_in_progress_hours is abandoned (a run that died
            # after claiming). It becomes eligible again; STOP_IF re-checks staleness.
            updated = issue.get("updatedAt") or issue.get("updated_at") or ""
            try:
                age_h = (now - dt.datetime.fromisoformat(updated.replace("Z", "+00:00"))).total_seconds() / 3600
            except ValueError:
                continue
            if age_h < stale_h:
                continue
        eligible.append(issue)
    eligible.sort(key=lambda i: (i.get("createdAt") or i.get("created_at") or "", i.get("number", 0)))
    return emit({"number": eligible[0]["number"] if eligible else None, "eligible": len(eligible)})


# ---------------------------------------------------------------------------------------
# Untrusted input handling and prompt rendering (spec §4.2, §6: issue text is data)
# ---------------------------------------------------------------------------------------

_HIDDEN_RANGES = [
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0x2066, 0x2069),
    (0xFEFF, 0xFEFF), (0xE0000, 0xE007F), (0x00AD, 0x00AD), (0x180E, 0x180E),
]


def _is_hidden(ch: str) -> bool:
    cp = ord(ch)
    if any(lo <= cp <= hi for lo, hi in _HIDDEN_RANGES):
        return True
    cat = unicodedata.category(ch)
    return cat in ("Cc", "Cf", "Co", "Cs") and ch not in ("\n", "\t")


def sanitize_untrusted(text: str, max_chars: int) -> str:
    """Strip HTML comments, hidden/bidi/tag characters and control codes; truncate."""
    text = text or ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if not _is_hidden(ch))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"</?\s*untrusted[^>]*>", "[removed-tag]", text, flags=re.I)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated by factory]"
    return text.strip()


def render_template(template: str, values: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"template placeholder {{{{{key}}}}} has no value")
        return values[key]
    return re.sub(r"\{\{([A-Z_]+)\}\}", repl, template)


def cmd_render_prompt(args) -> int:
    root = repo_root()
    policy = load_policy(root)
    limit = policy["limits"]["prompt_max_untrusted_chars"]
    issue = json.loads(Path(args.issue_json).read_text(encoding="utf-8")) if args.issue_json else {}
    handoff = json.loads(Path(args.handoff_json).read_text(encoding="utf-8")) if args.handoff_json else {}
    if isinstance(handoff, dict) and "handoff" in handoff:
        handoff = handoff["handoff"] or {}
    extra = json.loads(Path(args.extra_json).read_text(encoding="utf-8")) if args.extra_json else {}
    title = sanitize_untrusted(issue.get("title", ""), 300)
    body = sanitize_untrusted(issue.get("body", ""), limit)
    untrusted = (
        "<untrusted_issue>\n"
        f"title: {title}\n\n{body}\n"
        "</untrusted_issue>"
    )
    values = {
        "ISSUE_NUMBER": str(issue.get("number", "")),
        "UNTRUSTED_ISSUE": untrusted,
        "DONE_WHEN": sanitize_untrusted(str(handoff.get("done_when", "")), 500),
        "FILES_EXPECTED": sanitize_untrusted(str(handoff.get("files_expected", "")), 500),
        "GATE_LEVEL": str(extra.get("gate_level") or handoff.get("gate_level") or "full"),
        "BASE_SHA": str(extra.get("base_sha", "")),
        "RUN_ID": str(extra.get("run_id", "")),
        "DIFF_STAT": sanitize_untrusted(str(extra.get("diff_stat", "")), 4000),
        "GATES_LINE": sanitize_untrusted(str(extra.get("gates_line", "")), 500),
        "PROOF_LINE": sanitize_untrusted(str(extra.get("proof_line", "")), 500),
    }
    if values["GATE_LEVEL"] not in ("fast", "full", "ui", "deep"):
        return emit_error(f"invalid gate level {values['GATE_LEVEL']!r}")
    template = Path(args.template).read_text(encoding="utf-8")
    try:
        prompt = render_template(template, values)
    except KeyError as exc:
        return emit_error(str(exc))
    Path(args.out).write_text(prompt, encoding="utf-8")
    return emit({"status": "OK", "out": args.out, "chars": len(prompt)})


# ---------------------------------------------------------------------------------------
# PreToolUse guard hook (spec §4.3, §4.4). Exit 2 blocks; any internal error fails closed.
# ---------------------------------------------------------------------------------------

# A command word may be preceded by a separator, a quote (bash -c '...'), a path (/usr/bin/git)
# or a backslash (\git). `git <global options> verb` is matched too (git -C . push).
_PFX = r"(^|[\s;&|(`'\"/\\])"
_GIT = _PFX + r"git(\s+(-[cC]\s+\S+|--?[\w-]+(=\S+)?))*\s+"

_BASH_DENY = [
    (_GIT + r"push\b", "git push: the workflow publishes; agents never push"),
    (_GIT + r"merge\b", "git merge: merge decisions belong to a human"),
    (_GIT + r"(remote|config)\b", "git remote/config changes are not allowed"),
    (_GIT + r"(fetch|pull|clone|submodule|ls-remote)\b", "network git operations are not allowed in runs"),
    (_PFX + r"gh(\s|$)", "the GitHub CLI is not available to agents"),
    (r"mergePullRequest|/pulls/\d+/merge", "GitHub merge API"),
    (_PFX + r"(curl|wget|nc|ncat|telnet|ssh|scp|rsync|ftp)\b", "network tools are not allowed"),
    (_PFX + r"(env|printenv|set|export)\s*($|[;&|)])", "dumping the environment is not allowed"),
    (r"/proc/[^\s]*/environ", "reading process environments is not allowed"),
    (_PFX + r"(npm|pnpm|yarn)\s+(publish|login|adduser|token)\b", "package registry credentials"),
    (_PFX + r"npm\s+(i|install|add)\s+[^-\s]", "adding dependencies is tier 2; not allowed unattended"),
    (_PFX + r"(pnpm|yarn)\s+add\b", "adding dependencies is tier 2; not allowed unattended"),
    (_PFX + r"(sudo|su|chmod\s+[0-7]*s|chown)\b", "privilege changes are not allowed"),
    (r"--no-verify\b", "skipping hooks is not allowed"),
    (_PFX + r"(npx|npm\s+exec|pnpm\s+dlx|bunx)\s", "downloading and executing packages is not allowed"),
]

_WRITE_VERBS = r"(>|>>|\btee\b|\bsed\s+-i|\bmv\b|\bcp\b|\brm\b|\btruncate\b|\bln\b|\binstall\b|\bpatch\b|\bperl\s+-[a-z]*i)"


_SENSITIVE_READ = [
    r"(^|/)\.git/config$", r"(^|/)\.git-credentials$", r"(^|/)\.git/credentials",
    r"^/proc/", r"(^|/)\.ssh/", r"(^|/)\.aws/", r"(^|/)\.config/gh/", r"(^|/)\.netrc$",
    r"(^|/)\.npmrc$", r"(^|/)\.docker/config\.json$", r"(^|/)\.env($|\.)",
    r"claude-execution-output\.json$", r"(^|/)\.claude\.json$", r"(^|/)\.claude/\.credentials",
]

# The same credential locations as they appear inside shell commands.
_SENSITIVE_SHELL = [
    r"\.git/config\b", r"\.git-credentials\b", r"\.git/credentials", r"/proc/",
    r"(^|[\s'\"=/])~?/?\.ssh/", r"(^|[\s'\"=/])~?/?\.aws/", r"\.config/gh\b", r"\.netrc\b",
    r"\.npmrc\b", r"\.docker/config\.json", r"(^|[\s'\"=/])\.env(\b|\.)",
    r"claude-execution-output\.json", r"\.claude\.json\b", r"\.claude/\.credentials",
    r"\bgit\s+config\s+(--get|-l|--list)", r"\bgit\s+credential\b",
]

# Commands blocked even in interactive sessions: merging and force-pushing are human acts
# performed outside the agent (charter: NEVER_AUTOMATE merge decisions).
_BASH_DENY_ALWAYS = [
    (_GIT + r"merge\b", "git merge: merge decisions belong to a human"),
    (_PFX + r"gh\s+pr\s+merge\b", "gh pr merge: merge decisions belong to a human"),
    (r"mergePullRequest|/pulls/\d+/merge", "GitHub merge API"),
    (_GIT + r"push\s+.*(--force|-f\b|\s\+)", "force push"),
]


def guard_decision(payload: dict, root: Path, policy: dict,
                   unattended: bool | None = None) -> tuple[bool, str]:
    """Return (allowed, reason). Pure apart from git lookups for existing tests.

    Unattended factory runs (FACTORY_RUN=1) get the full rule set. Interactive sessions,
    where a human approves each action, are only blocked from merging and force-pushing.
    """
    if unattended is None:
        unattended = os.environ.get("FACTORY_RUN") == "1"
    tool = payload.get("tool_name") or ""
    tin = payload.get("tool_input") or {}
    if not unattended:
        if tool == "Bash":
            cmd = tin.get("command") or ""
            for rx, why in _BASH_DENY_ALWAYS:
                if re.search(rx, cmd):
                    return False, why
        return True, "ok"
    if tool in ("Read", "Grep", "Glob", "NotebookRead"):
        target = tin.get("file_path") or tin.get("path") or tin.get("notebook_path") or ""
        if target and any(re.search(rx, target) for rx in _SENSITIVE_READ):
            return False, f"reading credential or runtime files is not allowed: {target}"
        return True, "ok"
    if tool in ("WebFetch", "WebSearch"):
        return False, f"{tool} is disabled in factory runs (no network before trust; spec §4.4 rule 5)"
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        raw = tin.get("file_path") or tin.get("notebook_path") or ""
        if not raw:
            return False, "edit without a file path"
        p = Path(raw)
        abs_path = (p if p.is_absolute() else (root / p)).resolve()
        try:
            rel = abs_path.relative_to(root.resolve()).as_posix()
        except ValueError:
            return False, f"edit outside the repository: {raw}"
        if rel.startswith(".git/"):
            return False, "edits under .git are not allowed"
        if matches_any(rel, policy["load_bearing"]):
            return False, f"{rel} is load-bearing (charter); unattended runs never edit it"
        if matches_any(rel, policy["test_globs"]):
            res = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", rel],
                                 capture_output=True, text=True)
            if res.returncode == 0:
                return False, f"{rel} is an existing test file; add a new test file instead (charter: TESTS_ARE_LOAD_BEARING)"
        return True, "ok"
    if tool == "Bash":
        cmd = tin.get("command") or ""
        for rx, why in _BASH_DENY_ALWAYS + _BASH_DENY:
            if re.search(rx, cmd):
                return False, why
        for rx in _SENSITIVE_SHELL:
            if re.search(rx, cmd):
                return False, "shell access to credential or runtime files is not allowed"
        if re.search(_WRITE_VERBS, cmd):
            for target in re.findall(r"[\w./*@-]+", cmd):
                t = norm_path(target)
                if t and matches_any(t, policy["load_bearing"]):
                    return False, f"shell write touching load-bearing path {t}"
        return True, "ok"
    return True, "ok"


def cmd_hook_guard(args) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        root = repo_root(payload.get("cwd"))
        policy = load_policy(root)
        allowed, reason = guard_decision(payload, root, policy)
    except Exception as exc:  # fail closed: the hook must never be the thing that lets a call through
        print(f"BLOCKED by factory guard (internal error, failing closed): {exc}", file=sys.stderr)
        return 2
    if allowed:
        return 0
    print(f"BLOCKED by factory guard: {reason}", file=sys.stderr)
    print("If the task cannot be done without this, stop and return status 'escalate' with the reason.",
          file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------------------
# Dependency and lockfile gate (spec §6.5, Phase B deliverable 7)
# ---------------------------------------------------------------------------------------

_BAD_SPEC = re.compile(r"^(git\+|git:|github:|gitlab:|bitbucket:|https?:|file:|link:|\.{0,2}/)", re.I)


def lockfile_check(root: Path, policy: dict) -> dict:
    dep = policy.get("dependency_policy") or {}
    registries = tuple(dep.get("registries") or ["https://registry.npmjs.org/"])
    allowed_scripts = dep.get("install_scripts_allowed") or []
    violations: list[str] = []
    install_scripts: list[str] = []
    pkg_path, lock_path = root / "package.json", root / "package-lock.json"
    if not lock_path.is_file():
        return {"ok": False, "violations": ["package-lock.json missing"], "install_scripts": []}
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8")) if pkg_path.is_file() else {}
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "violations": [f"unparseable manifest: {exc}"], "install_scripts": []}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        for name, spec in (pkg.get(section) or {}).items():
            if isinstance(spec, str) and _BAD_SPEC.match(spec.strip()):
                violations.append(f"package.json {section}.{name}: non-registry specifier {spec!r}")
    if lock.get("lockfileVersion", 0) < 2:
        violations.append(f"lockfileVersion {lock.get('lockfileVersion')} < 2 (regenerate with npm >= 7)")
    if pkg and lock.get("name") not in (None, pkg.get("name")):
        violations.append("package-lock.json name does not match package.json")
    for path, entry in (lock.get("packages") or {}).items():
        if path == "":
            continue
        if entry.get("link") or not (path.startswith("node_modules/") or "/node_modules/" in path):
            # Linked, workspace or local packages run repository code under a package's name
            # (for example a fake node_modules/.bin/playwright). Never allowed in 0.x.
            violations.append(f"{path}: linked, workspace or local package (only registry packages are allowed)")
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        if entry.get("name") and entry["name"] != name:
            violations.append(f"{name}: npm alias for {entry['name']!r} (aliases are not allowed)")
        resolved = entry.get("resolved", "")
        if not resolved.startswith(registries):
            violations.append(f"{name}: resolved from {resolved or '(nothing)'} (not an allowed registry)")
        if not str(entry.get("integrity", "")).startswith(dep.get("min_integrity", "sha512") + "-"):
            violations.append(f"{name}: missing or weak integrity hash")
        if entry.get("hasInstallScript"):
            install_scripts.append(name)
            if not matches_any(name, allowed_scripts):
                violations.append(f"{name}: has an install script and is not in install_scripts_allowed")
    return {"ok": not violations, "violations": violations, "install_scripts": sorted(set(install_scripts)),
            "packages": sum(1 for p in (lock.get("packages") or {}) if p)}


def cmd_lockfile_check(args) -> int:
    root = Path(args.root).resolve() if args.root else repo_root()
    policy = load_policy(root) if (root / POLICY_REL).is_file() or os.environ.get("FACTORY_POLICY") else \
        load_policy(Path(__file__).resolve().parents[2])
    result = lockfile_check(root, policy)
    return emit(result, 0 if result["ok"] else 1)


# ---------------------------------------------------------------------------------------
# Settings rendering: absolute sandbox paths (spec §4.4)
# ---------------------------------------------------------------------------------------


def render_settings(settings: dict, root: Path, hooks_dir: str | None = None) -> dict:
    """Return a copy of Claude Code settings with project-relative sandbox paths made absolute.

    Relative `sandbox.filesystem` paths resolve against the project root in project settings
    but have been reported to resolve against the settings file's directory when the file is
    passed with --settings (anthropics/claude-code issue 46111). The workflow passes settings
    that way, so every relative entry is written as an absolute path under the workspace.
    """
    out = json.loads(json.dumps(settings))
    fs = ((out.get("sandbox") or {}).get("filesystem")) or {}
    for key in ("denyWrite", "denyRead", "allowWrite", "allowRead"):
        entries = fs.get(key)
        if not entries:
            continue
        resolved = []
        for e in entries:
            if e.startswith(("/", "~")):
                resolved.append(e)
            else:
                rel = e[2:] if e.startswith("./") else e
                resolved.append(str((root / rel).resolve()) if rel not in ("", ".") else str(root.resolve()))
        fs[key] = sorted(set(resolved), key=resolved.index)
    if hooks_dir:
        # Hooks run outside the sandbox; in factory runs they come from the trusted pre-agent
        # copy so nothing in the agent-writable working tree is ever executed by a hook.
        for entries in (out.get("hooks") or {}).values():
            for entry in entries:
                for h in entry.get("hooks", []):
                    cmd = h.get("command", "")
                    h["command"] = cmd.replace('"$CLAUDE_PROJECT_DIR/.factory/hooks/', f'"{hooks_dir.rstrip("/")}/')
    return out


def cmd_render_settings(args) -> int:
    root = Path(args.root).resolve() if args.root else repo_root()
    settings = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rendered = render_settings(settings, root, args.hooks_dir)
    Path(args.out).write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    deny = (((rendered.get("sandbox") or {}).get("filesystem")) or {}).get("denyWrite", [])
    return emit({"status": "OK", "out": args.out, "denyWrite": deny})


# ---------------------------------------------------------------------------------------
# Configuration hashes (spec §4.4 rule 4: bind approvals to content, not names)
# ---------------------------------------------------------------------------------------


def config_files(root: Path, policy: dict) -> list[str]:
    tracked = git(root, "ls-files", "-z").split("\0")
    return sorted(p for p in tracked if p
                  if matches_any(p, policy["config_hash_globs"]) and p != HASHES_REL)


def compute_hashes(root: Path, policy: dict) -> dict[str, str]:
    out = {}
    for rel in config_files(root, policy):
        data = (root / rel).read_bytes() if (root / rel).is_file() else b""
        out[rel] = hashlib.sha256(data).hexdigest()
    return out


def cmd_config_hashes(args) -> int:
    root = repo_root()
    policy = load_policy(root)
    current = compute_hashes(root, policy)
    hfile = root / HASHES_REL
    if args.update:
        lines = [f"{h}  {p}" for p, h in sorted(current.items())]
        hfile.write_text("# sha256 of factory configuration; regenerate only in a human-reviewed PR\n"
                         + "\n".join(lines) + "\n", encoding="utf-8")
        return emit({"status": "UPDATED", "files": len(current)})
    if not hfile.is_file():
        return emit({"status": "MISCONFIGURED", "error": f"{HASHES_REL} missing"}, 2)
    recorded = {}
    for line in hfile.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        h, p = line.split(None, 1)
        recorded[p.strip()] = h
    changed = sorted(p for p in current if recorded.get(p) != current[p])
    missing = sorted(p for p in recorded if p not in current)
    ok = not changed and not missing
    return emit({"status": "OK" if ok else "MISMATCH", "changed": changed, "missing": missing}, 0 if ok else 1)


# ---------------------------------------------------------------------------------------
# Long-lived key scan (Phase A exit criterion: zero long-lived keys in agent context)
# ---------------------------------------------------------------------------------------

KEY_PATTERNS = [
    ("anthropic-key", r"sk-ant-(api|admin)[0-9]{2}-[A-Za-z0-9_\-]{10,}"),
    ("github-pat", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    ("github-fine-grained-pat", r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private-key", r"-----BEGIN (RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
    ("slack-token", r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),
]
WORKFLOW_PATTERNS = [
    ("static-anthropic-input", r"^\s*(anthropic_api_key|claude_code_oauth_token)\s*:"),
    ("anthropic-key-env", r"^\s*ANTHROPIC_API_KEY\s*:"),
    ("non-github-secret", r"\$\{\{\s*secrets\.(?!GITHUB_TOKEN\b)[A-Za-z0-9_]+"),
    ("pull-request-target", r"^\s*pull_request_target\s*:"),
]


def scan_text_for_keys(path: str, text: str, allowed_secrets: set[str] | None = None) -> list[dict]:
    findings = []
    allowed_secrets = allowed_secrets or set()
    for name, rx in KEY_PATTERNS:
        for m in re.finditer(rx, text):
            findings.append({"path": path, "kind": name, "line": text[:m.start()].count("\n") + 1})
    if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
        for name, rx in WORKFLOW_PATTERNS:
            for m in re.finditer(rx, text, flags=re.M):
                if name == "non-github-secret" and m.group(0).split(".", 1)[1].strip() in allowed_secrets:
                    continue
                findings.append({"path": path, "kind": name, "line": text[:m.start()].count("\n") + 1})
    return findings


def cmd_scan_keys(args) -> int:
    root = repo_root()
    try:
        allowed = {k for k in (load_policy(root).get("allowed_workflow_secrets") or {}) if not k.startswith("_")}
    except SystemExit:
        allowed = set()
    tracked = git(root, "ls-files", "-z").split("\0")
    candidates = [p for p in tracked + git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p]
    findings = []
    for rel in sorted(set(candidates)):
        if rel.startswith("tests/fixtures/") or rel.endswith((".png", ".jpg", ".pdf", ".lock")):
            continue
        fp = root / rel
        if not fp.is_file() or fp.stat().st_size > 2_000_000:
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings += scan_text_for_keys(rel, text, allowed)
    env_hits = [k for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")
                if os.environ.get(k)]
    for k in env_hits:
        findings.append({"path": "<environment>", "kind": f"env:{k}", "line": 0})
    return emit({"status": "CLEAN" if not findings else "FOUND", "findings": findings}, 0 if not findings else 1)


# ---------------------------------------------------------------------------------------
# Run ledger (spec §4.2 failure signals, §4.6 estimates vs billing, §8 metrics)
# ---------------------------------------------------------------------------------------


def summarize_execution(messages: list[dict], schema_expected: bool = False) -> dict:
    init = next((m for m in messages if m.get("type") == "system" and m.get("subtype") == "init"), {})
    result = next((m for m in reversed(messages) if m.get("type") == "result"), None)
    failures: list[str] = []
    summary: dict = {
        "model": init.get("model"),
        "claude_code_version": init.get("claude_code_version"),
    }
    bad_mcp = [s.get("name") for s in init.get("mcp_servers", []) or []
               if isinstance(s, dict) and s.get("status") not in (None, "connected")]
    if bad_mcp:
        failures.append("MCP_SERVER_ERRORS:" + ",".join(map(str, bad_mcp)))
    if init.get("plugin_errors"):
        failures.append("PLUGIN_ERRORS")
    for m in messages:
        if m.get("type") == "system" and m.get("subtype") == "api_retry" \
                and m.get("error") in ("authentication_failed", "billing_error", "account_on_hold"):
            failures.append("API_" + str(m.get("error")).upper())
    if result is None:
        failures.append("NO_RESULT")
        summary.update({"failures": failures, "failed": True})
        return summary
    denials = result.get("permission_denials") or []
    model_usage = result.get("modelUsage") or {}
    summary.update({
        "subtype": result.get("subtype"),
        "is_error": bool(result.get("is_error")),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "total_cost_usd_estimate": result.get("total_cost_usd"),
        "permission_denials": len(denials) if isinstance(denials, list) else int(result.get("permission_denials_count") or 0),
        "session_id": result.get("session_id"),
        "usage": result.get("usage"),
        "model_usage": {k: {kk: vv for kk, vv in (v or {}).items()
                            if kk in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                                      "cacheCreationInputTokens", "costUSD", "webSearchRequests")}
                        for k, v in model_usage.items()},
        "has_structured_output": result.get("structured_output") is not None,
        "terminal_reason": result.get("terminal_reason"),
    })
    if result.get("subtype") != "success":
        failures.append("SUBTYPE_" + str(result.get("subtype")).upper())
    if result.get("is_error"):
        failures.append("IS_ERROR")
    warnings = []
    if summary["permission_denials"]:
        warnings.append("PERMISSION_DENIALS")  # policy hit: logged; escalated if it recurs (spec §4.2)
    summary["warnings"] = warnings
    if schema_expected and result.get("structured_output") is None:
        failures.append("NO_STRUCTURED_OUTPUT")
    summary["failures"] = failures
    summary["failed"] = bool(failures)
    return summary


def cmd_ledger(args) -> int:
    meta = {}
    for kv in args.meta or []:
        k, _, v = kv.partition("=")
        meta[k] = v
    exec_summary = {"failures": ["NO_EXECUTION_FILE"], "failed": True}
    if args.execution_file and Path(args.execution_file).is_file():
        try:
            messages = json.loads(Path(args.execution_file).read_text(encoding="utf-8"))
            if isinstance(messages, dict):
                messages = [messages]
            exec_summary = summarize_execution(messages, schema_expected=args.schema_expected)
        except json.JSONDecodeError:
            exec_summary = {"failures": ["UNPARSEABLE_EXECUTION_FILE"], "failed": True}
    record = {
        "record_version": 1,
        "recorded_at": now_utc(),
        "cost_basis": "client-side estimate at list price; reconcile to Console billing (spec §4.6)",
        **meta,
        "agent": exec_summary,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return emit({"status": "WRITTEN", "out": args.out, "agent_failed": exec_summary.get("failed"),
                 "failures": exec_summary.get("failures", [])})


def load_ledger(ledger_dir: Path) -> list[dict]:
    records = []
    for fp in sorted(ledger_dir.rglob("*.json")):
        try:
            records.append(json.loads(fp.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return records


def run_cost(record: dict) -> float:
    total = 0.0
    for key in ("agent", "verify_agent", "triage_agent"):
        part = record.get(key) or {}
        try:
            total += float(part.get("total_cost_usd_estimate") or 0)
        except (TypeError, ValueError):
            pass
    return total


def cmd_spend_today(args) -> int:
    records = load_ledger(Path(args.ledger_dir)) if Path(args.ledger_dir).is_dir() else []
    day = args.day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    spend = sum(run_cost(r) for r in records if str(r.get("recorded_at", "")).startswith(day))
    item_runs = []
    if args.issue:
        item_runs = [{"outcome": r.get("outcome"), "run_id": r.get("run_id")}
                     for r in records if str(r.get("issue")) == str(args.issue) and r.get("role") == "implement"]
    return emit({"day": day, "spend_today_usd": round(spend, 4), "item_runs": item_runs})


def cmd_reconcile(args) -> int:
    """Compare ledger estimates with billed amounts and compute cost per merged PR."""
    records = load_ledger(Path(args.ledger_dir))
    est_by_day: dict[str, float] = {}
    for r in records:
        day = str(r.get("recorded_at", ""))[:10]
        est_by_day[day] = est_by_day.get(day, 0.0) + run_cost(r)
    billed_by_day: dict[str, float] = {}
    if args.billing_csv:
        with open(args.billing_csv, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                day = (row.get("date") or "")[:10]
                try:
                    billed_by_day[day] = billed_by_day.get(day, 0.0) + float(row.get("amount_usd") or 0)
                except ValueError:
                    continue
    days = sorted(set(est_by_day) | set(billed_by_day))
    if args.since:
        days = [d for d in days if d >= args.since]
    if args.until:
        days = [d for d in days if d <= args.until]
    rows = []
    for d in days:
        e, b = est_by_day.get(d, 0.0), billed_by_day.get(d)
        gap = None if not b else round(100.0 * (b - e) / b, 2)
        rows.append({"date": d, "estimate_usd": round(e, 4), "billed_usd": b, "gap_pct": gap})
    est_total = round(sum(est_by_day.get(d, 0.0) for d in days), 4)
    billed_total = round(sum(billed_by_day.get(d, 0.0) for d in days), 4) if billed_by_day else None
    merged = []
    if args.merged_json:
        merged = json.loads(Path(args.merged_json).read_text(encoding="utf-8"))
    n_merged = len(merged)
    basis = billed_total if billed_total else est_total
    tol = args.tolerance_pct
    gap_total = None if not billed_total else round(100.0 * (billed_total - est_total) / billed_total, 2)
    report = {
        "days": rows,
        "estimate_total_usd": est_total,
        "billed_total_usd": billed_total,
        "gap_total_pct": gap_total,
        "within_tolerance": None if gap_total is None else abs(gap_total) <= tol,
        "tolerance_pct": tol,
        "merged_prs": n_merged,
        "cost_per_merged_pr_usd": round(basis / n_merged, 2) if n_merged else None,
        "cost_basis": "billed" if billed_total else "estimate (not reconciled)",
        "note": "Tokens only. Add review time and rework at your own rates for the full unit cost (spec §8.3).",
    }
    code = 0 if report["within_tolerance"] in (True, None) else 1
    return emit(report, code)


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="factory.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("classify", help="classify the working-tree diff against a base ref")
    s.add_argument("--base", required=True)
    s.set_defaults(fn=cmd_classify)

    s = sub.add_parser("check-diff", help="post-run diff policy; exit 1 if the diff must not be published")
    s.add_argument("--base", required=True)
    s.add_argument("--max-tier", type=int, help="tier allowed by preflight (2 only with an approved spec)")
    s.set_defaults(fn=cmd_check_diff)

    s = sub.add_parser("predict-tier", help="predict the tier from expected file paths (triage)")
    s.add_argument("--files", help="comma-separated paths")
    s.add_argument("--files-json", help="JSON list of paths")
    s.set_defaults(fn=cmd_predict_tier)

    s = sub.add_parser("parse-handoff", help="latest trusted factory-handoff:v1 from issue comments JSON")
    s.add_argument("--comments-json", required=True)
    s.set_defaults(fn=cmd_parse_handoff)

    s = sub.add_parser("select-item", help="oldest eligible ready issue from gh issue list JSON")
    s.add_argument("--issues-json", required=True)
    s.set_defaults(fn=cmd_select_item)

    s = sub.add_parser("stop-if", help="evaluate STOP_IF conditions; exit 1 means stop")
    s.add_argument("--state", required=True)
    s.set_defaults(fn=cmd_stop_if)

    s = sub.add_parser("render-prompt", help="render a prompt template with sanitized untrusted input")
    s.add_argument("--template", required=True)
    s.add_argument("--issue-json")
    s.add_argument("--handoff-json")
    s.add_argument("--extra-json")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_render_prompt)

    s = sub.add_parser("hook-guard", help="PreToolUse hook: reads hook JSON on stdin; exit 2 blocks")
    s.set_defaults(fn=cmd_hook_guard)

    s = sub.add_parser("lockfile-check", help="dependency and lockfile policy (registry, integrity, scripts)")
    s.add_argument("--root", help="repository root (default: git top-level)")
    s.set_defaults(fn=cmd_lockfile_check)

    s = sub.add_parser("render-settings", help="write Claude Code settings with absolute sandbox paths")
    s.add_argument("--in", dest="input", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--root", help="workspace root (default: git top-level)")
    s.add_argument("--hooks-dir", help="run hook scripts from this (trusted) directory")
    s.set_defaults(fn=cmd_render_settings)

    s = sub.add_parser("config-hashes", help="check (default) or --update configuration hashes")
    s.add_argument("--update", action="store_true")
    s.set_defaults(fn=cmd_config_hashes)

    s = sub.add_parser("scan-keys", help="scan tracked files and environment for long-lived keys")
    s.set_defaults(fn=cmd_scan_keys)

    s = sub.add_parser("ledger", help="write a run record from a Claude execution file")
    s.add_argument("--execution-file")
    s.add_argument("--meta", action="append", help="key=value fields for the record")
    s.add_argument("--schema-expected", action="store_true")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_ledger)

    s = sub.add_parser("spend-today", help="estimated spend today and prior runs for an issue")
    s.add_argument("--ledger-dir", required=True)
    s.add_argument("--day")
    s.add_argument("--issue")
    s.set_defaults(fn=cmd_spend_today)

    s = sub.add_parser("reconcile", help="ledger estimates vs billed amounts; cost per merged PR")
    s.add_argument("--ledger-dir", required=True)
    s.add_argument("--billing-csv", help="CSV with columns date,amount_usd (from Console usage export)")
    s.add_argument("--merged-json", help="JSON list of merged factory PRs (gh pr list --json)")
    s.add_argument("--since")
    s.add_argument("--until")
    s.add_argument("--tolerance-pct", type=float, default=10.0)
    s.set_defaults(fn=cmd_reconcile)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
