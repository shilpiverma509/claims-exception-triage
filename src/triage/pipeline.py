"""End-to-end triage pipeline (T7).

ingest → validate → redact → evidence lookup → LLM triage → guard → ranked store

Run:
  python -m triage.pipeline --input data/dev_claims.json --mode mock --prompt v1
  python -m triage.pipeline --input data/stress_malformed.json --mode mock
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from triage import audit, config, store
from triage.guard import apply_guard
from triage.llm import triage_call
from triage.models import Claim
from triage.redact import redact

TODAY = date(2026, 8, 23)  # pinned for reproducibility; --today overrides
ROOT = Path(__file__).resolve().parents[2]


def run(input_path: Path, mode: str, prompt_version: str, today: date = TODAY) -> dict:
    raw_claims = json.loads(input_path.read_text())
    results, errors = [], []

    for raw in raw_claims:
        cid = raw.get("claim_id", "UNKNOWN")
        # 1. validate — malformed records go to the error report, batch survives
        try:
            claim = Claim(**raw)
        except ValidationError as e:
            errors.append({"claim_id": cid, "error": str(e).splitlines()[0],
                           "detail_lines": len(str(e).splitlines())})
            audit.log("ingest", cid, "REJECTED: schema validation failed")
            continue
        c = json.loads(claim.model_dump_json())

        # 2. redact free text before anything leaves the boundary
        for field in ("adjudicator_note", "history_snippet"):
            clean, findings = redact(c[field])
            if findings:
                audit.log("redact", cid, {"field": field, "findings": findings})
            c[field] = clean

        # 3. evidence from mock source systems
        evidence = store.evidence_bundle(c)

        # 4. LLM triage (failure-isolated per claim)
        try:
            assessment = triage_call(c, evidence, prompt_version, mode)
            audit.log("llm", cid, assessment.model_dump(), model=config.MODEL_NAME,
                      prompt_version=prompt_version, input_text=c["adjudicator_note"])
        except Exception as e:  # noqa: BLE001
            errors.append({"claim_id": cid, "error": f"llm: {e}"})
            audit.log("llm", cid, f"FAILED: {e}", prompt_version=prompt_version)
            continue

        # 5. deterministic guard
        outcome = apply_guard(assessment, c, evidence, today)
        if outcome.adjustments:
            audit.log("guard", cid, outcome.adjustments)

        results.append({
            "claim": c, "evidence": evidence,
            "assessment": assessment.model_dump(),
            "guard": {"adjustments": outcome.adjustments,
                       "forced_human_review": outcome.forced_human_review},
            "final_urgency": outcome.final_urgency,
            "final_band": config.band_for(outcome.final_urgency),
            "final_queue": outcome.final_queue.value,
            "prompt_version": prompt_version, "mode": mode,
        })

    # Rank by urgency; break ties by dollars so a flat guard floor (e.g. 19 claims
    # all pinned to 95 by the SLA-breach rule) can't bury a $42k claim under a $45 one.
    results.sort(key=lambda r: (r["final_urgency"], r["claim"]["billed_amount_cents"]),
                 reverse=True)
    run_name = f"triage_{input_path.stem}_{mode}_{prompt_version}"
    out_path = store.save_run(results, run_name)
    if errors:
        (store.OUT_DIR / f"{run_name}_errors.json").write_text(json.dumps(errors, indent=2))
    summary = {"processed": len(results), "errored": len(errors), "output": str(out_path),
               "human_review": sum(r["guard"]["forced_human_review"] for r in results),
               "bands": {b: sum(1 for r in results if r["final_band"] == b)
                          for b in config.URGENCY_BANDS}}
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--mode", default=config.TRIAGE_MODE, choices=["live", "mock", "cached"])
    p.add_argument("--prompt", default="v1", choices=["v1", "v2", "v3"])
    p.add_argument("--today", default=str(TODAY))
    args = p.parse_args()
    summary = run(ROOT / args.input if not Path(args.input).is_absolute() else Path(args.input),
                  args.mode, args.prompt, date.fromisoformat(args.today))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
