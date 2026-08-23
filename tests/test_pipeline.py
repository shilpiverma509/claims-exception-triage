import json
from pathlib import Path

from triage.pipeline import TODAY, run as pipeline_run

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def test_malformed_record_survives_batch():
    summary = pipeline_run(DATA / "stress_malformed.json", "mock", "v1", TODAY)
    assert summary["processed"] == 0
    assert summary["errored"] == 1
    errors_path = Path(summary["output"]).parent / "triage_stress_malformed_mock_v1_errors.json"
    assert errors_path.exists()


def test_phi_stress_redacted_before_storage_and_audited():
    summary = pipeline_run(DATA / "stress_phi_like.json", "mock", "v1", TODAY)
    results = json.loads(Path(summary["output"]).read_text())
    note = results[0]["claim"]["adjudicator_note"]
    for leaked in ("123-45-6789", "01/02/1980", "John", "Testcase"):
        assert leaked not in note

    audit_rows = [json.loads(line) for line in (ROOT / "outputs" / "audit.jsonl").read_text().splitlines()]
    claim_id = results[0]["claim"]["claim_id"]
    assert any(r["stage"] == "redact" and r["claim_id"] == claim_id for r in audit_rows)


def test_dev_set_mock_run_is_sorted_and_fully_scored():
    summary = pipeline_run(DATA / "dev_claims.json", "mock", "v1", TODAY)
    assert summary["processed"] == 50
    assert summary["errored"] == 0
    assert sum(summary["bands"].values()) == 50

    results = json.loads(Path(summary["output"]).read_text())
    urgencies = [r["final_urgency"] for r in results]
    assert urgencies == sorted(urgencies, reverse=True)
    assert all(r["final_queue"] for r in results)

    # ranking policy: within equal urgency, higher dollars come first (tie-break)
    for a, b in zip(results, results[1:]):
        if a["final_urgency"] == b["final_urgency"]:
            assert a["claim"]["billed_amount_cents"] >= b["claim"]["billed_amount_cents"]
