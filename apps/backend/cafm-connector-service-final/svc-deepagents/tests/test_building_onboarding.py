from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from src.api.homework_identity import TestPrincipal
from src.services.ingestion_hw1.service import IngestionService


class MemoryRepo:
    def __init__(self):
        self.principal = None
        class Session:
            async def execute(self,*args,**kwargs): pass
        self.session=Session()
        self.tables = {name: {} for name in ("records", "sources", "proposals", "history")}
        self.tables["records"]["portfolio-1"] = {
            "id": "portfolio-1", "kind": "portfolio", "org": "org-1", "buildings": ["b-1"],
            "data": {"portfolio_id": "portfolio-a", "building_ids": ["b-1"], "version": 1},
        }
        self.tables["records"]["b-1"] = {
            "id": "b-1", "kind": "building", "org": "org-1", "buildings": ["b-1"],
            "data": {"name": "Existing Building", "version": 1},
        }

    async def list(self, table, kind=None):
        rows = list(self.tables[table].values())
        return [row for row in rows if kind is None or row["kind"] == kind]

    async def scoped(self,table,scope,kinds,identifier=None):
        from fastapi import HTTPException
        rows=[r for r in self.tables[table].values() if r["kind"] in kinds
              and r["buildings"] and set(r["buildings"])<=set(scope)
              and (identifier is None or r["id"]==identifier)]
        if identifier is not None and not rows: raise HTTPException(404,"Record unavailable")
        return rows

    async def get(self, table, identifier, lock=False):
        if identifier not in self.tables[table]:
            raise HTTPException(404, "Record unavailable")
        return self.tables[table][identifier]

    async def insert(self, table, kind, buildings, data, identifier=None):
        identifier = identifier or f"{table}-{len(self.tables[table])}"
        self.tables[table][identifier] = {"id": identifier, "kind": kind, "org": "org-1",
                                          "buildings": list(buildings), "data": data}
        return identifier

    async def update(self, table, row, data):
        self.tables[table][row["id"]]["data"] = data

    async def audit(self, *args, **kwargs):
        return None


class MemoryStore:
    def __init__(self):
        self.repo = MemoryRepo()

    @asynccontextmanager
    async def transaction(self, principal, *_args, **_kwargs):
        self.repo.principal = principal
        yield self.repo


def fields():
    return {"name": "Fictional New Building", "country": "TEST", "city": "Sample City",
            "building_type": "office", "floor_count": 4, "floor_area": "1200", "area_unit": "m2"}


@pytest.mark.asyncio
async def test_portfolio_manager_adds_building_with_source_and_fixed_hoi_profile():
    store = MemoryStore()
    service = IngestionService(store)
    principal = TestPrincipal("pm-1", "portfolio_manager", "org-1", ("b-1",), "portfolio-a")
    preview = await service.preview_building(principal, "Fictional source profile.", fields())

    result = await service.confirm_building(principal, preview["proposal_id"], preview["preview_hash"],
        "Approved property handover profile.")

    assert result["outcome"] == "created"
    assert result["hoi_score"] == 5
    building_id = result["building_id"]
    assert store.repo.tables["records"][building_id]["data"]["source_id"] == result["source_id"]
    assert building_id in store.repo.tables["records"]["portfolio-1"]["data"]["building_ids"]
    assert store.repo.tables["records"][building_id + "-hoi-manifest"]["data"]["formula"] == "hoi-fixed-coverage-v1"
    assert store.repo.tables["records"][building_id + "-energy-profile-1"]["data"]["floor_area_m2"] == "1200"
    retry = await service.confirm_building(principal, preview["proposal_id"], preview["preview_hash"],
        "Approved property handover profile.")
    assert retry["outcome"] == "already_created"
    assert retry["building_id"] == building_id


@pytest.mark.asyncio
async def test_building_onboarding_denies_property_manager_and_non_metric_area():
    service = IngestionService(MemoryStore())
    property_manager = TestPrincipal("pm-2", "property_manager", "org-1", ("b-1",))
    with pytest.raises(HTTPException) as denied:
        await service.preview_building(property_manager, "Source.", fields())
    assert denied.value.status_code == 403

    invalid = fields()
    invalid["area_unit"] = "ft2"
    portfolio_manager = TestPrincipal("pm-1", "portfolio_manager", "org-1", ("b-1",), "portfolio-a")
    with pytest.raises(HTTPException) as bad_area:
        await service.preview_building(portfolio_manager, "Source.", invalid)
    assert bad_area.value.status_code == 422


@pytest.mark.asyncio
async def test_correcting_hoi_evidence_recalculates_and_notifies_even_if_score_is_unchanged():
    store = MemoryStore()
    service = IngestionService(store)
    principal = TestPrincipal("pm-1", "portfolio_manager", "org-1", ("b-1",), "portfolio-a")
    onboarding = await service.preview_building(principal, "Fictional source profile.", fields())
    created = await service.confirm_building(principal, onboarding["proposal_id"],
        onboarding["preview_hash"], "Approved property handover profile.")
    building_id = created["building_id"]
    expanded = TestPrincipal("pm-1", "portfolio_manager", "org-1", ("b-1",building_id), "portfolio-a")
    item = store.repo.tables["records"][building_id + "-hoi-profile-0"]
    corrected_fields = dict(item["data"]["fields"], floor_count=5)

    preview = await service.preview_correction(expanded, building_id, item["id"],
        "Approved building schedule with five occupied floors.", corrected_fields)
    result = await service.confirm_correction(expanded, preview["proposal_id"], preview["preview_hash"],
        "Floor count is corrected from the signed building schedule.")

    assert result["hoi_score"]["before"] == result["hoi_score"]["after"] == created["hoi_score"]
    assert len(result["hoi_notifications"]) == 2
    assert store.repo.tables["records"][item["id"]]["data"]["superseded"] is True
    histories = [row["data"] for row in store.repo.tables["history"].values()
                 if row["kind"] == "hoi" and row["data"].get("source_id") == result["source_id"]]
    assert len(histories) == 1
    assert histories[0]["before"] == histories[0]["after"]
