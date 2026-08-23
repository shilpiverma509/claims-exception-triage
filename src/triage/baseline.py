"""Rule-based baseline (T9): quantifies the LLM's lift.

A claims-ops "strawman" with no LLM call at all: look up root cause from the
pend_reason_code alone (the only structured signal an analyst has before
reading the note), route by the fixed taxonomy map, and score urgency from
billed dollars alone — no SLA, no note, no source-system evidence. This is
deliberately dumb: ~30% of claims carry a generic pend code (P99/P00, see
generate_data.py) that reveals nothing, so the baseline must guess blind.
Every point evaluate.py shows the LLM beating this by is the argument for
building the assistant at all (Ratified P1 in LOW_LEVEL_PLAN.md).

Run:
  python -m triage.baseline --input data/dev_claims.json
  python -m triage.baseline --input data/eval_claims.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from triage import config, store
from triage.models import Claim, NextAction, RootCause, ROOT_CAUSE_TO_QUEUE

ROOT = Path(__file__).resolve().parents[2]

# P10..P16 are assigned 1:1 to the taxonomy in generate_data.py — this table
# is the only "diagnosis" a FIFO/code-lookup process has today.
PEND_CODE_TO_CAUSE: dict[str, RootCause] = {
    f"P{10 + i}": cause for i, cause in enumerate(RootCause)
}

# No signal at all (generic P99/P00 code) → fall back to a fixed guess. This
# IS the failure mode the baseline exists to expose, not a bug to fix.
DEFAULT_CAUSE = RootCause.CODING_ERROR
DEFAULT_CONFIDENCE = 0.3
KNOWN_CODE_CONFIDENCE = 1.0

ACTION_MAP: dict[RootCause, NextAction] = {
    RootCause.MISSING_PRIOR_AUTH: NextAction.REQUEST_PRIOR_AUTH_DOCS,
    RootCause.ELIGIBILITY_MISMATCH: NextAction.VERIFY_ELIGIBILITY,
    RootCause.CODING_ERROR: NextAction.RETURN_FOR_RECODING,
    RootCause.DUPLICATE_CLAIM: NextAction.DENY_AS_DUPLICATE,
    RootCause.COB_CONFLICT: NextAction.COORDINATE_BENEFITS,
    RootCause.PROVIDER_DATA_MISMATCH: NextAction.UPDATE_PROVIDER_RECORD,
    RootCause.PRICING_MISMATCH: NextAction.REPRICE_PER_CONTRACT,
}


def baseline_cause(pend_reason_code: str) -> tuple[RootCause, float]:
    cause = PEND_CODE_TO_CAUSE.get(pend_reason_code)
    if cause is not None:
        return cause, KNOWN_CODE_CONFIDENCE
    return DEFAULT_CAUSE, DEFAULT_CONFIDENCE


def baseline_urgency(billed_amount_cents: int) -> int:
    """Dollars-only score, deliberately blind to SLA/history/note — the gap
    this leaves vs the guard's SLA floors and the LLM's read of the note is
    exactly what the eval report is meant to surface."""
    dollars = billed_amount_cents / 100
    return min(100, max(1, round(dollars / 100)))


def assess(claim: dict) -> dict:
    cause, confidence = baseline_cause(claim["pend_reason_code"])
    queue = ROOT_CAUSE_TO_QUEUE[cause]
    urgency = baseline_urgency(claim["billed_amount_cents"])
    return {
        "claim_id": claim["claim_id"],
        "root_cause": cause.value,
        "summary": f"[BASELINE] pend_reason_code={claim['pend_reason_code']} -> {cause.value}"
                   f" (dollars-only urgency, no note/evidence read).",
        "urgency_score": urgency,
        "urgency_reasons": [f"billed ${claim['billed_amount_cents'] / 100:,.2f} (dollars-only)"],
        "proposed_action": ACTION_MAP[cause].value,
        "owner_queue": queue.value,
        "confidence": confidence,
    }


def run(input_path: Path) -> dict:
    raw_claims = json.loads(input_path.read_text())
    results, errors = [], []

    for raw in raw_claims:
        cid = raw.get("claim_id", "UNKNOWN")
        try:
            claim = json.loads(Claim(**raw).model_dump_json())
        except ValidationError as e:
            errors.append({"claim_id": cid, "error": str(e).splitlines()[0]})
            continue
        assessment = assess(claim)
        results.append({
            "claim": claim,
            "assessment": assessment,
            "guard": {"adjustments": [], "forced_human_review": False},
            "final_urgency": assessment["urgency_score"],
            "final_band": config.band_for(assessment["urgency_score"]),
            "final_queue": assessment["owner_queue"],
            "prompt_version": "baseline", "mode": "baseline",
        })

    # Same ranking policy as the pipeline: urgency desc, dollars break ties.
    results.sort(key=lambda r: (r["final_urgency"], r["claim"]["billed_amount_cents"]),
                 reverse=True)
    run_name = f"triage_{input_path.stem}_baseline"
    out_path = store.save_run(results, run_name)
    if errors:
        (store.OUT_DIR / f"{run_name}_errors.json").write_text(json.dumps(errors, indent=2))
    return {"processed": len(results), "errored": len(errors), "output": str(out_path)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    args = p.parse_args()
    path = Path(args.input)
    summary = run(path if path.is_absolute() else ROOT / path)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
