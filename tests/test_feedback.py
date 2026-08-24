import json
from datetime import date

import pytest

from triage import config
from triage.feedback import (TAXONOMY_GAP, Correction, build_regression_cases,
                             calibration_table, candidate_exemplars, confusion_pairs,
                             build_report, load_corrections, recommend_floor,
                             taxonomy_gaps)

TODAY = date(2026, 8, 23)


def make_run(tmp_path, claims):
    """A minimal triage run file in the shape the pipeline writes."""
    rows = []
    for cid, cause, queue, conf, amount in claims:
        rows.append({
            "claim": {"claim_id": cid, "billed_amount_cents": amount,
                      "sla_due_date": "2026-09-20", "pend_reason_code": "P99",
                      "adjudicator_note": f"note for {cid}"},
            "assessment": {"claim_id": cid, "root_cause": cause, "confidence": conf},
            "final_queue": queue, "final_urgency": 50, "final_band": "MEDIUM",
            "guard": {"adjustments": [], "forced_human_review": False},
            "prompt_version": "v3", "mode": "live",
        })
    p = tmp_path / "run.json"
    p.write_text(json.dumps(rows))
    return p


def make_audit(tmp_path, decisions):
    """decisions: (claim_id, decision, corrected_cause, note)"""
    lines = []
    for i, (cid, decision, cause, note) in enumerate(decisions):
        lines.append(json.dumps({
            "ts": f"2026-08-24T10:{i:02d}:00", "claim_id": cid,
            "stage": "human_decision", "actor": "analyst_1", "model": "",
            "prompt_version": "", "input_hash": "",
            "outcome": {"decision": decision, "corrected_root_cause": cause,
                        "corrected_queue": None, "note": note},
        }))
    p = tmp_path / "audit.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def corr(cid="C1", decision="reassign", cause="coding_error", conf=0.8):
    return Correction(claim_id=cid, ts="2026-08-24T10:00:00", actor="a",
                      decision=decision, system_root_cause="cob_conflict",
                      system_queue="cob_unit", system_confidence=conf,
                      prompt_version="v3", corrected_root_cause=cause)


# ── The rule the whole loop rests on ──────────────────────────────────────

def test_approvals_never_become_labels():
    """An approval may mean 'correct' or may mean the analyst clicked through.
    Treating it as ground truth would feed the system its own output."""
    approved = corr(decision="approve", cause=None)
    assert not approved.is_correction
    assert not approved.has_usable_label


def test_approved_claims_are_excluded_from_regression_cases(tmp_path):
    run = make_run(tmp_path, [("C1", "cob_conflict", "cob_unit", 0.9, 50_000),
                              ("C2", "cob_conflict", "cob_unit", 0.7, 50_000)])
    audit = make_audit(tmp_path, [("C1", "approve", None, ""),
                                  ("C2", "reassign", "coding_error", "actually coding")])
    corrections = load_corrections(run, audit)
    cases = build_regression_cases(corrections, run, TODAY)
    assert [c["claim_id"] for c in cases] == ["C2"]


def test_bare_reject_has_no_usable_label():
    """'This is wrong' tells us the answer was wrong, not what is right —
    useful for calibration, useless as a label."""
    c = corr(decision="reject", cause=None)
    assert c.is_correction and not c.has_usable_label


# ── Use 1: regression cases ───────────────────────────────────────────────

def test_regression_cases_match_the_label_schema(tmp_path):
    run = make_run(tmp_path, [("C1", "cob_conflict", "cob_unit", 0.8, 1_500_000)])
    audit = make_audit(tmp_path, [("C1", "reassign", "coding_error", "")])
    cases = build_regression_cases(load_corrections(run, audit), run, TODAY)
    case = cases[0]
    assert case["root_cause"] == "coding_error"
    assert case["owner_queue"] == "coding_review"      # derived via taxonomy map
    assert 1 <= case["severity"] <= 5
    assert case["source"] == "analyst_correction"
    assert case["system_said"] == "cob_conflict"       # provenance retained


def test_severity_rises_with_dollars(tmp_path):
    run = make_run(tmp_path, [("LOW", "cob_conflict", "cob_unit", 0.8, 4_500),
                              ("HIGH", "cob_conflict", "cob_unit", 0.8, 1_500_000)])
    audit = make_audit(tmp_path, [("LOW", "reassign", "coding_error", ""),
                                  ("HIGH", "reassign", "coding_error", "")])
    by_id = {c["claim_id"]: c for c in
             build_regression_cases(load_corrections(run, audit), run, TODAY)}
    assert by_id["HIGH"]["severity"] > by_id["LOW"]["severity"]


def test_latest_decision_wins(tmp_path):
    """Analysts change their minds; the last word is the real one."""
    run = make_run(tmp_path, [("C1", "cob_conflict", "cob_unit", 0.8, 50_000)])
    audit = make_audit(tmp_path, [("C1", "reassign", "coding_error", "first"),
                                  ("C1", "reassign", "pricing_mismatch", "actually this")])
    corrections = load_corrections(run, audit)
    assert len(corrections) == 1
    assert corrections[0].corrected_root_cause == "pricing_mismatch"


# ── Use 2: calibration ────────────────────────────────────────────────────

def test_calibration_buckets_by_confidence():
    corrections = ([corr(cid=f"L{i}", decision="reassign", conf=0.62) for i in range(4)]
                   + [corr(cid=f"H{i}", decision="approve", cause=None, conf=0.95)
                      for i in range(6)])
    rows = {r["confidence_range"]: r for r in calibration_table(corrections)}
    low = rows["0.60–0.65"]
    high = rows["0.95–1.00"]
    assert low["correction_rate"] == 1.0     # every low-confidence one was corrected
    assert high["correction_rate"] == 0.0


def test_recommend_floor_finds_a_clean_threshold():
    """Errors concentrated below 0.7 should produce a floor around there."""
    corrections = ([corr(cid=f"bad{i}", decision="reassign", conf=0.60) for i in range(10)]
                   + [corr(cid=f"ok{i}", decision="approve", cause=None, conf=0.90)
                      for i in range(30)])
    rec = recommend_floor(corrections, target_error_rate=0.05)["recommendation"]
    assert rec is not None
    assert rec["recommended_floor"] >= 0.65
    assert rec["claims_escalated_at_this_floor"] == 10   # the cost is reported


def test_recommend_floor_declines_when_evidence_is_thin():
    """With errors everywhere, no floor is defensible — say so rather than guess."""
    corrections = [corr(cid=f"c{i}", decision="reassign", conf=0.5 + i * 0.05)
                   for i in range(10)]
    out = recommend_floor(corrections, target_error_rate=0.05)
    assert out["recommendation"] is None
    assert "Insufficient evidence" in out["note"]


def test_calibration_handles_perfect_confidence_without_index_error():
    assert calibration_table([corr(conf=1.0)])[0]["n_decided"] == 1


# ── Use 3: prompt material ────────────────────────────────────────────────

def test_confusion_pairs_rank_by_frequency():
    corrections = [corr(cid=f"a{i}", cause="coding_error") for i in range(3)]
    corrections += [corr(cid="b1", cause="pricing_mismatch")]
    pairs = confusion_pairs(corrections)
    assert pairs[0] == {"system_said": "cob_conflict",
                        "analyst_said": "coding_error", "count": 3}


def test_taxonomy_gaps_are_separated_from_labels(tmp_path):
    """'None of these' is information about the taxonomy and must never be
    emitted as a training label."""
    run = make_run(tmp_path, [("C1", "cob_conflict", "cob_unit", 0.8, 50_000)])
    audit = make_audit(tmp_path, [("C1", "reject", TAXONOMY_GAP, "new pend reason")])
    corrections = load_corrections(run, audit)
    assert taxonomy_gaps(corrections)[0]["claim_id"] == "C1"
    assert build_regression_cases(corrections, run, TODAY) == []


def test_exemplars_cite_the_correction(tmp_path):
    run = make_run(tmp_path, [("C1", "cob_conflict", "cob_unit", 0.8, 50_000)])
    audit = make_audit(tmp_path, [("C1", "reassign", "coding_error", "")])
    text = candidate_exemplars(load_corrections(run, audit), run)[0]
    assert "coding_error" in text and "NOT cob_conflict" in text


# ── Report ────────────────────────────────────────────────────────────────

def test_report_is_honest_when_there_is_no_feedback(tmp_path):
    run = make_run(tmp_path, [("C1", "cob_conflict", "cob_unit", 0.8, 50_000)])
    audit = tmp_path / "empty.jsonl"
    audit.write_text("")
    report = build_report(load_corrections(run, audit), run)
    assert "No analyst decisions recorded yet" in report


def test_report_states_the_approval_caveat(tmp_path):
    """The caveat must travel with the numbers — a reader who skims the table
    should still be told approvals are not verified."""
    run = make_run(tmp_path, [("C1", "cob_conflict", "cob_unit", 0.8, 50_000)])
    audit = make_audit(tmp_path, [("C1", "approve", None, "")])
    report = build_report(load_corrections(run, audit), run)
    assert "Approvals are counted as denominators only" in report
    assert "lower bound on true error" in report


def test_report_labels_the_case_set_as_a_regression_suite(tmp_path):
    """Never let these cases be mistaken for a representative sample."""
    run = make_run(tmp_path, [("C1", "cob_conflict", "cob_unit", 0.8, 50_000)])
    audit = make_audit(tmp_path, [("C1", "reassign", "coding_error", "")])
    report = build_report(load_corrections(run, audit), run)
    assert "regression suite, not a representative sample" in report


def test_reassign_keeping_the_same_cause_is_not_a_diagnostic_correction(tmp_path):
    """An analyst moving work without disputing the diagnosis must not pad the
    regression suite with a test the system already passes, nor produce an
    exemplar reading "X, NOT X". Found by running the loop end-to-end."""
    run = make_run(tmp_path, [("C1", "coding_error", "coding_review", 0.9, 50_000)])
    audit = make_audit(tmp_path, [("C1", "reassign", "coding_error", "same cause, other team")])
    corrections = load_corrections(run, audit)
    c = corrections[0]
    assert c.is_correction              # the analyst did intervene (counts for calibration)
    assert c.has_usable_label           # and named a valid cause
    assert not c.is_cause_correction    # but did not dispute the diagnosis
    assert build_regression_cases(corrections, run, TODAY) == []
    assert confusion_pairs(corrections) == []
    assert candidate_exemplars(corrections, run) == []
