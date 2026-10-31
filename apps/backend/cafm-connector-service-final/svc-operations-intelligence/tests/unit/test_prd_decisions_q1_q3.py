"""
PRD open questions Q1–Q3 locked behaviour tests (Compliance-scoped).

Q4 = FE (not unit-tested here). Q5 = Feature B (deferred).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings, settings
from src.shared.approvals import build_email_handoff
from src.engines.compliance.verification import soft_authenticity_warning


class TestQ1EmailHandoff:
    def test_default_delivery_mode_is_handoff(self, monkeypatch):
        """The DEFAULT, which is what PRD Q1 locked — not whatever this host is set to.

        This read the live `settings` singleton, so it asserted the CONFIGURED value and
        failed on any deployment that had chosen the other mode. It was red for exactly
        that reason while the environment said "smtp", and it would now hold every
        deployment at handoff — the opposite of a setting.

        Constructed with the variable absent, so what is under test is the fallback in the
        Field, which is the thing PRD Q1 actually decided.
        """
        monkeypatch.delenv("EMAIL_DELIVERY_MODE", raising=False)
        monkeypatch.delenv("email_delivery_mode", raising=False)
        assert Settings(_env_file=None).email_delivery_mode == "handoff"

    @pytest.mark.parametrize("mode", ["handoff", "platform_send"])
    def test_both_modes_the_dispatcher_implements_are_accepted(self, monkeypatch, mode):
        monkeypatch.setenv("EMAIL_DELIVERY_MODE", mode)
        assert Settings(_env_file=None).email_delivery_mode == mode

    def test_an_empty_value_takes_the_documented_default(self, monkeypatch):
        """`EMAIL_DELIVERY_MODE=` in an env file, or `${EMAIL_DELIVERY_MODE:-}` in a compose
        file, hands the validator "" — pydantic-settings does not drop an empty variable.
        Refusing it stopped the service at import for a value that means "not chosen"; an
        unset choice is the default, not a typo."""
        monkeypatch.setenv("EMAIL_DELIVERY_MODE", "")
        assert Settings(_env_file=None).email_delivery_mode == "handoff"

    @pytest.mark.parametrize("mode", ["smtp", "platform", "graph", "send"])
    def test_a_mode_the_dispatcher_does_not_implement_is_refused(self, monkeypatch, mode):
        """A typo here is an outage nobody is told about.

        shared/approvals.py asks `if mode == "platform_send"` and treats everything else as
        handoff, so an unrecognised value silently means "do not send". That happened twice
        on this one setting — "platform" first, then "smtp", which is a real transport name
        and reads like it would send. Neither failed. Mail simply stopped leaving and the
        console went on reporting that it had.
        """
        monkeypatch.setenv("EMAIL_DELIVERY_MODE", mode)
        with pytest.raises(ValidationError, match="not a delivery mode"):
            Settings(_env_file=None)

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
