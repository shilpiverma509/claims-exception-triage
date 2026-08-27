# AI Evidence — Claims Exception Triage Assistant

The audit trail of every AI decision in this project: what model and why, how the prompts evolved, how it was evaluated, what failed, and where humans sit in the loop. Every number below comes from a saved run in `outputs/`, reproducible from the committed code.

---

## 1. Model and tool choices

| Choice           | What we picked                                                                      | Rationale                                                                                                                                                                                                                                                                                                                                  |
| ---------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Model            | `claude-sonnet-4-5`                                                                 | Triage of one claim is a bounded classification/reasoning task over ~2,300 input tokens. Dev results (§4) show this tier saturates the task; a frontier-large model would add cost and latency for no measurable lift. Right-sizing is itself the judgment on display.                                                                     |
| Output mechanism | Forced tool-use against a JSON Schema (`tool_choice` pinned to `triage_assessment`) | The model _cannot_ return prose, a cause outside the 7-item taxonomy, an out-of-range score, or the `human_review` queue. Malformed output is prevented at the API layer, then re-validated by pydantic on our side. Free-text JSON parsing was rejected as an unnecessary failure surface.                                                |
| Architecture     | One LLM call per claim inside a deterministic Python pipeline                       | Multi-agent was considered and rejected (PRD §10): no dynamic tool choice is needed, there is nothing to parallelise across "specialists," and evaluation must be attributable to be useful. The orchestration this genuinely needs — ordering, retries, validation, thresholds — is deterministic, so it lives in ordinary testable code. |
| Modes            | `live` / `cached` / `mock`                                                          | `mock` (keyword heuristic, zero network) develops and tests the pipeline for free; `cached` replays real responses with no network dependency; only `live` numbers are ever reported. Mock is documented in-code as "NEVER used for reported eval numbers."                                                                                |
| Money            | Integer cents throughout                                                            | Floats in claims processing are how you lose pennies at scale.                                                                                                                                                                                                                                                                             |

## 2. Prompt versions (a deliberate, logged progression)

All three live in `prompts.py` as versioned constants. The version used is stamped into every output row and every audit line, so any recommendation can be traced to the exact instructions that produced it.

- **V1 — zero-shot, taxonomy-constrained.** Role framing ("you propose; a human decides"), the 7-cause taxonomy with definitions, the allowed action and queue lists, output rules. _Predicted weakness:_ trusting the adjudicator note over the source systems.
- **V2 — V1 + verification discipline + few-shot.** Adds explicit rules ("never cite `missing_prior_auth` without checking `evidence.pa_registry`… source systems outrank notes") plus three worked examples, one of which models honest low confidence as the _correct_ answer on an ambiguous claim.
- **V3 — V2 + mandatory evidence citation.** Every urgency reason and the summary must quote the note fragment, history event, or registry field that supports it. This is for the analyst and the audit trail rather than for accuracy: a cited recommendation can be verified in seconds and reconstructed months later.

### The frozen prompt (V3), copied verbatim from `src/triage/prompts.py`

This is the exact prompt that produced every number in §4a — assembled by `build_prompt("v3", claim, evidence, today)`, which concatenates these blocks in order (role header → taxonomy → verify → few-shot → citation → output rules → the claim + evidence JSON):

```
You are a claims exception triage assistant helping an operations analyst. Diagnose why
this pended claim is stuck, how urgent it is, and what should happen next. You propose;
a human decides.

Root causes (choose exactly one "root_cause"):
- missing_prior_auth: service required prior authorization and none valid is on file
- eligibility_mismatch: member coverage inactive/mismatched for the date of service
- coding_error: CPT/modifier/diagnosis inconsistency; miskeyed codes
- duplicate_claim: same service already submitted and in process/paid
- cob_conflict: another carrier may be primary; coordination unresolved
- provider_data_mismatch: NPI/roster/address/tax-ID inconsistent with credentialing
- pricing_mismatch: contract rate missing or disputed for this service

Actions (choose exactly one "proposed_action"): request_prior_auth_docs, verify_eligibility,
return_for_recoding, deny_as_duplicate, coordinate_benefits, update_provider_record,
reprice_per_contract, escalate_to_supervisor

Queues (choose exactly one "owner_queue"): prior_auth_team, eligibility_team, coding_review,
cob_unit, provider_data_mgmt, pricing_team
(You may NOT choose human_review — that routing is decided by deterministic controls, not you.)

IMPORTANT — verify before concluding:
- Never cite missing_prior_auth without checking evidence.pa_registry: if status is "approved"
  with valid dates, the auth EXISTS and the real cause is elsewhere, whatever the note says.
- Never cite duplicate_claim unless evidence.history.prior_submissions shows a matching submission.
- A missing evidence.contract_rate entry is positive evidence FOR pricing_mismatch.
Notes are written by busy humans and are sometimes wrong; source systems outrank notes.

Examples of good assessments:

Claim: note "Modifier 59 inconsistent with 97110 on same DOS", pend code P12, PA registry: approved auth on file.
→ root_cause: coding_error (NOT missing_prior_auth — the registry shows a valid auth; the
   coding conflict is the actual blocker), action: return_for_recoding, queue: coding_review.

Claim: note "Provider resubmitted after no response", history shows prior submission same DOS/CPT/amount in_process.
→ root_cause: duplicate_claim, action: deny_as_duplicate, queue: coding_review, and urgency LOW
   unless dollars/SLA say otherwise — duplicates rarely harm members.

Claim: vague note "docs may be incomplete, unclear if auth or coding issue", PA registry: none, $8,500, SLA in 4 days.
→ root_cause: missing_prior_auth (registry confirms no auth), confidence ~0.6 (note ambiguous),
   high urgency (dollars + SLA). Low confidence is honest and correct here.

For each urgency reason and for the summary, cite your evidence: quote the exact
fragment of the note, history event, or registry field that supports the conclusion, e.g.
'PA registry: status=none' or note: "resubmitted after no response". An assessment without
citations is incomplete.

Respond ONLY with the triage_assessment tool call. urgency_score is 1-100
(consider billed amount, days to SLA, member impact, escalation signals). confidence is your
honest 0-1 estimate that the root_cause is correct — do not inflate it.

Today: <ISO date>

Claim:
<claim JSON: claim_id, member_id, provider, cpt_code, billed_amount_cents, received_date,
sla_due_date, pend_code, adjudicator_note (redacted), history_snippet (redacted)>

Source-system evidence:
<evidence JSON: pa_registry lookup, claim_history lookup, contract_rates lookup>
```

The taxonomy/action/queue lists are enforced structurally too, not just by instruction: the API call pins `tool_choice` to the `triage_assessment` tool, whose JSON Schema (`models.py`) makes an out-of-taxonomy value or the `human_review` queue impossible to return, not just discouraged.

## 3. Evaluation method

- 75 seeded synthetic claims from a committed, reproducible generator, split 50 dev / 25 sealed eval. Each carries a ground-truth label (cause, queue, severity 1–5, ambiguity flag) that is never shown to the model.
- Evidence fixtures for three mock source systems are generated _from_ those labels, with planted adversarial cases: three "disprovable traps" where the note implies a missing prior-auth but the registry holds a valid approval, and deliberate rate-table gaps that serve as the _positive_ evidence for pricing claims.
- Metrics: root-cause accuracy, routing accuracy, critical-recall@10 (severity ≥4 claims surfaced in the top 10), Spearman correlation between final urgency and true severity, and human-review rate. `evaluate.py` also emits a confusion matrix and a per-run markdown report.
- **Sealed-set discipline:** the 25 eval claims were scored **exactly once**, on 2026-08-27, after the prompt was frozen at V3. The result is reported verbatim in §4a below, including the miss.

## 4. Results — dev set, 50 claims, live Claude calls

| Version                | Root-cause acc. | Routing acc. | Critical-recall@10 | Spearman | Human-review rate |
| ---------------------- | --------------- | ------------ | ------------------ | -------- | ----------------- |
| Rule-based baseline    | 72%             | 76%          | 50%                | 0.55     | 0%                |
| V1 (zero-shot)         | 100%            | 100%         | 100%               | 0.82     | 0%                |
| V2 (+verify, few-shot) | 100%            | 100%         | 100%               | 0.82     | 0%                |
| V3 (+citations)        | 100%            | 100%         | 100%               | 0.81     | 0%                |

The baseline is the honest strawman for "why an LLM at all": root cause from the pend code, urgency from dollars alone. Roughly 30% of claims carry a generic pend code (P99/P00) by design, so the lookup table guesses blindly exactly where real analysts have to think. The 28-point root-cause gap is the quantified answer.

**The result we did not expect, reported anyway.** The V1→V3 ladder was built on the assumption that zero-shot V1 would fall for the planted traps and that V2's verification instructions would rescue it. V1 cleared all three traps unprompted — its summary for trap claim `CLM-2026-8001` reads _"Valid prior auth exists (PA-125103)… ruling out auth/pricing issues"_ with nothing in V1 telling it to check. Two honest conclusions follow:

1. With the evidence placed in context, this model tier verifies against source systems by default. V2 and V3's measurable value here is citation quality and behavioural guardrails, **not accuracy**.
2. **The dev set is saturated.** It can no longer distinguish prompt quality at this model tier, so further prompt iteration against it would be measuring noise. The discriminating tests that remain are the sealed set and a real-data shadow pilot.

We state this rather than claiming the ladder "improved accuracy," because on this data it did not. An earlier V3 run scored 98%; that single miss led directly to failure mode #1.

## 4a. Sealed-set result — scored once, reported verbatim

25 held-out claims, prompt V3 frozen, live Claude calls. Never used during prompt iteration.

| Metric                       | Result            |
| ---------------------------- | ----------------- |
| Root-cause accuracy          | **96%** (24 / 25) |
| Routing accuracy             | **96%**           |
| Critical-recall@10           | **100%**          |
| Spearman urgency correlation | **0.826**         |
| Human-review rate            | 0%                |
| Claims processed / errored   | 25 / 0            |

**This is the number to trust**, not the 100% on dev — the dev set is saturated (§4) and can no longer discriminate. A 4-point drop from dev to held-out data is a normal, healthy generalisation gap; an identical 100% would have been the more suspicious result.

**The one miss, in full — `CLM-2026-8050`.** Truth: `eligibility_mismatch`. The model said `pricing_mismatch` at 0.85 confidence. The note read _"Member shows termed coverage on DOS. Enrollment file lag suspected."_

Its stated reasoning: the prior-auth registry showed an approved auth covering the date of service, which it took as proof coverage was active, so it discounted the note — then found `contract_rate: null` and concluded the missing rate was the real blocker.

**Two distinct faults, and the first one is ours:**

1. **Our prompt caused it.** V3's `VERIFY_BLOCK` states: _"A missing evidence.contract_rate entry is positive evidence FOR pricing_mismatch."_ The rate was missing, so the model applied our rule as written. That instruction is too absolute — a missing rate can coexist with an eligibility problem, and nothing told the model to weigh the two. **Fix: soften the rule to "contributing evidence, not decisive."** Recorded as a prompt change for the next version rather than applied now, because the prompt is frozen and re-running the sealed set would destroy its value.
2. **A reasoning error the prompt did not cause.** An approved prior authorisation does not prove eligibility on the date of service — those are separate systems. The model treated one as evidence for the other.

Worth stating plainly: the model produced a _coherent, evidence-citing, wrong_ answer at 0.85 confidence. No confidence threshold would have caught it, because it was not uncertain — it was confidently misled by an instruction we wrote. That is the strongest argument in this package for the guard layer, the human approval gate, and the feedback loop existing at all.

## 5. Failure-mode catalog

Each entry: what happened, how it was detected, what we did. All dated in `DECISION_LOG.md`.

1. **Evidence contamination via shared fixture keys.** The contract-rate table is keyed `provider|cpt`, which is _shared_ across claims — so other claims filled in seven rate gaps that were supposed to be the evidence for pricing-mismatch claims. On `CLM-2026-8048` the model flagged the contradiction itself (_"the note says the rate table is missing but evidence.contract_rate shows the rate EXISTS"_), followed the corrupted evidence, and was scored wrong. _Detected by:_ an eval miss, then reading the model's own cited reasoning. _Fixed:_ the generator now strips every pricing claim's key after generation; verified zero collisions. _Production lesson:_ when the model and the system-of-record disagree with a human note, suspect the **data pipeline** before the model — and require citations, because they are what made this diagnosable in minutes.

2. **A ranking metric that measured luck.** The guard's SLA floor flattened 19 breached claims to urgency 95. With no tie-break, "top 10" membership was insertion order, and critical-recall@10 came out at 0.375 — _below the dumb baseline's_ 0.50. _Detected by:_ refusing to accept a nonsensical number and re-ranking by raw model scores (0.625) and with a dollar tie-break (1.00). _Fixed:_ ranking policy is now urgency-desc, dollars-desc on ties, applied identically in the pipeline, baseline, evaluator, and UI, with a regression test.

3. **Trap design falsified / dev-set saturation.** Described in §4. Treated as a finding rather than hidden. Harder adversarial cases — contradictory evidence across _multiple_ systems, auths with stale effective dates, notes phrased unlike anything in the training distribution — are the designed next iteration.

4. **Hallucinated or out-of-range fields.** Structurally prevented: schema-forced tool call, then pydantic re-validation, then the guard's cause↔queue lookup table. Mock mode's deliberately gullible heuristic keeps these guard paths exercised in tests on every run.

5. **Malformed input records.** A stress fixture with a negative amount and missing fields is rejected at validation into an error report while the batch continues (test-covered). One bad record never kills a run.

6. **PHI-pattern leakage.** A stress note containing a fake SSN, DOB, and name is scrubbed before any LLM call; findings are audit-logged; the clinically useful content (CPT code, "no PA on file") survives redaction. Test-covered.

7. **Confidence floor never fired on real output (open).** Across all three live dev runs, no claim fell below the 0.65 floor — the minimum observed confidence was 0.75. That is weak evidence the threshold is well-calibrated; it may simply be set too low to ever bind on data this clean. On messier real notes it could bind too rarely to protect anyone. _Mitigation plan:_ calibrate against shadow-pilot outcomes rather than guessing; monitor the distribution of model confidence as a drift signal.

8. **Automation bias (open, structural).** Analysts may rubber-stamp high-confidence recommendations, which would convert the human approval gate into a formality. _Mitigations:_ confidence and cited evidence are shown on every card so verification is fast; the audit log makes rubber-stamping _measurable_ through time-to-approve distributions; a spot-audit workflow on approved claims is the designed next step.

9. **The fix for failure #1 created its mirror image — surfaced by the sealed set (open).** Failure #1 was "shared `provider|cpt` keys let other claims _fill_ a rate gap that should be empty." Our fix strips every pricing claim's key after generation — but because the key is shared, it also strips rates from non-pricing claims that legitimately had one. **15 of 75 claims (20%) are shown a missing contract rate they should not be.**

   This produced the one sealed-set miss, `CLM-2026-8050`. The note read _"Member shows termed coverage on DOS"_ — unambiguously eligibility. The rate was absent through our bug, and prompt V3 states _"a missing contract_rate entry is positive evidence FOR pricing_mismatch."_ The model followed our instruction over a clear note, at 0.85 confidence, and was scored wrong.

   _Detected by:_ declining to accept the first explanation offered ("the prompt instruction is too absolute") and asking the further question — why was the rate missing at all? _Deliberately not fixed:_ correcting either the fixture or the prompt would require re-running the sealed set, and a held-out set scored twice is not held out. Both are queued for the next version.

   _Three lessons, and the middle one is the general case:_ (a) when you fix a data bug, test the **inverse** — we verified pricing gaps survived, never that non-pricing rates did; (b) **evidence keyed more coarsely than the thing it describes will leak in both directions**, which is a data-modelling fault, not a prompt fault; (c) no confidence threshold catches this class, because the model was confidently wrong while correctly following bad inputs — which is the whole argument for the deterministic guard and the human gate.

## 6. The feedback loop — how analyst corrections improve the system

Implemented in `src/triage/feedback.py`; run with `make feedback`.

Every Approve / Reject / Reassign already lands in the audit log. The review UI now also asks a correcting analyst **what the actual root cause was** — previously it captured only the corrected queue, which recorded the symptom and lost the diagnosis, since routing is derived from cause via the taxonomy map.

### The rule everything else follows from: an approval is not a label

A Reject or Reassign costs the analyst effort, so it carries real information. An Approve may mean "correct" — or may mean someone clicked through forty claims in four minutes, which is precisely the automation bias listed in failure mode #8. **Treating approvals as confirmed-correct would feed the system its own output as ground truth and let errors reinforce themselves.** So corrections drive learning; approvals appear only in calibration denominators and are never emitted as labels. This is pinned by `test_feedback.py::test_approvals_never_become_labels`.

### What the loop produces

**1. Regression cases** (`data/feedback_cases.json`) — corrected claims written in the same label schema `evaluate.py` already reads, so they are scoreable immediately. Severity is derived arithmetically from dollars and SLA rather than asked of the analyst: severity here means "what does getting this wrong cost," which is a function of claim facts, and every extra field on the review screen is friction that reduces how much feedback arrives at all.

_This set is a regression suite, not a representative sample._ Every case in it is one the system got wrong, so it answers "have we fixed what we broke?" — never "how accurate are we." Averaging it with the dev set would produce a meaningless number, which is why it lives in its own file. It is also the direct answer to the dev-set saturation in §4: real corrections are the hard cases our generator cannot invent.

**2. Confidence calibration** — correction rate bucketed by model confidence. If confidence is meaningful, correction rate should fall as confidence rises; if it doesn't, confidence is decoration and the floor protects nobody. `recommend_floor()` suggests the lowest floor whose residual error clears a target, **and reports what that floor costs** in claims escalated — a floor that routes everything to a human is perfectly safe and perfectly useless. It declines to recommend at all when the evidence is thin rather than guessing. This is the path to closing failure mode #7, where the 0.65 floor has never actually fired on real output.

**3. Prompt improvement** — the confusion table (which cause pairs get mixed up) plus candidate few-shot exemplars rendered ready to paste. A repeated (said X, actually Y) pair is a _prompt_ problem, not a model problem: it means the taxonomy definitions for X and Y do not separate cleanly in the instructions. Analysts can also mark **"none of these fit"**, which is recorded as a taxonomy gap and never used as a label — recurring gaps are evidence the 7-cause taxonomy itself needs to change.

### What the loop deliberately does not do

No retraining, and no feeding corrections into an inference-time lookup. Every output lands in front of a human before it changes behaviour: eval cases get reviewed, a suggested floor is a recommendation in a report, and exemplars are pasted into a versioned prompt by a person. **The loop closes through a release, not silently** — otherwise the prompt drifts between eval runs and the numbers stop meaning anything.

### Known limits of feedback data itself

These are properties of the data, not bugs, and they bound what any loop can achieve:

- **Delayed and displaced ground truth.** A reassignment says "not my queue" — it does not reliably say which queue _is_ right. The trustworthy label is which team actually closed the claim, known days or weeks later. A first-click correction should not be promoted to ground truth without that confirmation.
- **No counterfactual.** We only observe outcomes for the routing we chose. If a claim goes to Team A and they resolve it, we never learn Team B would have been faster. Only shadow-routing or a holdout slice can measure that.
- **Selection bias.** Feedback exists only where someone bothered to click. Silent downstream fixes never reach the loop, so the correction rate systematically understates the true error rate — which is why the calibration table labels it a lower bound.
- **Survivorship in the archive.** If corrections ever feed an inference-time corpus, errors nobody corrected quietly become precedent. Any such design needs a re-validation set that the loop never touches.

## 7. Human checkpoints

1. **Every action requires human approval.** The system proposes; Approve / Reject / Reassign in the review UI are audit entries. Nothing executes.
2. **The escalation queue is code-owned.** Confidence below 0.65, or a stated cause contradicted by a source system, routes to the Senior Analyst Desk — and the model is schema-blocked from selecting that queue itself, so it cannot dodge accountability by hedging or suppress a review by sounding confident.
3. **The guard is the human's deterministic proxy:** urgency floors, taxonomy-enforced routing, and contradiction checks, all unit-tested to only ever escalate.
4. **Full reconstructability:** every audit row carries model name, prompt version, input hash, actor, and outcome, append-only.

## 8. How AI assisted the build itself

This project was built pair-programming with Claude Code, with the developer directing scope, ratifying every design decision (`DECISION_LOG.md`), and reviewing all output. The division of labour is worth stating plainly: the human chose the track, the taxonomy, the guard philosophy, and the evaluation discipline — and caught the "recall@10 worse than baseline" anomaly by refusing to accept a number that made no sense. The AI wrote module code against those contracts, ran the diagnostic loops that isolated the tie-break gap and traced the fixture collision, and drafted documentation the human edited.

Both production bugs in §5 were found _because_ the workflow demanded an explanation for every anomalous number instead of accepting the summary table. All AI-generated code is covered by the same 26-test suite as everything else, including a property test asserting the guard can never lower urgency.
