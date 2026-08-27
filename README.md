# Claims Exception Triage Assistant

**Repo:** [github.com/shilpiverma509/claims-exception-triage](https://github.com/shilpiverma509/claims-exception-triage)
**Author:** Shilpi Verma · UHC Tech AI Transformation, Cohort 5 · Track 1

A triage assistant for a claims-ops exception queue: it diagnoses why each
pended claim is stuck, ranks urgency, proposes a next action and an owner
queue, and routes low-confidence or contradictory cases to a human — with a
deterministic guard layer and an append-only audit log around every call.
See `PRD-spec.md` for the full problem brief and `LOW_LEVEL_PLAN.md` for the
technical execution plan and task breakdown.

## Submission documents

- **Runnable prototype:** review UI runs as a Streamlit app, served locally
  at **http://localhost:8501**. See Quickstart below for exact commands and
  a sample run.
- **Problem brief:** `docs/PROBLEM_BRIEF.md` (source) / `docs/PROBLEM_BRIEF.pdf` (submitted 1-page PDF)
- **AI evidence:** `docs/AI_EVIDENCE.md`
- **Enterprise readiness:** `docs/ENTERPRISE_READINESS.md`

## What it does

1. **Ingest** a synthetic exception queue (75 claims — see Data below).
2. **Validate** each claim against a pydantic schema; a malformed record is
   routed to an error report instead of crashing the batch.
3. **Redact** PHI-pattern text (SSN, DOB, phone, email, member names) from
   the free-text note and history *before* either reaches an LLM call or a
   log line.
4. **Look up evidence** in three mock source systems (prior-auth registry,
   claim history, contract rates).
5. **Diagnose** with one Claude call per claim, constrained to a fixed
   taxonomy — it must return one of 7 causes, a cited summary, an urgency
   score, a next action, and an owner queue. It cannot reply in free text
   and cannot select `human_review` itself.
6. **Guard**: deterministic Python re-checks the answer. It can only make
   the outcome *more* conservative — raise urgency on dollar/SLA floors,
   correct the queue via a fixed cause→team map, or force human review on
   evidence contradictions or low confidence. It never lowers urgency and
   never raises confidence.
7. **Rank** worst-first: urgency descending, billed dollars as the tie-break
   (see `DECISION_LOG.md`, 2026-08-23, for why the tie-break exists).
8. **Review**: an analyst approves, rejects, or reassigns in the Streamlit
   UI. Nothing executes automatically.
9. **Audit**: every stage above writes one line to an append-only log.
10. **Learn**: analyst corrections feed back into regression tests,
    confidence calibration, and prompt-improvement material for the *next*
    prompt version — never applied automatically.

## Architecture

```
queue.json → validate (pydantic) → redact (PHI regex) → evidence lookup
(pa_registry · claim_history · contract_rates) → LLM triage call
(taxonomy-constrained tool call) → deterministic guard (urgency floors,
taxonomy routing, contradiction checks) → ranked store (JSON)
→ Streamlit review UI (approve/reject/reassign) → audit log (JSONL)
                                                         │
                                          analyst corrections → feedback.py
                                          → regression cases, confidence
                                            calibration, prompt material
```

One LLM call per claim inside an ordinary Python pipeline — no agent
framework, no LLM-to-LLM delegation. The reasoning: triage of one claim is a
single bounded task with nothing to parallelise across "specialists," and
the orchestration this workflow actually needs (ordering, retries,
validation, thresholds) is deterministic, so it lives in testable code
rather than in another model call. See `PRD-spec.md` §10 for the full
architecture decision and the rejected alternative.

### Where each piece lives

| Concern | Module |
|---|---|
| Fixed taxonomies (root causes, queues, actions) + the pydantic models everything passes between stages | `src/triage/models.py` |
| Every threshold, urgency band, and team — single source of truth | `src/triage/config.py` |
| PHI redaction | `src/triage/redact.py` |
| Evidence lookups against the mock source systems | `src/triage/store.py` |
| LLM call (`live`/`mock`/`cached` modes) | `src/triage/llm.py` |
| Versioned prompts (`v1`–`v3`, `v3` frozen) | `src/triage/prompts.py` |
| Deterministic guard | `src/triage/guard.py` |
| Append-only audit log | `src/triage/audit.py` |
| End-to-end orchestration | `src/triage/pipeline.py` |
| Accuracy/routing/ranking metrics + confusion matrix | `src/triage/evaluate.py` |
| Rule-based baseline (quantifies the LLM's lift) | `src/triage/baseline.py` |
| Turns analyst corrections into regression cases + calibration + prompt material | `src/triage/feedback.py` |
| Streamlit review UI | `app/review_app.py` |

## Data

`make data` runs two seeded, reproducible generators:

1. **`generate_data.py`** — 75 synthetic claims (50 dev / 25 sealed eval),
   each with a `Claim` (id, member/provider refs, CPT code, billed amount in
   cents, received/SLA dates, pend code, free-text note, history snippet)
   and a `GroundTruth` label (root cause, owner queue, severity 1–5,
   ambiguity flag) never shown to the model. About 30% of claims carry
   generic pend codes (P99/P00) by design, so a pend-code lookup alone
   can't diagnose them — exactly where the LLM has to read the evidence.
2. **`generate_mock_systems.py`** — three JSON fixtures under
   `data/mock_systems/` standing in for enterprise source systems, with the
   same `lookup(system, key)` semantics a real API would have (swapping in
   real APIs is meant to be an adapter change, not a rewrite):
   - `pa_registry.json` — prior-auth status by member+CPT, including 3
     deliberate "disprovable" traps where the note implies a missing auth
     but the registry actually holds a valid one.
   - `claim_history.json` — prior touches and prior submissions per claim
     (duplicate-detection signal).
   - `contract_rates.json` — fee-schedule rate by provider+CPT, with
     deliberate gaps for pricing-mismatch claims (a missing rate is
     evidence, not an error).

### The 7-cause root-cause taxonomy (`models.py::RootCause`)

| Cause | Meaning | Owner queue |
|---|---|---|
| `missing_prior_auth` | Service required prior authorization and none valid is on file | Prior Authorization Ops |
| `eligibility_mismatch` | Member coverage inactive/mismatched for the date of service | Eligibility & Enrollment |
| `coding_error` | CPT/modifier/diagnosis inconsistency; miskeyed codes | Clinical Coding Review |
| `duplicate_claim` | Same service already submitted and in process/paid | Clinical Coding Review |
| `cob_conflict` | Another carrier may be primary; coordination unresolved | Coordination of Benefits |
| `provider_data_mismatch` | NPI/roster/address/tax-ID inconsistent with credentialing | Provider Data Management |
| `pricing_mismatch` | Contract rate missing or disputed for this service | Contract Pricing |

The LLM must pick exactly one of these seven — the routing above is
*derived* from the cause via a fixed map, never separately guessed, and the
model can never select the eighth bucket, `human_review`; only the guard
routes there (confidence < 65%, or a source system contradicting the note).

## Audit and output files

Everything under `outputs/` is gitignored and regenerated by the commands
below — nothing here ships in the repo itself.

| File | What it is |
|---|---|
| `outputs/audit.jsonl` | Append-only log, one line per ingest/redact/llm/guard/human_decision event: timestamp, claim ID, stage, actor, model name, prompt version, an input hash, and the outcome. This is the full reconstruction trail for any recommendation. |
| `outputs/triage_<input>_<mode>_<prompt>.json` | One pipeline run's ranked output — every claim's final assessment, guard adjustments, and routing. |
| `outputs/eval_report_<run>.md` + `confusion_matrix_<run>.png` | `make eval` output: root-cause accuracy, routing accuracy, human-review rate, critical-recall@10, Spearman urgency correlation, and a confusion matrix. |
| `outputs/feedback_report_<run>.md` | `make feedback` output: confidence-calibration table and prompt-improvement material (confusion pairs, exemplar drafts). |
| `outputs/llm_cache.jsonl` | Every real (`live`-mode) LLM response, keyed by `claim_id|prompt_version`, so a run can be replayed offline via `cached` mode. |
| `data/feedback_cases.json` | Generated at runtime from real analyst Approve/Reject/Reassign decisions (gitignored, not fixture data) — a regression suite of corrected claims. |

## Why the feedback loop exists

The Streamlit review UI's Approve/Reject/Reassign buttons write to the audit
log. `make feedback` turns those decisions into three things a human then
acts on — nothing here retrains anything automatically:

1. **Regression cases** — corrected claims written in the same schema
   `evaluate.py` reads, so a fix can be proven not to have regressed.
2. **Confidence calibration** — correction rate by confidence bucket, used
   to recommend (never auto-apply) a new confidence floor, along with the
   escalation cost of that floor.
3. **Prompt material** — confusion pairs and candidate few-shot exemplars
   for a human to review into the next prompt version.

The governing rule: **an approval is not a label.** Only corrections drive
learning — an approval may just mean someone clicked through quickly, and
treating it as confirmed-correct would let the system reinforce its own
mistakes. This is pinned by
`test_feedback.py::test_approvals_never_become_labels`.

## Setup

```bash
git clone git@github.com:shilpiverma509/claims-exception-triage.git
cd claims-exception-triage
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # only needed for --mode live
```

Everything below is a plain `make`/`python3`/`pytest` command run from an
ordinary terminal — no dependency on any particular editor or AI tool.

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
make demo               # streamlit review UI, served at http://localhost:8501
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
> `PRD-spec.md` §6/§11. It has already been scored once (2026-08-27, prompt
> v3): 96% root-cause accuracy, 96% routing accuracy, 100%
> critical-recall@10, 0.826 Spearman correlation — reported verbatim in
> `docs/PROBLEM_BRIEF.md` and `docs/AI_EVIDENCE.md`. Don't rerun it.

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
recent `outputs/triage_*.json` run, served at **http://localhost:8501**.
Each claim expands to its summary, evidence, redaction notice (if any), and
guard adjustments, with Approve/Reject/Reassign buttons that append to
`outputs/audit.jsonl` — the app never executes an action itself. A second
tab shows the full audit trail.
