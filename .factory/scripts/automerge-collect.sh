#!/usr/bin/env bash
# automerge-collect.sh - gather trusted evidence for auto-merge decisions (spec §9.6).
# Runs only git, gh and the factory's own scripts: never the pull request's code.
#
#   automerge-collect.sh pr <number> <outdir>        classify.json, pr.json, evidence.json
#   automerge-collect.sh history <class> <outdir>    history.json for the qualification report
#
# Needs GH_TOKEN (read), GITHUB_REPOSITORY, a checkout of the default branch, and
# FACTORY_POLICY / FACTORY_SCRIPTS pointing at the trusted copy taken before any PR fetch.
#
# Data contract (consumed by automerge.py decide):
#   pr.json        gh pr view fields + raw_files, dependency_bumps, other_dependency_changes,
#                  lock_delta, commits
#   evidence.json  classified_sha, default_branch, ledger, run, gates_run, review_run, class_prs, freeze,
#                  open_sampled_reviews, escapes, reverts, redteam, salt
# Trust assumption: users with write access are trusted. The ledger branch and labels can be
# written by them; the checks here stop the agent and outside contributors, not maintainers.
set -euo pipefail
MODE="${1:?pr|history}"; ARG="${2:?}"; OUT="${3:?outdir}"
REPO="${GITHUB_REPOSITORY:?}"
S="${FACTORY_SCRIPTS:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
mkdir -p "$OUT"
export GIT_ATTR_SOURCE=4b825dc642cb6eb9a060e54bf8d69288fbee4904
DEFAULT_BRANCH="$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)"
git fetch --quiet --no-tags origin "+refs/heads/$DEFAULT_BRANCH:refs/factory/default"

classify_pr() {   # <number> <dir>: trusted diff policy on the PR head against its merge base
  local n="$1" d="$2" wt head base
  mkdir -p "$d"
  gh pr view "$n" -R "$REPO" --json number,title,headRefName,headRefOid,baseRefName,author,labels,isCrossRepository,isDraft,mergeable,mergedAt,closedAt,state > "$d/pr-raw.json"
  git fetch --quiet --no-tags origin "+refs/pull/$n/head:refs/factory/pr/$n"
  head="$(git rev-parse "refs/factory/pr/$n^{commit}")"
  base="$(git merge-base refs/factory/default "$head")"
  echo "$head" > "$d/classified-sha.txt"
  wt="$(mktemp -d)"; rmdir "$wt"
  git -c core.hooksPath=/dev/null worktree add --detach --quiet "$wt" "$head"
  # Hooks and filters never run: no git command here executes repository content.
  ( cd "$wt" && python3 -I "$S/factory.py" check-diff --base "$base" > "$d/classify.json" ) || true
  [ -s "$d/classify.json" ] || echo '{}' > "$d/classify.json"
  git worktree remove --force "$wt"
  # Every changed path, including any the tier classifier is told to ignore.
  git diff --name-only -z --no-renames "$base" "$head" > "$d/raw-files.bin"
  gh api --paginate "repos/$REPO/pulls/$n/commits?per_page=100" \
    --jq '.[] | {sha: .sha, author_login: (.author.login // ""), committer_login: (.committer.login // ""), verified: (.commit.verification.verified // false)}' > "$d/commits.jsonl"
  python3 -I - "$d" "$base" "$head" <<'PY'
import json, subprocess, sys, pathlib
d, base, head = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]

def show(rev, path):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "invalid"

pr = json.loads((d / "pr-raw.json").read_text())
pr["raw_files"] = [p.decode("utf-8", "surrogateescape") for p in (d / "raw-files.bin").read_bytes().split(b"\0") if p]
bumps, delta = [], []
old_pkg, new_pkg = show(base, "package.json"), show(head, "package.json")
if isinstance(old_pkg, dict) and isinstance(new_pkg, dict):
    for sec in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, v in (new_pkg.get(sec) or {}).items():
            o = (old_pkg.get(sec) or {}).get(name)
            if o is not None and o != v:
                bumps.append({"name": name, "from": o, "to": v, "section": sec})
    other = [f for f in ("peerDependencies", "peerDependenciesMeta", "bundleDependencies", "bundledDependencies",
                         "overrides", "resolutions", "engines", "packageManager", "workspaces")
             if old_pkg.get(f) != new_pkg.get(f)]
    pr["other_dependency_changes"] = other
elif old_pkg != new_pkg:
    pr["other_dependency_changes"] = ["package.json missing or unparseable"]
old_lock, new_lock = show(base, "package-lock.json"), show(head, "package-lock.json")
if old_lock == "invalid" or new_lock == "invalid":
    delta.append({"path": "package-lock.json", "name": "", "change": "unparseable"})
elif isinstance(old_lock, dict) or isinstance(new_lock, dict):
    a = (old_lock or {}).get("packages") or {}
    b = (new_lock or {}).get("packages") or {}
    for path in sorted(set(a) | set(b)):
        if path == "":
            continue   # the root entry mirrors package.json, which is checked separately
        x, y = a.get(path), b.get(path)
        if x == y:
            continue
        name = (y or x or {}).get("name") or path.rsplit("node_modules/", 1)[-1]
        change = "added" if x is None else "removed" if y is None else "changed"
        delta.append({"path": path, "name": name, "change": change,
                      "old": (x or {}).get("version"), "new": (y or {}).get("version"),
                      "resolved": (y or {}).get("resolved")})
pr["dependency_bumps"] = bumps
pr["lock_delta"] = delta
pr["commits"] = [json.loads(l) for l in (d / "commits.jsonl").read_text().splitlines() if l.strip()]
(d / "pr.json").write_text(json.dumps(pr, indent=2))
PY
}

ledger_dir() {
  local d="$OUT/ledger"; mkdir -p "$d"
  if git ls-remote --exit-code --heads origin factory/ledger >/dev/null 2>&1; then
    git fetch --quiet --depth 1 origin factory/ledger:refs/remotes/origin/factory/ledger
    git archive refs/remotes/origin/factory/ledger | tar -x -C "$d"
  fi
  echo "$d"
}

escapes_and_reverts() {   # writes escapes.json and reverts.json
  gh issue list -R "$REPO" --state all --label factory:escape --limit 500 --json number,body,createdAt \
    --jq '[.[] | {issue: .number, date: .createdAt, pr: ((.body | capture("Escaped from #(?<n>[0-9]+)").n // "0") | tonumber)}]' > "$OUT/escapes.json"
  # GitHub's revert button: title 'Revert "..."', body 'Reverts owner/repo#N'. Other titles that
  # merely contain the word are not reverts.
  gh pr list -R "$REPO" --state merged --search "Revert in:title" --limit 500 --json number,title,body,mergedAt \
    --jq '[.[] | select(.title | startswith("Revert \"")) | {revert: .number, date: .mergedAt, pr: ((.body | capture("Reverts [^ #]*#(?<n>[0-9]+)").n // "0") | tonumber)} | select(.pr > 0)]' > "$OUT/reverts.json"
}

class_prs() {   # auto-merged PRs per class, from the class labels the merge job adds
  python3 -I -c 'import json,os,sys; p=json.load(open(os.environ.get("FACTORY_POLICY") or sys.argv[1])); print(" ".join(k for k in (p.get("auto_merge") or {}).get("classes", {}) if not k.startswith("_")))' "$S/../policy.json" > "$OUT/classes.txt"
  echo '{}' > "$OUT/class-prs.json"
  for c in $(cat "$OUT/classes.txt"); do
    [[ "$c" =~ ^[a-z0-9-]+$ ]] || continue
    gh pr list -R "$REPO" --state merged --label "factory:class-$c" --limit 500 --json number --jq '[.[].number]' > "$OUT/class-$c.json" || echo '[]' > "$OUT/class-$c.json"
    python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); d[sys.argv[2]]=json.load(open(sys.argv[3])); json.dump(d,open(sys.argv[1],"w"))' "$OUT/class-prs.json" "$c" "$OUT/class-$c.json"
  done
}

case "$MODE" in
  pr)
    classify_pr "$ARG" "$OUT"
    L="$(ledger_dir)"
    escapes_and_reverts
    SHA="$(cat "$OUT/classified-sha.txt")"
    # The ledger record for this PR (latest), and the workflow run that wrote it.
    python3 -I - "$L" "$ARG" > "$OUT/ledger.json" <<'PY'
import json, sys, pathlib
best = {}
for f in sorted(pathlib.Path(sys.argv[1]).rglob("*.json")):
    try:
        r = json.loads(f.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        continue
    if isinstance(r, dict) and str(r.get("pr_number")) == sys.argv[2] and str(r.get("recorded_at", "")) >= str(best.get("recorded_at", "")):
        best = r
agent = best.get("agent") or {}
print(json.dumps({} if not best else {
    "verdict": best.get("verdict"), "head_sha": best.get("head_sha"), "run_id": best.get("run_id"),
    "gates_line": best.get("gates_line"), "proof_line": best.get("proof_line"), "monitor": best.get("monitor") or {},
    "model": agent.get("model"), "claude_code_version": agent.get("claude_code_version"),
    "verifier_model": (best.get("verify_agent") or {}).get("model"), "harness_action_sha": best.get("harness_action_sha"),
    "outcome": best.get("outcome")}))
PY
    RUN_ID="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("run_id") or "")' "$OUT/ledger.json")"
    echo '{}' > "$OUT/run.json"
    if [[ "$RUN_ID" =~ ^[0-9]+$ ]]; then
      gh api "repos/$REPO/actions/runs/$RUN_ID" --jq '{path: .path, head_branch: .head_branch, event: .event, conclusion: .conclusion}' > "$OUT/run.json" || echo '{}' > "$OUT/run.json"
    fi
    # Dependabot: the factory-gates run for exactly this commit, and its verdict job.
    echo '{}' > "$OUT/gates-run.json"
    GRUN="$(gh api "repos/$REPO/actions/workflows/factory-gates.yml/runs?head_sha=$SHA&event=pull_request&per_page=20" \
      --jq '[.workflow_runs[] | select(.head_sha == "'"$SHA"'")] | sort_by(.created_at) | last | if . == null then "" else "\(.id) \(.path) \(.head_sha)" end' 2>/dev/null || true)"
    if [ -n "$GRUN" ]; then
      read -r gid gpath gsha <<<"$GRUN"
      gstatus="$(gh api "repos/$REPO/actions/runs/$gid/jobs?per_page=50" --jq '[.jobs[] | select(.name == "status")][0].conclusion // ""' 2>/dev/null || true)"
      python3 -I -c 'import json,sys; print(json.dumps({"id": sys.argv[1], "path": sys.argv[2], "head_sha": sys.argv[3], "status_job": sys.argv[4] or None}))' \
        "$gid" "$gpath" "$gsha" "$gstatus" > "$OUT/gates-run.json"
    fi
    # Dependabot: the dependency-review job (vulnerabilities, licences) for the same commit.
    echo '{}' > "$OUT/review-run.json"
    RRUN="$(gh api "repos/$REPO/actions/workflows/factory-security.yml/runs?head_sha=$SHA&event=pull_request&per_page=20" \
      --jq '[.workflow_runs[] | select(.head_sha == "'"$SHA"'")] | sort_by(.created_at) | last | if . == null then "" else "\(.id) \(.path) \(.head_sha)" end' 2>/dev/null || true)"
    if [ -n "$RRUN" ]; then
      read -r rid rpath rsha <<<"$RRUN"
      rstatus="$(gh api "repos/$REPO/actions/runs/$rid/jobs?per_page=50" --jq '[.jobs[] | select(.name == "dependency-review")][0].conclusion // ""' 2>/dev/null || true)"
      python3 -I -c 'import json,sys; print(json.dumps({"id": sys.argv[1], "path": sys.argv[2], "head_sha": sys.argv[3], "dependency_review_job": sys.argv[4] or None}))' \
        "$rid" "$rpath" "$rsha" "$rstatus" > "$OUT/review-run.json"
    fi
    class_prs
    python3 -I "$S/redteam.py" > "$OUT/redteam.json" 2>/dev/null || [ -s "$OUT/redteam.json" ] || echo 'null' > "$OUT/redteam.json"
    gh issue list -R "$REPO" --state open --label factory:sampled-review --json number --jq length > "$OUT/open-reviews.txt" || echo 999 > "$OUT/open-reviews.txt"
    gh issue list -R "$REPO" --state open --label factory:freeze --json number --jq length > "$OUT/freeze.txt" || echo 1 > "$OUT/freeze.txt"
    python3 -I - "$OUT" "$SHA" "$DEFAULT_BRANCH" "${GITHUB_REPOSITORY_ID:-}" <<'PY'
import json, sys, pathlib
out, sha, default_branch, salt = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
def j(f, d=None):
    try:
        return json.loads((out / f).read_text())
    except (OSError, json.JSONDecodeError):
        return d
def n(f, d):
    try:
        return int((out / f).read_text().strip())
    except (OSError, ValueError):
        return d
ev = {"classified_sha": sha, "default_branch": default_branch,
      "ledger": j("ledger.json", {}) or {}, "run": j("run.json", {}) or {}, "gates_run": j("gates-run.json", {}) or {},
      "review_run": j("review-run.json", {}) or {}, "class_prs": j("class-prs.json", {}) or {},
      "freeze": n("freeze.txt", 1) > 0, "open_sampled_reviews": n("open-reviews.txt", 999),
      "escapes": j("escapes.json", []) or [], "reverts": j("reverts.json", []) or [],
      "redteam": j("redteam.json"), "salt": salt}
(out / "evidence.json").write_text(json.dumps(ev, indent=2))
PY
    ;;
  history)
    CLASS="$ARG"
    L="$(ledger_dir)"
    escapes_and_reverts
    PRODUCER="$(python3 -I -c 'import json,os,sys; p=json.load(open(os.environ.get("FACTORY_POLICY") or sys.argv[1])); print(p["auto_merge"]["classes"][sys.argv[2]]["producer"])' "$S/../policy.json" "$CLASS")"
    case "$PRODUCER" in factory) Q="head:claude/fq-" ;; dependabot) Q="author:app/dependabot" ;; *) echo "unknown producer" >&2; exit 2 ;; esac
    gh pr list -R "$REPO" --state closed --search "$Q" --limit 200 --json number --jq '.[].number' > "$OUT/numbers.txt"
    echo '[]' > "$OUT/prs.json"
    while read -r n; do
      [ -n "$n" ] || continue
      d="$OUT/pr-$n"
      classify_pr "$n" "$d" || continue
      gh pr view "$n" -R "$REPO" --json reviews > "$d/reviews.json"
      python3 -I - "$d" "$OUT/prs.json" "$CLASS" "$S" <<'PY'
import json, sys, pathlib, importlib.util, os
d, acc, cls, s = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4]
spec = importlib.util.spec_from_file_location("am", os.path.join(s, "automerge.py")); am = importlib.util.module_from_spec(spec)
sys.argv = ["automerge.py"]; spec.loader.exec_module(am)
pr = json.loads((d / "pr.json").read_text()); c = json.loads((d / "classify.json").read_text() or "{}")
m = am.match_class(c, pr, am.policy())
reviews = json.loads((d / "reviews.json").read_text()).get("reviews", [])
human = any(r.get("state") == "APPROVED" and r.get("authorAssociation") in ("OWNER", "MEMBER", "COLLABORATOR")
            and not str((r.get("author") or {}).get("login", "")).endswith("[bot]") for r in reviews)
rows = json.loads(acc.read_text())
rows.append({"number": pr["number"], "class": m["class"], "mergedAt": pr.get("mergedAt"), "closedAt": pr.get("closedAt"),
             "human_approved": human})
acc.write_text(json.dumps(rows))
PY
    done < "$OUT/numbers.txt"
    python3 -I -c 'import json,sys; o=sys.argv[1]; json.dump({"prs": json.load(open(o+"/prs.json")), "escapes": json.load(open(o+"/escapes.json")), "reverts": json.load(open(o+"/reverts.json"))}, open(o+"/history.json","w"), indent=2)' "$OUT"
    ;;
  *) echo "mode must be pr or history" >&2; exit 2 ;;
esac
