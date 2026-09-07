"""
PRD open questions Q1–Q3 locked behaviour tests (Compliance-scoped).

Q4 = FE (not unit-tested here). Q5 = Feature B (deferred).
"""
from __future__ import annotations

from src.config import settings
from src.shared.approvals import build_email_handoff
from src.engines.compliance.verification import soft_authenticity_warning


class TestQ1EmailHandoff:
    def test_default_delivery_mode_is_handoff(self):
        assert settings.email_delivery_mode == "handoff"

    def test_handoff_payload_shape(self):
        out = build_email_handoff(
            {
                "to": "pm@example.com",
                "cc_senior": "senior@example.com",
                "subject": "[URGENT] EICR expiry — Site A — 5 days remaining",
                "body": "Please approve booking.",
            }
        )
        assert out["mode"] == "handoff"
        assert out["mailto_uri"].startswith("mailto:")
        assert "cc=" in out["mailto_uri"]
        assert out["subject"].startswith("[URGENT]")


class TestQ2SoftAuthenticity:
    def test_soft_not_hard_block(self):
        msg = soft_authenticity_warning(
            certificate_type_code="CP17",
            inspector_accreditation_number=None,
            register_name="Gas Safe",
        )
        assert msg is not None
        # Must not instruct a hard stop
        assert "cannot proceed" not in msg.lower()
        assert "hard block" not in msg.lower()


class TestQ3NavigationOnly:
    def test_verify_module_has_no_http_client(self):
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "engines"
            / "compliance"
            / "verification.py"
        )
        text = path.read_text(encoding="utf-8")
        assert "httpx" not in text
        assert "requests" not in text
        assert "urllib.request" not in text
