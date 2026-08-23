# How one claim travels through the triage assistant

Every stage explained in plain English, with the actual data for one real claim (`CLM-2026-8001`) shown at each step — so you can see not just what the code says, but what it does.

---

## The big picture

Nine files, one job each. A claim walks through them left to right — the same idea as an assembly line: each station does one thing, checks its work, and hands the claim to the next station.

```
raw JSON → models.py → redact.py → store.py → llm.py → guard.py → store.py
           (validate)  (scrub PHI) (fetch     (ask the  (sanity-  (save
                                    evidence)  model)    check)    ranked)
```

**Why split it into so many small files?** Each file can be tested on its own — `redact.py` doesn't need to know an LLM exists, `guard.py` doesn't need to know where the claim came from. That's the whole reason the tests in `tests/` can check "does the guard ever *lower* urgency?" in complete isolation, in milliseconds, with no network call.

---

## Python words you'll see below

A short glossary — just enough to read the snippets that follow. Skip anything you already know.

- **function** — a named, reusable block of code: `def redact(text):` defines a function called `redact` that takes one input.
- **dict** — a lookup table of key → value, written `{"status": "approved"}`. Read a value with `claim["status"]`.
- **class** — a blueprint for a structured object. `class Claim:` defines what fields a claim has.
- **pydantic model** — a class that also checks its own data on creation: wrong type or missing field raises an error immediately, instead of crashing later somewhere confusing.
- **enum** — a fixed list of allowed values. `RootCause.CODING_ERROR` is one of exactly seven allowed causes — nothing else is legal.
- **type hint** — `def f(x: int) -> str:` — `x` should be an int, the function returns a string. Documentation Python can also check.
- **f-string** — `f"billed ${amount}"` — the `f` lets you drop a variable straight into a string.
- **list comprehension** — `[c.value for c in RootCause]` — build a new list by looping in one line: "the `.value` of each `c`, for every `c` in `RootCause`".
- **try / except** — "try this; if it errors, do this instead" — how one bad claim gets caught without crashing the other 74.
- **decorator (`@`)** — a line like `@st.cache_data` above a function that wraps extra behavior around it — here, "remember the result, don't reload the file every click."

---

## Our example claim

This is claim `CLM-2026-8001`, exactly as it exists in `data/dev_claims.json` — one of 75 fake claims a script invented (no real patient data anywhere in this project).

```json
{
  "claim_id": "CLM-2026-8001",
  "member_ref": "MBR-571029",
  "provider_name": "Summit Family Medicine",
  "cpt_code": "70553",
  "billed_amount_cents": 4500,
  "received_date": "2026-07-16",
  "sla_due_date": "2026-08-15",
  "pend_reason_code": "P11",
  "adjudicator_note": "Coverage question raised by intake. Member recently changed employers per note.",
  "history_snippet": "Provider called 2x this week."
}
```

In plain English: a $45 claim came in on July 16, was supposed to be resolved by August 15, and got stuck with a vague note about the member's coverage. Today's date in this project is pinned to **August 23, 2026** — which matters in a minute, because that means this claim's deadline has already passed.

---

## 1. `models.py` — the shape of the data

`src/triage/models.py`

Before anything else happens, the raw dict above gets turned into a `Claim` object. This is the first and most important safety net in the whole project.

```python
class Claim(BaseModel):
    claim_id: str
    billed_amount_cents: int = Field(ge=0, ...)
    received_date: date
    sla_due_date: date
    pend_reason_code: str
    adjudicator_note: str
    ...

class RootCause(str, Enum):
    MISSING_PRIOR_AUTH = "missing_prior_auth"
    ELIGIBILITY_MISMATCH = "eligibility_mismatch"
    CODING_ERROR = "coding_error"
    ...  # 7 total, nothing else is legal
```

**What "pydantic" is doing here:** `BaseModel` comes from a library called pydantic. When you write `Claim(**raw_dict)`, pydantic checks every field: is `billed_amount_cents` really a whole number ≥ 0? Is `received_date` a real date? If anything's wrong, it raises a `ValidationError` right there — the bad data never gets further into the pipeline pretending to be good data.

**Why this matters for the demo:** there's a deliberately broken record in `data/stress_malformed.json` (negative dollar amount, missing fields). When the pipeline hits it, this is the exact check that catches it — the batch reports one error and keeps going instead of crashing. That's `test_pipeline.py::test_malformed_record_survives_batch`.

> **Our claim, after this step:** Valid. It becomes a real `Claim` object — every field the right type, nothing missing.

---

## 2. `config.py` — one place for every number

`src/triage/config.py`

Every threshold in the whole app — urgency bands, dollar floors, the confidence cutoff — lives in exactly one file.

```python
CONFIDENCE_FLOOR = 0.65   # below this → always human review

URGENCY_BANDS = {
    "CRITICAL": (80, 100),
    "HIGH":     (60, 79),
    "MEDIUM":   (35, 59),
    "LOW":      (1, 34),
}

FLOOR_SLA_BREACHED_SCORE = 95   # deadline already passed → score = 95 minimum
```

**Why one file, not scattered constants:** if a reviewer asks "why is $10,000 the cutoff for critical?", there's exactly one line to point at — and one line to change if the answer is "actually let's try $8,000." No other file is allowed to hardcode a number like this.

---

## 3. `redact.py` — scrub before it leaves

`src/triage/redact.py`

This runs on every note *before* it's allowed anywhere near an LLM call. Our example claim's note happens to be clean, so here's a different one from `data/stress_phi_like.json`, built on purpose to contain fake PHI-looking text:

```
"Member John Q. Testcase DOB 01/02/1980 SSN 123-45-6789 called re: knee MRI.
 No PA on file for 70553."
```

```python
PATTERNS = [
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("DOB", re.compile(r"\bDOB[:\s]*\d{1,2}/\d{1,2}/\d{2,4}\b", re.I)),
    ("MEMBER_NAME", re.compile(r"\bMember\s+([A-Z][a-z]+...)")),
    ...
]

def redact(text):
    findings = []
    for name, pattern in PATTERNS:
        if pattern.search(clean):
            findings.append(name)
            clean = pattern.sub(f"[{name} REDACTED]", clean)
    return clean, findings
```

**What a "regex" is:** `re.compile(r"\b\d{3}-\d{2}-\d{4}\b")` is a *regular expression* — a pattern for matching text shapes, not exact text. This one reads as "three digits, a dash, two digits, a dash, four digits" — i.e. the shape of a US Social Security Number, wherever it appears.

> **Result:** Output text: `"Member [REDACTED] DOB [DOB REDACTED] SSN [SSN REDACTED] called re: knee MRI. No PA on file for 70553."` — the clinically useful part (`70553`, "no PA on file") survives; the identity doesn't. `findings = ["SSN", "DOB", "MEMBER_NAME"]` gets written to the audit log, and this cleaned text — never the original — is what any LLM call would see.

---

## 4. `store.py` — asking the other systems

`src/triage/store.py`

A real claims analyst doesn't just read the note — they check the prior-auth system, the claim's history, the contract rate table. This file simulates those three systems as JSON files and looks them up for the LLM.

```python
def evidence_bundle(claim: dict) -> dict:
    return {
        "history": lookup_history(claim["claim_id"]),
        "pa_registry": lookup_pa(claim["member_ref"], claim["cpt_code"]),
        "contract_rate": lookup_rate(claim["provider_npi_ref"], claim["cpt_code"]),
    }
```

> **Our claim, after this step:**
> - `pa_registry`: `status: "approved"`, auth `PA-125103`, valid Jul 16 – Aug 15
> - `history`: 2 prior touches, no prior submissions (not a duplicate)
> - `contract_rate`: on file, no pricing gap

**Why this specific claim is a trap:** the note ("coverage question... changed employers") *sounds* like it could be about missing authorization. But the registry says a valid prior auth already exists. This claim is one of three the data generator deliberately built this way — to test whether the model actually checks the evidence, or just trusts what the note implies. See `src/triage/generate_mock_systems.py`, `trap_ids`.

---

## 5. `llm.py` — the three modes

`src/triage/llm.py`

This is the file that decides *how* to get a diagnosis: a real API call, a replayed one, or a fake one. Here's what the fake one (mock mode) does with our claim:

```python
def _mock_assess(claim, evidence):
    note = claim["adjudicator_note"].lower()
    rules = [
        (("pa", "auth"), RootCause.MISSING_PRIOR_AUTH),
        (("termed", "eligib", "enrollment", ...), RootCause.ELIGIBILITY_MISMATCH),
        ...
    ]
    cause = RootCause.CODING_ERROR              # default if nothing matches
    for keys, rc in rules:
        if any(k in note for k in keys):
            cause = rc
            break
    ...
    vague = any(k in note for k in ("unclear", "unsure", "may be", ...))
    return {"root_cause": cause.value, "confidence": 0.55 if vague else 0.82, ...}
```

**Reading this loop:** `any(k in note for k in keys)` means "is at least one of these keywords present in the note text?" — it's just string search, repeated for each rule in order, first match wins. There's no reasoning, no reading the evidence — it's a keyword scanner standing in for an LLM.

> **Our claim, after this step:** The note contains "coverage" and "employers" → matches the eligibility keywords → `root_cause = eligibility_mismatch`. `confidence = 0.55` (the phrasing reads as borderline/vague to the heuristic). `urgency_score = 22` (a $45 claim scores low on the "dollars-only" part of this formula).

**The important part: mock mode never looks at the evidence.** Notice `_mock_assess(claim, evidence)` takes `evidence` as an argument but never reads it — it never checks `pa_registry`. That's deliberate: mock mode exists purely so the pipeline/guard/UI can be developed and tested with zero network calls and zero cost. It's explicitly documented in the code as "NEVER used for reported eval numbers." A real LLM call, given the same prompt, is instructed to check the registry before concluding anything (see `prompts.py`'s `VERIFY_BLOCK`) — that's the whole point of the trap.

---

## 6. `guard.py` — the model proposes, code disposes

`src/triage/guard.py`

Whatever the LLM (or mock) says, this file gets the final word. It can only make things *more* cautious — raise urgency, force a human to look — never less.

```python
days_to_sla = (claim["sla_due_date"] - today).days

if days_to_sla <= 0 and urgency < FLOOR_SLA_BREACHED_SCORE:
    adjustments.append(f"urgency floor: SLA breached -> {FLOOR_SLA_BREACHED_SCORE}")
    urgency = FLOOR_SLA_BREACHED_SCORE

if assessment.confidence < CONFIDENCE_FLOOR:
    adjustments.append(f"confidence {assessment.confidence:.2f} < {CONFIDENCE_FLOOR} -> human review")
    forced = True

if forced:
    queue = OwnerQueue.HUMAN_REVIEW
```

> **Our claim, after this step:**
> - `days_to_sla`: Aug 15 due date − Aug 23 "today" = **-8** (already breached)
> - floor #1: SLA breached → urgency forced from 22 up to **95**
> - floor #2: confidence 0.55 < 0.65 → **forced_human_review = true**
> - final: **CRITICAL · 95** → routed to `human_review`, not `eligibility_team`

**Notice what *didn't* fire:** there's a third guard check that specifically catches the PA trap: "if the model says `missing_prior_auth` but the registry shows an approved auth, force review." Mock mode guessed `eligibility_mismatch`, not `missing_prior_auth`, so that specific check stayed silent here — the claim still landed in human review anyway, just via the confidence floor instead. You can see that exact contradiction check fire on a different input in `tests/test_guard.py::test_pa_trap_contradiction_forces_review`.

---

## 7. `pipeline.py` — tying it together

`src/triage/pipeline.py`

Everything above is just functions sitting in files. This is the file that actually calls them, in order, once per claim, for every claim in a batch.

```python
for raw in raw_claims:
    try:
        claim = Claim(**raw)                    # 1. validate
    except ValidationError as e:
        errors.append(...); continue            #    bad record -> skip, keep going

    for field in ("adjudicator_note", "history_snippet"):
        c[field], findings = redact(c[field])   # 2. redact

    evidence = store.evidence_bundle(c)          # 3. fetch evidence

    assessment = triage_call(c, evidence, ...)   # 4. ask the LLM (or mock)

    outcome = apply_guard(assessment, c, ...)    # 5. sanity-check

    results.append({...})                        # 6. keep it

results.sort(key=lambda r: r["final_urgency"], reverse=True)
store.save_run(results, run_name)                # 7. save, ranked
```

**Reading `try / except / continue`:** "Try to validate this claim. If it fails, record the error and `continue` — jump straight to the next claim in the loop." This is the whole mechanism behind "one bad claim never takes down the batch."

> **Our claim, all the way through:** 50 claims went in. This one came out ranked **#1** — highest urgency of the whole dev set — sitting in the human-review queue with two guard adjustments logged next to it, ready for an analyst to open and see exactly why.

---

## 8. `audit.py` — the paper trail

`src/triage/audit.py`

One function, `log()`, called after every stage above. It appends one line of JSON per event to `outputs/audit.jsonl` — never edits, never deletes, just adds.

```json
{"ts": "2026-08-23T...", "claim_id": "CLM-2026-8001", "stage": "guard",
 "outcome": ["urgency floor: SLA breached -> 95", "confidence 0.55 < 0.65 -> human review"]}
```

**Why append-only matters:** if a regulator or an analyst later asks "why did the system say this?", every input, every model version, every guard decision, every human click is already sitting in this file in the order it happened. Nothing can be quietly edited after the fact.

---

## 9. `baseline.py` & `evaluate.py` — is it even good?

`src/triage/baseline.py` · `src/triage/evaluate.py`

`baseline.py` is a deliberately dumb comparison: guess the cause from the pend code alone (no note, no evidence), score urgency from dollars alone (no deadline) — "what an analyst has today without reading anything." `evaluate.py` then scores both against the 50 labeled dev claims.

| Metric | Rule-based baseline | Mock LLM (this run) |
|---|---|---|
| Root-cause accuracy | 72% | 88% |
| Routing accuracy | 76% | 78% |
| Urgency vs. severity correlation | 0.55 | 0.86 |

**Why build a "dumb" baseline at all:** without it, "88% accuracy" is a number with no meaning. With it, you can say "the model beats what ops has today by 16 points on diagnosis and reads urgency far more like a human would" — that's the actual argument for building this at all, and even mock mode (the gullible version) already clears the bar.

---

## 10. `review_app.py` — the screen a human sees

`app/review_app.py`

A Streamlit page — Python that turns into a web UI without writing HTML. It loads a saved run from `outputs/`, shows claims ranked by urgency with color-coded bands, and three buttons per claim: Approve, Reject, Reassign.

```python
st.button("Approve", key=f"approve_{cid}",
          on_click=record_decision, args=(cid, "approve"))

def record_decision(claim_id, decision, note=""):
    audit.log("human_decision", claim_id, {"decision": decision}, actor="analyst_streamlit")
```

**The app never *does* anything:** clicking "Approve" doesn't pay a claim or send a letter — it writes one line to the same audit log everything else writes to. The human is always the one who acts; the software only ever records that they did.

---

## Are we calling the real API?

**No — not by accident, and not by default.** `TRIAGE_MODE` defaults to `mock` in `config.py`. Everything above (the 88% vs 72% numbers, the guard catching the SLA breach) came from the keyword-matching fake standing in for Claude — deliberately, so development and tests cost nothing.

| Mode | What happens | Needs a key? | Costs money? | Good for |
|---|---|---|---|---|
| `mock` | Keyword-matching fake, shown above | No | No | Development, tests, CI |
| `live` | Real call to Claude via the Anthropic API | Yes | Yes (small) | Real eval numbers, the actual demo |
| `cached` | Replays a *previous* live response from `outputs/llm_cache.jsonl` | No | No (already paid once) | Reliable, offline, repeatable demo |

**Where this is decided in code:** `config.py`: `TRIAGE_MODE = os.getenv("TRIAGE_MODE", "mock")` — "read the `TRIAGE_MODE` environment variable; if it isn't set, use `\"mock\"`." Set it in `.env` or pass `--mode live` on the command line to override it.

We already ran a live test batch of 8 claims — including this exact example. Real Claude explicitly checked the PA registry and correctly reasoned that prior auth was *not* the issue (rather than mock's blind keyword guess), landing on `eligibility_mismatch` with 0.85 confidence and cited evidence.

---

## Getting ready for the demo

This was already the plan on paper — `PRD-spec.md` flags "demo fragility" as a risk and its own mitigation is: *cache the LLM responses ahead of time; live call optional during the actual demo.* Concretely:

1. **Get an `ANTHROPIC_API_KEY`** and put it in a local `.env` file (never committed).
2. **Run one real batch in `live` mode** on the dev set — every response gets appended to `outputs/llm_cache.jsonl` as it comes back.
3. **Switch to `cached` mode** and re-run — same real answers, replayed instantly, no network needed.
4. **On demo day:** drive the app in `cached` mode for reliability, and optionally trigger one single claim in `live` mode on stage as the "yes, this is really calling Claude" moment.
