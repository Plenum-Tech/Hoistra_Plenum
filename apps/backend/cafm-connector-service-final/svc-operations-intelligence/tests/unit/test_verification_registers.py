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

GOVUK_SEARCH = (
    "https://find-energy-certificate.service.gov.uk"
    "/find-a-certificate/search-by-reference-number"
)


def test_govuk_energy_register_is_in_the_table_for_all_three_energy_types():
    """EPC, DEC and air-conditioning reports are the only documents on a public register
    that lists the DOCUMENT rather than the contractor, so all three must reach it."""
    reg = resolve_verification_register(certificate_type_code="EPC")
    assert reg is not None and "GOV.UK" in (reg["register"] or "")
    assert reg["verification_url"] == GOVUK_SEARCH
    for code in ("DEC", "TM44"):
        assert resolve_verification_register(certificate_type_code=code) == reg


@pytest.mark.asyncio
async def test_verify_now_opens_the_certificate_itself_for_a_lodged_reference():
    """A lodgement reference addresses its own public page, so the link opens the
    certificate rather than a search form the person still has to fill in."""
    import src.engines.compliance.verification as ver

    row = SimpleNamespace(
        certificate_type_name="Display Energy Certificate (DEC)",
        issuing_body="Accredited Energy Assessor",
        trade_category="Energy",
        verification_url=GOVUK_SEARCH,
    )
    original = ver.get_pack_type
    ver.get_pack_type = AsyncMock(return_value=row)
    try:
        result = await build_verify_now_link(
            MagicMock(),
            certificate_type_code="DEC",
            certificate_number="9920-1010-0626-0890-2091",
        )
    finally:
        ver.get_pack_type = original
    assert result["verification_url"] == (
        "https://find-energy-certificate.service.gov.uk"
        "/energy-certificate/9920-1010-0626-0890-2091"
    )


@pytest.mark.asyncio
async def test_verify_now_falls_back_to_the_registers_own_search_field():
    """Without a lodgement reference the link must still be usable, and the GOV.UK form
    reads `reference_number` - the generic `q` this builder uses elsewhere is ignored."""
    import src.engines.compliance.verification as ver

    row = SimpleNamespace(
        certificate_type_name="Energy Performance Certificate (EPC)",
        issuing_body="Accredited Energy Assessor",
        trade_category="Energy",
        verification_url=GOVUK_SEARCH,
    )
    original = ver.get_pack_type
    ver.get_pack_type = AsyncMock(return_value=row)
    try:
        not_a_reference = await build_verify_now_link(
            MagicMock(), certificate_type_code="EPC", certificate_number="EPC-SYN-16FA8CA4"
        )
        nothing_at_all = await build_verify_now_link(
            MagicMock(), certificate_type_code="EPC"
        )
    finally:
        ver.get_pack_type = original
    # GOV.UK validates the field as a 20-digit number and answers anything else with
    # "Enter a 20-digit certificate number" (seen live on 25 Sep 2026 with EPC-SYN-FA96128A),
    # so a number that is not a lodgement reference opens the empty form, not an error page.
    assert not_a_reference["verification_url"] == GOVUK_SEARCH
    assert "q=" not in not_a_reference["verification_url"]
    assert nothing_at_all["verification_url"] == GOVUK_SEARCH


@pytest.mark.asyncio
async def test_a_twenty_digit_reference_without_dashes_still_opens_the_certificate():
    import src.engines.compliance.verification as ver

    row = SimpleNamespace(
        certificate_type_name="Energy Performance Certificate (EPC)",
        issuing_body="Accredited Energy Assessor",
        trade_category="Energy",
        verification_url=GOVUK_SEARCH,
    )
    original = ver.get_pack_type
    ver.get_pack_type = AsyncMock(return_value=row)
    try:
        r = await build_verify_now_link(
            MagicMock(), certificate_type_code="EPC", certificate_number="99201010062608902091"
        )
    finally:
        ver.get_pack_type = original
    assert r["verification_url"] == (
        "https://find-energy-certificate.service.gov.uk"
        "/energy-certificate/9920-1010-0626-0890-2091"
    )
