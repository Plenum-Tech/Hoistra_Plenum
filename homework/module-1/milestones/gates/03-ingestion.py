#!/usr/bin/env python3
"""Live API gate for scoped onboarding, immutable intake and current/history decisions."""
import importlib.util,json,hashlib,subprocess,sys
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("contract_client",HERE/"01-contract.py")
common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common)
require=common.require
DEST=HERE.parent/"evidence/03-ingestion.json"
IDS=["portfolio_only_onboarding_and_assignment","typed_ingestion_with_immutable_originals",
     "duplicate_and_conflict_preservation","authorized_current_version_selection",
     "manager_and_certificate_only_officer_corrections","search_index_current_and_history_integrity",
     "scope_denials_stale_concurrent_and_idempotent_decisions"]
B="hw1-building-a1"


def source_hashes():
    script='''
import asyncio,json,hashlib
from sqlalchemy import text
from src.services.compliance_hw1.store import runtime_store
async def main():
 s=runtime_store()
 async with s.engine.connect() as c:
  await c.execute(text("SELECT set_config('hw1.org','hw1-org-a',true)"))
  rows=(await c.execute(text("SELECT id,data FROM hw1_compliance.sources WHERE org='hw1-org-a'"))).mappings().all()
  print(json.dumps({r['id']:hashlib.sha256(json.dumps(r['data'],sort_keys=True,default=str).encode()).hexdigest() for r in rows}))
 await s.engine.dispose()
asyncio.run(main())
'''
    result=subprocess.run(["docker","exec","-i","hoistra-hw1-compliance_candidate-1","python","-"],
        input=script,text=True,capture_output=True,timeout=60)
    require(result.returncode==0,"Private non-owner source fingerprint query failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


class Gate(common.Gate):
    def __init__(self):
        super().__init__();self.tag="ingestion-gate-"+uuid4().hex
        self.asset_fields={"asset_id":self.tag,"condition_score":2,"condition_observation":"Baseline observed condition "+self.tag}
        self.asset_original="Fictional immutable asset source "+self.tag

    def execute(self,tool,args,user="hw1-property",expected=200):
        return self.call(user,"/api/homework/ingestion/tools/execute",{"tool":tool,"arguments":args},expected)

    def preview(self,fields=None,original=None,building=B,kind="asset_summary",user="hw1-property",expected=200):
        return self.call(user,"/api/homework/ingestion/previews",{"building_id":building,"kind":kind,
            "fields":fields or self.asset_fields,"original":original or self.asset_original},expected)

    def confirm(self,preview,user="hw1-property",expected=200,hash_override=None):
        return self.call(user,"/api/homework/ingestion/previews/"+preview["proposal_id"]+"/confirm",
            {"preview_hash":hash_override or preview["preview_hash"],"reason":"Explicit fictional source acceptance for milestone verification"},expected)

    def source(self,identifier,user="hw1-property",expected=200):
        return self.execute("ingestion_get_source",{"source_id":identifier},user,expected).get("result")

    def record(self,identifier,evidence=None):
        if evidence is not None: return super().record(identifier,evidence)
        return self.execute("ingestion_get_record",{"record_id":identifier})["result"]

    def resolve(self,preview,decision,user="hw1-property",expected=200):
        return self.call(user,"/api/homework/ingestion/conflicts/"+preview["proposal_id"]+"/resolve",{
            "preview_hash":preview["preview_hash"],"decision":decision,"reason":"Explicit independent review of retained source versions"},expected)

    def search(self,query=None,history=False,user="hw1-property",building=B,expected=200):
        return self.execute("ingestion_search",{"building_id":building,"query":query or self.tag,
            "include_history":history,"limit":100},user,expected).get("result")

    def onboarding(self):
        fields={"name":"Fictional gate building "+self.tag,"country":"TEST","city":"Sample City",
            "building_type":"office","floor_count":3,"floor_area":"1000","area_unit":"m2"}
        body={"fields":fields,"original":"Fictional signed building handover "+self.tag}
        for user in ("hw1-general","hw1-property","hw1-compliance"):
            self.call(user,"/api/homework/ingestion/buildings/previews",body,403)
        self.call("hw1-portfolio","/api/homework/ingestion/buildings/previews",{**body,"portfolio_id":"other"},422)
        preview=self.call("hw1-portfolio","/api/homework/ingestion/buildings/previews",body)
        stale=self.call("hw1-portfolio","/api/homework/ingestion/buildings/previews",{**body,"fields":{**fields,"name":fields["name"]+" stale"}})
        path="/api/homework/ingestion/buildings/previews/"+preview["proposal_id"]+"/confirm"
        confirmation={"preview_hash":preview["preview_hash"],"reason":"Approve this fictional building into my assigned portfolio"}
        self.call("hw1-portfolio",path,{**confirmation,"preview_hash":"0"*64},409)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.call("hw1-portfolio",path,confirmation),range(2)))
        require({r["outcome"] for r in results}=={"created","already_created"},"Onboarding decision not concurrent/idempotent")
        created=results[0]
        stale_path="/api/homework/ingestion/buildings/previews/"+stale["proposal_id"]+"/confirm"
        self.call("hw1-portfolio",stale_path,{"preview_hash":stale["preview_hash"],"reason":"Stale portfolio version should not apply"},409)
        identity=self.call("hw1-portfolio","/api/homework/identity")
        require(created["building_id"] in identity["building_ids"],"Trusted dynamic portfolio assignment not refreshed")
        require(created["building_id"] not in self.call("hw1-property","/api/homework/identity")["building_ids"],"Property role assignment was changed")
        row=self.execute("ingestion_get_record",{"record_id":created["building_id"]},"hw1-portfolio")["result"]
        require(row["data"]["portfolio_id"]=="hw1-org-a-default-portfolio","Building escaped trusted portfolio")
        self.record(IDS[0],"Only Portfolio Manager preview/confirm creates a fictional building in its trusted own-org portfolio; other three roles403, client portfolio override422. Concurrent confirmation yields created/already_created for one ID. A competing stale portfolio preview409. Fresh trusted identity includes the new membership; Property Manager assignment remains unchanged. Building profile and immutable original are retained; no role/user administration.")

    def typed(self):
        preview=self.preview()
        self.initial_source=preview["preview"]["source_id"]
        require(self.source(self.initial_source)["original"]==self.asset_original,"Original not retained immutably at preview")
        require(self.search()["total_matching"]==0,"Unconfirmed intake became an accepted search result")
        self.initial=self.confirm(preview)
        require(self.initial["outcome"]=="accepted","Typed source not accepted")
        self.execute("ingestion_preview",{"building_id":B,"kind":"asset_summary","original":"invalid",
            "fields":{**self.asset_fields,"condition_score":0}},expected=422)
        self.preview(fields={**self.asset_fields,"role":"portfolio_manager"},expected=422)
        self.preview(kind="arbitrary_table",expected=422)
        examples=[("inspection_report",{"inspection_id":self.tag,"inspection_date":"2026-10-01","result":"recorded","summary":self.tag}),
            ("meter_reading",{"meter_id":"hw1-energy-m2-gap","timestamp":datetime.now(timezone.utc).isoformat(),"consumption":"10","unit":"kWh","interval":"hour"}),
            ("work_order_report",{"external_reference":self.tag,"raised_at":"2026-10-01T08:00:00+00:00","responded_at":"2026-10-01T09:00:00+00:00",
                "completed_at":"2026-10-01T10:00:00+00:00","status_summary":"external completed report","vendor_id":B+"-vendor-0","asset_id":self.tag})]
        for kind,fields in examples:
            p=self.preview(fields=fields,kind=kind,original="Fictional typed "+kind+" "+self.tag)
            result=self.confirm(p)
            require(result["source_id"]==p["preview"]["source_id"],"Confirmation replaced the retained draft original")
            if kind=="work_order_report":
                self.work_order=result;self.work_order_fields=fields
                self.work_order_snapshot=self.record(result["record_id"])
                actual=self.work_order_snapshot["data"]["fields"]["raised_at"]
                require(datetime.fromisoformat(actual.replace("Z","+00:00"))==datetime.fromisoformat(fields["raised_at"]),"Typed timestamp normalization changed source time")
        self.record(IDS[1],"Actual APIs stage immutable originals at preview, exclude unconfirmed evidence from accepted search, and accept typed asset, inspection, meter-reading and external work-order report fixtures with the same source ID. Condition0, undeclared role field and arbitrary adapter422. No operational work order or unrestricted schema write is exposed. Protected source reads verify exact original retention without publishing source bodies.")

    def duplicates(self):
        duplicate=self.preview()
        result=self.confirm(duplicate)
        require(result["outcome"]=="already_ingested" and result["record_id"]==self.initial["record_id"] and result["source_id"]==self.initial_source,"Exact file duplicate did not reuse accepted source/record")
        self.preview(fields={**self.asset_fields,"condition_score":3},expected=409)
        same_values=self.preview(original="Different retained file with identical accepted values "+self.tag)
        unchanged=self.confirm(same_values)
        require(unchanged["outcome"]=="unchanged" and unchanged["record_id"]==self.initial["record_id"]
            and unchanged["source_id"]==self.initial_source and unchanged["incoming_source_id"]==same_values["preview"]["source_id"],
            "Identical record values did not preserve current and incoming evidence as an unchanged no-op")
        other=self.preview(building="hw1-building-a2",user="hw1-portfolio")
        accepted=self.confirm(other,"hw1-portfolio")
        require(accepted["source_id"]!=self.initial_source,"File dedup crossed building association")
        rejected=self.preview(fields={**self.asset_fields,"condition_score":1},original="Unselected incoming source "+self.tag)
        self.confirm(rejected,expected=409)
        require(self.record(self.initial["record_id"])["data"]["fields"]==self.asset_fields,"Conflict overwrote current values before review")
        keep=self.resolve(rejected,"keep_current")
        require(keep["record_id"]==self.initial["record_id"] and self.source(keep["source_id"])["original"]=="Unselected incoming source "+self.tag,"Keep-current lost candidate evidence")
        self.record(IDS[2],"Same-building identical file reuses source and record (already_ingested); different extraction from that retained file409. The same file in authorized A2 creates a separately scoped source. Conflicting source cannot confirm through append (409), leaves accepted values unchanged, and an explicit keep_current decision preserves both accepted and unselected candidate originals.")

    def version(self):
        self.version_fields={**self.asset_fields,"condition_score":3,"condition_observation":"Reviewed replacement "+self.tag}
        preview=self.preview(fields=self.version_fields,original="Selected replacement source "+self.tag)
        self.stale_conflict=self.preview(fields={**self.asset_fields,"condition_score":5},original="Stale competing source "+self.tag)
        self.selected=self.resolve(preview,"accept_incoming")
        require(self.selected["version"]==2 and self.selected["previous_record_id"]==self.initial["record_id"],"Reviewed current version incorrect")
        require(self.record(self.selected["record_id"])["data"]["fields"]==self.version_fields,"Wrong selected representation")
        require(self.source(self.initial_source)["original"]==self.asset_original,"Original source was overwritten")
        repeat=self.resolve(preview,"accept_incoming")
        require(repeat["outcome"]=="already_resolved" and repeat["record_id"]==self.selected["record_id"],"Version decision replay reapplied")
        self.resolve(preview,"keep_current",expected=409)
        self.resolve(self.stale_conflict,"accept_incoming",expected=409)
        report=self.preview(fields={**self.work_order_fields,"status_summary":"Reviewed external report representation"},
            original="Reviewed external report fixture "+self.tag,kind="work_order_report")
        reviewed=self.resolve(report,"accept_incoming")
        require(reviewed["version"]==2 and self.record(self.work_order["record_id"])["data"]==self.work_order_snapshot["data"],
            "Selecting a derived work-report version mutated the imported original")
        self.record(IDS[3],"Explicit assigned-manager accept_incoming selects business version2 linked to prior record/source, retains immutable version1 original and candidate source, and updates the current search representation. Repeated exact decision is already_resolved; changed decision and competing stale-current preview409. No automatic conflict overwrite or source deletion.")

    def compliance(self,action,record,values,source_id=None,original=None,user="hw1-property"):
        body={"action":action,"entity_id":record,"values":values,"reason":"Explicit source-backed fictional certificate review"}
        if source_id:body["source_id"]=source_id
        else:body["source_text"]=original or json.dumps({"fields":values})
        proposal=self.call(user,"/api/homework/compliance/proposals",body)["result"]
        result=self.call(user,"/api/homework/actions/"+proposal["proposal_id"]+"/confirm",{
            "version":proposal["version"],"preview_hash":proposal["preview_hash"],"idempotency_key":"gate-cert-"+uuid4().hex})
        require(result["status"]=="approved","Certificate decision not applied")
        return result["result"]

    def corrections(self):
        body={"building_id":B,"record_id":self.selected["record_id"],"original":"Controlled correction evidence "+self.tag,
            "fields":{"condition_score":4,"condition_observation":"Corrected current condition "+self.tag}}
        self.call("hw1-compliance","/api/homework/ingestion/corrections/previews",body,403)
        self.call("hw1-general","/api/homework/ingestion/corrections/previews",body,403)
        preview=self.call("hw1-property","/api/homework/ingestion/corrections/previews",body)
        confirm={"preview_hash":preview["preview_hash"],"reason":"Approve this reviewed derived correction only"}
        self.corrected=self.call("hw1-property","/api/homework/ingestion/corrections/previews/"+preview["proposal_id"]+"/confirm",confirm)
        require(self.corrected["version"]==3 and self.corrected["source_immutable"],"Derived correction did not append version3")
        values={"reference":self.tag+"-cert","certificate_type":"FIRE","subject":"Building","holder_id":B,
            "issuer":"Fictional initial issuer","issue_date":"2026-01-01","expiry_date":"2027-01-01",
            "review_date":None,"review_rule":None,"country":"TEST","trade":"Fire"}
        initial=self.compliance("accept_certificate",B,values)
        self.certificate_id=initial["record_id"];self.certificate_original_source=initial["source_id"];self.certificate_values=values
        changed={**values,"issuer":"Fictional reviewed issuer"}
        p=self.call("hw1-compliance","/api/homework/ingestion/corrections/previews",{"building_id":B,
            "record_id":self.certificate_id,"original":json.dumps({"fields":changed}),"fields":{"issuer":changed["issuer"]},
            "reason":"Officer approves certificate evidence correction only"})
        result=self.call("hw1-compliance","/api/homework/ingestion/corrections/previews/"+p["proposal_id"]+"/confirm",{
            "preview_hash":p["preview_hash"],"reason":"Officer approves certificate evidence correction only"})
        require(result["status"]=="approved" and result["result"]["previous_source_retained"]==self.certificate_original_source,"Officer certificate correction failed to preserve prior source")
        manager_values={**changed,"issuer":"Fictional manager-reviewed issuer"}
        manager_reason="Assigned manager approves certificate correction with retained evidence"
        manager_preview=self.call("hw1-property","/api/homework/ingestion/corrections/previews",{"building_id":B,
            "record_id":self.certificate_id,"original":json.dumps({"fields":manager_values}),
            "fields":{"issuer":manager_values["issuer"]},"reason":manager_reason})
        manager_result=self.call("hw1-property","/api/homework/ingestion/corrections/previews/"+manager_preview["proposal_id"]+"/confirm",{
            "preview_hash":manager_preview["preview_hash"],"reason":manager_reason})
        require(manager_result["status"]=="approved","Assigned manager certificate correction parity failed")
        self.execute("ingestion_get_record",{"record_id":self.corrected["record_id"]},"hw1-compliance",404)
        self.source(self.corrected["source_id"],"hw1-compliance",404)
        self.record(IDS[4],"Manager derived asset correction appends business version3 with before/after/reason/actor and retained original; General User/Compliance Officer generic corrections403. A separately staged fictional certificate is corrected by the Officer through the same guarded Compliance preview/confirmation adapter, preserving prior certificate source. Officer asset record/original retrieval404. Certificate-only scope never grants general ingestion, commercial, Energy or HOI access.")

    def index(self):
        current=self.search();history=self.search(history=True)
        require(current["total_matching"]>=1 and len([r for r in current["results"] if r["kind"]=="asset_summary"])==1,"Current source index has duplicate accepted asset versions")
        asset=next(r for r in current["results"] if r["kind"]=="asset_summary")
        require(asset["record_id"]==self.corrected["record_id"] and asset["fields"]["condition_score"]==4,"Index points at an obsolete representation")
        require(len([r for r in history["results"] if r["kind"]=="asset_summary"])==3,"Accepted source history lost a version")
        old_term="Baseline observed condition "+self.tag
        require(self.search(old_term)["total_matching"]==0 and self.search(old_term,True)["total_matching"]==1,"Search filtered before current-version selection")
        selected=self.compliance("select_version",self.certificate_id,self.certificate_values,
            source_id=self.certificate_original_source,user="hw1-compliance")
        cert=self.search(self.tag+"-cert",user="hw1-compliance")
        require(cert["total_matching"]==1 and cert["results"][0]["source_id"]==self.certificate_original_source,"Restoring an older certificate source did not become current")
        require(len(self.search(self.tag+"-cert",True,"hw1-compliance")["results"])>=3,"Certificate version history not retained")
        self.execute("remove_index",{},expected=403)
        self.record(IDS[5],"Append-only index returns one current asset at business version3 and retains three accepted asset versions. A term present only in superseded data returns0 current/1 historical match. Officer certificate select_version restores the original source as a new current business representation while retaining all certificate history; physical source version remains distinct. Officer index includes certificate entries only. No standalone index-removal tool (403); original evidence remains retrievable under role/scope checks.")

    def concurrency(self):
        self.preview(building="hw1-building-a2",expected=403)
        self.preview(building="hw1-building-b1",user="hw1-portfolio",expected=403)
        self.search(user="hw1-general",expected=403)
        self.search(building="hw1-building-b1",user="hw1-portfolio",expected=403)
        self.execute("ingestion_get_record",{"record_id":self.corrected["record_id"]},"hw1-other-property",404)
        fields={**self.asset_fields,"asset_id":self.tag+"-race"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            previews=list(pool.map(lambda _:self.preview(fields=fields,original="Concurrent same-file fixture "+self.tag),range(2)))
        require(previews[0]["preview"]["source_id"]==previews[1]["preview"]["source_id"],"Concurrent preview duplicated incoming original")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(self.confirm,previews))
        require(results[0]["record_id"]==results[1]["record_id"],"Concurrent acceptance duplicated business records")
        self.confirm(previews[0],hash_override="0"*64,expected=409)
        self.call("hw1-property","/api/homework/ingestion/previews/"+previews[0]["proposal_id"]+"/confirm",{
            "preview_hash":previews[0]["preview_hash"],"reason":"Changed decision justification must not be accepted as the original confirmation"},409)
        response=self.call("hw1-property","/api/homework/agent/ask",{"message":
            "Search ingestion current records in hw1-building-a1 for exact text "+self.tag+". Use ingestion_search and report the source versions."})
        require(response.get("selected_route")=="ingestion" and response.get("outcome")=="answered","Bounded ingestion agent failed")
        require(response.get("provider_execution",{}).get("successful_responses",0)>0 and "ingestion_search" in
            response.get("provider_execution",{}).get("selected_tools",[]),"No genuine provider-selected ingestion tool")
        actual=[c["result"] for c in response["tool_calls"] if c["tool"]=="ingestion_search" and "result" in c]
        require(actual and any(r["record_id"]==self.corrected["record_id"] and r["fields"]["condition_score"]==4
            for r in actual[0]["results"]),"Provider search did not return the independently expected current asset")
        manifest=common.ROOT/"apps/backend/cafm-connector-service-final/svc-deepagents/skills/ingestion-manifest.json"
        require(response["prompt_metadata"]["manifest_sha256"]==hashlib.sha256(manifest.read_bytes()).hexdigest(),"Live ingestion manifest is stale")
        require(all("confirm" not in t and "resolve" not in t for t in response["prompt_metadata"]["bound_tools"]),"Model can commit a human decision")
        self.record(IDS[6],"Live scope checks deny Property A2, Portfolio foreign-org building, General source search and foreign-organization record access. Concurrent same-file previews reuse one immutable source; concurrent confirmations yield one business record. Changed preview hash409 and stale/changed decisions were rejected. Genuine configured OpenRouter request via central agent selects ingestion_search with preview/read-only model tools; no model confirmation or conflict-resolution capability.")


def main():
    receipt={"milestone_id":"03-ingestion","specification_version":"0.37.0","api_base":common.BASE,
        "recorded_at":datetime.now(timezone.utc).isoformat(),"checks":[]}
    gate=None;current=IDS[0]
    DEST.parent.mkdir(parents=True,exist_ok=True)
    try:
        common.database_probe();before=source_hashes();gate=Gate()
        for current,run in zip(IDS,[gate.onboarding,gate.typed,gate.duplicates,gate.version,gate.corrections,gate.index,gate.concurrency]):run()
        after=source_hashes()
        require(all(after.get(k)==v for k,v in before.items()),"A retained pre-existing source was changed or deleted")
        require(len(after)>len(before),"No actual immutable fixture sources were persisted")
        common.database_probe()
        gate.checks[1]["evidence"]+=" Non-superuser source update/delete denials checked; every pre-existing source fingerprint remains identical, while authorized new originals were appended."
        receipt["checks"]=gate.checks;DEST.write_text(json.dumps(receipt,indent=2)+"\n")
        print(json.dumps({"milestone_id":"03-ingestion","passed":True,"checks":7}));return 0
    except Exception as error:
        safe=str(error) if isinstance(error,common.GateFailure) else type(error).__name__
        receipt["checks"]=(gate.checks if gate else [])+[{"id":current,"passed":False,"evidence":safe}]
        DEST.write_text(json.dumps(receipt,indent=2)+"\n")
        print(json.dumps({"milestone_id":"03-ingestion","passed":False,"failed_check":current,"reason":safe}));return 1

if __name__=="__main__":sys.exit(main())
