from datetime import date
from contextlib import asynccontextmanager
import pytest
from fastapi import HTTPException
from src.agents.energy_prompt import allowed_tools, assemble, validate
from src.agents.homework_energy_agent import explicit_energy_request, specs
from src.api.homework_identity import TestPrincipal
from src.services.energy_hw1.contracts import EnergyQuery
from src.services.energy_hw1.service import EnergyService


def principal(role="property_manager", buildings=("hw1-building-a1",)):
    return TestPrincipal("hw1-energy-test", role, "hw1-org-a", buildings)


def test_energy_manifest_hashes_and_scope_projection():
    manifest, prompt = validate()
    assert manifest["specification_version"] == "0.37.0"
    assert "period EUI" in prompt
    assert allowed_tools(principal()) == ["energy_anomaly_act", "energy_anomaly_list", "energy_anomaly_scan", "energy_benchmarks", "energy_eui", "energy_gap_retry", "energy_gap_scan", "energy_get_monthly_report", "energy_monthly_report", "energy_readings", "energy_remediation_queue"]
    assert allowed_tools(principal("general_user")) == []
    assert allowed_tools(principal("compliance_officer")) == []
    assert "energy_anomaly_scan" in prompt
    assert len(specs(principal())) == 10
    assert "energy_anomaly_act" not in assemble(principal())[1]["bound_tools"]


def test_explicit_anomaly_request_requires_one_scoped_building_and_date():
    assert explicit_energy_request("Scan anomalies hw1-building-a1 as of 2026-10-25") == (
        "energy_anomaly_scan", {"building_id": "hw1-building-a1", "as_of": "2026-10-25"})
    assert explicit_energy_request("Scan anomalies as of 2026-10-25") is None


@pytest.mark.parametrize("role", ["general_user", "compliance_officer"])
async def test_non_manager_denied_before_energy_store(role):
    class MustNotTouchStore:
        async def audit(self, *_a, **_kw):
            return None
        async def transaction(self, *_a, **_kw):
            raise AssertionError("authorization precedes store access")
    query = {"building_id": "hw1-building-a1", "period_start": "2026-10-01", "period_end": "2026-10-31"}
    with pytest.raises(HTTPException) as error:
        await EnergyService(MustNotTouchStore()).execute(principal(role), "energy_eui", query)
    assert error.value.status_code == 403


async def test_anomaly_actions_require_reason_and_are_idempotent():
    from src.services.energy_hw1.contracts import AnomalyAction
    with pytest.raises(ValueError):
        AnomalyAction(anomaly_id="e1", action="mark_expected")

    class Repo:
        def __init__(self):
            class Session:
                async def execute(self,*args,**kwargs): pass
            self.session=Session()
            self.rows = {"a1": {"id":"a1", "kind":"energy_anomaly", "buildings":["hw1-building-a1"],
                "data":{"anomaly_id":"a1", "building_id":"hw1-building-a1"}}}
            self.events = []
        async def get(self, table, identifier): return self.rows[identifier]
        async def list(self, table, kind=None):
            if kind == "energy_anomaly_action": return self.events
            if kind == "energy_anomaly": return [self.rows["a1"]]
            return []
        async def insert(self, table, kind, buildings, data, identifier):
            self.events.append({"id":identifier,"kind":kind,"buildings":buildings,"data":data})
        async def audit(self, *args, **kwargs): pass
    repo=Repo()
    class Store:
        @asynccontextmanager
        async def transaction(self, *_args, **_kwargs): yield repo
    service=EnergyService(Store())
    action=AnomalyAction(anomaly_id="a1",action="monitor")
    first=await service.execute(principal(),"energy_anomaly_act",action.model_dump())
    again=await service.execute(principal(),"energy_anomaly_act",action.model_dump())
    assert first["result"]["outcome"]=="recorded"
    assert again["result"]["outcome"]=="already_applied"
    queue=await service.execute(principal(),"energy_anomaly_list",{"building_id":"hw1-building-a1","status":"monitoring"})
    assert len(queue["result"]["anomalies"])==1


def test_energy_period_adapter_rejects_reversed_or_oversized_periods():
    with pytest.raises(ValueError):
        EnergyQuery(building_id="b", period_start=date(2026, 10, 2), period_end=date(2026, 10, 1))
    with pytest.raises(ValueError):
        EnergyQuery(building_id="b", period_start=date(2025, 1, 1), period_end=date(2026, 1, 2))
