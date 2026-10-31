"""Source retention, identity and current/history regressions with independent fixtures."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_ingestion_corrections import MemoryStore
from src.api.homework_identity import TestPrincipal
from src.services.ingestion_hw1.service import IngestionService
from src.agents.ingestion_prompt import assemble

P=TestPrincipal("pm-1","property_manager","org-1",("b-1",))


def service():
    store=MemoryStore()
    row=store.repo.tables["records"]["asset-1"]
    store.repo.tables["sources"]["source-original"]={"id":"source-original","kind":"asset_summary",
        "org":"org-1","buildings":["b-1"],"data":{"fields":dict(row["data"]["fields"]),
        "original":"Fictional retained original","version":1,"scope_verified":True}}
    return IngestionService(store),store


async def test_preview_original_is_immutable_before_and_after_acceptance():
    instance,store=service()
    fields={"asset_id":"new-asset","condition_score":3,"condition_observation":"observed"}
    preview=await instance.preview(P,"b-1","asset_summary","Immutable staged original",fields)
    source_id=preview["preview"]["source_id"]
    before=deepcopy(store.repo.tables["sources"][source_id])
    assert not any(r["data"].get("adapter_key")=="new-asset" for r in store.repo.tables["records"].values())
    accepted=await instance.confirm(P,preview["proposal_id"],preview["preview_hash"],"Explicit acceptance of staged source")
    assert accepted["source_id"]==source_id
    assert store.repo.tables["sources"][source_id]==before
    with pytest.raises(HTTPException) as error:
        await instance.preview(P,"b-1","asset_summary","Immutable staged original",{**fields,"condition_score":4})
    assert error.value.status_code==409


async def test_meter_identity_includes_timestamp_and_interval():
    instance,_=service();ids=[]
    for hour in (1,2):
        preview=await instance.preview(P,"b-1","meter_reading",f"Fictional reading {hour}",{
            "meter_id":"external-meter","timestamp":f"2026-10-20T0{hour}:00:00+00:00",
            "consumption":"10","unit":"kWh","interval":"hour"})
        assert not preview["preview"]["conflicts"]
        result=await instance.confirm(P,preview["proposal_id"],preview["preview_hash"],"Approve separate source measurement period")
        ids.append(result["record_id"])
    assert ids[0]!=ids[1]


async def test_search_selects_current_before_matching_superseded_terms():
    instance,_=service()
    preview=await instance.preview(P,"b-1","asset_summary","New reviewed source",{
        "asset_id":"chiller-1","condition_score":3,"condition_observation":"new-observation"})
    await instance.resolve_conflict(P,preview["proposal_id"],preview["preview_hash"],"accept_incoming","Select retained newer evidence")
    current=await instance.execute(P,"ingestion_search",{"building_id":"b-1","query":"corrosion"})
    history=await instance.execute(P,"ingestion_search",{"building_id":"b-1","query":"corrosion","include_history":True})
    assert current["result"]["total_matching"]==0
    assert history["result"]["total_matching"]==1
    assert history["result"]["results"][0]["source_id"]=="source-original"


async def test_work_report_version_selection_never_updates_imported_row():
    instance,store=service()
    original_update=store.repo.update
    async def guarded(table,row,data):
        if table=="records" and row["kind"]=="work_order_report":
            raise AssertionError("Imported work report update prohibited")
        await original_update(table,row,data)
    store.repo.update=guarded
    fields={"external_reference":"ext-1","raised_at":"2026-10-20T08:00:00+00:00",
        "responded_at":None,"completed_at":None,"status_summary":"external source status",
        "vendor_id":"external-vendor","asset_id":"external-asset"}
    first=await instance.preview(P,"b-1","work_order_report","Original imported report",fields)
    accepted=await instance.confirm(P,first["proposal_id"],first["preview_hash"],"Accept original external source report")
    before=deepcopy(store.repo.tables["records"][accepted["record_id"]])
    next_preview=await instance.preview(P,"b-1","work_order_report","New source report",{**fields,"status_summary":"reviewed representation"})
    chosen=await instance.resolve_conflict(P,next_preview["proposal_id"],next_preview["preview_hash"],"accept_incoming","Choose reviewed source representation")
    assert chosen["version"]==2
    assert store.repo.tables["records"][accepted["record_id"]]==before


async def test_stale_correction_cannot_apply_after_another_review():
    instance,_=service()
    a=await instance.preview_correction(P,"b-1","asset-1","Evidence A",{"condition_score":3})
    b=await instance.preview_correction(P,"b-1","asset-1","Evidence B",{"condition_score":4})
    await instance.confirm_correction(P,a["proposal_id"],a["preview_hash"],"Approve first reviewed correction")
    with pytest.raises(HTTPException) as error:
        await instance.confirm_correction(P,b["proposal_id"],b["preview_hash"],"Attempt stale reviewed correction")
    assert error.value.status_code==409


def test_prompt_manifest_excludes_all_business_decision_tools():
    _,metadata=assemble(P)
    assert len(metadata["loaded_instructions"])==6
    assert not any("confirm" in t or "resolve" in t or "delete" in t for t in metadata["bound_tools"])
    _,officer=assemble(TestPrincipal("co","compliance_officer","org-1",("b-1",)))
    assert "ingestion_preview" not in officer["bound_tools"]
    assert "ingestion_building_preview" not in officer["bound_tools"]


async def test_duplicate_confirmation_replay_rejects_a_changed_reason():
    instance,_=service()
    fields={"asset_id":"new-duplicate","condition_score":3,"condition_observation":"observed"}
    a=await instance.preview(P,"b-1","asset_summary","Retained duplicate original",fields)
    await instance.confirm(P,a["proposal_id"],a["preview_hash"],"First explicit source confirmation")
    b=await instance.preview(P,"b-1","asset_summary","Retained duplicate original",fields)
    await instance.confirm(P,b["proposal_id"],b["preview_hash"],"Duplicate source review confirmation")
    with pytest.raises(HTTPException) as error:
        await instance.confirm(P,b["proposal_id"],b["preview_hash"],"Changed decision reason after resolution")
    assert error.value.status_code==409


async def test_officer_record_projection_cannot_leak_hoi_or_commercial_metadata():
    instance,store=service()
    store.repo.tables["records"]["cert"]={"id":"cert","kind":"certificate","org":"org-1","buildings":["b-1"],
        "data":{"issuer":"Fictional issuer","source_id":"cert-source","version":1,"hoi_item_id":"hidden-hoi",
                "hoi_score":100,"commercial_cost":"restricted"}}
    officer=TestPrincipal("co","compliance_officer","org-1",("b-1",))
    result=await instance.execute(officer,"ingestion_get_record",{"record_id":"cert"})
    assert result["result"]["data"]=={"issuer":"Fictional issuer","source_id":"cert-source","version":1}


async def test_identical_values_from_another_file_keep_both_originals_without_new_record():
    instance,store=service()
    fields=dict(store.repo.tables["records"]["asset-1"]["data"]["fields"])
    before=len(store.repo.tables["records"])
    p=await instance.preview(P,"b-1","asset_summary","Another immutable source file",fields)
    result=await instance.confirm(P,p["proposal_id"],p["preview_hash"],"Review unchanged values with retained evidence")
    assert result["outcome"]=="unchanged" and result["source_id"]=="source-original"
    assert result["incoming_source_id"]==p["preview"]["source_id"]
    assert len(store.repo.tables["records"])==before
    assert store.repo.tables["sources"][result["incoming_source_id"]]["data"]["original"]=="Another immutable source file"
