"""Seeded synthetic claims generator.

Produces 75 labeled claims (50 dev / 25 held-out eval) plus two deliberate
stress cases: a malformed record and a claim whose note contains PHI-looking
text (to exercise the redaction guard). Reproducible: same seed → same data.

Run:  python -m triage.generate_data
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

from triage.models import Claim, GroundTruth, OwnerQueue, RootCause, ROOT_CAUSE_TO_QUEUE

SEED = 42
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

PROVIDERS = [
    ("Lakeside Orthopedic Group", "NPIREF-1001"), ("Summit Family Medicine", "NPIREF-1002"),
    ("Riverbend Imaging Center", "NPIREF-1003"), ("Cedar Valley Cardiology", "NPIREF-1004"),
    ("Northgate Surgical Associates", "NPIREF-1005"), ("Harborview Physical Therapy", "NPIREF-1006"),
]

CPTS = ["99213", "99214", "27447", "70553", "93000", "97110", "29881", "45378"]

# note templates per root cause: (clear variant, vague variant)
NOTES: dict[RootCause, tuple[list[str], list[str]]] = {
    RootCause.MISSING_PRIOR_AUTH: (
        ["No PA on file for {cpt}. Provider states auth requested {days}d ago, nothing in system.",
         "Service requires prior auth per plan; auth number field blank on submission."],
        ["Provider office called twice re: status. Docs may be incomplete, unclear if auth or coding issue."],
    ),
    RootCause.ELIGIBILITY_MISMATCH: (
        ["Member shows termed coverage on DOS. Enrollment file lag suspected.",
         "Plan ID on claim does not match active segment for the member on date of service."],
        ["Coverage question raised by intake. Member recently changed employers per note."],
    ),
    RootCause.CODING_ERROR: (
        ["Modifier {mod} inconsistent with {cpt} on same DOS. Needs coding review.",
         "Dx-to-procedure mismatch flagged by edit. Likely miskeyed CPT."],
        ["Edit fired on code combo, adjudicator unsure whether payable as billed."],
    ),
    RootCause.DUPLICATE_CLAIM: (
        ["Exact match to claim {dup} on DOS/CPT/amount. Probable duplicate submission.",
         "Provider resubmitted after no response; original still in process."],
        ["Similar claim in history, amounts differ slightly. Possible corrected claim vs duplicate."],
    ),
    RootCause.COB_CONFLICT: (
        ["Other coverage on file; primacy undetermined. EOB from other carrier not attached.",
         "Medicare indicated primary per member record. Claim submitted to us first."],
        ["Spouse plan mentioned in call notes. COB questionnaire outstanding since {days}d."],
    ),
    RootCause.PROVIDER_DATA_MISMATCH: (
        ["Rendering NPI not found in network file, group contract active. Roster lag suspected.",
         "Servicing address does not match credentialed location for this provider."],
        ["Provider record looks off — tax ID mismatch per note, may be recent group merge."],
    ),
    RootCause.PRICING_MISMATCH: (
        ["Contract rate table missing for {cpt} effective this quarter. Manual price needed.",
         "Allowed amount computed at 0; fee schedule gap for this service."],
        ["Payment amount disputed by provider vs contract terms, escalated by service rep."],
    ),
}

def _severity(amount_cents: int, days_to_sla: int) -> int:
    """Ground-truth severity from dollars + SLA pressure (this is the label, not the model's logic)."""
    s = 1
    if amount_cents > 100_000:  # > $1,000
        s += 1
    if amount_cents > 1_000_000:  # > $10,000
        s += 1
    if days_to_sla <= 3:
        s += 1
    if days_to_sla <= 0:
        s += 1
    return min(s, 5)


def generate(seed: int = SEED) -> tuple[list[Claim], list[GroundTruth]]:
    rng = random.Random(seed)
    today = date(2026, 8, 22)
    claims: list[Claim] = []
    labels: list[GroundTruth] = []
    causes = list(RootCause)

    for i in range(75):
        cause = causes[i % len(causes)]
        clear, vague = NOTES[cause]
        ambiguous = rng.random() < 0.2
        template = rng.choice(vague if ambiguous else clear)
        provider, npi = rng.choice(PROVIDERS)
        cpt = rng.choice(CPTS)
        amount = rng.choice([45_00, 120_00, 350_00, 900_00, 2_400_00, 8_500_00, 15_000_00, 42_000_00])
        received = today - timedelta(days=rng.randint(2, 40))
        sla_due = received + timedelta(days=30)
        note = template.format(cpt=cpt, mod=rng.choice(["25", "59", "LT"]),
                               days=rng.randint(3, 21), dup=f"CLM-2026-{7000 + i}")
        # generic/stale pend codes on ~30%: the reason an LLM beats a code lookup
        pend_code = rng.choice(["P99", "P00"]) if rng.random() < 0.3 else f"P{10 + causes.index(cause)}"

        claims.append(Claim(
            claim_id=f"CLM-2026-{8000 + i}",
            member_ref=f"MBR-{rng.randint(100000, 999999)}",
            provider_name=provider, provider_npi_ref=npi,
            cpt_code=cpt, modifier=rng.choice([None, "25", "59"]),
            billed_amount_cents=amount,
            received_date=received, sla_due_date=sla_due,
            pend_reason_code=pend_code,
            adjudicator_note=note,
            history_snippet=rng.choice([
                "First touch.", "Second touch; pended once before for docs.",
                "Provider called 2x this week.", "Reprocessed after enrollment update.",
            ]),
        ))
        days_to_sla = (sla_due - today).days
        labels.append(GroundTruth(
            claim_id=claims[-1].claim_id, root_cause=cause,
            owner_queue=ROOT_CAUSE_TO_QUEUE[cause],
            severity=_severity(amount, days_to_sla), is_ambiguous=ambiguous,
        ))
    return claims, labels


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    claims, labels = generate()
    dev, eval_ = claims[:50], claims[50:]
    dev_l, eval_l = labels[:50], labels[50:]

    for name, payload in [("dev_claims", dev), ("eval_claims", eval_),
                          ("dev_labels", dev_l), ("eval_labels", eval_l)]:
        (DATA_DIR / f"{name}.json").write_text(
            json.dumps([json.loads(x.model_dump_json()) for x in payload], indent=2))

    # stress case 1: malformed record (missing fields, bad amount) — pipeline must not crash
    (DATA_DIR / "stress_malformed.json").write_text(json.dumps([{
        "claim_id": "CLM-2026-9998", "billed_amount_cents": -50,
        "adjudicator_note": "corrupted upstream record"}], indent=2))

    # stress case 2: PHI-looking free text — redaction guard must strip before any LLM call
    phi_like = json.loads(dev[0].model_dump_json())
    phi_like.update({
        "claim_id": "CLM-2026-9999",
        "adjudicator_note": ("Member John Q. Testcase DOB 01/02/1980 SSN 123-45-6789 called re: knee MRI. "
                             "No PA on file for 70553."),
    })
    (DATA_DIR / "stress_phi_like.json").write_text(json.dumps([phi_like], indent=2))

    print(f"dev={len(dev)} eval={len(eval_)} + 2 stress cases → {DATA_DIR}")


if __name__ == "__main__":
    main()
