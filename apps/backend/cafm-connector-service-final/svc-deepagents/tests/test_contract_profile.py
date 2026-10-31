from datetime import date

import pytest
from fastapi import HTTPException

from src.agents.contract_prompt import allowed_tools, assemble, validate
from src.api.homework_identity import TestPrincipal
from src.services.contract_hw1.contracts import PerformanceQuery
from src.services.contract_hw1.service import ContractService


def principal(role="portfolio_manager", buildings=("hw1-building-a1", "hw1-building-a2")):
    return TestPrincipal("hw1-test-user", role, "hw1-org-a", buildings)


def test_manifest_hashes_and_role_tool_surface_match_python_authority():
    manifest, _, _, loaded = validate()
    assert len(loaded) == 6
    assert manifest["role_tools"]["compliance_officer"] == []
    assert "contract_prepare_scoring_configuration" not in allowed_tools(principal("property_manager"))
    assert "contract_prepare_scoring_configuration" in allowed_tools(principal())
    prompt, metadata = assemble(principal())
    assert "lapse/block history" in prompt
    assert metadata["policy_version"] == "hw1-contract-v1"
    assert metadata["bound_tools"] == allowed_tools(principal())


def test_performance_request_requires_a_complete_calendar_month():
    with pytest.raises(ValueError):
        PerformanceQuery(vendor_id="v", period_start=date(2026, 10, 2),
                         period_end=date(2026, 10, 31), idempotency_key="testing-key-1")


@pytest.mark.parametrize("role", ["general_user", "compliance_officer"])
async def test_general_user_and_compliance_officer_are_denied_before_store_access(role):
    class MustNotTouchStore:
        async def transaction(self, *_args, **_kwargs):
            raise AssertionError("authorization must happen before data access")

    service = ContractService(MustNotTouchStore())
    with pytest.raises(HTTPException) as caught:
        await service.execute(principal(role), "contract_vendor_performance", {}, "request-id")
    assert caught.value.status_code == 403


async def test_property_manager_cannot_read_organization_wide_configuration():
    class MustNotTouchStore:
        async def transaction(self, *_args, **_kwargs):
            raise AssertionError("authorization must happen before data access")

    service = ContractService(MustNotTouchStore())
    with pytest.raises(HTTPException) as caught:
        await service.execute(principal("property_manager", ("hw1-building-a1",)),
                              "contract_scoring_configuration", {}, "request-id")
    assert caught.value.status_code == 403
