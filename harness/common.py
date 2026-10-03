"""Shared helpers: load tasks, image names, test commands, grading."""
import json, re, subprocess, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "dataset" / "tasks.jsonl"

# where pytest runs inside the image, per repo (matches the Dockerfiles' WORKDIR)
WORKDIR = {"emberserve": "/testbed", "serverless-lakehouse": "/testbed", "aether": "/testbed/daemons/raven-core"}
# -rA so every test prints PASSED/FAILED; -p no:cacheprovider so the agent's .pytest_cache can't matter
TEST_CMD = {
    "emberserve": "python -m pytest -q -o addopts= -p no:cacheprovider -W ignore --timeout=600 -rA --tb=short -m 'not gpu and not hf'",
    "serverless-lakehouse": "python -m pytest -q -o addopts= -p no:cacheprovider -W ignore --timeout=600 -rA --tb=short -m 'not slow'",
    "aether": "python -m pytest -q -o addopts= -p no:cacheprovider -W ignore --timeout=600 -rA --tb=short",
}


def load_tasks(split=None, ids=None, repo=None):
    rows = [json.loads(l) for l in TASKS.read_text().splitlines() if l.strip()]
    if split: rows = [r for r in rows if r["split"] in split.split(",")]
    if repo: rows = [r for r in rows if r["repo_name"] == repo]
    if ids: rows = [r for r in rows if r["instance_id"] in ids]
    return rows


def image_name(task):
    return f"shwin/{task['repo_name']}:{task['base_commit'][:12]}"


def parse_pytest(out):
    passed, failed = set(), set()
    for l in out.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR) (\S+)", l)
        if m: (passed if m.group(1) == "PASSED" else failed).add(m.group(2))
    return passed, failed


def docker_run(image, script, workdir, timeout=1800, network="none", files=None, cpus="2", memory="4g"):
    """Run a bash script in a fresh container. `files` = {container_path: text} copied in before the script."""
    name = f"shwin-{int(time.time()*1000)%10**9}"
    cmd = ["docker", "create", "--name", name, "--network", network, f"--cpus={cpus}", f"--memory={memory}", "-w", workdir, image, "bash", "-lc", script]
    subprocess.run(cmd, check=True, capture_output=True)
    try:
        for path, text in (files or {}).items():
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".patch") as f:
                f.write(text); f.flush()
            subprocess.run(["docker", "cp", f.name, f"{name}:{path}"], check=True, capture_output=True)
        r = subprocess.run(["docker", "start", "-a", name], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        return 124, "TIMEOUT"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def grade(task, model_patch, timeout=1800):
    """Apply the agent's patch and the hidden test patch in a fresh container, run the suite, judge."""
    repo = task["repo_name"]
    script = (
        "set -e; cd /testbed; git apply --whitespace=nowarn /tmp/model.patch || { echo SHWIN_PATCH_FAILED; exit 3; }; "
        "git apply --whitespace=nowarn /tmp/test.patch || { echo SHWIN_TEST_PATCH_FAILED; exit 4; }; "
        f"cd {WORKDIR[repo]}; {TEST_CMD[repo]} || true"
    )
    rc, out = docker_run(task and image_name(task), script, WORKDIR[repo], timeout=timeout,
                         files={"/tmp/model.patch": model_patch or "", "/tmp/test.patch": task["test_patch"]})
    passed, failed = parse_pytest(out)
    f2p = task["FAIL_TO_PASS"]; p2p = task["PASS_TO_PASS"]
    f2p_ok = [t for t in f2p if t in passed]
    p2p_broken = [t for t in p2p if t not in passed]
    status = ("PATCH_FAILED" if "SHWIN_PATCH_FAILED" in out else "TEST_PATCH_FAILED" if "SHWIN_TEST_PATCH_FAILED" in out
              else "TIMEOUT" if rc == 124 else "RESOLVED" if len(f2p_ok) == len(f2p) and not p2p_broken else "UNRESOLVED")
    return dict(status=status, resolved=status == "RESOLVED", f2p_passed=f"{len(f2p_ok)}/{len(f2p)}",
                p2p_broken=p2p_broken[:20], p2p_broken_count=len(p2p_broken), tests_seen=len(passed) + len(failed), log_tail=out[-3000:])
