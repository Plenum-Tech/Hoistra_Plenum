"""Unit tests — Quality/Adversary gates."""
from datetime import date, timedelta

from src.swarm.adversary import validate_block_state_change, validate_vendor_email_draft


def test_block_rejected_when_expiry_future():
    result = validate_block_state_change(
        accreditation_type="Gas Safe",
        expiry_date=date.today() + timedelta(days=10),
        proposed_block_state="Blocked",
    )
    assert result.approved is False
    assert "expiry_still_in_future_block_not_allowed" in result.reasons


def test_block_approved_when_lapsed():
    result = validate_block_state_change(
        accreditation_type="Gas Safe",
        expiry_date=date.today() - timedelta(days=1),
        proposed_block_state="Blocked",
    )
    assert result.approved is True


def test_vendor_email_contact_mismatch():
    draft = {
        "to": "wrong@example.com",
        "subject": "[URGENT] Accreditation renewal required — Gas Safe — Acme",
        "body": "Gas Safe\nhttps://www.gassaferegister.co.uk/find-an-engineer/",
    }
    result = validate_vendor_email_draft(
        vendor_contact_email="right@example.com",
        accreditation_type="Gas Safe",
        renewal_url="https://www.gassaferegister.co.uk/find-an-engineer/",
        draft=draft,
    )
    assert result.approved is False
    assert "vendor_contact_mismatch" in result.reasons


def test_vendor_email_approved():
    url = "https://www.gassaferegister.co.uk/find-an-engineer/"
    draft = {
        "to": "right@example.com",
        "subject": "[URGENT] Accreditation renewal required — Gas Safe — Acme",
        "body": f"Gas Safe renewal: {url}",
    }
    result = validate_vendor_email_draft(
        vendor_contact_email="right@example.com",
        accreditation_type="Gas Safe",
        renewal_url=url,
        draft=draft,
    )
    assert result.approved is True
