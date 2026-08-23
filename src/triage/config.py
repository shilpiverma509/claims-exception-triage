"""Single source of truth for all thresholds, bands, teams, and runtime settings.

No other module may hardcode a threshold. If a reviewer asks "why this number?",
the honest answer everywhere: it's a sponsor-tunable assumption; the mechanism
(deterministic floors, confidence fallback) is the design.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from triage.models import OwnerQueue

# ── LLM runtime ────────────────────────────────────────────────────────────
MODEL_NAME = "claude-sonnet-4-5"  # right-sized: triage is a bounded reasoning task
TRIAGE_MODE = os.getenv("TRIAGE_MODE", "mock")  # live | mock | cached
MAX_RETRIES = 2
CONFIDENCE_FLOOR = 0.65  # below → human_review, never a guessed queue

# ── Urgency bands (score 1–100) ───────────────────────────────────────────
URGENCY_BANDS = {
    "CRITICAL": (80, 100),  # SLA breached/≤3d, or ≥$10k, or member-impacting
    "HIGH": (60, 79),       # SLA ≤7d, or ≥$2,400, or repeat escalation
    "MEDIUM": (35, 59),     # SLA 8–20d, mid-dollar, single touch
    "LOW": (1, 34),         # SLA >20d and <$350 and quiet
}

# Guard floors — deterministic, may only RAISE urgency (sponsor-tunable)
FLOOR_HIGH_DOLLAR_CENTS = 1_000_000   # ≥ $10,000  → score ≥ 80
FLOOR_HIGH_DOLLAR_SCORE = 80
FLOOR_SLA_SOON_DAYS = 3               # ≤ 3 days   → score ≥ 80
FLOOR_SLA_SOON_SCORE = 80
FLOOR_SLA_BREACHED_SCORE = 95         # ≤ 0 days   → score ≥ 95


def band_for(score: int) -> str:
    for name, (lo, hi) in URGENCY_BANDS.items():
        if lo <= score <= hi:
            return name
    return "LOW"


# ── Owner team registry ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Team:
    queue: OwnerQueue
    name: str
    handles: str


TEAMS: dict[OwnerQueue, Team] = {t.queue: t for t in [
    Team(OwnerQueue.PRIOR_AUTH_TEAM, "Prior Authorization Ops",
         "Missing/invalid prior auth, PA-claim mismatches"),
    Team(OwnerQueue.ELIGIBILITY_TEAM, "Eligibility & Enrollment",
         "Coverage termed or lagged, plan-segment mismatches"),
    Team(OwnerQueue.CODING_REVIEW, "Clinical Coding Review",
         "CPT/modifier conflicts, dx-procedure mismatch, duplicates needing coder judgment"),
    Team(OwnerQueue.COB_UNIT, "Coordination of Benefits",
         "Other-carrier primacy, Medicare-primary questions, COB questionnaires"),
    Team(OwnerQueue.PROVIDER_DATA_MGMT, "Provider Data Management",
         "NPI, roster, address, and tax-ID mismatches"),
    Team(OwnerQueue.PRICING_TEAM, "Contract Pricing",
         "Fee-schedule gaps, contract-rate disputes, manual pricing"),
    Team(OwnerQueue.HUMAN_REVIEW, "Senior Analyst Desk",
         "Low-confidence or guard-flagged cases; only the guard routes here, never the LLM"),
]}
