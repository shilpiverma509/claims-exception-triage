# Low-Level Technical Execution Plan — Claims Exception Triage Assistant

Version 1.0 · 2026-08-22 · Companion to `claude/capstone-spec.md` (v1.1)
Written so that each task is a self-contained work packet: clear inputs, outputs, and acceptance checks. Any task can be handed to a subagent or a smaller model — the contracts, not the executor, guarantee quality. Final integration and review always happen in the main session.

---

## 1. Application structure

```
claims-triage/
├── README.md                  # T12: setup + run instructions     [BUILT ✅]
├── DECISION_LOG.md            # every ratified decision (ongoing)
├── requirements.txt           # T12: pydantic, anthropic, streamlit, pytest, matplotlib, scipy [BUILT ✅]
├── .env.example                # T12: ANTHROPIC_API_KEY=sk-...  TRIAGE_MODE=live|mock  [BUILT ✅]
├── Makefile                   # T12: make data|triage|baseline|eval|eval-baseline|test|demo [BUILT ✅]
├── pyproject.toml             # pytest pythonpath=src (so `pytest`/`make test` both resolve triage) [BUILT ✅]
├── data/                      # generated artifacts (gitignored except samples)
│   ├── dev_claims.json        # 50 practice claims          [BUILT ✅]
│   ├── eval_claims.json       # 25 sealed claims            [BUILT ✅]
│   ├── dev_labels.json / eval_labels.json  # ground truth   [BUILT ✅]
│   ├── stress_malformed.json / stress_phi_like.json         [BUILT ✅]
│   └── mock_systems/          # simulated enterprise sources (T2)      [BUILT ✅]
│       ├── claim_history.json #   per-claim prior touches & prior submissions
│       ├── pa_registry.json   #   prior-auth approvals "system of record"
│       └── contract_rates.json#   provider fee schedule / contract table
├── src/triage/
│   ├── models.py               # pydantic domain models      [BUILT ✅]
│   ├── generate_data.py        # seeded synthetic generator  [BUILT ✅]
│   ├── generate_mock_systems.py# T2: mock source-system fixture generator (incl. PA traps) [BUILT ✅]
│   ├── config.py                # T3: thresholds, urgency bands, team registry, model name [BUILT ✅]
│   ├── redact.py                # T4: PHI-pattern scrubber (regex: SSN, DOB, names-in-notes) [BUILT ✅]
│   ├── llm.py                   # T5: provider wrapper — live Anthropic | mock | cached [BUILT ✅]
│   ├── prompts.py                # T6: versioned prompt templates (PROMPT_V1..V3)  [BUILT ✅]
│   ├── pipeline.py               # T7: ingest → redact → triage → guard → store    [BUILT ✅]
│   ├── guard.py                  # T8: deterministic checks & overrides            [BUILT ✅]
│   ├── audit.py                  # T8: append-only JSONL audit writer              [BUILT ✅]
│   ├── baseline.py               # T9: pend-code→queue lookup + dollars-only urgency strawman [BUILT ✅]
│   ├── evaluate.py               # T10: metrics, confusion matrix PNG, prompt-version log [BUILT ✅]
│   └── store.py                  # T7: results store (JSON; swappable for SQLite) [BUILT ✅]
├── app/
│   └── review_app.py            # T11: Streamlit ranked-queue review UI            [BUILT ✅]
├── tests/                       # T4–T10 each ship with tests
│   ├── test_redact.py  test_guard.py  test_pipeline.py  test_baseline.py  test_eval.py  [BUILT ✅ 26 passing]
├── outputs/                     # triage runs, eval reports, charts (gitignored)
└── docs/                        # LOW_LEVEL_PLAN.md (this file, mirrored)   [BUILT ✅]
                                 # problem brief PDF, AI_EVIDENCE.md, ENTERPRISE_READINESS.md,
                                 # DEMO_SCRIPT.md — T13, still open
```

---

## 2. Data assumptions (canonical definitions — single source of truth = `config.py`)

### 2.1 Urgency bands
Score 1–100 produced by LLM, then guard-adjusted. Bands drive UI sort + color only; the number drives ranking.

| Band | Score | Definition | Example |
|---|---|---|---|
| CRITICAL | 80–100 | SLA breached or ≤3 days away, OR billed ≥ $10,000, OR member-impacting (denial risk on major procedure) | $42k knee surgery, SLA in 2 days |
| HIGH | 60–79 | SLA ≤ 7 days OR billed ≥ $2,400 OR repeat provider escalation | Imaging claim, provider called twice |
| MEDIUM | 35–59 | SLA 8–20 days, mid-dollar, single touch | $900 cardiology visit, 14 days to SLA |
| LOW | 1–34 | SLA > 20 days AND < $350 AND no escalation signals | $45 office visit, first touch |

Guard floor rule (deterministic, can only raise): billed ≥ $10,000 → score ≥ 80; days-to-SLA ≤ 3 → score ≥ 80; days-to-SLA ≤ 0 → score = 95 minimum.

### 2.2 Owner teams (routing targets)
| Queue id | Team | Handles |
|---|---|---|
| prior_auth_team | Prior Authorization Ops | Missing/invalid PA, PA-claim mismatches |
| eligibility_team | Eligibility & Enrollment | Coverage termed/lagged, plan-segment mismatches |
| coding_review | Clinical Coding Review | CPT/modifier conflicts, dx-procedure mismatch, duplicates needing coder judgment |
| cob_unit | Coordination of Benefits | Other-carrier primacy, Medicare-primary questions, COB questionnaires |
| provider_data_mgmt | Provider Data Management | NPI/roster/address/tax-ID mismatches |
| pricing_team | Contract Pricing | Fee-schedule gaps, contract-rate disputes, manual pricing |
| human_review | Senior Analyst Desk | Anything the model is not confident about (< 0.65) or guard-flagged. Never LLM-guessed into; only guard routes here. |

### 2.3 Mock enterprise source systems (`data/mock_systems/`) — T2
These simulate the systems a production triage service would query, and give the LLM (and guards) real evidence to cite:
- **`claim_history.json`** — keyed by claim_id: list of prior touches `{date, event, actor}` (e.g. "pended for docs", "provider call logged") and prior submissions by the same member+CPT+DOS (fuel for duplicate detection). Generated alongside claims, consistent with each claim's ground-truth cause.
- **`pa_registry.json`** — keyed by member_ref+cpt: `{auth_number, status: approved|pending|none, effective_dates}`. For missing-PA claims the registry deliberately has `none` or `pending`; for a few tricky non-PA claims it has a valid auth (so "no PA" can be *disproved* by evidence — tests whether the model checks).
- **`contract_rates.json`** — keyed by provider_npi_ref+cpt: `{rate_cents, effective_quarter}` with deliberate gaps for pricing-mismatch claims.

Assumption stated in the brief: in production these are claims-platform APIs; here they are JSON fixtures with identical read semantics (a `lookup(system, key)` function), so the swap is an adapter change, not a redesign.

### 2.4 Confidence & thresholds (config.py)
`CONFIDENCE_FLOOR = 0.65` (below → human_review) · `TAXONOMY = RootCause enum` · `ACTIONS = NextAction enum` · `MODEL = claude-sonnet-4-5` (triage is a bounded extraction/reasoning task; frontier-large model unnecessary — cost/latency judgment reviewers will like; documented in AI evidence) · `TRIAGE_MODE = live | mock | cached`.

---

## 3. Task breakdown (work packets)

Each packet: **In** → **Out** → **Accept**. Sizes: S (≤30 min), M (≤90 min).
Status legend: ✅ done · ⬜ open. Order respects dependencies; T4–T6 are parallelizable (subagent-friendly), as are T9/T11.

- **T1 ✅ Skeleton + models + generator** — done and verified (50 dev / 25 eval / 2 stress).
- **T2 ✅ M — Mock source systems.** 3 JSON fixtures per §2.3 generated by `generate_mock_systems.py` + `lookup_history/lookup_pa/lookup_rate/evidence_bundle()` in store.py. Verified: 75/75 claims have history, 3 planted "disprovable" PA traps (`CLM-2026-8001/8019/8020`), rerun is deterministic (seed 4242).
- **T3 ✅ S — config.py.** Urgency bands, guard floors, team registry (§2.2), `CONFIDENCE_FLOOR`, `MODEL_NAME`, `TRIAGE_MODE`. No other module hardcodes a threshold.
- **T4 ✅ S — redact.py + tests.** `redact(text) -> (clean_text, findings)` covering SSN, DOB, phone, email, "Member <Name>". `test_redact.py` green (5 tests) incl. the PHI stress fixture.
- **T5 ✅ M — llm.py.** `triage_call(claim, evidence, prompt_version, mode) -> TriageAssessment` with `live`/`mock`/`cached` modes, retries ×2 with backoff, cache round-trip via `outputs/llm_cache.jsonl`, per-claim failure isolation (raised to caller, caught in pipeline.py).
- **T6 ✅ S — prompts.py.** PROMPT V1 (zero-shot + taxonomy), V2 (+VERIFY_BLOCK + few-shot exemplars), V3 (+CITE_BLOCK evidence citation). Version stamped into every audit row via `pipeline.py`.
- **T7 ✅ M — pipeline.py + store.py.** `python -m triage.pipeline --input <file> --mode mock|cached|live --prompt v1|v2|v3` → validate → redact → evidence lookup → LLM → guard → ranked JSON in `outputs/`. Verified: `stress_malformed.json` (1 bad record) → 0 processed / 1 errored, no crash; `dev_claims.json` → 50/50 processed, sorted by `final_urgency` desc.
- **T8 ✅ M — guard.py + audit.py + tests.** Taxonomy-map queue correction, urgency floors (§2.1, can only raise), PA-registry/duplicate contradiction checks → forced human review, confidence floor. `test_guard.py` green (7 tests) incl. the "guard never lowers urgency" property. `audit.jsonl` append-only, stamped with model/prompt_version/input_hash.
- **T9 ✅ S — baseline.py.** `PEND_CODE_TO_CAUSE` lookup (P10-P16 per generate_data.py's taxonomy order) + `baseline_urgency()` (dollars-only, no SLA). Generic P99/P00 codes fall back to a fixed guess — the deliberate failure mode that quantifies the LLM's lift. Same result schema as pipeline.py, scored by the same evaluate.py. `test_baseline.py` green (5 tests). Dev-set result: 72% root-cause accuracy vs mock-mode LLM's 88% (see `outputs/eval_report_triage_dev_claims_baseline.md`).
- **T10 ✅ M — evaluate.py + charts.** `python -m triage.evaluate --run <file> --labels <file>` → root-cause accuracy, routing accuracy, human_review_rate, critical-recall@10 (severity ≥4), Spearman urgency correlation (scipy), confusion-matrix PNG (matplotlib), markdown eval report in `outputs/`. `--compare v1=<f> v2=<f> v3=<f>` writes the prompt-version comparison table. `test_eval.py` green (6 tests). **Still open:** the sealed `eval_claims.json` run — per §5/PRD §6/§11, that's a one-time event after the prompt is frozen (Day 4), not something to pre-run casually; not executed as part of this build pass.
- **T11 ✅ M — review_app.py.** Streamlit: run picker, mode banner (MOCK/CACHED/LIVE/BASELINE), ranked queue with band-colored expanders (summary, evidence, redaction notice, guard adjustments), Approve/Reject/Reassign buttons appending to `audit.jsonl`, Audit Trail tab. Smoke-tested headless (`streamlit run app/review_app.py --server.headless true`) — starts clean, `/_stcore/health` returns `ok`.
- **T12 ✅ S — README + Makefile + requirements.txt + .env.example.** Also added `pyproject.toml` (`pytest` pythonpath) so both `pytest` and `make test` resolve `triage` without manual `PYTHONPATH`. Verified fresh-flow: `make data && make triage MODE=mock && make baseline && make eval && make eval-baseline && make test` — all succeed, 26/26 tests pass. `make demo` starts Streamlit cleanly (see T11).
- **T13 ⬜ M — Submission docs.** problem brief (1-page PDF), AI_EVIDENCE.md (prompts, model choice rationale, eval tables, failure-mode catalog, human checkpoints, how AI assisted the build), ENTERPRISE_READINESS.md (classification, access, audit, controls, kill switch, handoff owner + productionization list), DEMO_SCRIPT.md (5-min beat sheet: problem → live triage → guard catching a failure → eval numbers incl. baseline comparison → next iteration).
- **T14 ⬜ S — Submission-ready checklist.** Repo pushed (Shilpi, Mon); no key/PHI in git history; tag v1.0; demo recorded ≤5 min; submission email drafted per programme instructions with subject "Cohort 5 Capstone package — Shilpi Verma", repo link, brief PDF/docx, recording link; DECISION_LOG complete; drill ledger empty.

## 4. Execution model (smaller models / subagents)
Parallelizable packets (T4, T5, T6 | T9, T11) can be delegated to subagents — each packet's In/Out/Accept is the contract; executor model can be a smaller tier since correctness is enforced by tests + main-session review before merge. Sequential integration points (T7, T8, T10) stay in the main session. Inside the app itself, the triage model is Sonnet-tier by deliberate choice (§2.4) — "right-size the model" is itself demo material.

## 5. Test & scoring summary
`make test` = pytest across redact/guard/pipeline/baseline/eval (target: all green, ~20 cases incl. property test "guard never lowers urgency"). Scoring flow: iterate V1→V3 on dev (logged) → freeze → `make eval SET=eval` once → report verbatim + baseline comparison. Definition of done for the whole app = T14 checklist all ticked.
