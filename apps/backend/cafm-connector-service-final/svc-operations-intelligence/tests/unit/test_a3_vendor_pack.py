"""
A3 — Vendor Certificate Pack tests.

Covers: risk levels, block_state adversary, vendor email adversary,
block lift condition, score-cap readiness field (block_state).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.engines.compliance.lifecycle import vendor_risk_level
from src.shared.approvals import build_email_handoff
from src.swarm.adversary import validate_block_state_change, validate_vendor_email_draft


class TestA3VendorRiskLevels:
    def test_clear_above_90(self):
        assert vendor_risk_level(91) == "Clear"
        assert vendor_risk_level(365) == "Clear"

    def test_medium_31_to_90(self):
        assert vendor_risk_level(90) == "Medium Risk"
        assert vendor_risk_level(31) == "Medium Risk"
        assert vendor_risk_level(60) == "Medium Risk"

    def test_high_1_to_30(self):
        assert vendor_risk_level(30) == "High Risk"
        assert vendor_risk_level(1) == "High Risk"

    def test_lapsed_at_or_past_expiry(self):
        assert vendor_risk_level(0) == "Lapsed"
        assert vendor_risk_level(-5) == "Lapsed"

    def test_unknown_when_missing_days(self):
        assert vendor_risk_level(None) == "Unknown"


class TestA3BlockStateAdversary:
    def test_block_rejected_when_expiry_still_future(self):
        r = validate_block_state_change(
            accreditation_type="Gas Safe",
            expiry_date=date.today() + timedelta(days=5),
            proposed_block_state="Blocked",
        )
        assert r.approved is False
        assert "expiry_still_in_future_block_not_allowed" in r.reasons

    def test_block_approved_when_past_expiry(self):
        r = validate_block_state_change(
            accreditation_type="NICEIC",
            expiry_date=date.today() - timedelta(days=1),
            proposed_block_state="Blocked",
        )
        assert r.approved is True

    def test_block_requires_accreditation_type(self):
        r = validate_block_state_change(
            accreditation_type=None,
            expiry_date=date.today() - timedelta(days=1),
            proposed_block_state="Blocked",
        )
        assert r.approved is False
        assert "missing_accreditation_type" in r.reasons

    def test_invalid_block_state_rejected(self):
        r = validate_block_state_change(
            accreditation_type="Gas Safe",
            expiry_date=date.today() - timedelta(days=1),
            proposed_block_state="Suspended",
        )
        assert r.approved is False
        assert "invalid_block_state" in r.reasons


class TestA3VendorEmailAdversaryAndHandoff:
    def test_email_rejects_wrong_contact(self):
        draft = {
            "to": "wrong@x.com",
            "subject": "[URGENT] Accreditation renewal required — Gas Safe — Co",
            "body": "Gas Safe https://www.gassaferegister.co.uk/find-an-engineer/",
        }
        r = validate_vendor_email_draft(
            vendor_contact_email="right@x.com",
            accreditation_type="Gas Safe",
            renewal_url="https://www.gassaferegister.co.uk/find-an-engineer/",
            draft=draft,
        )
        assert r.approved is False
        assert "vendor_contact_mismatch" in r.reasons

    def test_email_requires_accreditation_in_subject_or_body(self):
        draft = {
            "to": "v@x.com",
            "subject": "[URGENT] please renew",
            "body": "hello",
        }
        r = validate_vendor_email_draft(
            vendor_contact_email="v@x.com",
            accreditation_type="Gas Safe",
            renewal_url=None,
            draft=draft,
        )
        assert r.approved is False
        assert "accreditation_type_not_in_email" in r.reasons

    def test_email_approved_when_fields_match(self):
        url = "https://www.gassaferegister.co.uk/find-an-engineer/"
        draft = {
            "to": "v@x.com",
            "subject": "[URGENT] Accreditation renewal required — Gas Safe — Co",
            "body": f"Gas Safe\n{url}",
        }
        r = validate_vendor_email_draft(
            vendor_contact_email="v@x.com",
            accreditation_type="Gas Safe",
            renewal_url=url,
            draft=draft,
        )
        assert r.approved is True

    def test_high_risk_email_uses_handoff_not_platform_send(self):
        """PRD Q1 — approve path returns mailto handoff for vendor emails."""
        draft = {
            "to": "vendor@example.com",
            "subject": "[URGENT] Accreditation renewal required — Gas Safe — Acme",
            "body": "Please renew.",
        }
        handoff = build_email_handoff(draft)
        assert handoff["mode"] == "handoff"
        assert "mailto:" in handoff["mailto_uri"]
        assert "does not send" in handoff["message"]

    def test_block_lift_condition_future_expiry(self):
        """Renewed cert with future expiry clears block (scan logic contract)."""
        days = 120
        risk = vendor_risk_level(days)
        should_lift = risk == "Clear" and days > 0
        assert should_lift is True
