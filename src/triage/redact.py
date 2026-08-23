"""PHI-pattern redaction (T4). Runs on every free-text field BEFORE any LLM call.

Synthetic data contains no real PHI, but the control must exist and be tested —
in production this is the last line of defense before text leaves the boundary.
"""
from __future__ import annotations

import re

# Order matters: most specific first.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("DOB", re.compile(r"\bDOB[:\s]*\d{1,2}/\d{1,2}/\d{2,4}\b", re.I)),
    ("DATE_OF_BIRTH", re.compile(r"\bdate of birth[:\s]*\d{1,2}/\d{1,2}/\d{2,4}\b", re.I)),
    ("PHONE", re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("MEMBER_NAME", re.compile(r"\bMember\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+)+)")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
]


def redact(text: str) -> tuple[str, list[str]]:
    """Return (clean_text, findings). findings lists pattern names hit, for the audit log."""
    findings: list[str] = []
    clean = text
    for name, pattern in PATTERNS:
        if pattern.search(clean):
            findings.append(name)
            if name == "MEMBER_NAME":
                clean = pattern.sub("Member [REDACTED]", clean)
            else:
                clean = pattern.sub(f"[{name} REDACTED]", clean)
    return clean, findings
