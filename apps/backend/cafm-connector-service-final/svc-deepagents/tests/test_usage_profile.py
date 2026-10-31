from contextlib import asynccontextmanager
from datetime import date
from fastapi import HTTPException
import pytest
from src.api.homework_identity import TestPrincipal
from src.services.usage_hw1.service import UsageService, record_measurement


def principal(role="property_manager", buildings=("hw1-building-a1",)):
    return TestPrincipal("hw1-usage-test", role, "hw1-org-a", buildings)


class FakeRepo:
    def __init__(self): self.inserted=[];self.audits=[]
    async def insert(self,*args): self.inserted.append(args)
    async def list(self,table,kind=None): return []
    async def audit(self,*args,**kwargs): self.audits.append((args,kwargs))


class FakeStore:
    def __init__(self): self.repo=FakeRepo()
    @asynccontextmanager
    async def transaction(self,*_args,**_kwargs): yield self.repo


@pytest.mark.parametrize("usage,state", [
    ({"input_tokens":12,"output_tokens":4},"measured"),
    (None,"token_usage_unavailable"),
    ({"input_tokens":-1,"output_tokens":2},"token_usage_unavailable"),
])
async def test_only_valid_provider_token_counts_are_recorded(usage,state):
    store=FakeStore()
    from unittest.mock import patch
    with patch.dict("os.environ",{"HW1_USAGE_ENABLED":"true"}), patch("src.services.usage_hw1.service.runtime_store",return_value=store):
        await record_measurement(principal(),"request-a","contract_performance","session-a",usage)
    data=store.repo.inserted[0][-2]
    assert data["measurement_state"]==state
    if state=="measured": assert (data["input_tokens"],data["output_tokens"])==(12,4)
    else: assert data["input_tokens"] is None and data["output_tokens"] is None


async def test_manual_report_requires_week_start_and_creates_no_unmeasured_zero():
    service=UsageService(FakeStore())
    with pytest.raises(HTTPException) as error:
        await service.weekly_report(principal(),date(2026,10,20))
    assert error.value.status_code==422


async def test_manual_monthly_report_uses_calendar_period_and_marks_missing_metrics():
    service=UsageService(FakeStore())
    report=await service.monthly_report(principal(),date(2026,10,1))
    assert report["reporting_period"]["start"]=="2026-10-01"
    assert report["reporting_period"]["end"]=="2026-10-31"
    assert report["trigger"]=="manual"
    assert report["metrics"]["provider_token_usage"]["input_tokens"] is None
    with pytest.raises(HTTPException) as error:
        await service.monthly_report(principal(),date(2026,10,2))
    assert error.value.status_code==422
