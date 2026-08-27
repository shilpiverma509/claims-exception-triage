# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A triage assistant for a claims-ops exception queue: diagnoses why each pended
claim is stuck, ranks urgency, proposes a next action and owner queue, and
routes low-confidence or contradictory cases to a human. Built around a
deterministic guard layer and an append-only audit log around every LLM call.
`PRD-spec.md` is the full problem brief; `LOW_LEVEL_PLAN.md` (mirrored at
`docs/LOW_LEVEL_PLAN.md`) is the technical execution plan and task breakdown
(T1–T20); `DECISION_LOG.md` records why each design choice was made.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # only needed for --mode live
```

`TRIAGE_MODE` env var selects the LLM path: `mock` (default, no network,
deterministic heuristic), `cached` (replay a prior live run from
`outputs/llm_cache.jsonl`), or `live` (real Anthropic API call, needs
`ANTHROPIC_API_KEY`).

## Commands

```bash
make data                          # generate 75 seeded synthetic claims + mock source systems
make triage MODE=mock              # full pipeline: ingest -> redact -> triage -> guard -> store
make triage MODE=live PROMPT=v3    # real Claude calls; INPUT/MODE/PROMPT all overridable
make eval                          # score a run against dev labels -> outputs/eval_report_*.md
make baseline / make eval-baseline # rule-based baseline (quantifies the LLM's lift)
make feedback                      # analyst corrections -> regression cases + calibration + prompt material
make test                          # pytest across redact/guard/pipeline/baseline/eval/feedback
make demo                          # Streamlit review UI (app/review_app.py)
```

Single test: `pytest tests/test_guard.py::test_guard_never_lowers_urgency -q`
(pytest's `pythonpath = src` in `pyproject.toml` means `triage` imports
resolve without manual `PYTHONPATH` juggling, though the Makefile sets it too).

Prompt-version comparison directly:
```bash
python -m triage.evaluate --labels data/dev_labels.json --compare \
  v1=outputs/triage_dev_claims_mock_v1.json v2=... v3=...
```

The sealed eval set (`data/eval_claims.json` / `eval_labels.json`) is scored
exactly once, after a prompt is frozen on the dev set — see PRD-spec.md §6/§11
and `docs/AI_EVIDENCE.md`. Don't rerun it casually or treat repeated runs
against it as valid evaluation.

## Architecture

Pipeline (`src/triage/pipeline.py`), one claim at a time, failure-isolated so
one bad claim never kills the batch:

```
ingest -> validate (pydantic) -> redact -> evidence lookup -> LLM triage -> deterministic guard -> ranked store
```

- **`models.py`** — every fixed taxonomy (`RootCause`, `OwnerQueue`,
  `NextAction`) and the `ROOT_CAUSE_TO_QUEUE` map lives here. The LLM must
  pick from these enums; it can never invent a category and can never select
  `HUMAN_REVIEW` itself (blocked at the API tool-schema level in `llm.py`) —
  only the guard routes there.
- **`config.py`** — single source of truth for every threshold, urgency band,
  and team. No other module hardcodes a number; if a threshold changes, it
  changes here only.
- **`redact.py`** — PHI-pattern regex scrub (SSN, DOB, phone, email, member
  names) run on `adjudicator_note` and `history_snippet` *before* either
  field reaches an LLM call or a log line.
- **`store.py`** — evidence lookups against `data/mock_systems/*.json`
  (`pa_registry`, `claim_history`, `contract_rates`), standing in for
  enterprise source-system APIs with identical lookup semantics — swapping in
  real APIs is meant to be an adapter change, not a rewrite.
- **`llm.py`** — provider wrapper with three modes (`live`/`mock`/`cached`).
  `mock` is deliberately a gullible, note-trusting heuristic (V1-style
  reader) so the guard and evidence-contradiction paths have something real
  to catch in tests/dev — it is never used for reported eval numbers. `live`
  responses are appended to `outputs/llm_cache.jsonl` keyed by
  `claim_id|prompt_version` so a run can be replayed offline via `cached`.
- **`prompts.py`** — versioned prompt templates (`v1`..`v3`). `v3` is frozen;
  `v1`/`v2` are kept as documented iteration history, not deleted.
- **`guard.py`** — deterministic layer that can only make the outcome *more*
  conservative: raise urgency (dollar/SLA floors), correct queue via the
  taxonomy map, force human review on evidence contradictions or low
  confidence. It never lowers urgency and never raises confidence — pinned by
  `tests/test_guard.py::test_guard_never_lowers_urgency`.
- **`audit.py`** — append-only JSONL log (`outputs/audit.jsonl`); every
  ingest/redact/llm/guard/human_decision stage is written with model name,
  prompt version, and an input hash.
- **`evaluate.py`** — root-cause accuracy, routing accuracy, human-review
  rate, critical-recall@N, Spearman urgency correlation, confusion matrix.
  Ranking for eval is urgency desc with billed-dollar tie-break, matching
  what an analyst actually sees in the review queue.
- **`feedback.py`** — turns analyst Approve/Reject/Reassign decisions (logged
  by `app/review_app.py`) into three outputs: regression cases (corrected
  claims as labeled tests — a regression suite, not an accuracy sample),
  confidence-floor calibration (`recommend_floor`, reports its escalation
  cost, never auto-applies), and prompt material (confusion pairs, few-shot
  exemplar drafts for a human to review into the next prompt version).
  **Governing rule: an approval is not a label** — only corrections drive
  learning; approvals count as calibration denominators only, because a
  rubber-stamped Approve is the same automation bias the guard exists to
  catch. Pinned by `test_feedback.py::test_approvals_never_become_labels`.
  Nothing here retrains automatically; every output lands in front of a
  human before it changes behavior — the loop closes through a release.
- **`app/review_app.py`** — Streamlit ranked queue over the most recent
  `outputs/triage_*.json` run. Approve/Reject/Reassign buttons append to
  `outputs/audit.jsonl`; the app never executes an action itself (model
  proposes, code disposes, a human approves).

Ranking rule used everywhere (pipeline output, eval, review UI): sort by
final urgency descending, **break ties by billed dollars**. Without the
tie-break, flat guard floors (e.g. many claims pinned to 95 by the
SLA-breach rule) can bury a genuinely larger claim — a real bug this project
shipped and documents in `DECISION_LOG.md` (2026-08-23).

## Working conventions

- Everything the pipeline passes between stages is a validated pydantic
  model (`models.py`) — no loose dicts crossing module boundaries.
- Any new/changed threshold, band, or team goes in `config.py`, never
  inline in the module that uses it.
- A prompt change is a new version in `prompts.py` (`v4`, ...) evaluated on
  the dev set before being frozen — never edit `v3` in place, and never
  score against the sealed eval set until the new version is frozen.
- The guard is one-directional by design (tests enforce this): a change that
  makes it *lower* urgency or *raise* confidence is a bug, not a feature.
- `.claude/skills/claims-triage/SKILL.md` documents the same triage
  procedure the pipeline automates, for reasoning about a claim by hand or
  explaining a routing decision. If it and the code disagree, the code
  (`prompts.py`, `guard.py`, `config.py`) is correct — treat a mismatch as a
  doc bug to fix, not a spec to follow.
- Data under `data/` (claims, labels, mock systems) is committed fixture
  data except `data/feedback_cases.json`, which is generated at runtime from
  real analyst decisions and gitignored. `outputs/` (runs, eval reports,
  audit log, LLM cache) is entirely gitignored — regenerate with `make data`
  / `make triage` / `make eval` rather than expecting it to be present.
- All claim data is synthetic; there is no real PHI in this repo, but the
  redaction control is still exercised and tested as if there were.
