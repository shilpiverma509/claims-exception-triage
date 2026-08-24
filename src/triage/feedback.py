"""Analyst feedback loop (T20): turn human decisions into system improvements.

The review UI already records every Approve / Reject / Reassign to the audit
log. This module reads those decisions back, joins them against what the system
actually said, and turns them into three things it can act on:

  1. REGRESSION CASES  — corrected claims become labeled test cases
  2. CALIBRATION       — correction rate by confidence bucket tunes the floor
  3. PROMPT MATERIAL   — confusion patterns and candidate few-shot exemplars

What this module deliberately does NOT do
-----------------------------------------
It does not retrain anything, and it does not feed corrections back into an
inference-time lookup. Both are possible; neither is safe to do unsupervised in
a regulated workflow at this evidence level. Every output here lands in front of
a human before it changes system behaviour: eval cases get reviewed, a suggested
confidence floor is a recommendation in a report, and exemplars are pasted into
a versioned prompt by a person. The loop closes through a release, not silently.

The rule that governs everything below
--------------------------------------
**An approval is not a label.** A Reject or Reassign cost the analyst effort and
carries real information. An Approve may mean "correct", or it may mean someone
clicked through forty claims in four minutes — which is exactly the automation
bias this project already lists as a known failure mode. Treating approvals as
confirmed-correct would feed the system its own output as ground truth and let
errors reinforce themselves. So corrections drive learning; approvals are counted
for calibration denominators only, and are never emitted as labels.

Run:
  python -m triage.feedback --run outputs/triage_dev_claims_live_v3.json --report
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from triage import config
from triage.models import ROOT_CAUSE_TO_QUEUE, RootCause

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "outputs" / "audit.jsonl"
OUT_DIR = ROOT / "outputs"
DATA_DIR = ROOT / "data"

# An analyst saying "none of these fit" is information about the TAXONOMY, not
# about the model. Tracked separately so it can never become a training label.
TAXONOMY_GAP = "none_of_these"

CORRECTING_DECISIONS = frozenset({"reject", "reassign"})


@dataclass
class Correction:
    """One human decision joined against what the system proposed."""
    claim_id: str
    ts: str
    actor: str
    decision: str                       # approve | reject | reassign
    system_root_cause: str
    system_queue: str
    system_confidence: float
    prompt_version: str
    corrected_root_cause: Optional[str] = None
    corrected_queue: Optional[str] = None
    note: str = ""

    @property
    def is_correction(self) -> bool:
        return self.decision in CORRECTING_DECISIONS

    @property
    def is_taxonomy_gap(self) -> bool:
        return self.corrected_root_cause == TAXONOMY_GAP

    @property
    def has_usable_label(self) -> bool:
        """True only when a human named a cause inside our taxonomy.

        A bare Reject ("this is wrong") tells us the answer was wrong but not
        what the right answer is — useful for calibration, useless as a label.
        """
        return (self.is_correction
                and self.corrected_root_cause is not None
                and not self.is_taxonomy_gap)

    @property
    def is_cause_correction(self) -> bool:
        """The analyst named a DIFFERENT cause than the system did.

        Reassigning while keeping the same cause is not a diagnostic correction —
        the analyst is moving the work, not disputing the diagnosis. Those cases
        still count as interventions for calibration, but they must not become
        regression cases (the system already gets them right, so they would pad
        the suite with passing tests) and must not become exemplars (an example
        reading "X, NOT X" teaches nothing).
        """
        return self.has_usable_label and self.corrected_root_cause != self.system_root_cause


def _load_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_corrections(run_path: Path, audit_path: Path = AUDIT_PATH) -> list[Correction]:
    """Join human decisions against the run that produced them.

    Where an analyst decided the same claim more than once, the latest decision
    wins — people change their minds, and the last word is the real one.
    """
    run = {r["claim"]["claim_id"]: r for r in json.loads(run_path.read_text())}

    latest: dict[str, dict] = {}
    for row in _load_audit(audit_path):
        if row.get("stage") == "human_decision" and row.get("claim_id") in run:
            latest[row["claim_id"]] = row   # later rows overwrite earlier ones

    corrections: list[Correction] = []
    for claim_id, row in latest.items():
        result = run[claim_id]
        outcome = row.get("outcome") or {}
        if isinstance(outcome, str):        # tolerate older free-text rows
            outcome = {"decision": outcome}
        corrections.append(Correction(
            claim_id=claim_id,
            ts=row.get("ts", ""),
            actor=row.get("actor", "unknown"),
            decision=outcome.get("decision", "unknown"),
            system_root_cause=result["assessment"]["root_cause"],
            system_queue=result["final_queue"],
            system_confidence=float(result["assessment"]["confidence"]),
            prompt_version=result.get("prompt_version", ""),
            corrected_root_cause=outcome.get("corrected_root_cause"),
            corrected_queue=outcome.get("corrected_queue"),
            note=outcome.get("note", ""),
        ))
    return corrections


# ── Use 1: grow the eval set ──────────────────────────────────────────────

def _severity_from_claim(claim: dict, today: date) -> int:
    """Severity for a feedback case, derived arithmetically from dollars and SLA.

    Deliberately NOT asked of the analyst. Severity in this project means
    "how much does getting this wrong cost", which is a function of claim facts,
    not an opinion — and every extra field on the review screen is friction that
    reduces how much feedback we get at all.
    """
    amount = claim["billed_amount_cents"]
    days = (date.fromisoformat(str(claim["sla_due_date"])) - today).days
    s = 1
    if amount > 100_000:
        s += 1
    if amount > 1_000_000:
        s += 1
    if days <= 3:
        s += 1
    if days <= 0:
        s += 1
    return min(s, 5)


def build_regression_cases(corrections: list[Correction], run_path: Path,
                            today: date = date(2026, 8, 23)) -> list[dict]:
    """Corrected claims, in the same label schema `evaluate.py` already reads.

    IMPORTANT — what this set is and is not. Every case here is one the system
    got WRONG, so it is a **regression suite**, not a representative sample.
    Scoring against it answers "have we fixed what we broke?" It does NOT answer
    "how accurate are we", and averaging it together with the dev set would
    produce a meaningless number. Kept in its own file for exactly that reason.
    """
    run = {r["claim"]["claim_id"]: r for r in json.loads(run_path.read_text())}
    cases = []
    for c in corrections:
        if not c.is_cause_correction:
            continue
        claim = run[c.claim_id]["claim"]
        cause = RootCause(c.corrected_root_cause)
        cases.append({
            "claim_id": c.claim_id,
            "root_cause": cause.value,
            "owner_queue": ROOT_CAUSE_TO_QUEUE[cause].value,
            "severity": _severity_from_claim(claim, today),
            "is_ambiguous": True,   # it fooled the system once; treat as hard
            "source": "analyst_correction",
            "corrected_by": c.actor,
            "corrected_at": c.ts,
            "system_said": c.system_root_cause,
        })
    return cases


# ── Use 2: calibrate the confidence floor ─────────────────────────────────

def calibration_table(corrections: list[Correction],
                      bucket: float = 0.05) -> list[dict]:
    """Correction rate by model-confidence bucket.

    If the model's confidence is meaningful, correction rate should fall as
    confidence rises. If it doesn't, confidence is decoration and the floor is
    protecting nobody — which is a finding worth having.

    Caveat that must travel with these numbers: the denominator counts approvals,
    and approvals are weak evidence of correctness. So the correction rate is a
    LOWER BOUND on the true error rate in every bucket.
    """
    buckets: dict[float, list[Correction]] = defaultdict(list)
    for c in corrections:
        # round() before int() because 0.95/0.05 evaluates to 18.999...; without
        # it, exact bucket boundaries fall one bucket too low.
        index = int(round(c.system_confidence / bucket, 9))
        floor = min(index * bucket, 1.0 - bucket)
        buckets[round(floor, 4)].append(c)

    rows = []
    for lo in sorted(buckets):
        group = buckets[lo]
        corrected = sum(1 for c in group if c.is_correction)
        rows.append({
            "confidence_range": f"{lo:.2f}–{lo + bucket:.2f}",
            "n_decided": len(group),
            "n_corrected": corrected,
            "correction_rate": corrected / len(group),
        })
    return rows


def recommend_floor(corrections: list[Correction], bucket: float = 0.05,
                    target_error_rate: float = 0.05) -> dict:
    """Lowest confidence floor whose correction rate sits under the target.

    Returns the recommendation plus its cost — how many claims would have been
    escalated at that floor — because raising the floor is not free. A floor that
    routes everything to a human is perfectly safe and perfectly useless.
    """
    rows = calibration_table(corrections, bucket)
    n = len(corrections)
    candidate = None
    for row in rows:
        lo = float(row["confidence_range"].split("–")[0])
        # A floor at `lo` auto-routes everything below it, so judge each bucket
        # from `lo` upward.
        at_or_above = [c for c in corrections if c.system_confidence >= lo]
        if not at_or_above:
            continue
        err = sum(1 for c in at_or_above if c.is_correction) / len(at_or_above)
        if err <= target_error_rate:
            candidate = {
                "recommended_floor": round(lo, 2),
                "residual_error_rate_above_floor": round(err, 4),
                "claims_escalated_at_this_floor": n - len(at_or_above),
                "escalation_rate": round((n - len(at_or_above)) / n, 4) if n else 0.0,
            }
            break
    return {
        "current_floor": config.CONFIDENCE_FLOOR,
        "target_error_rate": target_error_rate,
        "n_decisions": n,
        "recommendation": candidate,
        "note": ("Insufficient evidence — no confidence bucket meets the target. "
                 "Collect more decisions before moving the floor."
                 if candidate is None else
                 "Recommendation only. Changing config.CONFIDENCE_FLOOR is a "
                 "reviewed release, not an automatic update."),
    }


# ── Use 3: improve the prompt ─────────────────────────────────────────────

def confusion_pairs(corrections: list[Correction]) -> list[dict]:
    """Which cause pairs get mixed up, most frequent first.

    A repeated (said X, actually Y) pair is a prompt problem, not a model
    problem: it means the taxonomy definitions for X and Y do not separate the
    two clearly enough in the instructions.
    """
    pairs = Counter((c.system_root_cause, c.corrected_root_cause)
                    for c in corrections if c.is_cause_correction)
    return [{"system_said": a, "analyst_said": b, "count": n}
            for (a, b), n in pairs.most_common()]


def taxonomy_gaps(corrections: list[Correction]) -> list[dict]:
    """Cases where no existing cause fit — evidence the taxonomy needs a change."""
    return [{"claim_id": c.claim_id, "system_said": c.system_root_cause, "note": c.note}
            for c in corrections if c.is_taxonomy_gap]


def candidate_exemplars(corrections: list[Correction], run_path: Path,
                        limit: int = 3) -> list[str]:
    """Corrected claims rendered as few-shot exemplars, ready to paste.

    Emitted as text for a human to review and add to `prompts.py` as a new
    version. Not auto-injected: a prompt change must be a versioned, evaluated
    release, or the eval numbers stop meaning anything.
    """
    run = {r["claim"]["claim_id"]: r for r in json.loads(run_path.read_text())}
    out = []
    for c in corrections:
        if not c.is_cause_correction or len(out) >= limit:
            continue
        claim = run[c.claim_id]["claim"]
        out.append(
            f'Claim: note "{claim["adjudicator_note"]}", pend code '
            f'{claim["pend_reason_code"]}.\n'
            f'→ root_cause: {c.corrected_root_cause} (NOT {c.system_root_cause} — '
            f'an analyst corrected this case on {c.ts[:10]}), '
            f'queue: {c.corrected_queue or ROOT_CAUSE_TO_QUEUE[RootCause(c.corrected_root_cause)].value}.'
        )
    return out


# ── Report ────────────────────────────────────────────────────────────────

def build_report(corrections: list[Correction], run_path: Path) -> str:
    n = len(corrections)
    n_corr = sum(1 for c in corrections if c.is_correction)
    n_appr = sum(1 for c in corrections if c.decision == "approve")
    labeled = sum(1 for c in corrections if c.is_cause_correction)
    gaps = taxonomy_gaps(corrections)

    L = [f"# Feedback report — {run_path.name}", ""]
    if n == 0:
        L += ["No analyst decisions recorded yet for this run.", "",
              "Decisions are captured when someone clicks Approve / Reject / "
              "Reassign in the review app (`make demo`). Until then there is "
              "nothing to learn from — which is the honest state, not an error."]
        return "\n".join(L) + "\n"

    L += [f"- decisions recorded: **{n}** ({n_appr} approved, {n_corr} corrected)",
          f"- corrections carrying a usable label: **{labeled}**",
          f"- taxonomy gaps flagged: **{len(gaps)}**", "",
          "> Approvals are counted as denominators only. They are never emitted as "
          "labels: an approval may mean the answer was right, or may mean the "
          "analyst clicked through. Only corrections drive learning.", ""]

    L += ["## 1. Regression cases", ""]
    if labeled:
        L += [f"{labeled} corrected claims written to `data/feedback_cases.json` in the "
              "standard label schema.", "",
              "These are all claims the system got **wrong**, so this is a regression "
              "suite, not a representative sample. Score it to answer *did we fix what "
              "we broke*; never average it with the dev set.", ""]
    else:
        L += ["None yet — corrections need a corrected root cause, which the review "
              "app collects on Reject and Reassign.", ""]

    L += ["## 2. Confidence calibration", "",
          "| confidence | decided | corrected | correction rate |", "|---|---|---|---|"]
    for row in calibration_table(corrections):
        L.append(f"| {row['confidence_range']} | {row['n_decided']} | "
                 f"{row['n_corrected']} | {row['correction_rate']:.0%} |")
    rec = recommend_floor(corrections)
    L += ["", f"Current floor: **{rec['current_floor']}**. {rec['note']}"]
    if rec["recommendation"]:
        r = rec["recommendation"]
        L += ["", f"- suggested floor: **{r['recommended_floor']}**",
              f"- residual error above it: {r['residual_error_rate_above_floor']:.1%}",
              f"- cost: {r['claims_escalated_at_this_floor']} claims "
              f"({r['escalation_rate']:.0%}) would route to a human"]
    L += ["", "*Correction rate is a lower bound on true error — approvals in the "
          "denominator are unverified.*", ""]

    L += ["## 3. Prompt improvement", ""]
    pairs = confusion_pairs(corrections)
    if pairs:
        L += ["Most-confused cause pairs — repeated pairs mean the prompt's "
              "definitions do not separate these two clearly enough:", "",
              "| system said | analyst said | count |", "|---|---|---|"]
        L += [f"| {p['system_said']} | {p['analyst_said']} | {p['count']} |" for p in pairs]
        L.append("")
    exemplars = candidate_exemplars(corrections, run_path)
    if exemplars:
        L += ["Candidate few-shot exemplars for the next prompt version "
              "(review before adding to `prompts.py`):", "", "```"]
        L += exemplars
        L += ["```", ""]
    if gaps:
        L += ["### Taxonomy gaps", "",
              "Analysts marked these as fitting no existing cause. Recurring gaps are "
              "a signal to change the taxonomy, not the prompt:", ""]
        L += [f"- `{g['claim_id']}` (system said {g['system_said']}): {g['note']}" for g in gaps]
        L.append("")
    return "\n".join(L) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True, help="triage run the decisions were made against")
    p.add_argument("--audit", default=str(AUDIT_PATH))
    p.add_argument("--report", action="store_true", help="write the markdown report")
    args = p.parse_args()

    run_path = Path(args.run)
    run_path = run_path if run_path.is_absolute() else ROOT / run_path
    corrections = load_corrections(run_path, Path(args.audit))

    cases = build_regression_cases(corrections, run_path)
    if cases:
        (DATA_DIR / "feedback_cases.json").write_text(json.dumps(cases, indent=2))

    if args.report:
        OUT_DIR.mkdir(exist_ok=True)
        report_path = OUT_DIR / f"feedback_report_{run_path.stem}.md"
        report_path.write_text(build_report(corrections, run_path))
        print(f"report → {report_path}")

    print(json.dumps({
        "decisions": len(corrections),
        "corrections": sum(1 for c in corrections if c.is_correction),
        "regression_cases_written": len(cases),
        "taxonomy_gaps": len(taxonomy_gaps(corrections)),
        "floor_recommendation": recommend_floor(corrections)["recommendation"],
    }, indent=2))


if __name__ == "__main__":
    main()
