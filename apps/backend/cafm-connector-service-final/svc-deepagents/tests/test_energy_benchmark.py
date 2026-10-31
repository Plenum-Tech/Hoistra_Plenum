from contextlib import asynccontextmanager
from decimal import Decimal
from src.api.homework_identity import TestPrincipal
from src.services.energy_hw1.service import EnergyService, digest, tm46_pack


class Repo:
    def __init__(self):
        self.records=[]; self.sources=[]
        def add(identifier, kind, fields):
            source_id=identifier+"-source"
            data={**fields,"source_id":source_id,"fixture_version":"energy-fixtures-v1"}
            row={"id":identifier,"kind":kind,"org":"hw1-org-a","buildings":["hw1-building-a1"],"data":data}
            source={"id":source_id,"kind":"energy_source","org":"hw1-org-a","buildings":["hw1-building-a1"],
                    "data":{"fields":data,"content_sha256":digest(data)}}
            self.records.append(row);self.sources.append(source)
        add("hw1-building-a1-energy-meter-1","energy_meter",{
            "unit":"kWh","meter_type":"electricity","tariff_gbp_per_kwh":"0.2"})
        add("hw1-building-a1-energy-profile-1","energy_profile",{
            "floor_area_m2":"100","area_unit":"m2","building_type":"office"})
        add("reading-1","energy_reading",{
            "meter_id":"hw1-building-a1-energy-meter-1","timestamp":"2026-10-01T12:00:00+00:00",
            "consumption":"100","unit":"kWh","interval":"period"})
    async def list(self,table,kind=None):
        rows=self.records if table=="records" else self.sources if table=="sources" else []
        return [row for row in rows if kind is None or row["kind"]==kind]
    async def get(self,table,identifier):
        return next(row for row in self.sources if row["id"]==identifier)
    async def audit(self,*args,**kwargs): pass


def test_tm46_reference_is_bundled_and_explicitly_indicative():
    pack=tm46_pack()
    assert pack["building_types"]["office"]["electricity"]==95
    assert "indicative" in pack["note"]


async def test_eui_compares_to_verified_type_and_uses_only_meter_tariff():
    repo=Repo()
    class Store:
        @asynccontextmanager
        async def transaction(self,*_args,**_kwargs): yield repo
    principal=TestPrincipal("pm","property_manager","hw1-org-a",("hw1-building-a1",))
    result=await EnergyService(Store()).execute(principal,"energy_eui",{
        "building_id":"hw1-building-a1","period_start":"2026-10-01","period_end":"2026-10-01"})
    data=result["result"]
    assert data["benchmark_available"] is True
    assert data["benchmark_kwh_per_m2_year"]=="95"
    assert data["deviation_pct"]=="284.21"
    assert abs(Decimal(data["benchmark_excess_kwh"])-Decimal("73.972602739726027397260274")) < Decimal("1e-24")
    assert data["financial_excess_gbp"]=="14.79"
