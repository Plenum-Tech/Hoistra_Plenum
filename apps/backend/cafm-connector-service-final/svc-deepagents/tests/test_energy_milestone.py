"""Independent expectations for Energy source adapters and persistent workflows."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from src.api.homework_identity import TestPrincipal
from src.services.energy_hw1.fixtures import dataset
from src.services.energy_hw1.service import EnergyService
from src.services.energy_hw1.contracts import EnergyQuery
from src.services.energy_hw1.workflows import remediation

B="hw1-building-a1"
P=TestPrincipal("energy-test","property_manager","hw1-org-a",(B,))


class Session:
    async def execute(self,*args,**kwargs): pass


class Repo:
    def __init__(self):
        self.tables={key:{r["id"]:r for r in value} for key,value in deepcopy(dataset()).items()}
        self.tables.update(history={},outbox={})
        self.session=Session()
        self.principal=P

    async def list(self,table,kind=None):
        return [r for r in self.tables[table].values() if kind is None or r["kind"]==kind]

    async def get(self,table,identifier):
        if identifier not in self.tables[table]: raise HTTPException(404,"Record unavailable")
        return self.tables[table][identifier]

    async def insert(self,table,kind,buildings,data,identifier=None):
        identifier=identifier or str(len(self.tables[table]))
        assert identifier not in self.tables[table]
        self.tables[table][identifier]={"id":identifier,"kind":kind,"org":"hw1-org-a","buildings":buildings,"data":data}
        return identifier

    async def audit(self,*args,**kwargs): pass


class Store:
    def __init__(self): self.repo=Repo()
    @asynccontextmanager
    async def transaction(self,*args,**kwargs): yield self.repo


def query(scenario):
    return {"building_id":B,"meter_id":"hw1-energy-m2-"+scenario,
        "period_start":"2026-10-01","period_end":"2026-10-31"}


async def test_matched_weekend_profile_and_window_cost_are_independent_of_weekday_load():
    service=EnergyService(Store())
    result=(await service.execute(P,"energy_anomaly_scan",{"building_id":B,"meter_id":"hw1-energy-m2-spike","as_of":"2026-10-25"}))["result"]
    item=next(x for x in result["anomalies"] if x["type"]=="asset_spike")
    assert Decimal(item["metric_pct"])==200
    assert Decimal(item["excess_kwh"])==240
    assert Decimal(item["financial_gbp"])==48
    assert Decimal(item["annualized_financial_gbp"])==8760


async def test_occupancy_suppresses_drift_and_original_sources_are_unchanged():
    store=Store(); service=EnergyService(store); original=deepcopy(store.repo.tables["sources"])
    for scenario,expected in [("drift",True),("changed",False)]:
        result=(await service.execute(P,"energy_anomaly_scan",{"building_id":B,"meter_id":"hw1-energy-m2-"+scenario,"as_of":"2026-10-25"}))["result"]
        assert any(x["type"]=="baseline_drift" for x in result["anomalies"]) is expected
    assert original==store.repo.tables["sources"]


async def test_monthly_snapshot_and_negation_are_grounded_and_replayed():
    service=EnergyService(Store())
    first=(await service.execute(P,"energy_monthly_report",query("condition")))["result"]
    again=(await service.execute(P,"energy_monthly_report",query("condition")))["result"]
    assert first["report_id"]==again["report_id"] and again["outcome"]=="already_generated"
    assert first["eui"]["financial_excess_gbp"]=="78.63"
    assert len(first["remediation"]["recommendations"])==1
    assert first["remediation"]["recommendations"][0]["recommendation"]=="repair"
    assert "negated" in first["remediation"]["excluded"][0]["reason"]
    retrieved=(await service.execute(P,"energy_get_monthly_report",{"report_id":first["report_id"]}))["result"]
    assert retrieved["eui"]==first["eui"]


@pytest.mark.parametrize("annualized,expected",[("109.25",0),("109.2500001",1)])
async def test_condition_threshold_is_strict_and_uses_unrounded_values(annualized,expected):
    service=EnergyService(Store())
    eui={"source_ids":[],"meter_id":"hw1-energy-m2-condition","benchmark_available":True,"eui_available":True,
        "annualized_eui_kwh_per_m2_year":annualized,"benchmark_kwh_per_m2_year":"95","deviation_pct":"15.00"}
    result=await remediation(service,P,EnergyQuery(**query("condition")),eui,"request")
    assert len(result["recommendations"])==expected


async def test_gap_spacing_three_retries_escalation_and_no_fourth_attempt():
    service=EnergyService(Store())
    result=(await service.execute(P,"energy_gap_scan",{**query("gap"),"period_start":"2026-10-20","period_end":"2026-10-20"}))["result"]
    assert [gap["missing_periods"] for gap in result["gaps"]]==[2]
    gap=result["gaps"][0]["gap_id"]
    with pytest.raises(HTTPException) as error:
        await service.execute(P,"energy_gap_retry",{"gap_id":gap,"test_clock":"2026-10-20T04:09:00+00:00"})
    assert error.value.status_code==409
    for n,minute in enumerate((10,20,30),1):
        attempt=(await service.execute(P,"energy_gap_retry",{"gap_id":gap,"test_clock":f"2026-10-20T04:{minute}:00+00:00"}))["result"]
        assert attempt["retry_count"]==n
    assert attempt["status"]=="escalated"
    again=(await service.execute(P,"energy_gap_retry",{"gap_id":gap,"test_clock":"2026-10-20T05:00:00+00:00"}))["result"]
    assert again["retry_count"]==3


async def test_unknown_dcc_outcome_does_not_blindly_retry():
    store=Store();service=EnergyService(store)
    # Dedicated in-memory source-independent retry state fixture, created before execution.
    store.repo.tables["history"]["unknown-gap"]={"id":"unknown-gap","kind":"energy_gap","buildings":[B],
        "data":{"gap_id":"unknown-gap","first_retry_due":"2026-10-20T04:10:00+00:00","dcc_simulation":"unknown","source_ids":[]}}
    args={"gap_id":"unknown-gap","test_clock":"2026-10-20T04:10:00+00:00"}
    first=(await service.execute(P,"energy_gap_retry",args))["result"]
    again=(await service.execute(P,"energy_gap_retry",{**args,"test_clock":"2026-10-20T05:00:00+00:00"}))["result"]
    assert first["status"]=="unknown" and first["retry_count"]==again["retry_count"]==1


async def test_monthly_source_reading_cannot_be_arbitrarily_prorated():
    with pytest.raises(HTTPException) as error:
        await EnergyService(Store()).execute(P,"energy_eui",{**query("condition"),"period_end":"2026-10-20"})
    assert error.value.status_code==422


async def test_hourly_gaps_make_full_month_eui_unavailable_instead_of_filling_zero():
    result=(await EnergyService(Store()).execute(P,"energy_eui",query("spike")))["result"]
    assert result["coverage"]["missing_hourly_periods"]==144
    assert result["eui_available"] is False
    assert result["unavailable_reason"]=="incomplete_hourly_period"
