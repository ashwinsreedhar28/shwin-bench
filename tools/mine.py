#!/usr/bin/env python3
"""Split a (possibly bundled) fix commit into SWE-bench-style tasks.

For commit C with parent P:
  code_diff = diff(P, C) restricted to non-test, non-doc files
  test_diff = diff(P, C) restricted to test files
  candidates = tests collected at P+test_diff that are not collected at P
  keep t if: fails at P+test_diff  and  passes at P+test_diff+code_diff
  attribute: for each code hunk h, run candidates at P+test_diff+(code_diff-h);
             hunks whose removal makes t fail are t's gold hunks
  verify: P+test_diff+gold(t) passes t
Groups tests with identical gold-hunk sets into one task.
Writes <out>/<commit>.json
"""
import argparse, json, os, re, subprocess, sys, shutil, tempfile, time
from collections import defaultdict
from pathlib import Path

def sh(cmd, cwd=None, env=None, check=True, timeout=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str),
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {cmd}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return r

# ---------- diff parsing ----------
class Hunk:
    def __init__(self, file_header, text, path):
        self.file_header = file_header  # lines up to first @@ (diff --git, index, ---, +++)
        self.text = text                # from @@ to end of hunk
        self.path = path
    def patch(self):
        return "".join(self.file_header) + "".join(self.text)

def parse_diff(diff_text):
    """Return list of Hunk; whole-file ops (new/deleted/binary) are one hunk."""
    hunks = []
    blocks = re.split(r'(?m)^(?=diff --git )', diff_text)
    for b in blocks:
        if not b.strip():
            continue
        lines = b.splitlines(keepends=True)
        m = re.match(r'diff --git a/(\S+) b/(\S+)', lines[0])
        path = m.group(2)
        hdr, rest = [], []
        i = 0
        while i < len(lines) and not lines[i].startswith('@@'):
            hdr.append(lines[i]); i += 1
        if i == len(lines):  # binary / rename-only / mode-only
            hunks.append(Hunk(hdr, [], path)); continue
        cur = None
        for ln in lines[i:]:
            if ln.startswith('@@'):
                if cur: hunks.append(Hunk(hdr, cur, path))
                cur = [ln]
            else:
                cur.append(ln)
        if cur: hunks.append(Hunk(hdr, cur, path))
    # new files must stay atomic: if header has 'new file mode', merge its hunks
    merged, byfile = [], defaultdict(list)
    for h in hunks:
        byfile[h.path].append(h)
    for path, hs in byfile.items():
        if any('new file mode' in ''.join(h.file_header) or 'deleted file mode' in ''.join(h.file_header) for h in hs):
            merged.append(Hunk(hs[0].file_header, sum([h.text for h in hs], []), path))
        else:
            merged.extend(hs)
    return merged

def compose(hunks):
    """Assemble a patch from a subset of hunks (grouped by file, original order)."""
    out, byfile = [], defaultdict(list)
    for h in hunks:
        byfile[h.path].append(h)
    for path, hs in byfile.items():
        out.append("".join(hs[0].file_header))
        for h in hs:
            out.append("".join(h.text))
    return "".join(out)

# ---------- repo ops ----------
class Work:
    def __init__(self, repo, base, wt, py, pytest_args, env_extra, subdir=""):
        self.repo, self.base, self.wt, self.py = repo, base, wt, py
        self.pytest_args = pytest_args
        self.subdir = subdir
        self.cwd = wt / subdir if subdir else wt
        pp = str(self.cwd) + (":" + env_extra.pop("PYTHONPATH") if "PYTHONPATH" in env_extra else "")
        self.env = dict(os.environ, PYTHONPATH=pp, PYTHONDONTWRITEBYTECODE="1", **env_extra)
    def rel(self, p):
        return os.path.relpath(p, self.subdir) if self.subdir else p
    def reset(self):
        sh(["git", "checkout", "-q", "--", "."], cwd=self.wt)
        sh(["git", "clean", "-fdq"], cwd=self.wt)
    def apply(self, patch_text):
        if not patch_text.strip():
            return
        pf = self.wt / ".__patch"
        pf.write_text(patch_text)
        r = sh(["git", "apply", "--recount", "--whitespace=nowarn", str(pf)], cwd=self.wt, check=False)
        pf.unlink()
        if r.returncode != 0:
            raise RuntimeError("apply failed: " + r.stderr[-1500:])
    def collect(self, paths):
        r = sh([self.py, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider", *self.pytest_args, *paths],
               cwd=self.cwd, env=self.env, check=False, timeout=600)
        return {l.strip() for l in r.stdout.splitlines() if "::" in l and not l.startswith(("=", " "))}
    def run(self, tests, timeout=900):
        """Batch run; anything left 'missing' (interpreter crash mid-run) is rerun alone."""
        res = self._run(tests, timeout)
        for t in [t for t, v in res.items() if v == "missing"]:
            r = self._run([t], timeout)
            res[t] = r[t] if r[t] != "missing" else "crash"
        return res
    def _run(self, tests, timeout=900):
        """Return dict test_id -> 'passed'|'failed'|'error'|'missing'."""
        if not tests: return {}
        r = sh([self.py, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider", "-W", "ignore", "--timeout=300",
                "-rA", "--tb=no", *self.pytest_args, *tests],
               cwd=self.cwd, env=self.env, check=False, timeout=timeout)
        res = {t: "missing" for t in tests}
        for l in r.stdout.splitlines():
            m = re.match(r'(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED) (\S+)', l)
            if m:
                tid = m.group(2).split(" ")[0]
                if tid in res:
                    res[tid] = m.group(1).lower()
        if "error" in r.stdout.lower() and all(v == "missing" for v in res.values()):
            for t in res: res[t] = "error"
        return res

def is_test_path(p):
    return p.startswith("tests/") or "/tests/" in p or "/test/" in p or re.search(r'(^|/)test_[^/]+\.py$', p) or p.endswith(("_test.py", ".test.ts", ".spec.ts"))

def is_doc_path(p):
    return p.endswith((".md", ".txt", ".png", ".svg", ".json")) or p.startswith(("results/", "docs/"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("commit")
    ap.add_argument("--py", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pytest-args", default="")
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--skip-attribution", action="store_true")
    ap.add_argument("--subdir", default="", help="run pytest from this subdirectory of the repo")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    env_extra = dict(e.split("=", 1) for e in a.env)
    pytest_args = a.pytest_args.split() if a.pytest_args else []
    C = sh(["git", "rev-parse", a.commit], cwd=repo).stdout.strip()
    P = sh(["git", "rev-parse", a.commit + "^"], cwd=repo).stdout.strip()
    subj = sh(["git", "log", "-1", "--format=%s", C], cwd=repo).stdout.strip()
    date = sh(["git", "log", "-1", "--format=%cs", C], cwd=repo).stdout.strip()
    full = sh(["git", "diff", P, C], cwd=repo).stdout
    hunks = parse_diff(full)
    code_h = [h for h in hunks if not is_test_path(h.path) and not is_doc_path(h.path)]
    test_h = [h for h in hunks if is_test_path(h.path)]
    test_diff = compose(test_h)
    print(f"[{C[:7]}] {subj}\n  base={P[:7]} code hunks={len(code_h)} in {len({h.path for h in code_h})} files; test files={sorted({h.path for h in test_h})}")

    wt = Path(tempfile.mkdtemp(prefix=f"wt-{C[:7]}-"))
    sh(["git", "worktree", "add", "-q", "--detach", str(wt), P], cwd=repo)
    W = Work(repo, P, wt, a.py, pytest_args, env_extra, a.subdir)
    try:
        test_files = sorted({W.rel(h.path) for h in test_h if h.path.endswith(".py") and (not a.subdir or h.path.startswith(a.subdir))})
        if not test_files:
            print("  no python test changes; skipping"); return
        existing = [p for p in test_files if (W.cwd / p).exists()]
        before = W.collect(existing) if existing else set()
        W.apply(test_diff)
        after = W.collect(test_files)
        cands = sorted(after - before)
        print(f"  new tests: {len(cands)}")
        # 1) fail at base
        r0 = W.run(cands)
        if os.environ.get("MINE_DEBUG"): print("  r0:", r0)
        failing = [t for t in cands if r0.get(t) in ("failed", "error")]
        notfail = [t for t in cands if t not in failing]
        print(f"  fail at base: {len(failing)}; not failing (dropped): {notfail}")
        # 2) pass at base+code
        W.reset(); W.apply(test_diff); W.apply(compose(code_h))
        r1 = W.run(failing)
        keep = [t for t in failing if r1.get(t) == "passed"]
        print(f"  pass with gold: {len(keep)}; not passing (dropped, likely GPU/env): {[t for t in failing if t not in keep]}")
        needed = {t: set() for t in keep}
        if not a.skip_attribution and keep and len(code_h) > 1:
            for i, h in enumerate(code_h):
                W.reset(); W.apply(test_diff)
                try:
                    W.apply(compose([x for j, x in enumerate(code_h) if j != i]))
                except RuntimeError as e:
                    print(f"  hunk {i} {h.path}: cannot drop independently ({str(e)[:80]}); marking needed for all")
                    for t in keep: needed[t].add(i)
                    continue
                r = W.run(keep)
                dep = [t for t in keep if r.get(t) != "passed"]
                for t in dep: needed[t].add(i)
                print(f"  hunk {i:2d} {h.path:45s} needed by {len(dep)} tests", flush=True)
        else:
            for t in keep: needed[t] = set(range(len(code_h)))
        # 3) group + verify
        groups = defaultdict(list)
        for t, s in needed.items():
            groups[tuple(sorted(s))].append(t)
        tasks = []
        for k, (hs, tests) in enumerate(groups.items()):
            gold = compose([code_h[i] for i in hs]) if hs else ""
            W.reset(); W.apply(test_diff)
            if gold: W.apply(gold)
            runs = [W.run(tests) for _ in range(a.repeats)]
            ok_gold = all(all(r.get(t) == "passed" for t in tests) for r in runs)
            W.reset(); W.apply(test_diff)
            runs0 = [W.run(tests) for _ in range(a.repeats)]
            ok_base = all(all(r.get(t) != "passed" for t in tests) for r in runs0)
            files = sorted({code_h[i].path for i in hs})
            tasks.append(dict(
                task_id=f"{repo.name}__{C[:7]}-{k}", repo=repo.name, commit=C, base_commit=P, commit_date=date,
                commit_subject=subj, bundled=len(groups) > 1 or len(code_h) > len(hs),
                gold_hunks=list(hs), gold_files=files, gold_patch=gold,
                gold_lines=sum(1 for l in gold.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))),
                FAIL_TO_PASS=tests, test_patch=test_diff,
                validation=dict(base_fails=f"{sum(all(r.get(t)!='passed' for t in tests) for r in runs0)}/{a.repeats}",
                                gold_passes=f"{sum(all(r.get(t)=='passed' for t in tests) for r in runs)}/{a.repeats}",
                                valid=ok_gold and ok_base and bool(hs)),
            ))
            print(f"  task {k}: hunks={list(hs)} files={files} tests={len(tests)} base_fails={tasks[-1]['validation']['base_fails']} gold_passes={tasks[-1]['validation']['gold_passes']}")
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        (out / f"{repo.name}__{C[:7]}.json").write_text(json.dumps(dict(
            repo=repo.name, commit=C, base_commit=P, subject=subj, date=date,
            code_hunks=[dict(i=i, path=h.path, lines=sum(1 for l in h.text if l[:1] in "+-")) for i, h in enumerate(code_h)],
            new_tests=cands, dropped_not_failing=notfail, dropped_not_passing=[t for t in failing if t not in keep],
            tasks=tasks), indent=1))
    finally:
        sh(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=False)

if __name__ == "__main__":
    main()
