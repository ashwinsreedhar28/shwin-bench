#!/usr/bin/env python3
"""Compute PASS_TO_PASS for every validated task in a mined JSON.

For each base commit: run the full (CPU) suite twice at base; stable_pass = tests passing both times.
For each task: run the full suite at base + test_patch + gold once; PASS_TO_PASS = stable_pass ∩ passing_now.
Reports any stable-at-base test that the gold patch breaks (should be none).
"""
import argparse, json, os, re, subprocess, tempfile, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from mine import Work, sh

def _pytest(W, args, timeout=3000):
    r = sh([W.py, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider", "-W", "ignore", "--timeout=600",
            "-rA", "--tb=no", *W.pytest_args, *args], cwd=W.cwd, env=W.env, check=False, timeout=timeout)
    passed, failed = set(), set()
    for l in r.stdout.splitlines():
        m = re.match(r'(PASSED|FAILED|ERROR) (\S+)', l)
        if m: (passed if m.group(1) == "PASSED" else failed).add(m.group(2))
    return passed, failed

def full_run(W, extra=(), touched=()):
    """One pytest run for the untouched files; touched test files (new tests may crash the interpreter) run one at a time."""
    root = extra[0] if extra else "tests"
    touched = [t for t in touched if (W.cwd / t).exists()]
    passed, failed = _pytest(W, [root, *[f"--ignore={t}" for t in touched]])
    for f in touched:
        p, fl = _pytest(W, [f]); passed |= p; failed |= fl
    return passed, failed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json"); ap.add_argument("--py", required=True)
    ap.add_argument("--subdir", default=""); ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--tests-root", default="tests")
    ap.add_argument("--repos-dir", default=os.environ.get("SHWIN_REPOS", "/home/claude/ashwinsreedhar28"), help="directory holding clones of the source repos")
    a = ap.parse_args()
    d = json.loads(Path(a.json).read_text())
    repo = Path(a.repos_dir) / d["repo"]
    P = d["base_commit"]
    wt = Path(tempfile.mkdtemp(prefix=f"p2p-{d['commit'][:7]}-"))
    sh(["git", "worktree", "add", "-q", "--detach", str(wt), P], cwd=repo)
    env_extra = dict(e.split("=", 1) for e in a.env)
    W = Work(repo, P, wt, a.py, [], env_extra, a.subdir)
    try:
        tp = next((t["test_patch"] for t in d["tasks"] if t["validation"]["valid"]), None)
        if tp is None: print("no valid tasks"); return
        W.reset(); W.apply(tp)
        touched = sorted({W.rel(m.group(1)) for m in re.finditer(r'^\+\+\+ b/(\S+)', tp, re.M)})
        p1, f1 = full_run(W, [a.tests_root], touched); p2, f2 = full_run(W, [a.tests_root], touched)
        stable = p1 & p2
        unstable = (p1 | p2) - stable
        print(f"[{d['commit'][:7]}] base suite: {len(p1)}/{len(p1)+len(f1)} passed run1, {len(p2)} run2; stable={len(stable)} unstable={sorted(unstable)}")
        for t in d["tasks"]:
            if not t["validation"]["valid"]: continue
            W.reset(); W.apply(t["test_patch"]); W.apply(t["gold_patch"])
            p, f = full_run(W, [a.tests_root], touched)
            t["PASS_TO_PASS"] = sorted(stable & p)
            t["broken_by_gold"] = sorted(stable - p)
            t["unstable_at_base"] = sorted(unstable)
            missing_f2p = [x for x in t["FAIL_TO_PASS"] if x not in p]
            print(f"  {t['task_id']}: P2P={len(t['PASS_TO_PASS'])} broken_by_gold={t['broken_by_gold']} f2p_not_passing={missing_f2p}")
        Path(a.json).write_text(json.dumps(d, indent=1))
    finally:
        sh(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=False)

if __name__ == "__main__":
    main()
