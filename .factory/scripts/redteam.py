#!/usr/bin/env python3
"""redteam.py - replay the standing injection suite against the factory's deterministic layers
(spec §6.7; CI hardening item 17). Standard library only. Exit 1 if any case is not blocked.

Layers exercised: untrusted-text sanitising (prompt rendering), hand-off trust, the PreToolUse
guard in unattended mode, the post-run diff policy, and the lockfile gate. Cases live in
.factory/redteam/cases.json. A live-agent replay (the model itself facing these payloads) needs
Anthropic access and runs separately; see docs/factory/MANUAL-CONNECTIONS.md.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_engine():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("factory_engine", HERE / "factory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


E = load_engine()
LOCK = {"name": "rt", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
        "packages": {"": {"name": "rt", "version": "1.0.0"},
                     "node_modules/ok": {"version": "1.0.0", "resolved": "https://registry.npmjs.org/ok/-/ok-1.0.0.tgz",
                                         "integrity": "sha512-" + "A" * 86 + "=="}}}


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="rt", GIT_AUTHOR_EMAIL="rt@example.invalid",
                            GIT_COMMITTER_NAME="rt", GIT_COMMITTER_EMAIL="rt@example.invalid"))


def scratch_repo() -> tuple[Path, str]:
    root = Path(tempfile.mkdtemp(prefix="redteam-"))
    files = {"src/app.ts": "export const a = 1;\n", "src/auth/login.ts": "export const login = 1;\n",
             "test/app.test.ts": "// existing\n", "package.json": json.dumps({"name": "rt", "version": "1.0.0", "scripts": {"test": "x"}}),
             "package-lock.json": json.dumps(LOCK)}
    git(root, "init", "-q", "-b", "main")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root, subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()


def run_case(c: dict, policy: dict) -> tuple[bool, str]:
    kind = c["kind"]
    if kind == "sanitize":
        out = E.sanitize_untrusted(c["input"], 8000)
        leaked = [s for s in c["must_not_contain"] if s in out]
        return not leaked, f"leaked {leaked}" if leaked else "stripped"
    if kind == "handoff":
        h = E.latest_trusted_handoff(c["comments"], policy)
        if c["expect"] == "none":
            return h is None, f"accepted {h}" if h else "rejected"
        bad = {k: (h or {}).get(k) for k, v in c["expect"].items() if (h or {}).get(k) != v}
        return not bad, f"wrong fields {bad}" if bad else "trusted hand-off kept"
    if kind == "guard":
        root, _ = scratch_repo()
        try:
            allowed, why = E.guard_decision({"tool_name": c["tool_name"], "tool_input": c["tool_input"]},
                                            root, policy, unattended=True)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        return not allowed, why
    if kind == "diff":
        root, base = scratch_repo()
        try:
            for rel, text in (c.get("write") or {}).items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            if c.get("write_secret"):
                (root / c["write_secret"]).write_text("export const k = '" + "sk-ant-" + "api03-" + "Z" * 24 + "';\n")
            if c.get("move"):
                (root / c["move"][1]).parent.mkdir(parents=True, exist_ok=True)
                git(root, "mv", c["move"][0], c["move"][1])
            if c.get("pkg_field"):
                pkg = json.loads((root / "package.json").read_text())
                pkg[c["pkg_field"][0]] = c["pkg_field"][1]
                (root / "package.json").write_text(json.dumps(pkg))
            result = E.check_diff(root, base, policy)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        return c["expect"] in result["stop_reasons"], f"stop_reasons={result['stop_reasons']}"
    if kind == "lockfile":
        root = Path(tempfile.mkdtemp(prefix="redteam-lock-"))
        try:
            lock = copy.deepcopy(LOCK)
            lock["packages"][c.get("path", "node_modules/bad")] = dict(lock["packages"]["node_modules/ok"], **c["entry"])
            (root / "package-lock.json").write_text(json.dumps(lock))
            (root / "package.json").write_text(json.dumps({"name": "rt", "version": "1.0.0"}))
            result = E.lockfile_check(root, policy)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        hit = any(c["expect"] in v for v in result["violations"])
        return hit, "; ".join(result["violations"]) or "no violation"
    return False, f"unknown kind {kind}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="redteam.py", description=__doc__.splitlines()[0])
    p.add_argument("--cases", default=str(HERE.parent / "redteam/cases.json"))
    p.add_argument("--policy", default=os.environ.get("FACTORY_POLICY") or str(HERE.parent / "policy.json"))
    args = p.parse_args(argv)
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    results = []
    for c in cases:
        try:
            blocked, detail = run_case(c, policy)
        except Exception as exc:  # a crashing layer is a failure, never a pass
            blocked, detail = False, f"error: {exc}"
        results.append({"id": c["id"], "channel": c["channel"], "ref": c.get("ref"), "blocked": blocked, "detail": detail})
    failed = [r for r in results if not r["blocked"]]
    print(json.dumps({"cases": len(results), "blocked": len(results) - len(failed), "failed": failed,
                      "results": results}, indent=2, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
