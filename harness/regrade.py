#!/usr/bin/env python3
"""Re-grade an existing run from its saved patch.diff files (no model calls).
   python harness/regrade.py runs/<run_dir>            -> rewrites grade.json and results.jsonl in place"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import load_tasks, grade

run_dir = Path(sys.argv[1])
tasks = {t["instance_id"]: t for t in load_tasks()}
rows = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
out = []
for r in rows:
    t = tasks[r["instance_id"]]; patch = (run_dir / r["instance_id"] / "patch.diff").read_text()
    g = grade(t, patch) if patch.strip() else dict(status="NO_PATCH", resolved=False, f2p_passed="0/%d" % len(t["FAIL_TO_PASS"]), p2p_broken=[], p2p_broken_count=0, tests_seen=0, log_tail="")
    (run_dir / r["instance_id"] / "grade.json").write_text(json.dumps(g, indent=1))
    r.update({k: v for k, v in g.items() if k != "log_tail"}); out.append(r)
    print(f"{r['instance_id']:40s} {g['status']:12s} f2p={g['f2p_passed']} p2p_broken={g['p2p_broken_count']} tests_seen={g['tests_seen']}", flush=True)
(run_dir / "results.jsonl").write_text("\n".join(json.dumps(r) for r in out) + "\n")
n = len(out); print(f"\n{sum(r['resolved'] for r in out)}/{n} resolved")
