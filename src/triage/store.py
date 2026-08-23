"""Evidence lookup over mock enterprise systems + triage results store.

In production these lookups would be claims-platform API calls; here they read
JSON fixtures with identical semantics, so productionizing is an adapter swap.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MOCK_DIR = DATA_DIR / "mock_systems"
OUT_DIR = Path(__file__).resolve().parents[2] / "outputs"

_cache: dict[str, dict] = {}


def _system(name: str) -> dict:
    if name not in _cache:
        _cache[name] = json.loads((MOCK_DIR / f"{name}.json").read_text())
    return _cache[name]


def lookup_history(claim_id: str) -> dict:
    return _system("claim_history").get(claim_id, {"touches": [], "prior_submissions": []})


def lookup_pa(member_ref: str, cpt_code: str) -> Optional[dict]:
    return _system("pa_registry").get(f"{member_ref}|{cpt_code}")


def lookup_rate(provider_npi_ref: str, cpt_code: str) -> Optional[dict]:
    return _system("contract_rates").get(f"{provider_npi_ref}|{cpt_code}")


def evidence_bundle(claim: dict) -> dict[str, Any]:
    """Everything the LLM is allowed to see for one claim, from the source systems."""
    return {
        "history": lookup_history(claim["claim_id"]),
        "pa_registry": lookup_pa(claim["member_ref"], claim["cpt_code"]),
        "contract_rate": lookup_rate(claim["provider_npi_ref"], claim["cpt_code"]),
    }


def save_run(results: list[dict], name: str) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(results, indent=2, default=str))
    return path
