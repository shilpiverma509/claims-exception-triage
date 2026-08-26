# Demo Script — Claims Exception Triage Assistant

5-minute recording · Shilpi Verma, Cohort 5

Covers the four things asked for: **build decisions · tradeoffs · measured results · next iteration path.**

> Every number here is verified against a saved run. Do not round up, do not improvise figures — the honesty is the pitch.

---

## Before you hit record

| Check | Command / action |
|---|---|
| App running | `make demo` → http://localhost:8501 |
| Run selected | sidebar → **`triage_dev_claims_live_v3.json`** |
| Second window | terminal, in the project directory |
| Tab open | `outputs/eval_report_triage_dev_claims_live_v3.md` |
| Claim ready | scroll so **CLM-2026-8001** is visible at the top |

Everything replays from saved files. **No network needed.** Nothing can time out mid-recording.

---

## Beat 1 · The problem and the shape of the answer — 0:00–0:40

*Screen: the ranked queue.*

> "When a claim fails auto-adjudication it lands in an exception queue, and the analyst's first job isn't fixing it — it's working out *why* it's stuck. They read a stale pend code and a messy note, then check three other systems by hand. And queues get worked oldest-first, not worst-first, so a $42,000 claim past its deadline sits behind a $45 office visit.
>
> This assistant pre-diagnoses every claim — root cause, urgency, next action, owner team — and ranks by impact. A human approves, rejects or reassigns every one. It never adjudicates and never acts on its own."

## Beat 2 · Build decision: structured output over evidence — 0:40–1:40

*Click: expand **CLM-2026-8001**.*

> "A $45 claim, vague note — *'coverage question, member recently changed employers.'* Reads like a missing prior authorization. It isn't. We planted three traps like this: the note misleads, the registry holds a valid approval.
>
> Two build decisions matter. The model never sees just the note — the pipeline fetches evidence from three source systems first. And it can't answer in prose: fixed schema, one of exactly seven causes, so an invalid answer is impossible at the API level.
>
> Watch what it did — [point at summary] — *'PA registry shows approved auth, ruling out missing prior auth… no prior submissions, ruling out duplicate… contract rate exists, ruling out pricing.'* It eliminated three causes against evidence before committing to one. It reads evidence; it doesn't pattern-match notes."

## Beat 3 · Build decision: the model proposes, code disposes — 1:40–2:30

*Point at the guard adjustment on the same claim.*

> "Now the part I trust more than the model. Claude scored this 82. Its deadline passed eight days ago, so a deterministic rule — not the LLM — raised it to 95.
>
> That's the core decision: **the model proposes, code disposes.** The guard can only make the system more conservative — raise urgency, force review — never less. Not a slide claim: a test called `test_guard_never_lowers_urgency` fails the build if anyone breaks it.
>
> Below 0.65 confidence, or if the stated cause contradicts a source system, it goes to a senior analyst. The model is schema-blocked from choosing that queue itself — so it can't hedge its way out, and can't suppress a review by sounding confident."

## Beat 4 · Measured results, including what went wrong — 2:30–3:30

*Screen: the eval report.*

> "Is it good? We built the dumbest credible alternative on purpose: cause from the pend code, urgency from dollars alone. About 30% of claims carry a generic pend code, so it guesses blind exactly where analysts have to think.

| | Baseline | This system |
|---|---|---|
| Root-cause accuracy | 72% | **100%** |
| Routing accuracy | 76% | **100%** |
| Critical claims in top 10 | 50% | **100%** |
| Urgency vs severity correlation | 0.55 | **0.82** |

> "And on the sealed set — 25 claims held back, scored exactly once after freezing the prompt — **96%**. That's the number I'd trust, not the 100%.
>
> But the number I'd point at is a failure. Our first ranking metric came back *worse than that baseline* — 0.375 against 0.50. The eval caught it: the SLA rule had pinned nineteen claims to the same urgency, and with no tie-break, top-ten membership was luck. One line fixed it. Our single miss out of fifty turned out to be a bug in our own test-data generator — the model had flagged the contradiction itself and we'd scored it wrong.
>
> And V1, V2 and V3 all score 100% — zero-shot cleared the traps unprompted. **The dev set is saturated**; it can't discriminate any more. That's a limit of our data, not a win."

## Beat 5 · The feedback loop — 3:30–4:20

*Click: Approve on a claim, then the Audit Trail tab.*

> "Every analyst decision is captured. I approve — that's an audit row, not an executed action.
>
> When someone *corrects* a claim, the UI asks what the actual cause was, and that becomes three things: a permanent regression test, a calibration data point, and a candidate example for the next prompt version.
>
> One rule governs it: **an approval is not a label.** A reject costs the analyst effort, so it means something. An approval might mean 'correct' — or might mean clicking through forty claims in four minutes. Treating approvals as truth would feed the system its own output. So corrections drive learning; approvals are denominators only. And nothing retrains itself — **the loop closes through a release, not silently.**"

## Beat 6 · Tradeoffs and next iteration — 4:20–5:00

*Screen: back to the queue, or the decision log.*

> "Three tradeoffs I'd defend. **Sonnet, not the largest model** — bounded classification, and the results say the tier is sufficient. **A single deterministic pipeline, not multi-agent** — no runtime tool choice to make, and agents would add cost, latency and unattributable failures. **Escalation tuned conservative** — we'd rather waste an analyst's time than miss an error; that's a dial, not a fixed cost.
>
> Next: score the sealed 25-claim set once, now the prompt is frozen. Then a shadow pilot on real pends — every number here is synthetic and we controlled the difficulty, so the time-saving claim stays a hypothesis. Then mature the feedback loop to use *closure* records instead of first-click corrections. Then swap the JSON fixtures for real system adapters — built as adapters, so it's a swap, not a redesign.
>
> The one thing that doesn't change: a human approves every action. That's not a limitation — that's the product."

## If you overrun — the 3-minute cut

Keep Beats **1, 3, 4**. Drop Beat 2 (say "it checks three source systems before answering"), compress Beat 5 to one line ("corrections feed regression tests and calibration; approvals are never treated as labels"), and keep the last sentence of Beat 6.

## Numbers you must not get wrong

| Claim | Correct figure |
|---|---|
| Baseline root-cause / routing | 72% / 76% |
| System root-cause / routing | 100% / 100% |
| Critical-recall@10 | baseline 50% → system 100% |
| Spearman urgency correlation | 0.55 → 0.82 |
| Trap claim CLM-2026-8001 | model 82 → guard 95, confidence 0.88 |
| Sealed set (held out, scored once) | 96% cause · 96% routing · 100% recall@10 |
| Tests passing | 43 |
| Dev / sealed split | 50 / 25 |

## Likely questions

**"Why not multi-agent?"** — One bounded reasoning task per claim, no runtime tool selection needed. The orchestration this actually requires (ordering, retries, thresholds) is deterministic, so it belongs in testable code. Agents would add latency, cost, and make failures unattributable.

**"Why Sonnet and not the biggest model?"** — Bounded extraction and reasoning over ~2,300 tokens. Dev accuracy says the tier saturates the task, so a larger model buys cost and latency for no measured lift.

**"100% looks too good."** — Agreed, and that's why I flagged the saturation: V1, V2 and V3 all hit 100%, so the set can no longer discriminate. The sealed set and a shadow pilot are the real tests. Also note the baseline on the same data only reaches 72%, so the set isn't trivially easy — it's that the model handles this difficulty band.

**"What about real PHI?"** — Prototype is synthetic only. Production needs a BAA or in-VPC endpoint and field-level minimisation; the redaction layer then becomes defence-in-depth rather than the primary control. Details in ENTERPRISE_READINESS §1.

**"What if the analyst's correction is wrong?"** — The loop can't tell a correction from a mistake; it trusts the human. That's why corrections are provisional, and why production should confirm against which team actually closed the claim rather than the first click.

**"Did you consider retrieval / learning from past claims?"** — Yes, and prototyped it: kNN over resolved claims doubling as a cheap pre-filter before the LLM. It's on a branch, deliberately not in v1 — it needs a re-validation set the loop never touches, or uncorrected errors become precedent. Scoped as next iteration.

## Contingencies

- **App won't start:** the run is plain JSON — open `outputs/eval_report_triage_dev_claims_live_v3.md` and walk the same beats from the file.
- **Asked to prove a claim live:** `make test` runs 43 tests in about 4 seconds, including the guard invariant.
