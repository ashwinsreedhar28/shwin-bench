# shwin-bench

A SWE-bench-style benchmark for coding agents, built from real bug fixes in my own repos
([emberserve](https://github.com/ashwinsreedhar28/emberserve), an LLM inference engine;
[serverless-lakehouse](https://github.com/ashwinsreedhar28/serverless-lakehouse), a PySpark pipeline;
[aether](https://github.com/ashwinsreedhar28/aether), a desktop agent).

The point: SWE-bench tasks come from popular repos whose fixes have been public for years, so a model
may be remembering the answer. These fixes landed Sep 27 to Oct 2, 2026, in repos created days earlier.
Nothing here is in any current model's training data. A control set of old, public SWE-bench Verified
tasks runs through the same harness, so the gap between fresh and memorizable solve rates is measurable.

## Tasks

42 tasks from 15 fix commits, every one validated: the hidden test fails 3/3 runs at the base commit,
passes 3/3 with the real fix, and the fix breaks nothing else in the suite.

| Split | Tasks | Source | Fix dates |
| --- | --- | --- | --- |
| `main` (fresh, code) | 29 | emberserve 25, serverless-lakehouse 4 | 2026-09-27 to 2026-10-02 |
| `seed-data` (fresh, CSV corrections) | 3 | serverless-lakehouse | 2026-10-02 |
| `own-older` (own repo, obscure, pre-cutoff) | 10 | aether | 2026-06 to 2026-07 |

Each task carries two problem statements: `vague` (how a user would report it, no file or function
names) and `precise` (a good GitHub issue: observed vs expected, module, reproduction). Neither hints
at the fix. 15 tasks are flagged `interface_leak`: the hidden test imports a name the fix introduced,
so only the precise statement can be solved; report the vague split over the other 27.

All tasks run on CPU with no network; the emberserve suite is 422 tests in about 2 minutes.

## Format

`dataset/tasks.jsonl`, one JSON object per task, SWE-bench field names where the meaning matches:
`instance_id`, `repo`, `base_commit`, `patch` (gold), `test_patch` (hidden), `FAIL_TO_PASS`,
`PASS_TO_PASS`, `hints_text` (empty), plus `problem_statement_vague`, `problem_statement_precise`,
`split`, `created_at`, `fix_commit`, `environment` (install and test commands) and `meta`
(category, difficulty, interface_leak, gold file list and line count, validation record).

Grading: apply the agent's patch to `base_commit`, apply `test_patch`, run `environment.test_cmd`.
Solved when every `FAIL_TO_PASS` test passes and every `PASS_TO_PASS` test still passes.

## How the tasks were made

Fix commits in these repos were bundled (one review commit fixed 16 defects), so `tools/mine.py`
splits them by hunk attribution: apply the commit's test diff to the parent, collect the new tests,
keep those that fail at the parent and pass with the full fix, then remove one code hunk at a time
and see which tests break. A test's gold patch is exactly the hunks it needs; tests with the same
hunk set become one task. `tools/p2p.py` computes PASS_TO_PASS from two full-suite runs at
base+test_patch and one at base+test_patch+gold, and rejects a split whose gold breaks the suite.
`tools/build_dataset.py` assembles `dataset/tasks.jsonl` from `mined/*.json` and
`dataset/statements.json`.

```
# emberserve example (package was named pagedserve at these commits)
python3 -m venv .venv && . .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e '/path/to/emberserve[dev,server,hf]'
export PYTEST_ADDOPTS='-m "not gpu and not hf"'
python3 tools/mine.py /path/to/emberserve 7edbe0f --py .venv/bin/python --out mined
python3 tools/p2p.py mined/emberserve__7edbe0f.json --py .venv/bin/python --repos-dir /path/to
python3 tools/build_dataset.py
```

serverless-lakehouse: `PYTEST_ADDOPTS='-m "not slow"'` (the Spark tests need Hugging Face).
aether: `--subdir daemons/raven-core --env PYTHONPATH=<dir with a stub pyaudio.py>`.

## Known weaknesses

Problem statements are reconstructions (no issues existed), written from the patch and test and then
screened for hints. Fix and test were usually written together, so tests pin implementation details,
which is where the interface leaks come from. Tasks split from one commit are not independent; 42
tasks come from 15 commits, so report per-commit as well. Two timing tests are excluded as flaky.
N is small: with 29 fresh tasks a solve-rate difference under about 15 points is noise. GPU-only
bugs (the most interesting ones) are out by the CPU rule.

## Status

Dataset validated. Harness (mini-swe-agent + Docker, `--network none`) and model runs: in progress,
see `harness/README.md` (`make setup see `harness/`.see `harness/`. make images-pilot see `harness/`.see `harness/`. make pilot`).
