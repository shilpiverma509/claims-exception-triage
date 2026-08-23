"""Generate the mock enterprise source systems (T2).

Simulates the three systems a production triage service would query:
  claim_history.json   — prior touches + prior submissions per claim
  pa_registry.json     — prior-auth "system of record" (member_ref|cpt → auth)
  contract_rates.json  — provider fee schedule (npi|cpt → rate)

Fixtures are generated FROM the labeled claims so evidence is consistent with
ground truth — including 3 deliberate "disprovable" traps where a note implies
a missing PA but the registry actually holds a valid approval (tests whether
the model checks evidence instead of trusting the note).

Run:  python -m triage.generate_mock_systems   (after generate_data)
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from triage.models import RootCause

SEED = 4242
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
OUT_DIR = DATA_DIR / "mock_systems"

HISTORY_EVENTS = [
    "auto-adjudication attempted; pended",
    "pended for supporting docs",
    "provider call logged by service rep",
    "note added by adjudicator",
    "enrollment file refresh applied",
    "resubmission received",
]


def _load(name: str) -> list[dict]:
    return json.loads((DATA_DIR / name).read_text())


def main() -> None:
    rng = random.Random(SEED)
    claims = _load("dev_claims.json") + _load("eval_claims.json")
    labels = {l["claim_id"]: l for l in _load("dev_labels.json") + _load("eval_labels.json")}

    history: dict[str, dict] = {}
    pa_registry: dict[str, dict] = {}
    rates: dict[str, dict] = {}

    # pick 3 ambiguous NON-PA dev claims as disprovable traps (note sounds PA-ish,
    # registry holds a valid auth → correct root cause is NOT missing_prior_auth)
    trap_ids = [c["claim_id"] for c in claims[:50]
                if labels[c["claim_id"]]["root_cause"] != "missing_prior_auth"
                and labels[c["claim_id"]]["is_ambiguous"]][:3]

    for c in claims:
        cid = c["claim_id"]
        cause = labels[cid]["root_cause"]

        # ── claim history ──
        touches = [{"date": c["received_date"], "event": "claim received", "actor": "system"}]
        for _ in range(rng.randint(1, 3)):
            touches.append({"date": c["received_date"], "event": rng.choice(HISTORY_EVENTS),
                            "actor": rng.choice(["system", "svc_rep_11", "adjudicator_07"])})
        prior_submissions = []
        if cause == RootCause.DUPLICATE_CLAIM.value:
            prior_submissions.append({
                "claim_id": f"CLM-2026-{int(cid.split('-')[-1]) - 1000}",
                "cpt_code": c["cpt_code"], "billed_amount_cents": c["billed_amount_cents"],
                "status": "in_process", "same_dos": True,
            })
        history[cid] = {"touches": touches, "prior_submissions": prior_submissions}

        # ── PA registry ──
        key = f"{c['member_ref']}|{c['cpt_code']}"
        if cause == RootCause.MISSING_PRIOR_AUTH.value:
            pa_registry[key] = {"auth_number": None,
                                "status": rng.choice(["none", "pending"]),
                                "effective_dates": None}
        elif cid in trap_ids:
            pa_registry[key] = {"auth_number": f"PA-{rng.randint(100000, 999999)}",
                                "status": "approved",
                                "effective_dates": [c["received_date"], c["sla_due_date"]],
                                "_note": "trap: valid auth exists; note may mislead"}
        elif rng.random() < 0.5:
            pa_registry[key] = {"auth_number": f"PA-{rng.randint(100000, 999999)}",
                                "status": "approved",
                                "effective_dates": [c["received_date"], c["sla_due_date"]]}

        # ── contract rates ── (gap = the pricing_mismatch evidence)
        rkey = f"{c['provider_npi_ref']}|{c['cpt_code']}"
        if cause != RootCause.PRICING_MISMATCH.value:
            rates[rkey] = {"rate_cents": int(c["billed_amount_cents"] * rng.uniform(0.4, 0.8)),
                           "effective_quarter": "2026-Q3"}
        # pricing_mismatch claims deliberately get NO entry → the lookup gap IS the evidence

    # Rate keys are provider|cpt and therefore SHARED across claims. A non-pricing
    # claim on the same provider+cpt would otherwise fill the gap that is supposed
    # to be a pricing_mismatch claim's evidence — so strip every pricing key last.
    pricing_keys = {f"{c['provider_npi_ref']}|{c['cpt_code']}" for c in claims
                    if labels[c["claim_id"]]["root_cause"] == RootCause.PRICING_MISMATCH.value}
    for key in pricing_keys:
        rates.pop(key, None)

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "claim_history.json").write_text(json.dumps(history, indent=2))
    (OUT_DIR / "pa_registry.json").write_text(json.dumps(pa_registry, indent=2))
    (OUT_DIR / "contract_rates.json").write_text(json.dumps(rates, indent=2))
    print(f"history={len(history)} pa_entries={len(pa_registry)} rates={len(rates)} "
          f"traps={trap_ids} → {OUT_DIR}")


if __name__ == "__main__":
    main()
