# Demo Cheat Sheet — plain-English answers

Keep this open during the demo. Every answer here is checked against the actual code.

---

## Q: Where is `triage_dev_claims_live_v3.json`? I don't see it.

It's here: **`outputs/triage_dev_claims_live_v3.json`** (132 KB, on your laptop right now).

You don't see it on GitHub because **`outputs/` is in `.gitignore`** — results files aren't committed, only the code that produces them. In the Streamlit app it shows up in the **sidebar dropdown** as `triage_dev_claims_live_v3.json`.

## Q: What does "v3" mean?

It's the **prompt version** — which set of instructions Claude was given. There are three, and they build on each other:

| Version | What Claude is told |
|---|---|
| **v1** | Your job, and the list of 7 allowed causes. Nothing else. |
| **v2** | v1 **+** "check the evidence before you conclude" rules **+** 3 worked examples |
| **v3** | v2 **+** "quote the exact evidence you used" |

The filename records which one produced the results, so you can compare them fairly later.

## Q: What is a "run"? What do you mean by "the current run"?

A **run** = one time you processed a batch of claims. The output file **is** the run — a saved snapshot, like a printed report.

Read the filename left to right:

```
triage _ dev_claims _ live _ v3 .json
   |         |          |     |
   |         |          |     └── prompt version v3
   |         |          └──────── real Claude API calls (not the fake "mock" mode)
   |         └─────────────────── the 50 dev claims
   └───────────────────────────── a triage run
```

**Key point:** once written, a run file never changes. Clicking Approve or Reassign in the UI does **not** edit it. Your click goes to a *separate* file (the audit log). That's deliberate — the record of what the system said stays frozen, so you can always reconstruct it.

## Q: "An analyst can't create a cause/queue pairing the system considers illegal" — what?

In this system, **each cause has exactly one correct team.** It's a fixed table in `models.py`:

| Cause | Always goes to |
|---|---|
| missing_prior_auth | prior_auth_team |
| eligibility_mismatch | eligibility_team |
| coding_error | coding_review |
| duplicate_claim | coding_review |
| cob_conflict | cob_unit |
| provider_data_mismatch | provider_data_mgmt |
| pricing_mismatch | pricing_team |

So when you pick **"the cause is pricing_mismatch"**, the team is **automatically** pricing_team. You are never asked to choose the team separately, so you can't accidentally record something contradictory like *"cause = pricing problem, send to the COB team"* — that pairing doesn't exist in the system.

The same table is what the guard uses to correct the model. Humans and the model are held to the same rule.

## Q: Explain severity 5 with an example.

Severity is a **1-to-5 score of "how bad is it if we get this one wrong."** It is *calculated*, not opinion. Here is the actual rule:

```
Start at 1
  + 1  if billed over $1,000
  + 1  if billed over $10,000
  + 1  if the deadline is 3 days away or less
  + 1  if the deadline has already passed
Maximum 5
```

**Worked example — CLM-2026-8015** ($42,000, deadline was Aug 17, today is Aug 23):

| Check | This claim | Running score |
|---|---|---|
| start | — | **1** |
| over $1,000? | $42,000 → yes | **2** |
| over $10,000? | $42,000 → yes | **3** |
| deadline ≤ 3 days? | it's 6 days past → yes | **4** |
| deadline already passed? | yes, by 6 days | **5** |

**Severity 5** = "big money **and** already late" = the worst category.

Compare a $45 claim due next month: stays at **1**. Nobody gets hurt if we misjudge it.

**Why we don't ask the analyst:** severity is just arithmetic on dollars and dates — facts, not judgement. And every extra box on the review screen makes people less likely to give feedback at all.

## Q: Where is it mentioned that `system_said` is retained?

In `src/triage/feedback.py`, **line 197**, where each regression case is built:

```python
"system_said": c.system_root_cause,
```

So the saved case keeps **both** answers — what the human said was right, and what the system originally said:

```json
{
  "root_cause": "pricing_mismatch",      ← what the human said
  "system_said": "eligibility_mismatch"  ← what the system got wrong
}
```

Without that second field you'd know the right answer but not what mistake was made.

## Q: What is a "floor"?

A **floor is a minimum acceptable value** — like a minimum height to ride a rollercoaster.

Here it's the **confidence floor**, set in `config.py`:

```python
CONFIDENCE_FLOOR = 0.65   # below → human_review
```

Claude reports how sure it is, from 0 to 1. The rule in `guard.py` (line 54):

```python
if assessment.confidence < config.CONFIDENCE_FLOOR:
    forced = True          # send it to a human instead
```

**In words:** "If Claude is less than 65% sure, don't trust it — give it to a person."

- Raise the floor to 0.90 → safer, but far more work for humans
- Lower it to 0.30 → less human work, but more mistakes slip through

Picking that number well is what *calibration* is for.

## Q: When is `recommend_floor()` called?

**Never during triage.** It only runs when you analyse feedback. Two call sites, both in `feedback.py`:

| Line | Where | When it runs |
|---|---|---|
| 362 | inside `build_report()` | when the feedback report is written |
| 422 | inside `main()` | when you run the command |

Both happen only when you run:

```bash
make feedback
```

Triaging claims never calls it. It's an offline analysis tool.

## Q: What is the calibration table actually telling me?

It answers one question: **"When Claude says it's confident, is it actually right?"**

You group claims by how confident Claude was, then count how often humans corrected each group. A healthy system looks like this:

| Confidence | Decided | Corrected | Correction rate |
|---|---|---|---|
| 0.60–0.65 | 20 | 12 | **60%** ← unsure and often wrong ✅ makes sense |
| 0.80–0.85 | 30 | 6 | **20%** |
| 0.95–1.00 | 50 | 1 | **2%** ← confident and almost always right ✅ |

That's **good** — confidence is meaningful, so the floor protects you.

An unhealthy system looks like this:

| Confidence | Decided | Corrected | Correction rate |
|---|---|---|---|
| 0.60–0.65 | 20 | 8 | **40%** |
| 0.95–1.00 | 50 | 20 | **40%** ← confident but wrong just as often ❌ |

That's **bad** — confidence tells you nothing, so a confidence floor is protecting nobody, and you'd need a different safety mechanism.

**In our one-click example**, Claude was **0.95 confident and got corrected** → correction rate 100% in that bucket. One claim proves nothing, but if that pattern held across hundreds, it would mean the confidence score is decoration.

## Q: If I reassign to pricing, could I be wrong?

**Yes — and the system cannot tell.** It trusts whatever you click.

This is a real limitation, and saying it out loud is a strength in the demo:

- Your reassign is recorded as **provisional**, not proven truth
- The trustworthy answer is **which team actually closed the claim**, known days or weeks later
- Production would join corrections against closure records instead of trusting the first click

**For the specific claim CLM-2026-8015, the analyst would in fact be wrong.** The note says *"Plan ID on claim does not match active segment for the member on date of service"* — that genuinely is an eligibility problem, and the ground-truth label agrees with the system. Reassigning it to pricing would be a human error, and the loop would faithfully record that error.

> **Good demo line:** "The loop can't tell a correction from a mistake — it trusts the human. That's why a correction is provisional until the closing team confirms it."

## Q: Explain "confusion pair" again.

A confusion pair is just a **tally of "system said X, human said Y."**

Think of a teacher noticing students keep mixing up two similar words. One student doing it is a slip. Twenty students doing it means the lesson is unclear.

| system said | analyst said | count |
|---|---|---|
| eligibility_mismatch | pricing_mismatch | 7 |

Seven times the system said *eligibility* when humans said *pricing*. That's not a random error — it's a **pattern**, and it means the written definitions of those two causes in the prompt don't separate clearly enough. The fix is editing the prompt wording, not the model.

## Q: How did the system decide "eligibility_mismatch" from that note?

The note was:

> *"Plan ID on claim does not match active segment for the member on date of service."*

Claude was given this definition in the prompt (from `prompts.py`):

> `eligibility_mismatch`: **member coverage inactive/mismatched for the date of service**

Line them up:

| The note says | The definition says |
|---|---|
| "does not **match**" | "**mismatched**" |
| "active segment" | "coverage **inactive**" |
| "for the **member**" | "for the **member**" |
| "on **date of service**" | "for the **date of service**" |

It's nearly a word-for-word match — which is why Claude was **95% confident**. It also checked the evidence and ruled out the alternatives: prior auth existed, no duplicate submission, contract rate on file.

**It was right.** The ground-truth label for this claim is `eligibility_mismatch`.

---

## The 60-second version for the demo

1. A claim comes in. Code checks it's valid and scrubs anything personal.
2. Code looks up three other systems — prior auth, claim history, contract rates.
3. Claude gets the claim **and** that evidence, and must answer on a fixed form (7 allowed causes, nothing else).
4. **Deterministic code — not Claude — gets the final say.** It can raise urgency and force human review, never relax them.
5. The analyst sees a ranked queue and clicks Approve / Reject / Reassign. **Nothing executes automatically.**
6. A correction writes one line to the audit log. It becomes a permanent regression test, a calibration data point, and a candidate prompt example.
7. A human decides which of those to act on. **The loop closes through a release, not silently.**
