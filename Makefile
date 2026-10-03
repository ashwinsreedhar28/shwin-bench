# shwin-bench pilot. Needs Docker running and ANTHROPIC_API_KEY in the environment.
PY ?= python3
MODEL ?= anthropic/claude-haiku-4-5
PLATFORM ?=            # set PLATFORM=--platform linux/arm64 on Apple silicon if builds pick the wrong arch

setup:            ## venv with mini-swe-agent
	$(PY) -m venv .venv && . .venv/bin/activate && pip install -q mini-swe-agent pyyaml

images:           ## build every task image (shared dep layer per repo; ~10 min first time)
	. .venv/bin/activate && $(PY) harness/build_images.py $(PLATFORM)

images-pilot:     ## images for the 10 pilot tasks only
	. .venv/bin/activate && $(PY) harness/build_images.py --ids $(PILOT) $(PLATFORM)

verify:           ## re-run the 3/3 base-fail / gold-pass check inside the images
	. .venv/bin/activate && $(PY) harness/verify_images.py --split main,seed-data,own-older

PILOT = emberserve__7edbe0f-4 emberserve__7edbe0f-5 emberserve__4e0b5f0-2 emberserve__36f4e93-2 emberserve__fa672f0-0 \
        emberserve__7edbe0f-3 emberserve__4e0b5f0-0 emberserve__36f4e93-4 serverless-lakehouse__896803f-0 emberserve__b64847c-0

pilot:            ## 10 tasks (5 easy, 5 medium, no interface leak), precise statements, Haiku, stop at $5
	. .venv/bin/activate && $(PY) harness/run.py --model $(MODEL) --statement precise --split main,seed-data --ids $(PILOT) --cost-cap 5 --workers 2

dataset:          ## rebuild dataset/tasks.jsonl from mined/ + statements
	$(PY) tools/build_dataset.py

.PHONY: setup images images-pilot verify pilot dataset
