"""Append-only JSONL audit log (T8). Every model call, guard decision, and
human action lands here with model + prompt version stamped."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_PATH = Path(__file__).resolve().parents[2] / "outputs" / "audit.jsonl"


def log(stage: str, claim_id: str, outcome: Any, *, actor: str = "system",
        model: str = "", prompt_version: str = "", input_text: str = "") -> None:
    AUDIT_PATH.parent.mkdir(exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "claim_id": claim_id,
        "stage": stage,               # ingest|redact|llm|guard|human_decision
        "actor": actor,
        "model": model,
        "prompt_version": prompt_version,
        "input_hash": hashlib.sha256(input_text.encode()).hexdigest()[:16] if input_text else "",
        "outcome": outcome,
    }
    with AUDIT_PATH.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")
