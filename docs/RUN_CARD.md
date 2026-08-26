# Run Card — the five commands, and what to say while they run

Print this. It is the only thing you need in front of you for the terminal part of the demo.

## The mnemonic

> **Generate · Run · Score · Prove · Show**
> `data` → `triage` → `eval` → `test` → `demo`

They are in pipeline order. If you forget one, ask yourself which step of that sentence is missing.

## The five commands

| # | Command | What it does | What it writes |
|---|---|---|---|
| 1 | `make data` | Generates 75 seeded synthetic claims + 3 mock source systems | `data/dev_claims.json`, `data/eval_claims.json`, `data/mock_systems/` |
| 2 | `make triage` | ingest → redact → evidence lookup → LLM → guard → store | `outputs/triage_<input>_<mode>_<prompt>.json` |
| 3 | `make eval` | Scores that run against its label file | `outputs/eval_report_*.md`, `outputs/confusion_matrix_*.png` |
| 4 | `make test` | pytest: PHI redaction, guard invariants, malformed-record survival | pass/fail — green, or the claim isn't true |
| 5 | `make demo` | Streamlit review UI | `localhost:8501` |

**Three knobs, same five commands:**

```bash
make triage INPUT=data/eval_claims.json MODE=live PROMPT=v3
```

- `INPUT` — `data/dev_claims.json` (the 50 you tuned on) or `data/eval_claims.json` (the sealed 25)
- `MODE` — `mock` (no network, deterministic) · `cached` (replay a saved live run) · `live` (real API call)
- `PROMPT` — `v1` · `v2` · `v3`

The run file's name is built from those three, which is why `outputs/` reads like a lab notebook rather than a folder of `results_final_2.json`.

## Two extras worth having ready

```bash
make baseline && make eval-baseline      # the rule-based strawman, scored by the same scorer

python -m triage.evaluate --labels data/dev_labels.json --compare \
  baseline=outputs/triage_dev_claims_baseline.json \
  v1=outputs/triage_dev_claims_live_v1.json \
  v3=outputs/triage_dev_claims_live_v3.json
```

The `--compare` one is your strongest live moment: baseline, v1 and v3 in a single table, on screen, while you say the saturation line.

## What to say while it runs (~25 seconds)

> "Five commands, in pipeline order. `make data` generates the claims — seeded, so anyone cloning this gets the identical queue. `make triage` runs the pipeline. `make eval` scores it against the labels and writes the report I just showed you. `make test` is the one that matters most to me: the guard invariants live there, so if someone breaks 'the guard can only escalate', the build fails. `make demo` is this UI.
>
> Three knobs: which input — dev set or sealed set; which mode — mock, cached, or live; which prompt version. Mock needs no network at all, so this whole demo replays offline and nothing can time out on me."

## If a command fails on camera

Don't debug live. Say: *"That's the live path — let me show you the saved run instead"*, and open `outputs/eval_report_triage_dev_claims_live_v3.json`/`.md`. Every number in the demo comes from saved files, so nothing depends on a command succeeding.

## Setup you must have done before recording

```bash
source .venv/bin/activate
make demo        # leave running in its own window
```

`PYTHONPATH` is set inside the Makefile, so `make` works from the project root and bare `python -m triage.*` needs `PYTHONPATH=src`. Use `make` on camera — fewer ways to trip.
