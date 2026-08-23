"""Deterministic guard layer (T8).

Invariant (unit-tested): the guard can only make the system MORE conservative —
raise urgency, lower effective trust, force human review. It never lowers
urgency and never upgrades the model's confidence.
"""
from __future__ import annotations

from datetime import date

from triage import config
from triage.models import (GuardOutcome, OwnerQueue, RootCause,
                           ROOT_CAUSE_TO_QUEUE, TriageAssessment)


def apply_guard(assessment: TriageAssessment, claim: dict, evidence: dict,
                today: date) -> GuardOutcome:
    adjustments: list[str] = []
    urgency = assessment.urgency_score
    queue = assessment.owner_queue
    forced = False

    # 1. Cause ↔ queue consistency: routing follows the taxonomy map, not model whim.
    expected = ROOT_CAUSE_TO_QUEUE[assessment.root_cause]
    if queue != expected:
        adjustments.append(f"queue corrected {queue.value} → {expected.value} (taxonomy map)")
        queue = expected

    # 2. Urgency floors — may only raise.
    amount = claim["billed_amount_cents"]
    days_to_sla = (date.fromisoformat(str(claim["sla_due_date"])) - today).days
    if amount >= config.FLOOR_HIGH_DOLLAR_CENTS and urgency < config.FLOOR_HIGH_DOLLAR_SCORE:
        adjustments.append(f"urgency floor: ≥$10k → {config.FLOOR_HIGH_DOLLAR_SCORE}")
        urgency = config.FLOOR_HIGH_DOLLAR_SCORE
    if days_to_sla <= 0 and urgency < config.FLOOR_SLA_BREACHED_SCORE:
        adjustments.append(f"urgency floor: SLA breached → {config.FLOOR_SLA_BREACHED_SCORE}")
        urgency = config.FLOOR_SLA_BREACHED_SCORE
    elif days_to_sla <= config.FLOOR_SLA_SOON_DAYS and urgency < config.FLOOR_SLA_SOON_SCORE:
        adjustments.append(f"urgency floor: SLA ≤{config.FLOOR_SLA_SOON_DAYS}d → {config.FLOOR_SLA_SOON_SCORE}")
        urgency = config.FLOOR_SLA_SOON_SCORE

    # 3. Evidence cross-checks → force human review on contradiction.
    pa = evidence.get("pa_registry")
    if (assessment.root_cause == RootCause.MISSING_PRIOR_AUTH
            and pa and pa.get("status") == "approved"):
        adjustments.append("contradiction: cause=missing_prior_auth but registry shows approved auth")
        forced = True
    if assessment.root_cause == RootCause.DUPLICATE_CLAIM:
        if not evidence.get("history", {}).get("prior_submissions"):
            adjustments.append("contradiction: cause=duplicate_claim but no prior submission in history")
            forced = True

    # 4. Confidence floor.
    if assessment.confidence < config.CONFIDENCE_FLOOR:
        adjustments.append(f"confidence {assessment.confidence:.2f} < {config.CONFIDENCE_FLOOR} → human review")
        forced = True

    if forced:
        queue = OwnerQueue.HUMAN_REVIEW

    return GuardOutcome(assessment=assessment, adjustments=adjustments,
                        forced_human_review=forced, final_urgency=urgency,
                        final_queue=queue)
