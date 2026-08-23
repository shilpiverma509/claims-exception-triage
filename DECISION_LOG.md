# Decision Log — Claims Exception Triage Assistant

Every ratified build decision, with the rejected alternative and Shilpi's rationale.
This file is the raw script for the demo narrative's "build decisions and tradeoffs" third.

| Date | Decision | Alternative rejected | Rationale (Shilpi's words) |
|------|----------|----------------------|----------------------------|
| 2026-08-22 | Track 1: Claims Exception Triage over Prior Auth agent | Multi-step PA intake agent | Packet says choose smaller build and show it well; hours go to evals/controls, not plumbing |
| 2026-08-22 | Fixed root-cause taxonomy (7 causes) as enums; LLM must pick from list | Free-form root-cause text | _pending Shilpi's ratification_ |
| 2026-08-22 | Money as integer cents everywhere | Floats | _pending_ |
| 2026-08-22 | ~30% of claims get generic pend codes (P99/P00) | All claims carry accurate codes | _pending — this IS the answer to "why an LLM at all"_ |
| 2026-08-23 | Project renamed ClaimsTriageAssistance; target path ~/Documents/workspace/projects/ClaimsTriageAssistance | keep cloud-only name | match Shilpi's workspace layout |
| 2026-08-23 | Mock enterprise systems as JSON fixtures with adapter-style lookup() | hardcode evidence into prompts | production swap = adapter change; evidence citable |
| 2026-08-23 | 3 disprovable PA traps planted | all-consistent evidence | tests whether model checks registry vs trusting the note |
| 2026-08-23 | Redaction runs before every LLM call, findings audited | trust synthetic data | control must exist & be tested regardless of data |
| 2026-08-23 | Ranking policy: urgency desc, then billed dollars desc as tie-break (pipeline, baseline, evaluate, UI all share it) | Sort by urgency alone | Guard's SLA floor flattened 19 claims to 95; arbitrary tie order buried $42k claims under $45 ones — critical-recall@10 was 0.375 by luck of insertion order, 1.00 with the tie-break. Metric measured tie-breaking, not ranking. |
| 2026-08-23 | contract_rates fixture strips ALL provider+cpt keys belonging to pricing_mismatch claims (post-pass) | leave shared-key collisions | Rate keys are shared across claims; non-pricing claims filled 7 "deliberate gaps," contaminating the evidence. Found via the single live-run root-cause miss (CLM-2026-8048): Claude flagged the note/evidence contradiction and got penalized for trusting evidence that was wrong. Failure-mode catalog entry: evidence keyed too coarsely = cross-claim contamination. |
