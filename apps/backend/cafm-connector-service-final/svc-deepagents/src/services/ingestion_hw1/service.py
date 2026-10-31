"""Scoped source intake and reviewed derived corrections; originals are append-only."""
import hashlib, json
from datetime import datetime,timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from fastapi import HTTPException
from ..compliance_hw1.store import runtime_store
from ...api.homework_identity import TestPrincipal
from .contracts import validated, identity
from .index import lock, project

KINDS = {"asset_summary", "inspection_report", "work_order_report", "meter_reading"}
ALLOWED_FIELDS = {"asset_summary": {"asset_id", "condition_score", "condition_observation"},
 "inspection_report": {"inspection_id", "inspection_date", "result", "summary"},
 "work_order_report": {"external_reference", "raised_at", "responded_at", "completed_at", "status_summary", "vendor_id", "asset_id"},
 "meter_reading": {"meter_id", "timestamp", "consumption", "unit", "interval"}}
MANAGERS = {"property_manager", "portfolio_manager"}
ROLE_TOOLS={"general_user":[],"compliance_officer":["ingestion_correction_preview","ingestion_search","ingestion_get_record","ingestion_get_source"],
    "property_manager":["ingestion_preview","ingestion_correction_preview","ingestion_search","ingestion_get_record","ingestion_get_source"],
    "portfolio_manager":["ingestion_preview","ingestion_correction_preview","ingestion_building_preview","ingestion_search","ingestion_get_record","ingestion_get_source"]}

def canonical(data): return json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=False)
def sha(data): return hashlib.sha256(canonical(data).encode()).hexdigest()

async def current_rows(repo,kind,building,key):
    rows=[r for r in await repo.list("records",kind) if r["buildings"]==[building]
          and r["data"].get("adapter_key")==key and not r["data"].get("superseded")]
    version=max((r["data"].get("version",1) for r in rows),default=0)
    return [r for r in rows if r["data"].get("version",1)==version]

class IngestionService:
    def __init__(self,store=None): self.store=store or runtime_store()

    @staticmethod
    def _building_fields(fields):
        required={"name","country","city","building_type","floor_count","floor_area","area_unit"}
        if not isinstance(fields,dict) or set(fields)!=required:
            raise HTTPException(422,"Building profile fields do not match the approved schema")
        for key in ("name","country","city","building_type"):
            value=fields.get(key)
            if not isinstance(value,str) or not value.strip() or len(value)>160:
                raise HTTPException(422,"Building profile text fields must be non-empty and bounded")
        if type(fields.get("floor_count")) is not int or fields["floor_count"]<1:
            raise HTTPException(422,"Building floor count must be a positive integer")
        if fields.get("area_unit")!="m2":
            raise HTTPException(422,"Homework Energy onboarding requires floor area in m2")
        try:
            raw_area=str(fields.get("floor_area"))
            if len(raw_area)>40:
                raise InvalidOperation
            area=Decimal(raw_area)
        except (InvalidOperation,TypeError,ValueError):
            raise HTTPException(422,"Building floor area must be a finite positive number") from None
        if not area.is_finite() or area<=0:
            raise HTTPException(422,"Building floor area must be a finite positive number")
        return {**fields,"floor_area":str(area)}

    async def preview_building(self,principal,original,fields):
        if not isinstance(principal,TestPrincipal) or principal.role!="portfolio_manager":
            raise HTTPException(403,"Only a Portfolio Manager can add a building")
        if not principal.portfolio_id:
            raise HTTPException(403,"No trusted portfolio assignment is configured")
        normalized=self._building_fields(fields)
        if not isinstance(original,str) or not original.strip() or len(original.encode())>1_000_000:
            raise HTTPException(422,"Original building evidence is required and size limited")
        async with self.store.transaction(principal,repeatable=True) as repo:
            portfolios=[row for row in await repo.list("records","portfolio")
                        if row["data"].get("portfolio_id")==principal.portfolio_id]
            if len(portfolios)!=1:
                raise HTTPException(409,"Assigned portfolio source is missing or ambiguous")
            portfolio=portfolios[0]
            portfolio_buildings=set(portfolio["data"].get("building_ids",portfolio["buildings"]))
            if (portfolio["org"]!=principal.organization_id
                    or not portfolio_buildings.issubset(set(principal.building_ids))):
                raise HTTPException(403,"Assigned portfolio scope check failed")
            same_name=[row for row in await repo.list("records","building")
                       if row["id"] in portfolio_buildings
                       and str(row["data"].get("name") or "").strip().casefold()==normalized["name"].strip().casefold()]
            if same_name:
                raise HTTPException(409,"A building with this name already exists in the assigned portfolio")
            identifier="hw1-building-new-"+uuid4().hex
            preview={"building_id":identifier,"portfolio_id":principal.portfolio_id,
                "portfolio_version":portfolio["data"].get("version",1),"profile":normalized,
                "action":"add_building_to_assigned_portfolio","score_effect":"new fixed HOI profile evidence only"}
            source_id="building-profile-source-"+uuid4().hex
            await repo.insert("sources","building_profile",[identifier],{"original":original,
                "text":json.dumps({"fields":normalized},sort_keys=True),"fields":normalized,
                "content_sha256":sha(normalized),"version":1,"information_type":"building_profile","scope_verified":True},source_id)
            preview["source_id"]=source_id
            proposal_id=str(uuid4())
            await repo.insert("proposals","building_onboarding",[identifier],{
                "actor":principal.user_id,"role":principal.role,"preview":preview,
                "preview_hash":sha(preview),"original":original,"status":"previewed"},proposal_id)
            await repo.audit("building_onboarding_preview","succeeded",{
                "proposal_id":proposal_id,"portfolio_id":principal.portfolio_id})
            return {"proposal_id":proposal_id,"preview":preview,"preview_hash":sha(preview),
                    "outcome":"awaiting_confirmation"}

    async def confirm_building(self,principal,proposal_id,expected_hash,reason,request_id=None):
        if not isinstance(principal,TestPrincipal) or principal.role!="portfolio_manager":
            raise HTTPException(403,"Only a Portfolio Manager can add a building")
        if not principal.portfolio_id:
            raise HTTPException(403,"No trusted portfolio assignment is configured")
        if not isinstance(reason,str) or len(reason.strip())<8:
            raise HTTPException(422,"A specific building onboarding reason is required")
        async with self.store.transaction(principal) as repo:
            proposal=await repo.get("proposals",proposal_id,lock=True)
            data=proposal["data"];preview=data.get("preview",{})
            if (proposal["kind"]!="building_onboarding"
                    or preview.get("portfolio_id")!=principal.portfolio_id):
                raise HTTPException(404,"Building proposal unavailable")
            if data.get("status")=="confirmed":
                if expected_hash==data.get("preview_hash") and reason.strip()==data.get("reason"):
                    return {**data["result"],"outcome":"already_created"}
                raise HTTPException(409,"Changed building confirmation")
            if (data.get("status")!="previewed" or expected_hash!=sha(preview)
                    or data.get("role")!=principal.role
                    or preview.get("portfolio_id")!=principal.portfolio_id):
                raise HTTPException(409,"Stale or changed building preview")
            fields=self._building_fields(preview.get("profile"))
            portfolios=[row for row in await repo.list("records","portfolio")
                        if row["data"].get("portfolio_id")==principal.portfolio_id]
            if len(portfolios)!=1:
                raise HTTPException(409,"Assigned portfolio source is missing or ambiguous")
            portfolio=portfolios[0]
            portfolio=await repo.get("records",portfolio["id"],lock=True)
            old_members=set(portfolio["data"].get("building_ids",portfolio["buildings"]))
            if (portfolio["org"]!=principal.organization_id
                    or not old_members.issubset(set(principal.building_ids))
                    or portfolio["data"].get("version",1)!=preview.get("portfolio_version",1)):
                raise HTTPException(409,"Assigned portfolio changed since preview")
            building_id=preview["building_id"]
            if any(row["id"]==building_id for row in await repo.list("records","building")):
                raise HTTPException(409,"Generated building identifier is already in use")
            duplicate_name=any(str(row["data"].get("name") or "").strip().casefold()==fields["name"].strip().casefold()
                for row in await repo.list("records","building") if row["id"] in old_members)
            if duplicate_name:
                raise HTTPException(409,"Building name now conflicts; prepare a fresh preview")

            source_id=preview["source_id"]
            await repo.insert("records","building",[building_id],{
                **fields,"source_id":source_id,"version":1,"accepted":True,
                "portfolio_id":principal.portfolio_id,"created_by":principal.user_id,
                "scope_verified":True},building_id)
            from ..compliance_hw1.hoi import FORMULA,TYPES,building_score
            await repo.insert("records","hoi_manifest",[building_id],{
                "formula":FORMULA,"types":list(TYPES),"checklist_version":"hw1-fixed-fields-v1"},
                building_id+"-hoi-manifest")
            hoi_fields={"name":fields["name"],"country":fields["country"],"city":fields["city"],
                "building_type":fields["building_type"],"floor_count":fields["floor_count"],
                "floor_area":fields["floor_area"],"area_unit":fields["area_unit"]}
            hoi_source_id="building-hoi-profile-source-"+uuid4().hex
            await repo.insert("sources","hoi_item",[building_id],{
                "original":data["original"],"text":json.dumps({"fields":hoi_fields},sort_keys=True),
                "fields":hoi_fields,"content_sha256":sha(hoi_fields),"version":1,
                "information_type":"building_profile","scope_verified":True},hoi_source_id)
            await repo.insert("records","hoi_item",[building_id],{
                "item_type":"profile","slot":0,"fields":hoi_fields,
                "source_supported":list(hoi_fields),"accepted":True,"source_id":hoi_source_id},
                building_id+"-hoi-profile-0")
            # Portfolio membership is a scoped site membership; it does not edit user
            # assignments or grant access to sites outside this trusted portfolio.
            members=sorted(old_members|{building_id})
            await repo.update("records",portfolio,{**portfolio["data"],
                "building_ids":members,"version":int(portfolio["data"].get("version",1))+1})
            # The area adapter is derived from the same retained source and does not
            # fabricate a meter or any readings.
            energy_fields={"floor_area_m2":fields["floor_area"],"area_unit":"m2",
                "building_type":fields["building_type"]}
            energy_source_id="building-energy-profile-source-"+uuid4().hex
            energy_source_fields={**energy_fields,"source_id":energy_source_id,
                "fixture_version":"energy-fixtures-v1"}
            energy_hash=hashlib.sha256(json.dumps(energy_source_fields,sort_keys=True,
                separators=(",",":"),default=str).encode()).hexdigest()
            await repo.insert("sources","energy_profile",[building_id],{
                "original":data["original"],"fields":energy_source_fields,
                "content_sha256":energy_hash,"version":1,
                "information_type":"energy_profile","scope_verified":True},energy_source_id)
            await repo.insert("records","energy_profile",[building_id],{
                **energy_source_fields,"source_id":energy_source_id},
                building_id+"-energy-profile-1")
            score=await building_score(repo,building_id)
            await repo.insert("history","hoi",[building_id],{
                "event_id":"building-onboarding-"+proposal_id,"building_id":building_id,
                "before":None,"after":score["score"],"score":score["score"],
                "formula_version":FORMULA,"actor":principal.user_id,"reason":reason.strip(),
                "time":datetime.now(timezone.utc).isoformat()},"hoi-building-onboarding-"+proposal_id)
            await repo.insert("history","building_onboarding",[building_id],{
                "portfolio_id":principal.portfolio_id,"building_id":building_id,
                "source_id":source_id,"actor":principal.user_id,"role":principal.role,
                "reason":reason.strip(),"generated_at":datetime.now(timezone.utc).isoformat()},
                "building-onboarding-history-"+proposal_id)
            from ..compliance_hw1.outbox import notify_managers
            notifications=await notify_managers(repo,building_id,"building-onboarding-"+proposal_id,
                {"event_id":"building-onboarding-"+proposal_id,"before":None,"after":score["score"],
                    "actor":principal.user_id,"source_id":source_id,"reason":reason.strip(),"formula_version":FORMULA},"hoi")
            result={"building_id":building_id,"portfolio_id":principal.portfolio_id,
                "source_id":source_id,"version":1,"hoi_score":score["score"],
                "notifications":notifications,"outcome":"created"}
            await repo.update("proposals",proposal,{**data,"status":"confirmed","reason":reason.strip(),
                "result":result})
            await repo.audit("building_onboarding_confirm","succeeded",{
                "proposal_id":proposal_id,"building_id":building_id,"portfolio_id":principal.portfolio_id},request_id)
            return {**result,"source_immutable":True}

    async def preview(self, principal, building_id, kind, original, fields):
        await self._authorize(principal,building_id)
        try: fields=validated(kind,fields)
        except ValueError: raise HTTPException(422,"Invalid typed source fields") from None
        if not isinstance(original,str) or not original.strip() or len(original.encode())>1_000_000:
            raise HTTPException(422,"Original source content is required and size limited")
        digest=sha({"original":original,"kind":kind})
        async with self.store.transaction(principal) as repo:
            await lock(repo,building_id+":"+digest)
            sources=await repo.list("sources")
            # A shared file is independently associated per building; an organization-wide
            # content hash alone must never suppress a second authorized association.
            duplicate=next((s for s in sources if s["buildings"]==[building_id]
                            and s["data"].get("incoming_sha256")==digest),None)
            conflicts=[]
            key=identity(kind,fields)
            if key:
                for row in await current_rows(repo,kind,building_id,key):
                    if (row["buildings"]==[building_id] and row["data"].get("adapter_key")==key
                            and not row["data"].get("superseded")):
                        conflicts.append({"record_id":row["id"],"version":row["data"].get("version",1),"fields":row["data"].get("fields",{})})
            if duplicate and any(duplicate["data"].get("fields",{}).get(k)!=v for k,v in fields.items()):
                raise HTTPException(409,"Same retained original has different extraction; use controlled correction")
            source_id=duplicate["id"] if duplicate else "incoming-source-"+str(uuid4())
            if not duplicate:
                source_fields={**fields,"source_id":source_id,"fixture_version":"ingestion-v1"}
                await repo.insert("sources",kind,[building_id],{"original":original,"fields":source_fields,
                    "content_sha256":sha(source_fields),"incoming_sha256":digest,"version":1,
                    "information_type":kind,"scope_verified":True},source_id)
            proposal_id=str(uuid4())
            preview={"building_id":building_id,"kind":kind,"source_sha256":digest,"duplicate_source_id":duplicate["id"] if duplicate else None,
                "source_id":source_id,"conflicts":conflicts,"proposed_fields":fields,
                "action":"conflict_review" if conflicts else "duplicate" if duplicate else "append_new"}
            await repo.insert("proposals","ingestion",[building_id],{"actor":principal.user_id,"role":principal.role,
                "preview":preview,"preview_hash":sha(preview),"original":original,"fields":fields,"status":"previewed"},proposal_id)
            await repo.audit("ingestion_preview","succeeded",{"proposal_id":proposal_id,"building_id":building_id,"kind":kind})
            if conflicts:
                from .followthrough import review_notice
                await review_notice(repo,building_id,"ingestion-review-"+proposal_id,proposal_id)
            return {"proposal_id":proposal_id,"preview":preview,"preview_hash":sha(preview)}

    async def confirm(self,principal,proposal_id,expected_hash,reason,request_id=None):
        if not isinstance(principal,TestPrincipal) or principal.role not in MANAGERS:
            if isinstance(principal,TestPrincipal):
                await self.store.audit(principal,"ingestion_confirm","capability_denied",{"proposal_id":proposal_id},request_id)
            raise HTTPException(403,"Ingestion is not available to this role")
        async with self.store.transaction(principal) as repo:
            proposal=await repo.get("proposals",proposal_id,lock=True)
            await self._authorize(principal,proposal["buildings"][0],request_id)
            data=proposal["data"]; preview=data["preview"]
            if proposal["kind"]!="ingestion" or preview.get("action")=="derived_correction":
                raise HTTPException(409,"Wrong confirmation workflow")
            if data.get("status")=="confirmed":
                if expected_hash==data.get("preview_hash") and reason.strip()==data.get("decision_reason",reason.strip()):
                    return {"outcome":"already_accepted",**data["result"],"source_immutable":True}
                raise HTTPException(409,"Changed ingestion confirmation")
            if data.get("status")!="previewed" or expected_hash!=sha(preview):
                raise HTTPException(409,"Stale or changed ingestion preview")
            if not isinstance(reason,str) or len(reason.strip())<8:
                raise HTTPException(422,"A specific confirmation reason is required")
            building=proposal["buildings"][0]
            kind=preview["kind"]
            await lock(repo,building+":"+kind+":"+identity(kind,data["fields"]))
            sources=await repo.list("sources")
            duplicate=next((s for s in sources if s["buildings"]==[building]
                            and s["data"].get("incoming_sha256")==preview["source_sha256"]),None)
            if duplicate:
                same=next((r for r in await repo.list("records",kind)
                           if r["buildings"]==[building] and r["data"].get("source_id")==duplicate["id"]),None)
            else: same=None
            if same:
                result={"source_id":duplicate["id"],"record_id":same["id"] if same else None,
                        "version":same["data"].get("version",1) if same else duplicate["data"].get("version",1),
                        "outcome":"already_ingested"}
                await repo.update("proposals",proposal,{**data,"status":"confirmed","decision_reason":reason.strip(),"result":result})
                await repo.audit("ingestion_confirm","already_ingested",{"proposal_id":proposal_id,"source_id":duplicate["id"]},request_id)
                return {**result,"source_immutable":True}
            current=await current_rows(repo,kind,building,identity(kind,data["fields"]))
            if not preview["conflicts"] and current and any(r["data"].get("fields")!=data["fields"] for r in current):
                raise HTTPException(409,"Another source was accepted since preview; review a fresh conflict")
            existing=[{"record_id":r["id"],"version":r["data"].get("version",1),"fields":r["data"].get("fields",{})} for r in current]
            if existing:
                # Identical derived values are an unchanged no-op. Differing values retain
                # the accepted record until a separate controlled correction is reviewed.
                identical=next((x for x in existing if x.get("fields")==data["fields"]),None)
                if identical:
                    retained=await repo.get("records",identical["record_id"])
                    result={"source_id":retained["data"].get("source_id"),"incoming_source_id":preview["source_id"],"record_id":identical["record_id"],
                            "version":identical["version"],"outcome":"unchanged"}
                    await repo.update("proposals",proposal,{**data,"status":"confirmed","decision_reason":reason.strip(),"result":result})
                    await repo.audit("ingestion_confirm","unchanged",{"proposal_id":proposal_id,"record_id":identical["record_id"]},request_id)
                    return {**result,"source_immutable":True}
                # Conflicts are preserved for reviewer selection; no implicit overwrite.
                raise HTTPException(409,"Conflicting source requires a separate version-selection review")
            source_id=preview["source_id"]; record_id="ingest-record-"+str(uuid4())
            source_fields={**data["fields"],"source_id":source_id,"fixture_version":"ingestion-v1"}
            rowdata={**source_fields,"fields":dict(data["fields"]),"source_id":source_id,"version":1,
                     "adapter_key":identity(kind,data["fields"]),
                     "scope_verified":True,"accepted":True}
            await repo.insert("records",kind,[building],rowdata,record_id)
            await project(repo,{"id":record_id,"kind":kind,"org":principal.organization_id,"buildings":[building],"data":rowdata})
            from .followthrough import changed
            follow=await changed(repo,principal,building,"ingestion-"+proposal_id,source_id,record_id,reason)
            await repo.insert("history","ingestion",[building],{"source_id":source_id,"record_id":record_id,
                "version":1,"actor":principal.user_id,"role":principal.role,"reason":reason,"outcome":"accepted",
                "generated_at":datetime.now(timezone.utc).isoformat()})
            await repo.update("proposals",proposal,{**data,"status":"confirmed","decision_reason":reason.strip(),"result":{"source_id":source_id,"record_id":record_id,"version":1}})
            await repo.audit("ingestion_confirm","succeeded",{"proposal_id":proposal_id,"source_id":source_id,"record_id":record_id},request_id)
            return {"outcome":"accepted","source_id":source_id,"record_id":record_id,"version":1,"source_immutable":True,"hoi_follow_through":follow}

    async def resolve_conflict(self,principal,proposal_id,expected_hash,decision,reason,request_id=None):
        """Record a reviewer choice without overwriting or discarding either source."""
        if not isinstance(principal,TestPrincipal) or principal.role not in MANAGERS:
            raise HTTPException(403,"Ingestion conflict review is unavailable to this role")
        if decision not in {"keep_current","accept_incoming"}:
            raise HTTPException(422,"Choose keep_current or accept_incoming")
        if not isinstance(reason,str) or len(reason.strip())<8:
            raise HTTPException(422,"A specific conflict decision reason is required")
        async with self.store.transaction(principal) as repo:
            proposal=await repo.get("proposals",proposal_id,lock=True)
            building=proposal["buildings"][0]
            await self._authorize(principal,building,request_id)
            data=proposal["data"];preview=data.get("preview",{})
            if data.get("status")=="conflict_resolved":
                if (expected_hash==data.get("preview_hash") and data.get("decision")==decision
                        and reason.strip()==data["result"]["reason"]):
                    return {**data["result"],"outcome":"already_resolved"}
                raise HTTPException(409,"Conflict was already resolved with another decision")
            if data.get("status")!="previewed" or expected_hash!=sha(preview):
                raise HTTPException(409,"Stale or changed conflict preview")
            conflicts=preview.get("conflicts") or []
            if preview.get("action")!="conflict_review" or len(conflicts)!=1:
                raise HTTPException(409,"A fresh preview with exactly one matching record is required")
            conflict=conflicts[0]
            await lock(repo,building+":"+preview["kind"]+":"+identity(preview["kind"],data["fields"]))
            old=await repo.get("records",conflict["record_id"],lock=True)
            if (old["buildings"]!=[building] or old["kind"]!=preview.get("kind")
                    or old["data"].get("version",1)!=conflict.get("version")
                    or old["data"].get("fields",{})!=conflict.get("fields",{})
                    or old["data"].get("superseded")):
                raise HTTPException(409,"Conflicting record changed since preview; prepare a fresh preview")
            current=await current_rows(repo,old["kind"],building,old["data"].get("adapter_key"))
            if len(current)!=1 or current[0]["id"]!=old["id"]:
                raise HTTPException(409,"Current version changed; prepare a fresh conflict preview")

            source_id=preview["source_id"]
            version=int(old["data"].get("version",1))+(1 if decision=="accept_incoming" else 0)
            source_fields={**data["fields"],"source_id":source_id,"fixture_version":"ingestion-v1"}

            result={"decision":decision,"source_id":source_id,"previous_record_id":old["id"],
                    "previous_source_id":old["data"].get("source_id"),"version":version,
                    "reason":reason.strip()}
            if decision=="accept_incoming":
                new_id="conflict-record-"+str(uuid4())
                fields=data["fields"]
                new_data={**source_fields,"fields":fields,"source_id":source_id,"version":version,
                    "adapter_key":old["data"].get("adapter_key"),"accepted":True,"scope_verified":True,
                    "original_source_id":old["data"].get("original_source_id",old["data"].get("source_id")),
                    "supersedes_record_id":old["id"]}
                if old["kind"]!="work_order_report":
                    await repo.update("records",old,{**old["data"],"superseded":True,"superseded_by":new_id})
                await repo.insert("records",preview["kind"],[building],new_data,new_id)
                await project(repo,{"id":new_id,"kind":old["kind"],"org":principal.organization_id,"buildings":[building],"data":new_data},previous=old)
                result.update(outcome="accepted_as_new_version",record_id=new_id)
                from .followthrough import changed
                result["hoi_follow_through"]=await changed(repo,principal,building,"ingestion-conflict-hoi-"+proposal_id,source_id,new_id,reason)
                from ..compliance_hw1.outbox import notify_managers
                result["manager_notifications"]=await notify_managers(
                    repo,building,"ingestion-conflict-"+proposal_id,
                    "A reviewed source conflict was resolved as a new version; prior evidence remains retained.",
                    "ingestion")
            else:
                result.update(outcome="kept_current",record_id=old["id"])
            history_id="ingestion-conflict-history-"+proposal_id
            await repo.insert("history","ingestion_conflict_resolution",[building],{
                "proposal_id":proposal_id,"actor":principal.user_id,"role":principal.role,
                "decision":decision,"reason":reason.strip(),"source_id":source_id,
                "previous_source_id":old["data"].get("source_id"),"previous_record_id":old["id"],
                "new_record_id":result.get("record_id"),"version":version,
                "generated_at":datetime.now(timezone.utc).isoformat()},history_id)
            await repo.update("proposals",proposal,{**data,"status":"conflict_resolved",
                "decision":decision,"result":result})
            await repo.audit("ingestion_conflict_resolve",decision,{
                "proposal_id":proposal_id,"record_id":result["record_id"],"source_id":source_id},request_id)
            return {"outcome":result["outcome"],**result,"source_immutable":True}

    async def preview_correction(self,principal,building_id,record_id,original,fields,reason="Evidence-backed derived correction proposed for review"):
        if not isinstance(principal,TestPrincipal) or principal.role not in MANAGERS|{"compliance_officer"}:
            raise HTTPException(403,"Correction capability denied")
        from .certificates import prepare_certificate
        certificate=await prepare_certificate(self,principal,building_id,record_id,original,fields,reason)
        if certificate is not None: return certificate
        await self._authorize(principal,building_id)
        if not isinstance(fields,dict) or not fields:
            raise HTTPException(422,"At least one derived field correction is required")
        async with self.store.transaction(principal,repeatable=True) as repo:
            current=await repo.get("records",record_id)
            if current["buildings"]!=[building_id] or current["kind"] not in KINDS|{"hoi_item"}:
                raise HTTPException(404,"Correctable derived record unavailable")
            before=current["data"].get("fields",{})
            if not isinstance(before,dict):
                raise HTTPException(409,"Correctable derived fields are unavailable")
            if current["kind"]=="hoi_item":
                from ..compliance_hw1.hoi import TYPES
                item_type=current["data"].get("item_type")
                schema=TYPES.get(item_type)
                slot=current["data"].get("slot")
                if (not schema or type(slot) is not int or slot<0 or slot>=schema[0]
                        or set(fields)!=set(schema[1])):
                    raise HTTPException(422,"HOI correction must provide the complete fixed item schema")
            elif set(fields)-set(before):
                raise HTTPException(422,"Only existing derived fields can be corrected")
            proposed={**before,**fields}
            if current["kind"] in KINDS:
                try: proposed=validated(current["kind"],proposed)
                except ValueError: raise HTTPException(422,"Correction fields fail the typed adapter") from None
                if identity(current["kind"],proposed)!=identity(current["kind"],before):
                    raise HTTPException(422,"Correction cannot change record identity or association")
            if proposed==before:
                raise HTTPException(422,"Correction does not change the derived value")
            if not isinstance(original,str) or not original.strip() or len(original.encode())>1_000_000:
                raise HTTPException(422,"Correction evidence is required")
            preview={"action":"derived_correction","building_id":building_id,"record_id":record_id,
                "reason":reason,
                "kind":current["kind"],"previous_source_id":current["data"].get("source_id"),
                "previous_fields":before,"proposed_fields":proposed,
                "hoi_item":current["kind"]=="hoi_item",
                "evidence_sha256":sha({"original":original,"fields":fields})}
            proposal_id=str(uuid4())
            source_id="correction-draft-"+str(uuid4())
            source_fields={**proposed,"source_id":source_id,"fixture_version":"ingestion-v1"}
            await repo.insert("sources",current["kind"],[building_id],{"original":original,
                "fields":source_fields,"content_sha256":sha(source_fields),"version":1,
                "information_type":current["kind"],"scope_verified":True},source_id)
            preview["source_id"]=source_id
            await repo.insert("proposals","ingestion",[building_id],{"actor":principal.user_id,
                "role":principal.role,"preview":preview,"preview_hash":sha(preview),"original":original,
                "fields":proposed,"changed_fields":fields,"status":"previewed"},proposal_id)
            await repo.audit("derived_correction_preview","succeeded",{"proposal_id":proposal_id,"record_id":record_id})
            from .followthrough import review_notice
            await review_notice(repo,building_id,"correction-review-"+proposal_id,proposal_id)
            return {"proposal_id":proposal_id,"preview":preview,"preview_hash":sha(preview)}

    async def confirm_correction(self,principal,proposal_id,expected_hash,reason,request_id=None):
        if not isinstance(principal,TestPrincipal) or principal.role not in MANAGERS|{"compliance_officer"}:
            raise HTTPException(403,"Derived corrections are unavailable to this role")
        from .certificates import confirm_certificate
        certificate=await confirm_certificate(self,principal,proposal_id,expected_hash,reason)
        if certificate is not None: return certificate
        async with self.store.transaction(principal) as repo:
            proposal=await repo.get("proposals",proposal_id,lock=True)
            await self._authorize(principal,proposal["buildings"][0],request_id)
            data=proposal["data"]; preview=data["preview"]
            if preview.get("action")!="derived_correction": raise HTTPException(409,"Wrong correction workflow")
            if data.get("status")=="confirmed":
                if expected_hash==data.get("preview_hash") and reason.strip()==data.get("decision_reason",reason.strip()):
                    return {"outcome":"already_applied",**data["result"]}
                raise HTTPException(409,"Changed correction confirmation")
            if data.get("status")!="previewed" or expected_hash!=sha(preview):
                raise HTTPException(409,"Stale or changed correction preview")
            if not isinstance(reason,str) or len(reason.strip())<8:
                raise HTTPException(422,"A specific correction reason is required")
            key=identity(preview["kind"],preview["previous_fields"]) if preview["kind"] in KINDS else preview["record_id"]
            await lock(repo,preview["building_id"]+":"+preview["kind"]+":"+key)
            old=await repo.get("records",preview["record_id"],lock=True)
            if old["buildings"]!=[preview["building_id"]] or old["data"].get("fields")!=preview["previous_fields"] or old["data"].get("superseded"):
                raise HTTPException(409,"Derived record changed since preview")
            if old["kind"] in KINDS:
                current=await current_rows(repo,old["kind"],preview["building_id"],old["data"].get("adapter_key"))
                if len(current)!=1 or current[0]["id"]!=old["id"]:
                    raise HTTPException(409,"Current representation changed since correction preview")
            source_id=preview["source_id"]; new_id="corrected-record-"+str(uuid4())
            before_score=None
            if old["kind"]=="hoi_item":
                from ..compliance_hw1.hoi import building_score
                before_score=await building_score(repo,preview["building_id"])
            version=int(old["data"].get("version",1))+1
            fields=data["fields"]
            source_fields={**fields,"source_id":source_id,"fixture_version":"ingestion-v1"}
            corrected={**old["data"],**source_fields,"fields":fields,"source_id":source_id,
                "original_source_id":old["data"].get("original_source_id",old["data"].get("source_id")),
                "version":version,"superseded":False,"supersedes_record_id":old["id"],"accepted":True}
            if old["kind"]=="hoi_item":
                corrected["source_supported"]=list(fields)
            if old["kind"]!="work_order_report":
                await repo.update("records",old,{**old["data"],"superseded":True,"superseded_by":new_id})
            await repo.insert("records",old["kind"],[preview["building_id"]],corrected,new_id)
            await project(repo,{"id":new_id,"kind":old["kind"],"org":principal.organization_id,
                "buildings":old["buildings"],"data":corrected},previous=old)
            result={"record_id":new_id,"previous_record_id":old["id"],"source_id":source_id,"version":version}
            await repo.insert("history","ingestion_correction",[preview["building_id"]],{
                "actor":principal.user_id,"role":principal.role,"reason":reason.strip(),"before":preview["previous_fields"],
                "proposed":preview["proposed_fields"],"applied":fields,"old_record_id":old["id"],
                "new_record_id":new_id,"old_source_id":old["data"].get("source_id"),"new_source_id":source_id,
                "outcome":"applied","generated_at":datetime.now(timezone.utc).isoformat()},"correction-history-"+proposal_id)
            from ..compliance_hw1.outbox import notify_managers
            if old["kind"]=="hoi_item":
                result["manager_notifications"]=[]
                after_score=await building_score(repo,preview["building_id"])
                event_id="hoi-derived-correction-"+proposal_id
                change={"event_id":event_id,"building_id":preview["building_id"],
                    "before":before_score["score"],"after":after_score["score"],
                    "score":after_score["score"],"formula_version":after_score["formula_version"],
                    "actor":principal.user_id,"reason":reason.strip(),
                    "before_values":preview["previous_fields"],"after_values":fields,
                    "source_id":source_id,"record_id":new_id,"time":datetime.now(timezone.utc).isoformat()}
                await repo.insert("history","hoi",[preview["building_id"]],change,"hoi-"+event_id)
                result["hoi_score"]={"before":before_score["score"],"after":after_score["score"],
                    "formula_version":after_score["formula_version"]}
                result["hoi_notifications"]=await notify_managers(repo,preview["building_id"],event_id,
                    change,"hoi")
            else:
                from .followthrough import changed
                result["hoi_follow_through"]=await changed(repo,principal,preview["building_id"],"correction-hoi-"+proposal_id,source_id,new_id,reason)
                result["manager_notifications"]=await notify_managers(repo,preview["building_id"],"correction-"+proposal_id,
                    "An authorized derived record correction was applied; review its retained before/after history.","correction")
            await repo.update("proposals",proposal,{**data,"status":"confirmed","decision_reason":reason.strip(),"result":result})
            await repo.audit("derived_correction_confirm","succeeded",{"proposal_id":proposal_id,"record_id":new_id},request_id)
            return {"outcome":"applied",**result,"source_immutable":True}

    async def execute(self,principal,tool,raw,request_id=None):
        from .contracts import Intake,Correction,Building,Search,RecordQuery,SourceQuery
        from .index import search,authorized
        if not isinstance(principal,TestPrincipal) or tool not in ROLE_TOOLS.get(principal.role,[]):
            raise HTTPException(403,"Ingestion capability denied")
        models={"ingestion_preview":Intake,"ingestion_correction_preview":Correction,
            "ingestion_building_preview":Building,"ingestion_search":Search,"ingestion_get_record":RecordQuery,
            "ingestion_get_source":SourceQuery}
        try: query=models[tool].model_validate(raw)
        except ValueError: raise HTTPException(422,"Invalid declared ingestion arguments") from None
        if tool=="ingestion_preview": result=await self.preview(principal,**query.model_dump())
        elif tool=="ingestion_correction_preview": result=await self.preview_correction(principal,**query.model_dump())
        elif tool=="ingestion_building_preview": result=await self.preview_building(principal,**query.model_dump())
        elif tool=="ingestion_search": result=await search(self.store,principal,query)
        else:
            async with self.store.transaction(principal) as repo:
                if tool=="ingestion_get_record":
                    kinds=["certificate"] if principal.role=="compliance_officer" else sorted(KINDS|{"certificate","hoi_item","building"})
                    row=(await repo.scoped("records",principal.building_ids,kinds,query.record_id))[0]
                    if not authorized(principal,row): raise HTTPException(404,"Record unavailable")
                    if row["kind"] not in KINDS|{"certificate","hoi_item","building"}: raise HTTPException(404,"Typed record unavailable")
                    result={"record_id":row["id"],"kind":row["kind"],"building_ids":row["buildings"],"data":row["data"]}
                    if principal.role=="compliance_officer":
                        from ..compliance_hw1.contracts import CertificateValues
                        permitted=set(CertificateValues.model_fields)|{"source_id","version","assessment","assessment_reason","accepted","draft","holder_name"}
                        result["data"]={k:v for k,v in row["data"].items() if k in permitted}
                else:
                    kinds=["certificate"] if principal.role=="compliance_officer" else sorted(KINDS|{
                        "certificate","hoi_item","fixture","building_profile","energy_profile","energy_source","contract",
                        "vendor_block_history","vendor_accreditation","external_work_order_report","external_ppm_report","supplier_invoice"})
                    source=(await repo.scoped("sources",principal.building_ids,kinds,query.source_id))[0]
                    if (source["org"]!=principal.organization_id or not source["buildings"]
                            or not set(source["buildings"])<=set(principal.building_ids)
                            or not source["data"].get("scope_verified") or source["data"].get("sensitive")):
                        raise HTTPException(404,"Source unavailable")
                    data=source["data"]
                    original=data.get("original",data.get("text"))
                    if principal.role=="compliance_officer":
                        if data.get("information_type")!="certificate": raise HTTPException(404,"Source unavailable")
                        try:
                            if set(json.loads(original))!={"fields"}: raise ValueError
                            from ..compliance_hw1.actions import evidence_fields
                            if evidence_fields(original)!=data.get("fields"): raise ValueError
                        except (TypeError,ValueError): raise HTTPException(404,"Certificate evidence cannot be safely separated") from None
                    result={"source_id":source["id"],"version":data.get("version"),"original":original,
                        "fields":data.get("fields"),"immutable":True}
                await repo.audit(tool,"succeeded",{"record_id":getattr(query,"record_id",None),
                    "source_id":getattr(query,"source_id",None)},request_id)
        return {"tool":tool,"result":result,"policy_version":"hw1-ingestion-v1"}

    async def _authorize(self,principal,building,request_id=None):
        if not isinstance(principal,TestPrincipal) or principal.role not in MANAGERS:
            if isinstance(principal,TestPrincipal):
                await self.store.audit(principal,"ingestion_preview","capability_denied",{"building_id":building},request_id)
            raise HTTPException(403,"Ingestion is not available to this role")
        if building not in principal.building_ids:
            await self.store.audit(principal,"ingestion_preview","scope_denied",{"building_id":building},request_id)
            raise HTTPException(403,"Building access denied")
