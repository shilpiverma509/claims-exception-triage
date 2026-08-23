"""Domain models for the Claims Exception Triage Assistant.

Everything the pipeline passes around is a validated pydantic model —
no loose dicts. Synthetic data only; no PHI anywhere in this project.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RootCause(str, Enum):
    """Fixed taxonomy of why a claim pends. The LLM must pick from this list."""

    MISSING_PRIOR_AUTH = "missing_prior_auth"
    ELIGIBILITY_MISMATCH = "eligibility_mismatch"
    CODING_ERROR = "coding_error"
    DUPLICATE_CLAIM = "duplicate_claim"
    COB_CONFLICT = "cob_conflict"
    PROVIDER_DATA_MISMATCH = "provider_data_mismatch"
    PRICING_MISMATCH = "pricing_mismatch"


class OwnerQueue(str, Enum):
    """Teams an exception can be routed to."""

    PRIOR_AUTH_TEAM = "prior_auth_team"
    ELIGIBILITY_TEAM = "eligibility_team"
    CODING_REVIEW = "coding_review"
    COB_UNIT = "cob_unit"
    PROVIDER_DATA_MGMT = "provider_data_mgmt"
    PRICING_TEAM = "pricing_team"
    HUMAN_REVIEW = "human_review"  # fallback bucket — never guessed into by the LLM


class NextAction(str, Enum):
    """Fixed action list the assistant may propose. It proposes; a human disposes."""

    REQUEST_PRIOR_AUTH_DOCS = "request_prior_auth_docs"
    VERIFY_ELIGIBILITY = "verify_eligibility"
    RETURN_FOR_RECODING = "return_for_recoding"
    DENY_AS_DUPLICATE = "deny_as_duplicate"
    COORDINATE_BENEFITS = "coordinate_benefits"
    UPDATE_PROVIDER_RECORD = "update_provider_record"
    REPRICE_PER_CONTRACT = "reprice_per_contract"
    ESCALATE_TO_SUPERVISOR = "escalate_to_supervisor"


# Deterministic mapping used by the guard layer and by the rule-based baseline.
ROOT_CAUSE_TO_QUEUE: dict[RootCause, OwnerQueue] = {
    RootCause.MISSING_PRIOR_AUTH: OwnerQueue.PRIOR_AUTH_TEAM,
    RootCause.ELIGIBILITY_MISMATCH: OwnerQueue.ELIGIBILITY_TEAM,
    RootCause.CODING_ERROR: OwnerQueue.CODING_REVIEW,
    RootCause.DUPLICATE_CLAIM: OwnerQueue.CODING_REVIEW,
    RootCause.COB_CONFLICT: OwnerQueue.COB_UNIT,
    RootCause.PROVIDER_DATA_MISMATCH: OwnerQueue.PROVIDER_DATA_MGMT,
    RootCause.PRICING_MISMATCH: OwnerQueue.PRICING_TEAM,
}


class Claim(BaseModel):
    """One synthetic pended claim as it arrives from the (synthetic) queue."""

    claim_id: str
    member_ref: str = Field(description="Opaque synthetic member reference — not a real ID")
    provider_name: str
    provider_npi_ref: str
    cpt_code: str
    modifier: Optional[str] = None
    billed_amount_cents: int = Field(ge=0, description="Money as integer minor units, never float")
    received_date: date
    sla_due_date: date
    pend_reason_code: str = Field(description="System pend code, e.g. P27 — often generic or stale")
    adjudicator_note: str = Field(description="Free-text note; the messy signal the LLM reads")
    history_snippet: str = Field(description="Short prior-touch history for this claim")


class GroundTruth(BaseModel):
    """Label attached to each synthetic claim so the pipeline can be evaluated."""

    claim_id: str
    root_cause: RootCause
    owner_queue: OwnerQueue
    severity: int = Field(ge=1, le=5, description="1=trivial, 5=critical; drives ranking eval")
    is_ambiguous: bool = False


class TriageAssessment(BaseModel):
    """What the LLM must return for one claim — schema-constrained, taxonomy-bound."""

    claim_id: str
    root_cause: RootCause
    summary: str = Field(description="2-3 plain-English sentences an analyst reads first")
    urgency_score: int = Field(ge=1, le=100)
    urgency_reasons: list[str] = Field(min_length=1, max_length=4)
    proposed_action: NextAction
    owner_queue: OwnerQueue
    confidence: float = Field(ge=0.0, le=1.0)


class GuardOutcome(BaseModel):
    """Result of the deterministic guard layer over an LLM assessment."""

    assessment: TriageAssessment
    adjustments: list[str] = Field(default_factory=list)
    forced_human_review: bool = False
    final_urgency: int
    final_queue: OwnerQueue
