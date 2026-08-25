# Demo Storyboard — what's on screen, shot by shot

Companion to `DEMO_SCRIPT.md`. That file has the **words**; this has the **screen**.

Every file path and line number below is verified. Open files at these lines so you're never scrolling around live.

---

## Set up three windows before recording

| Window | Contents |
|---|---|
| **A — Browser** | http://localhost:8501, run = `triage_dev_claims_live_v3.json`, CLM-2026-8001 at top |
| **B — Editor** | the 6 files in the table below, each already open as a tab |
| **C — Terminal** | in the project dir, font size up, screen cleared |

**Pre-open these editor tabs, in this order** (they're used in this order):

| # | File | Jump to line | Used in |
|---|---|---|---|
| 1 | `src/triage/prompts.py` | **17** | Shot 3 |
| 2 | `src/triage/llm.py` | **43** | Shot 4 |
| 3 | `src/triage/guard.py` | **36** | Shot 6 |
| 4 | `tests/test_guard.py` | **43** | Shot 7 |
| 5 | `src/triage/feedback.py` | **20** | Shot 11 |
| 6 | `tests/test_feedback.py` | **58** | Shot 11 |

Do Not Disturb on. `Cmd+Shift+5` → Record Selected Portion → **Options → Microphone → MacBook Microphone** → Show Mouse Clicks.

---

## Shot list

### Shot 1 · 0:00–0:25 — Window A, ranked queue
**Screen:** the full queue, scrolled to top. Don't expand anything yet.
**Do:** slowly scroll down 2–3 rows and back up, so the colour bands register.
**Say:** Beat 1, first paragraph (the problem).

### Shot 2 · 0:25–0:40 — Window A, still the queue
**Screen:** same. Hover over the CRITICAL band chip.
**Say:** Beat 1, second paragraph ("this assistant pre-diagnoses…").

### Shot 3 · 0:40–0:55 — Window B, `prompts.py:17`
**Screen:** the `TAXONOMY_BLOCK` — the 7 root causes as plain text.
**Say:** "The model picks from exactly seven causes. This is the list, and it's the only thing it's allowed to answer with."
**Why this shot:** proves the taxonomy is real and fixed, not hand-waving.

### Shot 4 · 0:55–1:10 — Window B, `llm.py:43`
**Screen:** the enum line, especially `if q != OwnerQueue.HUMAN_REVIEW`.
**Say:** "And it's enforced by the API schema, not checked afterwards. Notice this line — `human_review` is excluded. The model is structurally unable to route a claim to a human. Only deterministic code does that."
**Why this shot:** this is your single most reviewer-proof line. Show it.

### Shot 5 · 1:10–1:40 — Window A, expand **CLM-2026-8001**
**Screen:** the expanded claim. Point the cursor at the **Summary** text as you read.
**Say:** Beat 2 — the trap claim, and the "ruling out… ruling out… ruling out" quote.
**Do:** also expand **Source-system evidence** briefly so the `pa_registry: approved` is visible.
**Why this shot:** the whole "reads evidence, doesn't pattern-match" argument lands here.

### Shot 6 · 1:40–2:05 — split: Window A guard box, then Window B `guard.py:36`
**Screen:** first the blue **Guard adjustments** box showing `urgency floor: SLA breached → 95`. Then cut to the code that produced it.
**Say:** Beat 3, first two paragraphs. "Claude scored this 82… a deterministic rule raised it to 95… and here's that rule."
**Why this shot:** showing the output *and* the code behind it in sequence is far more convincing than either alone.

### Shot 7 · 2:05–2:30 — Window B, `tests/test_guard.py:43`
**Screen:** `test_guard_never_lowers_urgency` — all five lines fit on screen.
**Say:** "Not a slide claim — this test fails the build if anyone breaks it."
**Optional flourish:** cut to Window C, run `make test`. It finishes in ~5 seconds. Strong live moment.

### Shot 8 · 2:30–2:50 — Window B, `config.py:18`
**Screen:** `CONFIDENCE_FLOOR = 0.65  # below → human_review, never a guessed queue`
**Say:** Beat 3, last paragraph (the escalation rules).

### Shot 9 · 2:50–3:20 — Window B or browser, the eval report
**Screen:** `outputs/eval_report_triage_dev_claims_live_v3.md`, the metrics table.
**Say:** Beat 4 up to and including the results table.
**Do:** if it renders as raw markdown that's fine — reviewers read markdown.

### Shot 10 · 3:20–3:40 — Window C, terminal
**Screen:** run this so the comparison is live on screen:
```bash
python -m triage.evaluate --labels data/dev_labels.json --compare \
  baseline=outputs/triage_dev_claims_baseline.json \
  v1=outputs/triage_dev_claims_live_v1.json \
  v3=outputs/triage_dev_claims_live_v3.json
```
**Say:** Beat 4's second half — the ranking-metric failure, the generator bug, and the saturation finding.
**Why this shot:** V1 = V2 = V3 = 100% is visible in one table. Say the saturation point *while it's on screen*.

### Shot 11 · 3:40–4:20 — Window A → Window B
**Screen:** in the app, expand a claim, pick a cause in **"Actual root cause"**, click **Reassign**. Then the **Audit Trail** tab so the new row appears. Then cut to `tests/test_feedback.py:58`.
**Say:** Beat 5. Land the "an approval is not a label" line while `test_approvals_never_become_labels` is on screen.
**Do:** use a claim you don't mind marking — the audit log is append-only, so the entry stays.

### Shot 12 · 4:20–5:00 — Window B, `DECISION_LOG.md`
**Screen:** scroll the decision log slowly — the sheer number of dated rows is the point.
**Say:** Beat 6 — the three tradeoffs, then the next-iteration list, ending on "a human approves every action. That's not a limitation — that's the product."

---

## Cheapest possible version (if you're out of time)

Shots **1 → 5 → 6 → 9 → 12**. Five shots, entirely in the browser plus two files. Covers problem, evidence-reading, the guard, results, and next steps. About three minutes.

---

## Things that will bite you

- **Forgetting the microphone.** `Cmd+Shift+5` defaults to **no audio**. Check Options every single take.
- **Recording the whole screen.** Select just the window — otherwise you catch notifications, your dock, other tabs.
- **Live-reloading Streamlit.** Don't edit files in Window B *while* the app is running from the same directory — Streamlit will detect the change and rerun, which looks like a crash mid-demo. Open files read-only, don't save.
- **Expanding two claims at once.** The queue gets long and you lose your place. Collapse one before expanding the next.
- **Reading the script word-for-word at speed.** Slow down about 20% from how it reads in your head. The timings already assume that.

## One rehearsal trick

Record Shot 1 alone, watch it back, and check three things: is your voice audible, is the text large enough to read, and are mouse clicks visible? Fix those once, then do the full take. It saves re-recording five minutes because the font was too small.
