PYTHONPATH := src
export PYTHONPATH

PY := python3
MODE ?= mock
PROMPT ?= v1
INPUT ?= data/dev_claims.json
LABELS ?= data/dev_labels.json
RUN_NAME := triage_$(basename $(notdir $(INPUT)))_$(MODE)_$(PROMPT)

.PHONY: install data run triage baseline eval eval-baseline feedback test demo clean

install:
	pip install -r requirements.txt

data:
	$(PY) -m triage.generate_data
	$(PY) -m triage.generate_mock_systems

triage run:
	$(PY) -m triage.pipeline --input $(INPUT) --mode $(MODE) --prompt $(PROMPT)

baseline:
	$(PY) -m triage.baseline --input $(INPUT)

eval:
	$(PY) -m triage.evaluate --run outputs/$(RUN_NAME).json --labels $(LABELS)

eval-baseline:
	$(PY) -m triage.evaluate --run outputs/triage_$(basename $(notdir $(INPUT)))_baseline.json --labels $(LABELS)

# Turn analyst decisions in the audit log into regression cases, a confidence
# calibration table, and prompt-improvement material.
feedback:
	$(PY) -m triage.feedback --run outputs/$(RUN_NAME).json --report

test:
	pytest -q

demo:
	streamlit run app/review_app.py

clean:
	rm -f outputs/*.json outputs/*.png outputs/*.md outputs/*.jsonl
