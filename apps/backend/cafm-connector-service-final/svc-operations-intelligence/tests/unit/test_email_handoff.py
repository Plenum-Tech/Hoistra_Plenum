"""PRD Q1 — email handoff helpers."""
from src.shared.approvals import build_email_handoff


def test_mailto_handoff_contains_subject_and_body():
    draft = {
        "to": "vendor@example.com",
        "subject": "[URGENT] Accreditation renewal required — Gas Safe — Acme",
        "body": "Please renew Gas Safe.\nhttps://www.gassaferegister.co.uk/find-an-engineer/",
    }
    result = build_email_handoff(draft)
    assert result["mode"] == "handoff"
    assert result["to"] == "vendor@example.com"
    assert result["mailto_uri"].startswith("mailto:vendor@example.com?")
    assert "subject=" in result["mailto_uri"]
    assert "body=" in result["mailto_uri"]
    assert "does not send" in result["message"]
