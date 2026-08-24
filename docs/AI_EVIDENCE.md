# AI Evidence — Claims Exception Triage Assistant

Submission component 3 · Shilpi Verma · Cohort 5 · August 2026

The audit trail of every AI decision in this project: what model and why, how the prompts evolved, how it was evaluated, what failed, and where humans sit in the loop. Every number below comes from a saved run in `outputs/`, reproducible from the committed code.

---

## 1. Model and tool choices

| Choice | What we picked | Rationale |
|---|---|---|
| Model | `claude-sonnet-4-5` | Triage of one claim is a bounded classification/reasoning task over ~2,300 input tokens. Dev results (§4) show this tier saturates the task; a frontier-large model would add cost and latency for no measurable lift. Right-sizing is itself the judgment on display. |
| Output mechanism | Forced tool-use against a JSON Schema (`tool_choice` pinned to `triage_assessment`) | The model *cannot* return prose, a cause outside the 7-item taxonomy, an out-of-range score, or the `human_review` queue. Malformed output is prevented at the API layer, then re-validated by pydantic on our side. Free-text JSON parsing was rejected as an unnecessary failure surface. |
| Architecture | One LLM call per claim inside a deterministic Python pipeline | Multi-agent was considered and rejected (PRD §10): no dynamic tool choice is needed, there is nothing to parallelise across "specialists," and evaluation must be attributable to be useful. The orchestration this genuinely needs — ordering, retries, validation, thresholds — is deterministic, so it lives in ordinary testable code. |
| Modes | `live` / `cached` / `mock` | `mock` (keyword heuristic, zero network) develops and tests the pipeline for free; `cached` replays real responses so the demo has no network dependency; only `live` numbers are ever reported. Mock is documented in-code as "NEVER used for reported eval numbers." |
| Money | Integer cents throughout | Floats in claims processing are how you lose pennies at scale. |

## 2. Prompt versions (a deliberate, logged progression)

All three live in `prompts.py` as versioned constants. The version used is stamped into every output row and every audit line, so any recommendation can be traced to the exact instructions that produced it.

- **V1 — zero-shot, taxonomy-constrained.** Role framing ("you propose; a human decides"), the 7-cause taxonomy with definitions, the allowed action and queue lists, output rules. *Predicted weakness:* trusting the adjudicator note over the source systems.
- **V2 — V1 + verification discipline + few-shot.** Adds explicit rules ("never cite `missing_prior_auth` without checking `evidence.pa_registry`… source systems outrank notes") plus three worked examples, one of which models honest low confidence as the *correct* answer on an ambiguous claim.
- **V3 — V2 + mandatory evidence citation.** Every urgency reason and the summary must quote the note fragment, history event, or registry field that supports it. This is for the analyst and the audit trail rather than for accuracy: a cited recommendation can be verified in seconds and reconstructed months later.

## 3. Evaluation method

- 75 seeded synthetic claims from a committed, reproducible generator, split 50 dev / 25 sealed eval. Each carries a ground-truth label (cause, queue, severity 1–5, ambiguity flag) that is never shown to the model.
- Evidence fixtures for three mock source systems are generated *from* those labels, with planted adversarial cases: three "disprovable traps" where the note implies a missing prior-auth but the registry holds a valid approval, and deliberate rate-table gaps that serve as the *positive* evidence for pricing claims.
- Metrics: root-cause accuracy, routing accuracy, critical-recall@10 (severity ≥4 claims surfaced in the top 10), Spearman correlation between final urgency and true severity, and human-review rate. `evaluate.py` also emits a confusion matrix and a per-run markdown report.
- **Sealed-set discipline:** the 25 eval claims are scored exactly once, after prompt freeze, and reported verbatim regardless of the result. As of this writing they remain untouched.

## 4. Results — dev set, 50 claims, live Claude calls

| Version | Root-cause acc. | Routing acc. | Critical-recall@10 | Spearman | Human-review rate |
|---|---|---|---|---|---|
| Rule-based baseline | 72% | 76% | 50% | 0.55 | 0% |
| V1 (zero-shot) | 100% | 100% | 100% | 0.82 | 0% |
| V2 (+verify, few-shot) | 100% | 100% | 100% | 0.82 | 0% |
| V3 (+citations) | 100% | 100% | 100% | 0.81 | 0% |

The baseline is the honest strawman for "why an LLM at all": root cause from the pend code, urgency from dollars alone. Roughly 30% of claims carry a generic pend code (P99/P00) by design, so the lookup table guesses blindly exactly where real analysts have to think. The 28-point root-cause gap is the quantified answer.

**The result we did not expect, reported anyway.** The V1→V3 ladder was built on the assumption that zero-shot V1 would fall for the planted traps and that V2's verification instructions would rescue it. V1 cleared all three traps unprompted — its summary for trap claim `CLM-2026-8001` reads *"Valid prior auth exists (PA-125103)… ruling out auth/pricing issues"* with nothing in V1 telling it to check. Two honest conclusions follow:

1. With the evidence placed in context, this model tier verifies against source systems by default. V2 and V3's measurable value here is citation quality and behavioural guardrails, **not accuracy**.
2. **The dev set is saturated.** It can no longer distinguish prompt quality at this model tier, so further prompt iteration against it would be measuring noise. The discriminating tests that remain are the sealed set and a real-data shadow pilot.

We state this rather than claiming the ladder "improved accuracy," because on this data it did not. An earlier V3 run scored 98%; that single miss led directly to failure mode #1.

## 5. Failure-mode catalog

Each entry: what happened, how it was detected, what we did. All dated in `DECISION_LOG.md`.

1. **Evidence contamination via shared fixture keys.** The contract-rate table is keyed `provider|cpt`, which is *shared* across claims — so other claims filled in seven rate gaps that were supposed to be the evidence for pricing-mismatch claims. On `CLM-2026-8048` the model flagged the contradiction itself (*"the note says the rate table is missing but evidence.contract_rate shows the rate EXISTS"*), followed the corrupted evidence, and was scored wrong. *Detected by:* an eval miss, then reading the model's own cited reasoning. *Fixed:* the generator now strips every pricing claim's key after generation; verified zero collisions. *Production lesson:* when the model and the system-of-record disagree with a human note, suspect the **data pipeline** before the model — and require citations, because they are what made this diagnosable in minutes.

2. **A ranking metric that measured luck.** The guard's SLA floor flattened 19 breached claims to urgency 95. With no tie-break, "top 10" membership was insertion order, and critical-recall@10 came out at 0.375 — *below the dumb baseline's* 0.50. *Detected by:* refusing to accept a nonsensical number and re-ranking by raw model scores (0.625) and with a dollar tie-break (1.00). *Fixed:* ranking policy is now urgency-desc, dollars-desc on ties, applied identically in the pipeline, baseline, evaluator, and UI, with a regression test.

3. **Trap design falsified / dev-set saturation.** Described in §4. Treated as a finding rather than hidden. Harder adversarial cases — contradictory evidence across *multiple* systems, auths with stale effective dates, notes phrased unlike anything in the training distribution — are the designed next iteration.

4. **Hallucinated or out-of-range fields.** Structurally prevented: schema-forced tool call, then pydantic re-validation, then the guard's cause↔queue lookup table. Mock mode's deliberately gullible heuristic keeps these guard paths exercised in tests on every run.

5. **Malformed input records.** A stress fixture with a negative amount and missing fields is rejected at validation into an error report while the batch continues (test-covered). One bad record never kills a run.

6. **PHI-pattern leakage.** A stress note containing a fake SSN, DOB, and name is scrubbed before any LLM call; findings are audit-logged; the clinically useful content (CPT code, "no PA on file") survives redaction. Test-covered.

7. **Confidence floor never fired on real output (open).** Across all three live dev runs, no claim fell below the 0.65 floor — the minimum observed confidence was 0.75. That is weak evidence the threshold is well-calibrated; it may simply be set too low to ever bind on data this clean. On messier real notes it could bind too rarely to protect anyone. *Mitigation plan:* calibrate against shadow-pilot outcomes rather than guessing; monitor the distribution of model confidence as a drift signal.

8. **Automation bias (open, structural).** Analysts may rubber-stamp high-confidence recommendations, which would convert the human approval gate into a formality. *Mitigations:* confidence and cited evidence are shown on every card so verification is fast; the audit log makes rubber-stamping *measurable* through time-to-approve distributions; a spot-audit workflow on approved claims is the designed next step.

## 6. Human checkpoints

1. **Every action requires human approval.** The system proposes; Approve / Reject / Reassign in the review UI are audit entries. Nothing executes.
2. **The escalation queue is code-owned.** Confidence below 0.65, or a stated cause contradicted by a source system, routes to the Senior Analyst Desk — and the model is schema-blocked from selecting that queue itself, so it cannot dodge accountability by hedging or suppress a review by sounding confident.
3. **The guard is the human's deterministic proxy:** urgency floors, taxonomy-enforced routing, and contradiction checks, all unit-tested to only ever escalate.
4. **Full reconstructability:** every audit row carries model name, prompt version, input hash, actor, and outcome, append-only.

## 7. How AI assisted the build itself

This project was built pair-programming with Claude Code, with the developer directing scope, ratifying every design decision (`DECISION_LOG.md`), and reviewing all output. The division of labour is worth stating plainly: the human chose the track, the taxonomy, the guard philosophy, and the evaluation discipline — and caught the "recall@10 worse than baseline" anomaly by refusing to accept a number that made no sense. The AI wrote module code against those contracts, ran the diagnostic loops that isolated the tie-break gap and traced the fixture collision, and drafted documentation the human edited.

Both production bugs in §5 were found *because* the workflow demanded an explanation for every anomalous number instead of accepting the summary table. All AI-generated code is covered by the same 26-test suite as everything else, including a property test asserting the guard can never lower urgency.
