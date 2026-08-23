from datetime import date

from triage import config
from triage.guard import apply_guard
from triage.models import NextAction, OwnerQueue, RootCause, TriageAssessment

TODAY = date(2026, 8, 23)


def make_assessment(**over):
    base = dict(claim_id="CLM-T", root_cause=RootCause.MISSING_PRIOR_AUTH,
                summary="s", urgency_score=50, urgency_reasons=["r"],
                proposed_action=NextAction.REQUEST_PRIOR_AUTH_DOCS,
                owner_queue=OwnerQueue.PRIOR_AUTH_TEAM, confidence=0.9)
    base.update(over)
    return TriageAssessment(**base)


def make_claim(**over):
    base = dict(claim_id="CLM-T", billed_amount_cents=50_000,
                sla_due_date="2026-09-20")
    base.update(over)
    return base


EMPTY_EV = {"history": {"touches": [], "prior_submissions": []},
            "pa_registry": None, "contract_rate": None}


def test_high_dollar_floor_raises():
    out = apply_guard(make_assessment(urgency_score=30),
                      make_claim(billed_amount_cents=1_500_000), EMPTY_EV, TODAY)
    assert out.final_urgency >= config.FLOOR_HIGH_DOLLAR_SCORE
    assert any("floor" in a for a in out.adjustments)


def test_sla_breach_floor():
    out = apply_guard(make_assessment(urgency_score=10),
                      make_claim(sla_due_date="2026-08-20"), EMPTY_EV, TODAY)
    assert out.final_urgency >= config.FLOOR_SLA_BREACHED_SCORE


def test_guard_never_lowers_urgency():
    for score in (5, 45, 85, 99):
        out = apply_guard(make_assessment(urgency_score=score, confidence=0.9),
                          make_claim(), EMPTY_EV, TODAY)
        assert out.final_urgency >= score


def test_low_confidence_forces_human_review():
    out = apply_guard(make_assessment(confidence=0.4), make_claim(), EMPTY_EV, TODAY)
    assert out.forced_human_review and out.final_queue == OwnerQueue.HUMAN_REVIEW


def test_pa_trap_contradiction_forces_review():
    ev = dict(EMPTY_EV, pa_registry={"auth_number": "PA-1", "status": "approved",
                                      "effective_dates": ["2026-08-01", "2026-09-01"]})
    out = apply_guard(make_assessment(), make_claim(), ev, TODAY)
    assert out.forced_human_review
    assert any("contradiction" in a for a in out.adjustments)


def test_duplicate_without_history_forces_review():
    out = apply_guard(make_assessment(root_cause=RootCause.DUPLICATE_CLAIM,
                                       proposed_action=NextAction.DENY_AS_DUPLICATE,
                                       owner_queue=OwnerQueue.CODING_REVIEW),
                      make_claim(), EMPTY_EV, TODAY)
    assert out.forced_human_review


def test_queue_corrected_to_taxonomy_map():
    out = apply_guard(make_assessment(owner_queue=OwnerQueue.PRICING_TEAM),
                      make_claim(), EMPTY_EV, TODAY)
    # missing_prior_auth must route to prior_auth_team (unless forced to review)
    assert out.final_queue in (OwnerQueue.PRIOR_AUTH_TEAM, OwnerQueue.HUMAN_REVIEW)
    assert any("queue corrected" in a for a in out.adjustments)
