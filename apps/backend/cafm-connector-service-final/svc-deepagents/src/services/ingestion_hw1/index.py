"""Append-only scoped search projections. Source removal/index administration is absent."""
import hashlib,json
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import text


async def lock(repo,key):
    await repo.session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),{"key":"ingestion:"+key})


async def project(repo,row,previous=None):
    if previous:
        await project(repo,previous)
    data=row["data"]
    fields=data.get("fields") or {k:v for k,v in data.items() if k not in {
        "scope_verified","source_id","assessment_reason","blocking_reasons"}}
    if row["kind"]=="certificate":
        from ..compliance_hw1.contracts import CertificateValues
        fields={k:data.get(k) for k in CertificateValues.model_fields}
    source_id=data.get("source_id")
    if not source_id: return
    key=data.get("lineage_key") or data.get("adapter_key") or row["id"]
    entry={"record_id":row["id"],"source_id":source_id,"kind":row["kind"],"lineage_key":str(key),
        "version":data.get("version",1),"fields":fields,"generated_at":datetime.now(timezone.utc).isoformat()}
    identifier="index-"+hashlib.sha256((row["id"]+":"+source_id+":"+str(entry["version"])).encode()).hexdigest()
    index_kind="search_index_certificate" if row["kind"]=="certificate" else "search_index_entry"
    if not any(r["id"]==identifier for r in await repo.list("history",index_kind)):
        await repo.insert("history",index_kind,row["buildings"],entry,identifier)


def authorized(principal,row):
    return (row["org"]==principal.organization_id and bool(row["buildings"])
        and set(row["buildings"])<=set(principal.building_ids)
        and (principal.role in {"property_manager","portfolio_manager"} or
             principal.role=="compliance_officer" and row["kind"]=="certificate"))


async def search(store,principal,query):
    if principal.role not in {"property_manager","portfolio_manager","compliance_officer"}:
        raise HTTPException(403,"Search requires a permitted manager/certificate role")
    if query.building_id not in principal.building_ids: raise HTTPException(403,"Building access denied")
    async with store.transaction(principal,repeatable=True) as repo:
        kinds=["search_index_certificate"]
        if principal.role!="compliance_officer": kinds.append("search_index_entry")
        candidates=await repo.scoped("history",[query.building_id],kinds)
        rows=[r for r in candidates if r["buildings"]==[query.building_id]
              and (principal.role!="compliance_officer" or r["data"]["kind"]=="certificate")]
        latest={}
        for row in rows:
            data=row["data"]; key=(data["kind"],data["lineage_key"])
            if key not in latest or (data["version"],data["generated_at"])>(latest[key]["version"],latest[key]["generated_at"]):
                latest[key]=data
        selected=[]
        for row in rows:
            item=row["data"]; current=latest[(item["kind"],item["lineage_key"])]
            is_current=(item["record_id"],item["source_id"],item["version"])==(current["record_id"],current["source_id"],current["version"])
            if not query.include_history and not is_current: continue
            if query.query.casefold() not in json.dumps(item["fields"],sort_keys=True).casefold(): continue
            source=await repo.get("sources",item["source_id"])
            if source["org"]!=principal.organization_id or source["buildings"]!=[query.building_id]:
                raise HTTPException(409,"Search source scope integrity failed")
            evidence=source["data"].get("fields",{})
            if any(evidence.get(k)!=v for k,v in item["fields"].items()):
                raise HTTPException(409,"Search projection differs from retained source evidence")
            selected.append({**item,"current":is_current,"source_version":source["data"].get("version")})
        await repo.audit("ingestion_search","succeeded",{"building_id":query.building_id,"count":len(selected)})
        return {"building_id":query.building_id,"results":selected[:query.limit],"total_matching":len(selected),
                "returned":min(len(selected),query.limit),"complete":len(selected)<=query.limit,
                "include_history":query.include_history,"index":"append-only source-preserving projection"}
