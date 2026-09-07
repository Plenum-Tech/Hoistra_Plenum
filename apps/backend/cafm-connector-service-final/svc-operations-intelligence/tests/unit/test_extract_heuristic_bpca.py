"""Heuristic extract coverage for real-world UK certificate layouts."""
from __future__ import annotations

from src.engines.compliance.extract import _heuristic_extract


BPCA_RENTOKIL = """
BPCA's logo and its derivatives are protected trade marks (UK00002630888).
This is to certify that
Membership number:
M15/ 035
Rentokil Pest Control
is a Full Member of the
British Pest Control Association for 2026
and has undertaken to observe the
Association's Codes of Best Practice
and Code of Conduct.

Chris Cagienard
President
Membership year: 1 January 2026 – 31 December 2026
Certificate valid until 28 February 2027
"""


def test_bpca_rentokil_membership_extract():
    fields = [
        "certificate_number",
        "issue_date",
        "expiry_date",
        "Company name",
        "BPCA member no.",
    ]
    out, conf = _heuristic_extract(BPCA_RENTOKIL, fields)
    assert out.get("certificate_number") == "M15/035"
    assert out.get("expiry_date") == "2027-02-28"
    assert out.get("issue_date") == "2026-01-01"
    assert "Rentokil" in (out.get("Company name") or "")
    assert conf.get("certificate_number") == "high"
    assert conf.get("expiry_date") in {"medium", "high"}


def test_numeric_uk_dates_still_work():
    text = "Certificate number: FRA-26198\nIssue date: 07/07/2025\nExpiry date: 07/07/2026\n"
    out, _ = _heuristic_extract(text, ["certificate_number", "issue_date", "expiry_date"])
    assert out["certificate_number"] == "FRA-26198"
    assert out["issue_date"] == "2025-07-07"
    assert out["expiry_date"] == "2026-07-07"
