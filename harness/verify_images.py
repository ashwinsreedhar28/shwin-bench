#!/usr/bin/env python3
"""Re-validate every task inside its Docker image before any model run:
   base + test_patch  -> every FAIL_TO_PASS test fails          (N repeats)
   base + test_patch + gold -> every FAIL_TO_PASS passes, PASS_TO_PASS still passes
Writes runs/verify.json. Anything not clean is listed at the end; fix or drop it before running agents."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import load_tasks, image_name, docker_run, parse_pytest, WORKDIR, TEST_CMD, ROOT

ap = argparse.ArgumentParser()
ap.add_argument("--split"); ap.add_argument("--repo"); ap.add_argument("--ids", nargs="*")
ap.add_argument("--repeats", type=int, default=3)
a = ap.parse_args()
out = ROOT / "runs"; out.mkdir(exist_ok=True)
results, bad = {}, []
for t in load_tasks(a.split, a.ids, a.repo):
    repo, wd = t["repo_name"], WORKDIR[t["repo_name"]]
    f2p = " ".join(f"'{x}'" for x in t["FAIL_TO_PASS"])
    base_ok, gold_ok = 0, 0
    for _ in range(a.repeats):
        _, o = docker_run(image_name(t), f"cd /testbed && git apply --whitespace=nowarn /tmp/test.patch && cd {wd} && {TEST_CMD[repo]} {f2p} || true",
                          wd, files={"/tmp/test.patch": t["test_patch"]}, timeout=900)
        p, f = parse_pytest(o)
        base_ok += all(x not in p for x in t["FAIL_TO_PASS"])
        _, o = docker_run(image_name(t), f"cd /testbed && git apply --whitespace=nowarn /tmp/test.patch && git apply --whitespace=nowarn /tmp/gold.patch && cd {wd} && {TEST_CMD[repo]} {f2p} || true",
                          wd, files={"/tmp/test.patch": t["test_patch"], "/tmp/gold.patch": t["patch"]}, timeout=900)
        p, f = parse_pytest(o)
        gold_ok += all(x in p for x in t["FAIL_TO_PASS"])
    # full suite with gold once, for PASS_TO_PASS
    _, o = docker_run(image_name(t), f"cd /testbed && git apply --whitespace=nowarn /tmp/test.patch && git apply --whitespace=nowarn /tmp/gold.patch && cd {wd} && {TEST_CMD[repo]} || true",
                      wd, files={"/tmp/test.patch": t["test_patch"], "/tmp/gold.patch": t["patch"]}, timeout=1800)
    p, f = parse_pytest(o)
    p2p_broken = [x for x in t["PASS_TO_PASS"] if x not in p]
    r = dict(base_fails=f"{base_ok}/{a.repeats}", gold_passes=f"{gold_ok}/{a.repeats}", p2p_broken=p2p_broken)
    clean = base_ok == a.repeats and gold_ok == a.repeats and not p2p_broken
    results[t["instance_id"]] = r
    print(("OK  " if clean else "BAD ") + t["instance_id"], r, flush=True)
    if not clean: bad.append(t["instance_id"])
(out / "verify.json").write_text(json.dumps(results, indent=1))
print(f"\n{len(results) - len(bad)}/{len(results)} clean. Not clean: {bad}")
