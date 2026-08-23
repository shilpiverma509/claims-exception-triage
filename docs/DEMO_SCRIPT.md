# Demo Script — Claims Exception Triage Assistant

5-minute recording · beat sheet + spoken lines · Shilpi Verma, Cohort 5

**Setup before recording:** `make demo` running with `triage_dev_claims_live_v3.json` selected in the sidebar; terminal visible in a second window; `outputs/eval_report_triage_dev_claims_live_v3.md` and the confusion-matrix PNG open in tabs. Everything runs offline from saved results — no network dependency during the demo. (Optional live-call moment in Beat 5 needs the API key.)

---

## Beat 1 — The problem (0:00–0:45)

*Screen: the Ranked Queue, zoomed out.*

> "Claims ops analysts spend most of their time *diagnosing* a stuck claim before they can fix it — decoding a stale pend code, reading a messy note, checking three other systems by hand. And because queues are worked oldest-first, not worst-first, a $42,000 surgery with a deadline in two days waits behind a routine $45 office visit.
>
> This assistant pre-diagnoses every exception — why it's stuck, how urgent it really is, what to do next, and who owns it — and a human approves, rejects, or reassigns every single action. It never adjudicates. It never acts on its own."

## Beat 2 — Live triage of a trap claim (0:45–2:00)

*Click: expand CLM-2026-8001 (top of queue, CRITICAL 95).*

> "Here's a $45 claim with a vague note — 'coverage question, member changed employers.' Sounds like it could be a missing prior-auth. We deliberately planted a trap here: the note misleads, but the prior-auth registry holds a valid approval.
>
> Watch what Claude did — [point at summary] — it checked: 'PA registry shows approved auth PA-125103… ruling out missing prior auth… history has no prior submissions, ruling out duplicate… contract rate exists, ruling out pricing.' It ruled causes *out* against source-system evidence before ruling one *in*, cited its evidence, and landed on eligibility mismatch at 0.85 confidence. It reads evidence — it doesn't pattern-match notes. Three traps like this are in the data; it cleared them."

## Beat 3 — The guard catching a failure (2:00–3:00)

*Point at the guard-adjustments box on the same claim.*

> "Now the part I trust more than the model. Claude scored this claim 78. But its SLA deadline passed eight days ago — so this deterministic rule, not the LLM, floored it to 95: [read the adjustment line]. The core design principle: **the model proposes, code disposes.** The guard can only make the system more conservative — raise urgency, force human review — never less. That's not a slide claim; it's a unit-tested invariant that runs on every commit."

*Click: a low-confidence or forced-review claim in the mock run (sidebar-switch briefly, or reference the human_review queue).*

> "And when confidence drops below 0.65, or the model's story contradicts the evidence, the claim doesn't go to a guessed team — it goes to a senior analyst desk. The model isn't even allowed to choose that queue itself; only the deterministic layer routes there."

## Beat 4 — The numbers, with a strawman (3:00–4:00)

*Screen: eval report / comparison table.*

> "Is it actually good? We built the dumbest credible alternative on purpose — root cause from the pend code, urgency from dollars alone. That's what a lookup table gives you, and 30% of our claims carry a generic pend code precisely because that's reality.
>
> Baseline: 72% right on root cause. Claude: 98%. Routing: 76 versus 98. All eight truly critical claims surface in the top ten. That gap **is** the answer to 'why an LLM at all.'
>
> One honest story: our first ranking metric came back *worse* than the baseline. The eval caught it — the guard's SLA floor had flattened 19 claims into a tie, and tie order was luck. One-line ranking fix, re-verified, logged in the decision log. And our single root-cause miss out of 50? Tracing it exposed a bug in our own *test-data generator* — the model had flagged the contradiction itself in its cited reasoning. That's what evaluation discipline buys you."

## Beat 5 — Human gate, audit trail, next iteration (4:00–5:00)

*Click: Approve on a claim, then the Audit Trail tab.*

> "Every action ends here: I approve, and that's a signed audit row — not an executed action. Every LLM input hash, model version, prompt version, guard override, and human click is in one append-only log. Any recommendation can be reconstructed months later.
>
> Next iteration: sealed 25-claim eval set gets scored exactly once now that the prompt is frozen; then shadow-mode pilot against real pends, analyst corrections feeding back as eval cases, and the platform team swaps our JSON fixtures for real system adapters — the lookups were built as adapters so that's a swap, not a redesign.
>
> The one thing that never changes in production: a human approves every action. That's not a limitation — that's the product."

*(Optional flourish if time allows: run `make triage MODE=live INPUT=...` on one claim in the terminal to show a real API call land, then return to the app.)*

---

## Contingencies

- **App won't start:** `outputs/triage_dev_claims_live_v3.json` is plain JSON — open the eval report markdown and walk the same beats from files.
- **Question: "why not multi-agent?"** — single bounded reasoning task per claim; orchestration needed (ordering, retries, thresholds) is deterministic, so it lives in testable code. Agents would add cost, latency, and an unattributable failure surface. (PRD §10, Architecture Decision.)
- **Question: "why Sonnet, not the biggest model?"** — bounded extraction/reasoning task; 98% dev accuracy says the tier is sufficient; right-sizing is itself the judgment being demonstrated.
- **Question: "what about real PHI?"** — see ENTERPRISE_READINESS.md §1: BAA/in-VPC endpoint, field minimization, redaction becomes defense-in-depth.
