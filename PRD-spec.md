# Claims Exception Triage Assistant — Capstone Spec

**Candidate:** Shilpi Verma · **Program:** UHC Tech AI Transformation, Cohort 5 FDE Qualification
**Track:** #1 — Claims Exception Triage Assistant
**Due:** Aug 28, 2026 EOD → email to the two programme reviewers (addresses held privately)
**Stack:** Python (Jupyter notebook for logic + evals) · Streamlit demo app · Anthropic API

---

## 1. One-line pitch

A triage assistant that takes a queue of stuck claims (exceptions), explains *why* each one is stuck, ranks which to work first, proposes the next action, and routes it to the right owner — with a human always approving before anything happens.

## 2. Problem framing (→ Submission component 1: Problem Brief)

- **Target user:** Claims operations analyst working an exception queue (claims that failed auto-adjudication).
- **Pain point:** Analysts spend most of their time *diagnosing* why a claim pended (reading codes, history, notes) before they can act. Queues are worked FIFO or by age, not by impact — high-dollar or SLA-breaching claims wait behind trivial ones.
- **Value hypothesis:** If the assistant pre-diagnoses root cause and ranks urgency, analysts start each case with a head start and work the right cases first → lower handle time, fewer SLA breaches.
- **Success metrics (measured in the prototype):**
  - Root-cause classification accuracy vs labeled synthetic ground truth (target ≥ 85% on the eval set).
  - Routing accuracy (correct owner queue) — target ≥ 90%.
  - Urgency ranking quality: % of "critical" ground-truth cases surfaced in the model's top-N.
  - Proxy time-saving claim stated honestly as a hypothesis, not a measured production result.
- **Constraints & assumptions:** synthetic data only; no PHI; human approves every proposed action; assistant never adjudicates or denies — it only triages.

## 3. Scope

**In scope (v1, demoable):**
1. Ingest a synthetic exception queue (CSV/JSON, ~60–80 claims).
2. Per claim: LLM produces structured output — likely root cause (from a fixed taxonomy), plain-English summary, urgency score with reasons, proposed next action (from a fixed action list), recommended owner queue.
3. Deterministic guard layer validates/overrides the LLM (schema validation, allowed-value checks, dollar/SLA-based urgency floor).
4. Streamlit dashboard: ranked queue, per-claim detail card, approve / reject / reassign buttons (human-in-loop), audit log of every decision.
5. Eval harness in the notebook: labeled eval set, accuracy metrics, confusion matrix, failure-mode catalog.

**Explicitly out of scope (say so in the brief — scoping is scored):** real adjudication, integration with claims platforms, auto-execution of actions, member/provider communication, fine-tuning.

## 4. Synthetic data design

Generate ~60–80 synthetic claims with a Python script (committed to repo, seeded, reproducible):

- Fields: claim_id, member_id (fake), provider, CPT/service code, billed amount, received date, SLA due date, pend reason code, free-text adjudicator note, claim history snippet.
- **Root-cause taxonomy (ground truth label per claim):** missing/invalid prior auth · eligibility mismatch · coding error (CPT/modifier) · duplicate claim · COB (other coverage) conflict · provider data mismatch · pricing/contract mismatch.
- **Owner queues:** PA team, eligibility team, coding review, COB unit, provider data mgmt, pricing.
- Include deliberately hard cases: ambiguous notes, conflicting signals, a claim that looks like PHI-ish free text (to show the redaction guard), one malformed record (to show exception-handling).
- Split: ~50 dev / ~25 held-out eval.

## 5. Architecture

```
queue.json → Ingest & validate (pydantic) → LLM triage call (structured JSON output,
few-shot prompt, taxonomy-constrained) → Guard layer (schema check, allowed values,
urgency floor rules, confidence threshold → "needs human review" fallback)
→ Ranked queue store (SQLite/JSON) → Streamlit UI (review + approve) → Audit log (JSONL)
```

Key design decisions to defend:
- **Structured output with a fixed taxonomy**, not free-form generation — makes evaluation possible and failure modes bounded.
- **LLM proposes, rules dispose:** deterministic guard layer can only make the system *more* conservative (raise urgency, demote confidence, force human review) — never less.
- **Low-confidence path:** below threshold → routed to "human review" bucket instead of guessing. This is a feature, shown in the demo.
- Retries with idempotency (claim_id as key); one claim's failure never kills the batch.

## 6. Evaluation plan (→ component 3: AI Evidence)

- **Eval set:** 25 held-out labeled claims, never used in prompt iteration.
- **Metrics:** root-cause accuracy, routing accuracy, urgency correlation (Spearman vs ground-truth severity), critical-case recall@10.
- **Prompt iteration log:** table of prompt versions → eval scores, showing at least 2–3 iterations (this is the "evaluation discipline" evidence).
- **Failure-mode catalog (be honest — integrity is scored):** ambiguous multi-cause claims, hallucinated reason codes (caught by guard), over-confident urgency on low-dollar claims, note-text distraction. For each: how it's detected, mitigated, or accepted.
- **Known risks:** taxonomy drift in production, synthetic-to-real distribution gap, automation bias (analysts rubber-stamping) — with mitigations.

## 7. Enterprise readiness (→ component 4)

- **Data classification:** prototype = synthetic only. Production assumption: claim data = PHI/restricted → in-VPC or BAA-covered model endpoint, no PHI to external APIs; field-level redaction before any LLM call.
- **Access:** role-based — analysts see their queues; audit log append-only.
- **Audit/logging:** every LLM input/output, guard decision, and human approve/reject logged with timestamp + user; model version and prompt version stamped on every record.
- **Controls:** human approval gate on all actions; confidence thresholds; guard layer; kill switch = fall back to plain FIFO queue.
- **Handoff owner:** claims ops platform team (named role in the doc), with a "what it would take to productionize" list.

## 8. Deliverables mapped to the submission package

| # | Component | Artifact |
|---|-----------|----------|
| 1 | Problem brief | 1-page PDF (from §2–3) |
| 2 | Working artifact | Repo: data generator, notebook (pipeline + evals), Streamlit app, README with setup + sample run |
| 3 | AI evidence | `AI_EVIDENCE.md`: prompts, model/tool choices, eval tables, failure modes, human checkpoints — plus notes on how AI assisted the build itself |
| 4 | Enterprise readiness | `ENTERPRISE_READINESS.md` (from §7) |
| 5 | Demo narrative | 5-min recording: problem → live triage of the queue → one failure case caught by guard → eval results → next iteration path |

## 9. Day-by-day plan (Aug 21 → 28)

- **Day 1 (Fri 21):** Repo skeleton, synthetic data generator + ground-truth labels, pydantic models.
- **Day 2 (Sat 22):** Triage prompt v1, structured-output pipeline, first end-to-end run on dev set.
- **Day 3 (Sun 23):** Guard layer + low-confidence fallback + audit log. Eval harness with metrics.
- **Day 4 (Mon 24):** Prompt iterations v2/v3 against dev set; log scores; freeze prompt; run held-out eval once.
- **Day 5 (Tue 25):** Streamlit app: ranked queue, detail cards, approve/reject, audit view.
- **Day 6 (Wed 26):** Failure-mode catalog, AI evidence doc, enterprise readiness doc, problem brief.
- **Day 7 (Thu 27):** Record 5-min demo, polish README, dry-run setup from clean clone. **Buffer.**
- **Aug 28:** Final read-through, package, email both addresses before EOD.

## 10. PRD addendum (v1.1)

### Goals
1. Cut analyst diagnosis time: assistant pre-writes root cause + summary so the analyst starts at "verify" not "investigate" (proxy metric: ≥85% root-cause accuracy on sealed eval set).
2. Work the queue by impact, not arrival: ≥80% of ground-truth critical claims (severity 4–5) surfaced in the model's top 10.
3. Route right the first time: ≥90% routing accuracy vs labels.
4. Zero unsafe automation: 100% of proposed actions pass through human approval; 0 PHI-like strings reach the LLM (redaction test must pass).
5. Prove judgment to reviewers: every claim in the demo traceable to a logged decision, metric, or documented failure mode.

### Non-Goals (v1)
- **No adjudication or denial decisions** — the assistant never decides payment; that's a regulated, high-stakes act far beyond a prototype's evidence bar.
- **No live system integration** (claims platforms, provider portals) — synthetic queue in, recommendations out; integration is the handoff owner's roadmap.
- **No auto-execution of actions** — even "safe" ones; the human-approval gate is the product's trust foundation, not a limitation.
- **No fine-tuning or custom models** — prompt + guard engineering on a frontier model is cheaper, faster, and easier to audit at this scale.
- **No multi-agent orchestration** — see Architecture Decision below.

### User stories (priority order)
1. As a **claims ops analyst**, I want each stuck claim pre-diagnosed with a plain-English root cause and evidence, so I verify instead of investigate.
2. As a **claims ops analyst**, I want the queue ranked by urgency (dollars + SLA + severity), so the claim that matters most is on top when I log in.
3. As a **claims ops analyst**, I want a proposed next action I can approve, reject, or reassign in one click, so acting is fast but always mine.
4. As a **team lead**, I want misrouted or low-confidence cases to land in a human-review bucket rather than a wrong queue, so trust in the tool survives its mistakes.
5. As a **compliance owner**, I want every model input/output and human decision in an append-only audit log with model+prompt versions, so any recommendation can be reconstructed later.
6. As the **handoff owner**, I want evals, failure modes, and controls documented, so I can decide what productionizing requires without re-doing discovery.

### Requirements
**P0 (cannot ship without):** ingest+validate queue incl. malformed-record survival; PHI-pattern redaction before every LLM call; taxonomy-constrained structured output; guard layer (schema check, urgency floor, confidence threshold → human-review bucket); ranked queue UI with approve/reject/reassign; append-only audit log; eval harness with sealed-set metrics; all five submission docs.
- Acceptance (samples): Given the malformed stress record, when the batch runs, then 74/75 claims process and the bad record lands in an error report — no crash. Given the fake-SSN note, when the claim is triaged, then the string sent to the API contains no SSN/DOB/name pattern. Given confidence < threshold, when guard runs, then queue = human_review and the UI shows "needs review", never a guessed team.

**P1 (nice-to-have):** rule-based baseline comparison in eval report (lookup-table strawman — quantifies the LLM's lift and answers the reviewer's question with a number); per-claim "evidence" list in UI; cached demo mode toggle.

**P2 (design for, don't build):** feedback loop (analyst corrections becoming eval cases); batch re-triage on data updates; multi-queue load balancing.

### Open questions
- **Shilpi (blocking, Day 4):** freeze-day integrity call — what do we report if sealed-set score < dev-set score? (Drill ledger item #2.)
- **Shilpi (non-blocking):** demo recording tool of choice; GitHub repo name.
- **Resolved:** single-pipeline over multi-agent (below); Python+Streamlit; Anthropic API with mock mode.

### Architecture Decision: single pipeline, not multi-agent
**Decision:** one LLM call per claim inside a deterministic Python pipeline; no agent framework, no LLM-to-LLM delegation, no dynamic tool choice.
**Why:** triage of one claim is a single bounded reasoning task over a small context — nothing to parallelize across "specialists," no step where the model must choose tools at runtime. Multi-agent would add latency and cost per claim, a much larger failure surface (inter-agent misunderstandings, cascading hallucinations), and make evaluation nearly impossible to attribute (which agent caused the wrong route?). The orchestration this workflow genuinely needs — ordering, retries, validation, thresholds — is deterministic, so it lives in ordinary code where it is testable and auditable. Agentic *character* is preserved where it earns trust: the LLM proposes, rules dispose, a human approves.
**Reviewer framing:** "We considered a multi-agent design and rejected it: enterprise reviewers don't score architecture ambition, they score whether every component can be tested, audited, and explained. A claim like this would justify agents only if it required multi-step tool use — the Prior Auth track's shape, not ours."

## 11. Risks

- **Scope creep** → the out-of-scope list in §3 is a contract; anything new goes to "next iteration path" in the demo.
- **Eval set contamination** → held-out set touched exactly once, after prompt freeze.
- **Demo fragility** → cache LLM responses for the demo run; live call optional.
- **Time** → Streamlit app is the cut line: if Day 5 slips, the notebook + a static ranked-queue HTML export is an acceptable fallback (packet allows "runnable notebook").
