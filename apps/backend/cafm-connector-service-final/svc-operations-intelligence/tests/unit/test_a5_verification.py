"""
A5 — Live accreditation verification tests (navigation only).

Covers: soft authenticity warning (Q2), verify-now URL building without DB scrape,
unknown type handling, no-API guarantee.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.engines.compliance.verification import (
    build_verify_now_link,
    soft_authenticity_warning,
)


class TestA5SoftAuthenticityWarning:
    def test_missing_number_soft_warns(self):
        msg = soft_authenticity_warning(
            certificate_type_code="CP17",
            inspector_accreditation_number=None,
            register_name="Gas Safe",
        )
        assert msg is not None
        assert "not found in [Gas Safe]" in msg
        assert "override" in msg.lower()

    def test_blank_number_soft_warns(self):
        msg = soft_authenticity_warning(
            certificate_type_code="EICR",
            inspector_accreditation_number="   ",
            register_name="NICEIC",
        )
        assert msg is not None
        assert "NICEIC" in msg

    def test_present_number_still_soft_not_hard_block(self):
        """Q2 — even with a number, message is soft verify guidance (PM override)."""
        msg = soft_authenticity_warning(
            certificate_type_code="EICR",
            inspector_accreditation_number="NICEIC-999",
            register_name="NICEIC",
        )
        assert msg is not None
        assert "override" in msg.lower() or "soft warning" in msg.lower()
        assert "blocked" not in msg.lower() or "hard" not in msg.lower()
        assert "cannot proceed" not in msg.lower()

    def test_no_register_means_no_warning(self):
        msg = soft_authenticity_warning(
            certificate_type_code="HS_POLICY",
            inspector_accreditation_number=None,
            register_name=None,
        )
        assert msg is None


class TestA5VerifyNowNavigation:
    @pytest.mark.asyncio
    async def test_unknown_type_returns_error(self):
        session = AsyncMock()
        # Patch get_pack_type via module
        import src.engines.compliance.verification as ver

        original = ver.get_pack_type
        ver.get_pack_type = AsyncMock(return_value=None)
        try:
            result = await build_verify_now_link(
                session,
                certificate_type_code="NOT_A_REAL_TYPE",
            )
            assert result["ok"] is False
            assert result["verification_url"] is None
        finally:
            ver.get_pack_type = original

    @pytest.mark.asyncio
    async def test_navigation_mode_with_url_and_number(self, mock_pack_row):
        import src.engines.compliance.verification as ver

        original = ver.get_pack_type
        ver.get_pack_type = AsyncMock(return_value=mock_pack_row)
        try:
            result = await build_verify_now_link(
                MagicMock(),
                certificate_type_code="GAS_SAFE",
                accreditation_number="123456",
            )
            assert result["ok"] is True
            assert result["mode"] == "navigation"
            assert result["verification_url"].startswith("https://")
            assert "123456" in result["verification_url"]
            assert "does not scrape" in result["message"].lower() or "Navigation" in result["message"] or "tab" in result["message"].lower()
        finally:
            ver.get_pack_type = original

    @pytest.mark.asyncio
    async def test_no_verification_url_returns_manual_mode(self):
        import src.engines.compliance.verification as ver

        row = SimpleNamespace(
            certificate_type_name="Health & Safety Policy",
            issuing_body="Duty Holder",
            verification_url=None,
        )
        original = ver.get_pack_type
        ver.get_pack_type = AsyncMock(return_value=row)
        try:
            result = await build_verify_now_link(
                MagicMock(),
                certificate_type_code="HS_POLICY",
            )
            assert result["ok"] is True
            assert result["mode"] == "manual"
            assert result["verification_url"] is None
        finally:
            ver.get_pack_type = original

    def test_phase_does_not_claim_api_integration(self):
        """Q3 — this phase is navigation only; no API client module required."""
        import src.engines.compliance.verification as ver
        import inspect

        src = inspect.getsource(ver)
        assert "scrap" in src.lower() or "Navigation" in src or "navigation" in src
        assert "requests.get" not in src
        assert "httpx" not in src
