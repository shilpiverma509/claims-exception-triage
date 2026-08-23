"""LLM provider wrapper (T5): live Anthropic | mock | cached.

- live:   real API calls, structured output via tool-use schema; responses
          appended to the cache so the demo can replay offline.
- cached: replay from outputs/llm_cache.jsonl (key: claim_id|prompt_version).
- mock:   deterministic heuristic — no network. Lets pipeline/guard/UI/dev
          proceed offline. NEVER used for reported eval numbers; it mimics a
          gullible V1-style reader (trusts the note, ignores the registry) so
          the guard and trap machinery can be exercised honestly.

Failure isolation: one claim's failure never kills the batch (caller catches).
Idempotency: cache key by claim_id|prompt_version; retries are safe.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional

from triage import config
from triage.models import NextAction, OwnerQueue, RootCause, ROOT_CAUSE_TO_QUEUE, TriageAssessment
from triage.prompts import build_prompt

CACHE_PATH = Path(__file__).resolve().parents[2] / "outputs" / "llm_cache.jsonl"

TOOL_SCHEMA = {
    "name": "triage_assessment",
    "description": "Structured triage assessment for one pended claim",
    "input_schema": {
        "type": "object",
        "properties": {
            "claim_id": {"type": "string"},
            "root_cause": {"type": "string", "enum": [c.value for c in RootCause]},
            "summary": {"type": "string"},
            "urgency_score": {"type": "integer", "minimum": 1, "maximum": 100},
            "urgency_reasons": {"type": "array", "items": {"type": "string"},
                                 "minItems": 1, "maxItems": 4},
            "proposed_action": {"type": "string", "enum": [a.value for a in NextAction]},
            "owner_queue": {"type": "string",
                            "enum": [q.value for q in OwnerQueue if q != OwnerQueue.HUMAN_REVIEW]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["claim_id", "root_cause", "summary", "urgency_score",
                      "urgency_reasons", "proposed_action", "owner_queue", "confidence"],
    },
}


def _cache_load() -> dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    out = {}
    for line in CACHE_PATH.read_text().splitlines():
        row = json.loads(line)
        out[row["key"]] = row["assessment"]
    return out


def _cache_append(key: str, assessment: dict) -> None:
    CACHE_PATH.parent.mkdir(exist_ok=True)
    with CACHE_PATH.open("a") as f:
        f.write(json.dumps({"key": key, "assessment": assessment}) + "\n")


def _mock_assess(claim: dict, evidence: dict) -> dict:
    """Deterministic, note-driven heuristic. Deliberately gullible (V1-like)."""
    note = claim["adjudicator_note"].lower()
    rules = [
        (("pa", "auth"), RootCause.MISSING_PRIOR_AUTH),
        (("termed", "eligib", "enrollment", "plan id", "employers", "coverage"), RootCause.ELIGIBILITY_MISMATCH),
        (("modifier", "miskeyed", "code combo", "dx-to-procedure", "edit fired"), RootCause.CODING_ERROR),
        (("duplicate", "resubmit", "similar claim"), RootCause.DUPLICATE_CLAIM),
        (("cob", "other carrier", "medicare", "spouse", "other coverage", "primacy"), RootCause.COB_CONFLICT),
        (("npi", "roster", "credential", "tax id", "servicing address", "provider record"), RootCause.PROVIDER_DATA_MISMATCH),
        (("rate", "fee schedule", "allowed amount", "price", "contract"), RootCause.PRICING_MISMATCH),
    ]
    cause = RootCause.CODING_ERROR
    for keys, rc in rules:
        if any(k in note for k in keys):
            cause = rc
            break
    amount = claim["billed_amount_cents"]
    score = min(95, 20 + (25 if amount > 240_000 else 0) + (30 if amount > 1_000_000 else 0)
                + (10 if "called" in note or "escalated" in note else 0) + len(note) % 7)
    vague = any(k in note for k in ("unclear", "unsure", "may be", "possible", "looks off", "question"))
    action_map = {
        RootCause.MISSING_PRIOR_AUTH: NextAction.REQUEST_PRIOR_AUTH_DOCS,
        RootCause.ELIGIBILITY_MISMATCH: NextAction.VERIFY_ELIGIBILITY,
        RootCause.CODING_ERROR: NextAction.RETURN_FOR_RECODING,
        RootCause.DUPLICATE_CLAIM: NextAction.DENY_AS_DUPLICATE,
        RootCause.COB_CONFLICT: NextAction.COORDINATE_BENEFITS,
        RootCause.PROVIDER_DATA_MISMATCH: NextAction.UPDATE_PROVIDER_RECORD,
        RootCause.PRICING_MISMATCH: NextAction.REPRICE_PER_CONTRACT,
    }
    return {
        "claim_id": claim["claim_id"], "root_cause": cause.value,
        "summary": f"[MOCK] Note suggests {cause.value.replace('_', ' ')}. "
                   f"Billed ${amount / 100:,.2f}; see note for detail.",
        "urgency_score": score,
        "urgency_reasons": [f"billed ${amount / 100:,.2f}", "note signal: " + cause.value],
        "proposed_action": action_map[cause].value,
        "owner_queue": ROOT_CAUSE_TO_QUEUE[cause].value,
        "confidence": 0.55 if vague else 0.82,
    }


def _live_assess(claim: dict, evidence: dict, prompt_version: str) -> dict:
    import anthropic  # lazy import: mock mode must not require the package

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    prompt = build_prompt(prompt_version, claim, evidence, today=str(date.today()))
    last_err: Optional[Exception] = None
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            msg = client.messages.create(
                model=config.MODEL_NAME, max_tokens=1024,
                tools=[TOOL_SCHEMA], tool_choice={"type": "tool", "name": "triage_assessment"},
                messages=[{"role": "user", "content": prompt}],
            )
            block = next(b for b in msg.content if b.type == "tool_use")
            return dict(block.input)
        except Exception as e:  # noqa: BLE001 — retry then surface
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after retries for {claim['claim_id']}: {last_err}")


def triage_call(claim: dict, evidence: dict, prompt_version: str,
                mode: Optional[str] = None) -> TriageAssessment:
    mode = mode or config.TRIAGE_MODE
    key = f"{claim['claim_id']}|{prompt_version}"
    if mode == "cached":
        cached = _cache_load().get(key)
        if cached is None:
            raise KeyError(f"No cached response for {key}; run live first or use mock")
        raw = cached
    elif mode == "mock":
        raw = _mock_assess(claim, evidence)
    elif mode == "live":
        raw = _live_assess(claim, evidence, prompt_version)
        _cache_append(key, raw)
    else:
        raise ValueError(f"Unknown TRIAGE_MODE: {mode}")
    raw["claim_id"] = claim["claim_id"]  # never trust echoed ids
    return TriageAssessment(**raw)
