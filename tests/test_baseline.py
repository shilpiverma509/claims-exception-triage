import json
from pathlib import Path

from triage.baseline import (DEFAULT_CAUSE, DEFAULT_CONFIDENCE, KNOWN_CODE_CONFIDENCE,
                             baseline_cause, baseline_urgency, run as baseline_run)
from triage.models import ROOT_CAUSE_TO_QUEUE, RootCause

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def test_known_pend_codes_map_to_taxonomy_in_order():
    for i, cause in enumerate(RootCause):
        got, confidence = baseline_cause(f"P{10 + i}")
        assert got == cause
        assert confidence == KNOWN_CODE_CONFIDENCE


def test_generic_pend_code_falls_back_to_default_guess():
    got, confidence = baseline_cause("P99")
    assert got == DEFAULT_CAUSE
    assert confidence == DEFAULT_CONFIDENCE


def test_urgency_is_dollars_only_and_monotonic():
    assert baseline_urgency(45_00) < baseline_urgency(2_400_00) < baseline_urgency(15_000_00)
    assert 1 <= baseline_urgency(0) <= 100
    assert baseline_urgency(50_000_00) == 100


def test_run_on_dev_set_matches_pipeline_output_schema():
    summary = baseline_run(DATA / "dev_claims.json")
    assert summary["processed"] == 50
    results = json.loads(Path(summary["output"]).read_text())
    urgencies = [r["final_urgency"] for r in results]
    assert urgencies == sorted(urgencies, reverse=True)
    for row in results:
        assert row["final_queue"] == ROOT_CAUSE_TO_QUEUE[RootCause(row["assessment"]["root_cause"])].value


def test_run_survives_malformed_record():
    summary = baseline_run(DATA / "stress_malformed.json")
    assert summary["processed"] == 0
    assert summary["errored"] == 1
