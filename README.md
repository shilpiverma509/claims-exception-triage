# Claims Exception Triage Assistant

A triage assistant for a claims-ops exception queue: it diagnoses why each
pended claim is stuck, ranks urgency, proposes a next action and an owner
queue, and routes low-confidence or contradictory cases to a human — with a
deterministic guard layer and an append-only audit log around every call.
See `PRD-spec.md` for the full problem brief and `LOW_LEVEL_PLAN.md` for the
technical execution plan and task breakdown.

## Submission documents

- **Runnable prototype:** this repo itself — no separate notebook. The triage
  logic runs as a CLI pipeline, `src/triage/pipeline.py` (`make triage`), and
  the review UI runs as a Streamlit app, `app/review_app.py` (`make demo`),
  served locally at **http://localhost:8501**. See Quickstart below for exact
  commands and a sample run.
- **Problem brief:** `docs/PROBLEM_BRIEF.md` (source) / `docs/PROBLEM_BRIEF.pdf` (submitted 1-page PDF)
- **AI evidence:** `docs/AI_EVIDENCE.md`
- **Enterprise readiness:** `docs/ENTERPRISE_READINESS.md`

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # only needed for --mode live
```

`TRIAGE_MODE` (in `.env` or the shell) selects the LLM path: `mock` (default,
no network, deterministic heuristic — safe for dev/CI), `cached` (replay a
prior live run from `outputs/llm_cache.jsonl`), or `live` (real Anthropic API
call; requires `ANTHROPIC_API_KEY`).

## Quickstart

```bash
make data              # generate the 75 seeded synthetic claims + mock source systems
make triage MODE=mock  # run the pipeline: ingest -> redact -> triage -> guard -> store
make eval               # score the run against dev labels; writes outputs/eval_report_*.md
make test               # pytest across redact/guard/pipeline/baseline/eval
make demo               # streamlit review UI
```

`make triage` accepts `INPUT`, `MODE` (`mock`|`cached`|`live`), and `PROMPT`
(`v1`|`v2`|`v3`) overrides, e.g.:

```bash
make triage INPUT=data/eval_claims.json MODE=mock PROMPT=v3
make eval INPUT=data/eval_claims.json PROMPT=v3
```

Run the rule-based baseline (quantifies the LLM's lift, see `src/triage/baseline.py`)
the same way:

```bash
make baseline
make eval-baseline
```

Compare prompt versions on the dev set directly:

```bash
python -m triage.evaluate --labels data/dev_labels.json --compare \
  v1=outputs/triage_dev_claims_mock_v1.json \
  v2=outputs/triage_dev_claims_mock_v2.json \
  v3=outputs/triage_dev_claims_mock_v3.json
```

> The sealed eval set (`data/eval_claims.json` / `eval_labels.json`) is meant
> to be scored exactly once, after the prompt is frozen on the dev set — see
> `PRD-spec.md` §6/§11.

## Project layout

See "Application structure" in `LOW_LEVEL_PLAN.md` for the annotated tree and
`src/triage/config.py` for every threshold/band/team (single source of truth
— no other module hardcodes one).

## Tests

```bash
make test
```

Covers PHI redaction, guard invariants (can only raise urgency / force
review, never relax), pipeline survival on malformed input, baseline scoring,
and the eval metrics themselves.

## Demo app

```bash
make demo
```

Opens a Streamlit ranked queue (colored by urgency band) over the most
recent `outputs/triage_*.json` run. Each claim expands to its summary,
evidence, redaction notice (if any), and guard adjustments, with
Approve/Reject/Reassign buttons that append to `outputs/audit.jsonl` — the
app never executes an action itself. A second tab shows the full audit
trail.
