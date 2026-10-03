#!/usr/bin/env python3
"""Assemble mined/*.json into dataset/tasks.jsonl (SWE-bench-compatible fields + extras)
and a drafting bundle per task for problem statements."""
import json, glob, sys
from pathlib import Path
S = Path(__file__).resolve().parent.parent   # repo root: mined/, dataset/
REPO_URL = {"emberserve": "https://github.com/ashwinsreedhar28/emberserve",
            "serverless-lakehouse": "https://github.com/ashwinsreedhar28/serverless-lakehouse",
            "aether": "https://github.com/ashwinsreedhar28/aether"}
ENV = {
 "emberserve": dict(python="3.12", install="pip install -e '.[dev,server,hf]'  # torch CPU wheel",
                    test_cmd="python -m pytest -o addopts= -p no:cacheprovider -W ignore -m 'not gpu and not hf' -rA --tb=no", test_dir="tests", subdir=""),
 "serverless-lakehouse": dict(python="3.12", install="pip install -r requirements.txt pytest-timeout  # + Java 17 for Spark (slow tests excluded)",
                    test_cmd="python -m pytest -o addopts= -p no:cacheprovider -W ignore -m 'not slow' -rA --tb=no", test_dir="tests", subdir=""),
 "aether": dict(python="3.12", install="pip install pytest numpy aiohttp requests pillow mss opencv-python-headless google-genai; stub pyaudio",
                    test_cmd="python -m pytest -o addopts= -p no:cacheprovider -W ignore -rA --tb=no", test_dir="tests", subdir="daemons/raven-core"),
}
statements = json.loads((S / "dataset" / "statements.json").read_text())
KNOWN_FLAKY = {"tests/test_steptrace.py::test_report_decomposes_a_cpu_trace",   # timing ratio, fails under CPU contention
               "tests/test_bench.py::test_http_load_generator_real_stream"}      # timing ratio, failed once at HEAD
rows, drafts = [], []
for f in sorted(glob.glob(str(S / "mined" / "*.json"))):
    d = json.loads(Path(f).read_text())
    for t in d["tasks"]:
        if not t["validation"]["valid"]: continue
        t["broken_by_gold"] = [x for x in t.get("broken_by_gold", []) if x not in KNOWN_FLAKY]
        t["PASS_TO_PASS"] = [x for x in t.get("PASS_TO_PASS", []) if x not in KNOWN_FLAKY]
        if t.get("broken_by_gold"): print("DROP (gold breaks suite):", t["task_id"], t["broken_by_gold"]); continue
        if "PASS_TO_PASS" not in t: print("WARN no P2P yet:", t["task_id"])
        st = statements.get(t["task_id"], {})
        env = dict(ENV[t["repo"]])
        if t["repo"] == "aether" and any(p.startswith("core/node_sdk") for p in t["gold_files"]):
            env["subdir"] = "core/node_sdk"; env["test_dir"] = "."
        row = dict(
            instance_id=t["task_id"], repo=REPO_URL[t["repo"]], repo_name=t["repo"],
            base_commit=t["base_commit"], fix_commit=t["commit"], created_at=t["commit_date"],
            split=("seed-data" if st.get("category") == "seed-data" else "main" if t["commit_date"] >= "2026-09-01" else "own-older"),
            problem_statement_vague=st.get("vague", ""), problem_statement_precise=st.get("precise", ""),
            hints_text="", patch=t["gold_patch"], test_patch=t["test_patch"],
            FAIL_TO_PASS=t["FAIL_TO_PASS"], PASS_TO_PASS=t.get("PASS_TO_PASS", []),
            environment=env,
            meta=dict(commit_subject=t["commit_subject"], bundled_commit=t["bundled"], gold_files=t["gold_files"],
                      gold_changed_lines=t["gold_lines"], validation=t["validation"],
                      broken_by_gold=t.get("broken_by_gold", []), unstable_tests_excluded=sorted(set(t.get("unstable_at_base", [])) | (KNOWN_FLAKY if t["repo"] == "emberserve" else set())),
                      category=st.get("category", ""), difficulty=st.get("difficulty", ""), interface_leak=st.get("interface_leak", False)),
        )
        rows.append(row)
        drafts.append(dict(task_id=t["task_id"], repo=t["repo"], subject=t["commit_subject"], tests=t["FAIL_TO_PASS"],
                           gold_patch=t["gold_patch"], test_patch_excerpt=t["test_patch"][:6000]))
out = S / "dataset"; out.mkdir(exist_ok=True)
(out / "tasks.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
from collections import Counter
print(len(rows), "tasks;", dict(Counter(r["split"] for r in rows)))
