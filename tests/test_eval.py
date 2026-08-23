import json
from pathlib import Path

import pytest

from triage import evaluate
from triage.baseline import run as baseline_run

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def make_result(cid, root_cause, queue, urgency, forced_review=False):
    return {"claim": {"claim_id": cid}, "assessment": {"root_cause": root_cause},
            "final_queue": queue, "final_urgency": urgency,
            "guard": {"forced_human_review": forced_review}}


def make_label(cid, root_cause, queue, severity):
    return {"claim_id": cid, "root_cause": root_cause, "owner_queue": queue, "severity": severity}


def test_root_cause_and_routing_accuracy():
    pairs = [
        (make_result("C1", "coding_error", "coding_review", 50), make_label("C1", "coding_error", "coding_review", 2)),
        (make_result("C2", "coding_error", "coding_review", 50), make_label("C2", "cob_conflict", "cob_unit", 2)),
        (make_result("C3", "cob_conflict", "cob_unit", 50), make_label("C3", "cob_conflict", "cob_unit", 2)),
    ]
    assert evaluate.root_cause_accuracy(pairs) == 2 / 3
    assert evaluate.routing_accuracy(pairs) == 2 / 3
    assert evaluate.root_cause_accuracy([]) is None


def test_human_review_rate():
    results = [make_result("C1", "x", "q", 10, forced_review=True),
               make_result("C2", "x", "q", 10, forced_review=False)]
    assert evaluate.human_review_rate(results) == 0.5


def test_critical_recall_at_n_uses_severity_floor():
    labels = {"C1": make_label("C1", "x", "q", 5), "C2": make_label("C2", "x", "q", 4),
              "C3": make_label("C3", "x", "q", 1)}
    results = [make_result("C1", "x", "q", 90), make_result("C3", "x", "q", 50),
               make_result("C2", "x", "q", 10)]
    assert evaluate.critical_recall_at_n(results, labels, n=2) == 0.5  # C1 in top 2, C2 is not


def test_spearman_urgency_correlation_perfect_monotonic():
    pairs = [(make_result(f"C{i}", "x", "q", i * 10), make_label(f"C{i}", "x", "q", i)) for i in range(1, 6)]
    rho = evaluate.spearman_urgency_correlation(pairs)
    assert rho == pytest.approx(1.0)


def test_confusion_counts():
    pairs = [
        (make_result("C1", "coding_error", "q", 1), make_label("C1", "coding_error", "q", 1)),
        (make_result("C2", "cob_conflict", "q", 1), make_label("C2", "coding_error", "q", 1)),
    ]
    counts = evaluate.confusion_counts(pairs)
    assert counts[("coding_error", "coding_error")] == 1
    assert counts[("coding_error", "cob_conflict")] == 1


def test_evaluate_end_to_end_on_baseline_dev_run():
    summary = baseline_run(DATA / "dev_claims.json")
    metrics = evaluate.evaluate(Path(summary["output"]), DATA / "dev_labels.json")
    assert metrics["n_results"] == 50
    assert metrics["n_scored"] == 50
    assert 0.0 <= metrics["root_cause_accuracy"] <= 1.0
    assert Path(metrics["confusion_matrix"]).exists()
    assert Path(metrics["report"]).exists()
