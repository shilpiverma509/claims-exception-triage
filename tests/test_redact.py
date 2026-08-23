import json
from pathlib import Path

from triage.redact import redact

DATA = Path(__file__).resolve().parents[1] / "data"


def test_ssn_removed():
    clean, findings = redact("SSN 123-45-6789 on file")
    assert "123-45-6789" not in clean and "SSN" in findings


def test_dob_removed():
    clean, findings = redact("Member called, DOB 01/02/1980, re: MRI")
    assert "01/02/1980" not in clean and "DOB" in findings


def test_member_name_redacted():
    clean, findings = redact("Member John Q. Testcase called about knee MRI")
    assert "John" not in clean and "Testcase" not in clean
    assert "Member [REDACTED]" in clean and "MEMBER_NAME" in findings


def test_clean_note_untouched():
    note = "No PA on file for 70553. Provider states auth requested 6d ago."
    clean, findings = redact(note)
    assert clean == note and findings == []


def test_phi_stress_fixture_comes_out_clean():
    claim = json.loads((DATA / "stress_phi_like.json").read_text())[0]
    clean, findings = redact(claim["adjudicator_note"])
    for leaked in ("123-45-6789", "01/02/1980", "John", "Testcase"):
        assert leaked not in clean
    assert {"SSN", "DOB", "MEMBER_NAME"} <= set(findings)
    # the clinically useful signal must survive redaction
    assert "70553" in clean and "PA" in clean
