"""Gap-fill coverage — extract heuristics, tokens, soft authenticity PRD wording."""
from __future__ import annotations

from src.engines.compliance.extract import _heuristic_extract
from src.engines.compliance.verification import soft_authenticity_warning
from src.shared.approvals import build_email_handoff


def test_heuristic_extract_finds_dates_and_result():
    text = """
    Electrical Installation Condition Report
    Certificate Number: EICR-998877
    Issue date: 2024-01-15
    Expiry date: 2029-01-15
    Result: Pass
    Inspector: Jane Smith
    """
    fields, conf = _heuristic_extract(
        text,
        ["certificate_number", "issue_date", "expiry_date", "result", "inspector_name"],
    )
    assert fields.get("certificate_number")
    assert fields.get("result") == "Pass"
    assert "issue_date" in fields or "expiry_date" in fields
    assert conf


def test_heuristic_extract_prefers_compliance_engine_fra_over_bafe_reg():
    """BAFE company reg must not become certificate_number; engine table wins."""
    text = """
Sentinel Fire & Safety Ltd
BAFE SP203-1 reg. 3421   |   Registered in England & Wales
FIRE RISK ASSESSMENT (FRA)
Type code: FRA   ·   Scope: Building   ·   Trade: Fire
Compliance Engine — canonical fields
certificate_number	FRA-26198
issue_date	07/07/2025
expiry_date	07/07/2026

Certificate details
Building address	Kingsgate Tower, 45 Marsh Wall, Canary Wharf, London E14 9GE
Date	07/07/2026
Assessor name and qualification	James Okafor
Review date	07/07/2026
"""
    fields, conf = _heuristic_extract(
        text,
        ["certificate_number", "issue_date", "expiry_date", "inspector_name"],
    )
    assert fields.get("certificate_number") == "FRA-26198"
    assert fields.get("issue_date") == "2025-07-07"
    assert fields.get("expiry_date") == "2026-07-07"
    assert conf.get("certificate_number") == "high"
    assert conf.get("expiry_date") == "high"


def test_soft_warning_prd_wording_when_missing_number():
    msg = soft_authenticity_warning(
        certificate_type_code="CP17",
        inspector_accreditation_number=None,
        register_name="Gas Safe",
    )
    assert msg is not None
    assert "not found in [Gas Safe]" in msg
    assert "override" in msg.lower()


def test_email_handoff_still_default_for_vendor_renewal():
    out = build_email_handoff(
        {
            "to": "vendor@co.com",
            "subject": "[URGENT] Accreditation renewal required — Gas Safe — Co",
            "body": "Please renew Gas Safe.",
        }
    )
    assert out["mode"] == "handoff"
    assert "mailto:" in out["mailto_uri"]
