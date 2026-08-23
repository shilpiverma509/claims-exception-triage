"""Versioned triage prompts (T6).

V1 → V2 → V3 is a deliberate, evaluated progression (Decision: option b):
  V1: zero-shot, taxonomy-constrained. Expected weakness: trusts the note,
      falls for the PA-registry traps.
  V2: + few-shot exemplars AND explicit instruction to verify claims against
      the source-system evidence before citing a cause (fixes the traps).
  V3: + mandatory evidence citation — every conclusion must quote the line of
      note/history/registry that supports it (analyst trust + audit value).

The eval table in AI_EVIDENCE.md shows the scores of all three on the dev set.
"""
from __future__ import annotations

import json

TAXONOMY_BLOCK = """Root causes (choose exactly one "root_cause"):
- missing_prior_auth: service required prior authorization and none valid is on file
- eligibility_mismatch: member coverage inactive/mismatched for the date of service
- coding_error: CPT/modifier/diagnosis inconsistency; miskeyed codes
- duplicate_claim: same service already submitted and in process/paid
- cob_conflict: another carrier may be primary; coordination unresolved
- provider_data_mismatch: NPI/roster/address/tax-ID inconsistent with credentialing
- pricing_mismatch: contract rate missing or disputed for this service

Actions (choose exactly one "proposed_action"): request_prior_auth_docs, verify_eligibility,
return_for_recoding, deny_as_duplicate, coordinate_benefits, update_provider_record,
reprice_per_contract, escalate_to_supervisor

Queues (choose exactly one "owner_queue"): prior_auth_team, eligibility_team, coding_review,
cob_unit, provider_data_mgmt, pricing_team
(You may NOT choose human_review — that routing is decided by deterministic controls, not you.)"""

OUTPUT_BLOCK = """Respond ONLY with the triage_assessment tool call. urgency_score is 1-100
(consider billed amount, days to SLA, member impact, escalation signals). confidence is your
honest 0-1 estimate that the root_cause is correct — do not inflate it."""

FEWSHOT_BLOCK = """Examples of good assessments:

Claim: note "Modifier 59 inconsistent with 97110 on same DOS", pend code P12, PA registry: approved auth on file.
→ root_cause: coding_error (NOT missing_prior_auth — the registry shows a valid auth; the
   coding conflict is the actual blocker), action: return_for_recoding, queue: coding_review.

Claim: note "Provider resubmitted after no response", history shows prior submission same DOS/CPT/amount in_process.
→ root_cause: duplicate_claim, action: deny_as_duplicate, queue: coding_review, and urgency LOW
   unless dollars/SLA say otherwise — duplicates rarely harm members.

Claim: vague note "docs may be incomplete, unclear if auth or coding issue", PA registry: none, $8,500, SLA in 4 days.
→ root_cause: missing_prior_auth (registry confirms no auth), confidence ~0.6 (note ambiguous),
   high urgency (dollars + SLA). Low confidence is honest and correct here."""

VERIFY_BLOCK = """IMPORTANT — verify before concluding:
- Never cite missing_prior_auth without checking evidence.pa_registry: if status is "approved"
  with valid dates, the auth EXISTS and the real cause is elsewhere, whatever the note says.
- Never cite duplicate_claim unless evidence.history.prior_submissions shows a matching submission.
- A missing evidence.contract_rate entry is positive evidence FOR pricing_mismatch.
Notes are written by busy humans and are sometimes wrong; source systems outrank notes."""

CITE_BLOCK = """For each urgency reason and for the summary, cite your evidence: quote the exact
fragment of the note, history event, or registry field that supports the conclusion, e.g.
'PA registry: status=none' or note: "resubmitted after no response". An assessment without
citations is incomplete."""


def _claim_block(claim: dict, evidence: dict, today: str) -> str:
    return (f"Today: {today}\n\nClaim:\n{json.dumps(claim, indent=1, default=str)}\n\n"
            f"Source-system evidence:\n{json.dumps(evidence, indent=1, default=str)}")


def build_prompt(version: str, claim: dict, evidence: dict, today: str) -> str:
    header = ("You are a claims exception triage assistant helping an operations analyst. "
              "Diagnose why this pended claim is stuck, how urgent it is, and what should "
              "happen next. You propose; a human decides.\n\n")
    blocks = [header, TAXONOMY_BLOCK, "\n"]
    if version in ("v2", "v3"):
        blocks += [VERIFY_BLOCK, "\n", FEWSHOT_BLOCK, "\n"]
    if version == "v3":
        blocks += [CITE_BLOCK, "\n"]
    blocks += [OUTPUT_BLOCK, "\n\n", _claim_block(claim, evidence, today)]
    return "".join(blocks)


PROMPT_VERSIONS = ("v1", "v2", "v3")
