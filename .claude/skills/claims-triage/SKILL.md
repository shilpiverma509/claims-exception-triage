---
name: claims-triage
description: Triage a stuck (pended) insurance claim end-to-end — diagnose the root cause from a fixed taxonomy, check source-system evidence, score urgency, route to an owner team, and decide whether a human must review it. Use when asked to triage, diagnose, or route a claim; to explain why a claim is stuck or why the system routed it somewhere; to audit a triage run; or to turn analyst corrections into system improvements.
---

# Claims Exception Triage Workflow

The operating procedure that `src/triage/` automates. Follow it when working a claim by hand, explaining a decision the system already made, or auditing a run.

> **What this skill is not.** It does not drive the application. The app's LLM follows `src/triage/prompts.py`; the deterministic rules live in `src/triage/guard.py`. This skill describes the same procedure for a human or an assistant reasoning about claims outside the pipeline. **If this document and the code ever disagree, the code is correct** — `prompts.py`, `guard.py`, and `config.py` are the source of truth.

## The rule everything else serves

**The model proposes, code disposes, a human approves.** Never present a triage conclusion as an action taken. Nothing here pays, denies, or adjusts a claim. The output is a recommendation plus a routing decision that a human accepts or overrides.

## Step 1 — Validate first

Confirm the claim parses against `Claim` in `src/triage/models.py`: required fields present, `billed_amount_cents` a non-negative integer, dates real.

If it fails, record the reason and move to the next claim. **One malformed record is an error-report line, never a crashed batch.**

## Step 2 — Redact before any text leaves the boundary

Run `redact.redact()` on `adjudicator_note` and `history_snippet` *before* the text reaches a model or a log. It strips SSN, date of birth, phone, email, and `Member <Name>` patterns.

Use the redacted text everywhere downstream. Never quote the original. Findings go to the audit log so the control is provable rather than asserted.

## Step 3 — Gather evidence; treat the note as a claim, not a fact

Adjudicator notes are written by busy people under time pressure and are often wrong or imprecise. Treat the note as a hypothesis, then check it:

| System | Lookup key | What it settles |
|---|---|---|
| `pa_registry` | `member_ref\|cpt_code` | whether a valid prior authorisation exists |
| `claim_history` | `claim_id` | prior touches; whether a matching earlier submission exists |
| `contract_rates` | `provider_npi_ref\|cpt_code` | whether a fee-schedule entry exists this quarter |

**Source systems outrank notes.** Three rules follow:

- Never conclude `missing_prior_auth` if the registry shows `status: approved` with dates covering the service — the auth exists, whatever the note says.
- Never conclude `duplicate_claim` without a matching entry in `history.prior_submissions`.
- A missing `contract_rate` entry is **contributing** evidence for `pricing_mismatch` — not decisive. It can coexist with an eligibility or coding problem. *(This wording matters: the prompt's older, absolute phrasing caused the single sealed-set miss, `CLM-2026-8050`. See AI_EVIDENCE §4a.)*

Also beware: an approved prior authorisation does **not** prove the member was eligible on the date of service. Those are separate systems answering separate questions.

## Step 4 — Produce the assessment

Choose exactly one value from each fixed list. Never invent a category.

- **Root cause:** `missing_prior_auth` · `eligibility_mismatch` · `coding_error` · `duplicate_claim` · `cob_conflict` · `provider_data_mismatch` · `pricing_mismatch`
- **Action:** `request_prior_auth_docs` · `verify_eligibility` · `return_for_recoding` · `deny_as_duplicate` · `coordinate_benefits` · `update_provider_record` · `reprice_per_contract` · `escalate_to_supervisor`
- **Queue:** `prior_auth_team` · `eligibility_team` · `coding_review` · `cob_unit` · `provider_data_mgmt` · `pricing_team`

**Never select `human_review`.** That routing belongs to the guard. Choosing it yourself would let an uncertain answer bypass the deterministic check that exists to catch it — the API schema blocks the model from this value for the same reason.

Cite evidence for every conclusion: quote the note fragment, history event, or registry field. State confidence honestly and do not inflate it. A well-founded 0.6 is more useful than a decorative 0.9.

## Step 5 — Apply the guard (deterministic, never skipped)

`guard.apply_guard()` may only make the outcome **more** conservative. Four checks, in order:

1. **Cause → queue consistency.** The team is looked up from the cause via `ROOT_CAUSE_TO_QUEUE`. A cause/queue pairing outside that table cannot be recorded.
2. **Urgency floors (raise only).** ≥ $10,000 → at least 80 · SLA due within 3 days → at least 80 · SLA already passed → at least 95.
3. **Evidence contradictions → human review.** Cause is `missing_prior_auth` but the registry shows an approved auth; or cause is `duplicate_claim` with no prior submission on record.
4. **Confidence floor.** Below `config.CONFIDENCE_FLOOR` (0.65) → human review.

The invariant, pinned by `tests/test_guard.py::test_guard_never_lowers_urgency`: **the guard never lowers urgency and never raises confidence.**

## Step 6 — Rank and hand off

Sort by final urgency descending, **breaking ties by billed dollars**. This matters: the SLA-breach floor pins many claims to 95 at once, and without the tie-break a $42,000 claim can sit below a $45 one — a bug this project actually shipped and caught (DECISION_LOG, 2026-08-23).

Write every stage to the append-only audit log with model name, prompt version, input hash, and actor.

## Step 7 — Close the loop on analyst corrections

When a human rejects or reassigns, the review app records the **corrected root cause**, not just the corrected queue — the queue is derived from the cause, so capturing only the queue keeps the symptom and loses the diagnosis.

`feedback.py` turns those corrections into three things:

1. **Regression cases** — corrected claims as labelled tests. This set is a *regression suite*, not a representative sample: every case in it is one the system got wrong. Score it to ask "did we fix what we broke", never "how accurate are we".
2. **Calibration** — correction rate by confidence bucket. `recommend_floor()` suggests a threshold **and reports its cost** in extra escalations, and declines to recommend when the evidence is thin.
3. **Prompt material** — confusion pairs and candidate few-shot exemplars. A repeated (said X, actually Y) pair is a *prompt* problem: those two definitions do not separate cleanly in the instructions.

**The governing rule: an approval is not a label.** A reject or reassign costs the analyst effort, so it carries information. An approval may mean "correct", or may mean someone clicked through forty claims in four minutes. Treating approvals as confirmed-correct would feed the system its own output and let errors reinforce themselves. Corrections drive learning; approvals are calibration denominators only. Pinned by `test_feedback.py::test_approvals_never_become_labels`.

Nothing retrains automatically. Every output goes in front of a human before it changes behaviour — **the loop closes through a release, not silently.**

## Limits to state when they are relevant

- **The loop cannot tell a correction from an analyst mistake.** It trusts the human. A correction is provisional; the trustworthy label is which team actually *closed* the claim, known days or weeks later.
- **Confidence does not catch confident errors.** The sealed-set miss was a coherent, evidence-citing, wrong answer at 0.85 confidence. No threshold would have caught it. That is why the guard, the human gate, and the feedback loop all exist rather than relying on the model's self-assessment.
- **All measured numbers are synthetic.** Difficulty was under our control; real notes vary far more.

## Commands

```bash
make data                          # regenerate claims and mock source systems
make triage MODE=mock              # full pipeline, no network, no cost
make triage MODE=live              # real Claude calls (needs ANTHROPIC_API_KEY); prompt defaults to v3
make eval                          # metrics, confusion matrix, markdown report
make feedback                      # analyst corrections -> regression cases, calibration, prompt material
make test                          # 43 tests, including the guard invariant
make demo                          # Streamlit review UI
```

Reference: `docs/AI_EVIDENCE.md` (evaluation and failure modes) · `src/triage/config.py` (every threshold, single source of truth) · `DECISION_LOG.md` (why each choice was made).
