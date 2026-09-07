"""UK verification register table + Verify-now prefill rules."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engines.compliance.verification import (
    build_verify_now_link,
    list_verification_registers,
    resolve_verification_register,
)


def test_register_table_matches_authoritative_set():
    regs = list_verification_registers()
    assert len(regs) >= 14
    urls = {r["verification_url"] for r in regs}
    assert "https://www.gassaferegister.co.uk/find-an-engineer/" in urls
    assert "https://bpca.org.uk/find-a-pest-controller" in urls
    assert "https://ssip.org.uk/members/" in urls
    assert "https://www.niceic.com/find-a-contractor" in urls
    assert "https://www.sia.homeoffice.gov.uk/Pages/licensing-check.aspx" in urls
    bpca = next(r for r in regs if "BPCA" in (r["register"] or ""))
    assert bpca["prefills_number"] is False
    gas = next(r for r in regs if (r["register"] or "").startswith("Gas Safe"))
    assert gas["prefills_number"] is True


def test_resolve_by_type_code_and_url():
    gas = resolve_verification_register(certificate_type_code="GAS_SAFE")
    assert gas is not None
    assert gas["prefills_number"] is True
    bpca = resolve_verification_register(certificate_type_code="BPCA")
    assert bpca is not None
    assert bpca["prefills_number"] is False
    by_url = resolve_verification_register(
        verification_url="https://www.nsi.org.uk/find-a-company/"
    )
    assert by_url is not None
    assert "NSI" in (by_url["register"] or "")


@pytest.mark.asyncio
async def test_verify_now_prefills_gas_safe_number():
    import src.engines.compliance.verification as ver

    row = SimpleNamespace(
        certificate_type_name="Gas Safe",
        issuing_body="Gas Safe registered engineer",
        trade_category="Gas",
        verification_url="https://www.gassaferegister.co.uk/find-an-engineer/",
    )
    original = ver.get_pack_type
    ver.get_pack_type = AsyncMock(return_value=row)
    try:
        result = await build_verify_now_link(
            MagicMock(),
            certificate_type_code="GAS_SAFE",
            accreditation_number="123456",
        )
        assert result["ok"] is True
        assert result["prefills_number"] is True
        assert "123456" in result["verification_url"]
    finally:
        ver.get_pack_type = original


@pytest.mark.asyncio
async def test_verify_now_does_not_prefill_bpca():
    import src.engines.compliance.verification as ver

    row = SimpleNamespace(
        certificate_type_name="BPCA",
        issuing_body="BPCA (British Pest Control Association)",
        trade_category="Pest Control",
        verification_url="https://bpca.org.uk/find-a-pest-controller",
    )
    original = ver.get_pack_type
    ver.get_pack_type = AsyncMock(return_value=row)
    try:
        result = await build_verify_now_link(
            MagicMock(),
            certificate_type_code="BPCA",
            accreditation_number="BPCA-999",
        )
        assert result["ok"] is True
        assert result["prefills_number"] is False
        assert result["verification_url"] == "https://bpca.org.uk/find-a-pest-controller"
        assert "BPCA-999" not in result["verification_url"]
    finally:
        ver.get_pack_type = original
