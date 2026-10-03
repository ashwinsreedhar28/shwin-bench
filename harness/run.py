#!/usr/bin/env python3
"""Run mini-swe-agent on shwin-bench tasks inside their Docker images (no network), then grade.

  export ANTHROPIC_API_KEY=...
  python harness/run.py --model anthropic/claude-haiku-4-5 --statement precise --split main --limit 10 --cost-cap 5
  python harness/run.py --model anthropic/claude-sonnet-5-5 --statement vague --split main --workers 3

Per task: runs/<run>/<instance_id>/{traj.json,patch.diff,grade.json}; runs/<run>/results.jsonl; summary printed.
The run stops when the summed model cost passes --cost-cap (default $5): that is the spend gate.
"""
import argparse, json, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).parent))
from common import load_tasks, image_name, grade, WORKDIR, ROOT

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.docker import DockerEnvironment
from minisweagent.models.litellm_model import LitellmModel

CFG = yaml.safe_load((ROOT / "harness" / "shwin.yaml").read_text())
SPENT = 0.0


def run_one(task, a, run_dir):
    global SPENT
    tdir = run_dir / task["instance_id"]; tdir.mkdir(parents=True, exist_ok=True)
    statement = task[f"problem_statement_{a.statement}"]
    env_cfg = dict(CFG["environment"], image=image_name(task), cwd=WORKDIR[task["repo_name"]])
    env_cfg["run_args"] = list(env_cfg.get("run_args", ["--rm"])) + ["--network", "none", f"--cpus={a.cpus}", f"--memory={a.memory}"]
    model = LitellmModel(model_name=a.model, **CFG.get("model", {}))
    env = DockerEnvironment(**env_cfg)
    agent_cfg = dict(CFG["agent"], step_limit=a.step_limit, cost_limit=a.task_cost_limit, output_path=str(tdir / "traj.json"))
    agent = DefaultAgent(model, env, **agent_cfg)
    t0 = time.time()
    try:
        info = agent.run(statement, instance_id=task["instance_id"], repo=task["repo_name"], workdir=WORKDIR[task["repo_name"]])
    except Exception as e:
        info = {"exit_status": f"Error: {type(e).__name__}", "submission": "", "error": traceback.format_exc()[-4000:]}
        (tdir / "error.txt").write_text(info["error"])
        print(f"{task['instance_id']}: agent error {type(e).__name__}: {str(e)[:300]}  (full trace in {tdir / 'error.txt'})", flush=True)
    patch = info.get("submission") or ""
    salvaged = False
    if not patch.strip():
        # Ran out of steps (or crashed) before submitting: take whatever it changed in tracked, non-test files.
        try:
            r = env.execute({"command": "cd /testbed && git diff -- . ':(exclude)tests' ':(exclude)**/tests/**' ':(exclude)**/test_*.py' ':(exclude)*.patch' ':(exclude)*.txt'"})
            out = r.get("output", "")
            if out.lstrip().startswith("diff --git"):
                patch, salvaged = out, True
        except Exception:
            pass
    (tdir / "patch.diff").write_text(patch)
    try: env.cleanup()
    except Exception: pass
    cost = float(getattr(agent, "cost", 0.0) or 0.0); SPENT += cost
    g = grade(task, patch) if patch.strip() else dict(status="NO_PATCH", resolved=False, f2p_passed="0/%d" % len(task["FAIL_TO_PASS"]), p2p_broken=[], p2p_broken_count=0, tests_seen=0, log_tail="")
    rec = dict(instance_id=task["instance_id"], split=task["split"], repo=task["repo_name"], statement=a.statement, model=a.model,
               category=task["meta"]["category"], difficulty=task["meta"]["difficulty"], interface_leak=task["meta"]["interface_leak"],
               exit_status=info.get("exit_status"), salvaged=salvaged, steps=getattr(agent, "n_calls", None), cost_usd=round(cost, 4),
               agent_seconds=round(time.time() - t0), **{k: v for k, v in g.items() if k != "log_tail"})
    (tdir / "grade_log.txt").write_text(g.pop("log", ""))
    (tdir / "grade.json").write_text(json.dumps(g, indent=1))
    with (run_dir / "results.jsonl").open("a") as f: f.write(json.dumps(rec) + "\n")
    print(f"{rec['instance_id']:40s} {g['status']:12s}{' (salvaged)' if salvaged else ''} f2p={g['f2p_passed']} p2p_broken={g['p2p_broken_count']} steps={rec['steps']} ${cost:.3f} total=${SPENT:.2f}", flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="litellm name, e.g. anthropic/claude-haiku-4-5")
    ap.add_argument("--statement", choices=["vague", "precise"], default="precise")
    ap.add_argument("--split", default="main"); ap.add_argument("--repo"); ap.add_argument("--ids", nargs="*")
    ap.add_argument("--limit", type=int); ap.add_argument("--difficulty")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--step-limit", type=int, default=75)
    ap.add_argument("--task-cost-limit", type=float, default=1.5, help="per-task $ cap inside the agent")
    ap.add_argument("--cost-cap", type=float, default=5.0, help="stop launching new tasks once total spend passes this")
    ap.add_argument("--cpus", default="2"); ap.add_argument("--memory", default="4g")
    ap.add_argument("--run-name")
    a = ap.parse_args()
    import os
    if a.model.startswith("anthropic/") and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set in this shell (export it, or put it in ~/Library/Application Support/mini-swe-agent/.env)")
    tasks = load_tasks(a.split, a.ids, a.repo)
    if a.difficulty: tasks = [t for t in tasks if t["meta"]["difficulty"] == a.difficulty]
    if a.statement == "vague": tasks = [t for t in tasks if not t["meta"]["interface_leak"]]
    if a.limit: tasks = tasks[: a.limit]
    run_dir = ROOT / "runs" / (a.run_name or f"{time.strftime('%Y%m%d-%H%M')}_{a.model.split('/')[-1]}_{a.statement}_{a.split}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "args.json").write_text(json.dumps(vars(a), indent=1))
    print(f"{len(tasks)} tasks -> {run_dir}  (cost cap ${a.cost_cap})")
    recs = []
    with ThreadPoolExecutor(a.workers) as ex:
        futs = []
        for t in tasks:
            if SPENT >= a.cost_cap:
                print(f"cost cap ${a.cost_cap} reached; not launching {t['instance_id']}"); continue
            futs.append(ex.submit(run_one, t, a, run_dir))
        for f in as_completed(futs): recs.append(f.result())
    n = len(recs); solved = sum(r["resolved"] for r in recs)
    print(f"\n{solved}/{n} resolved  ({a.model}, {a.statement}, {a.split})  spend ${SPENT:.2f}  mean ${SPENT/max(n,1):.3f}/task")
    for k in ("difficulty", "category", "repo"):
        groups = {}
        for r in recs: groups.setdefault(r[k], []).append(r["resolved"])
        print(f"  by {k}: " + ", ".join(f"{g} {sum(v)}/{len(v)}" for g, v in sorted(groups.items())))


if __name__ == "__main__":
    main()
