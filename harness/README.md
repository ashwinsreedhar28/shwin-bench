# Harness

mini-swe-agent (v2, bash-only agent) inside one Docker image per task, no network, graded by the hidden
tests. Nothing here has run yet against a model; the pilot is the first run.

## Pieces

| File | Does |
| --- | --- |
| `docker/Dockerfile.<repo>` | shared dependency layer per repo (torch CPU, pytest, ...), then `git checkout <base_commit>` as a thin layer. The gold and test patches are never in the image. |
| `build_images.py` | one `docker build` per distinct base commit, tagged `shwin/<repo>:<commit12>` |
| `verify_images.py` | repeats the dataset validation inside the images: FAIL_TO_PASS fails 3/3 at base, passes 3/3 with gold, PASS_TO_PASS intact. Run before any model run. |
| `shwin.yaml` | agent config, derived from mini-swe-agent's stock `swebench.yaml` (same prompt, same submit protocol, 30-step cap, "no network" sentence added) |
| `run.py` | runs the agent on tasks, writes trajectory + patch, grades in a fresh container, appends `results.jsonl`, stops launching tasks once total spend passes `--cost-cap` |
| `common.py` | task loading, image naming, `docker run` helper, pytest parsing, the grade rule |

Grade rule (`common.grade`): fresh container from the task image, `git apply` the agent's patch (fail = `PATCH_FAILED`),
`git apply` the hidden test patch, run the repo's test command with `-rA`, parse `PASSED`/`FAILED` lines.
`RESOLVED` only when every FAIL_TO_PASS test passed and every PASS_TO_PASS test still passed.

## Pilot

```
make setup                 # venv + mini-swe-agent
make images-pilot          # 10 task images, ~10 min the first time (torch wheel), seconds after
make verify                # optional but recommended: 3/3 checks inside the images, all 42 tasks (~1 h)
export ANTHROPIC_API_KEY=sk-ant-...
make pilot                 # 10 tasks, Haiku 4.5, precise statements, 2 at a time, hard stop at $5
```

Pilot set: 10 leak-free main tasks (4 easy, 6 medium) across logic, resource-accounting, config/cli,
numerics/kernel, data-handling, error-handling. Output in `runs/<timestamp>_<model>_precise_main,seed-data/`:
`results.jsonl` (one line per task: status, f2p, p2p_broken_count, steps, cost) and per task `traj.json`,
`patch.diff`, `grade.json`. The number to look at first is mean `cost_usd` per task; it sets the budget
for the full matrix (42 own tasks x 2 statements per model, plus the control set).

Apple silicon: the Dockerfiles are arch-neutral; if Docker Desktop picks the wrong platform pass
`PLATFORM="--platform linux/arm64"`.

## Full matrix (after the pilot, after the go)

```
python harness/run.py --model anthropic/claude-haiku-4-5  --statement precise --split main,seed-data,own-older --cost-cap 30 --workers 3
python harness/run.py --model anthropic/claude-haiku-4-5  --statement vague   --split main,seed-data,own-older --cost-cap 30 --workers 3
python harness/run.py --model anthropic/claude-sonnet-5-5 --statement precise --split main,seed-data,own-older --cost-cap 60 --workers 3
...
```

`--statement vague` automatically drops the `interface_leak` tasks (unsolvable without the interface name).
Model names are litellm names; any provider litellm supports works the same way (`openrouter/...`).

## Control set

SWE-bench Verified `<15 min fix` instances run through the same `run.py` once an adapter maps a Verified
row to this task format and its `image_name` to the official `swebench/sweb.eval.x86_64.<id>` image.
Not written yet; see the day-1 report for the candidate list and the memorization probe.
