#!/usr/bin/env python3
"""Build one Docker image per task (shared dependency layer per repo, thin checkout layer per base commit).

  python harness/build_images.py                 # all tasks
  python harness/build_images.py --split main    # one split
  python harness/build_images.py --ids emberserve__7edbe0f-2
"""
import argparse, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import load_tasks, image_name, ROOT, DOCKER

ap = argparse.ArgumentParser()
ap.add_argument("--split"); ap.add_argument("--repo"); ap.add_argument("--ids", nargs="*")
ap.add_argument("--platform", default=None, help="e.g. linux/arm64 on Apple silicon (default: host)")
a = ap.parse_args()
tasks = load_tasks(a.split, a.ids, a.repo)
seen = set()
for t in tasks:
    img = image_name(t)
    if img in seen: continue
    seen.add(img)
    df = ROOT / "harness" / "docker" / f"Dockerfile.{t['repo_name']}"
    cmd = [DOCKER, "build", "-f", str(df), "--build-arg", f"BASE_COMMIT={t['base_commit']}", "-t", img, str(df.parent)]
    if a.platform: cmd[2:2] = ["--platform", a.platform]
    print("building", img, flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:]); sys.exit(f"build failed: {img}")
print(f"{len(seen)} images built")
