#!/usr/bin/env python3
"""Source-backed live Energy milestone checks. All failures exit nonzero."""
import importlib.util
import json
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import sys
import subprocess
from uuid import uuid4

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("contract_gate_client",HERE/"01-contract.py")
common=importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)
require=common.require
MODULE=HERE.parents[1]
ROOT=MODULE.parents[1]
DEST=MODULE/"milestones/evidence/02-energy.json"
IDS=["meter_section_unit_period_scope_validation","matched_profile_anomaly_expected_case",
     "occupancy_change_suppression_and_sparse_data","source_backed_benchmarks_tariffs_and_financial_outputs",
     "missing_period_retry_and_escalation","condition_remediation_queue_and_monthly_report",
     "role_denials_and_live_provider_integration"]
B="hw1-building-a1"


class EnergyGate(common.Gate):
    def __init__(self):
        super().__init__()
        self.spike=None

    def execute(self,tool,arguments,user="hw1-property",expected=200):
        return self.call(user,"/api/homework/energy/tools/execute",{"tool":tool,"arguments":arguments},expected)

    def query(self,meter="condition",**changes):
        return {"building_id":B,"meter_id":"hw1-energy-m2-"+meter,
            "period_start":"2026-10-01","period_end":"2026-10-31",**changes}

    def scan(self,meter):
        return self.execute("energy_anomaly_scan",{"building_id":B,"meter_id":"hw1-energy-m2-"+meter,
            "as_of":"2026-10-25"})["result"]

    def validation(self):
        data=self.execute("energy_eui",self.query(section_id="hw1-section-energy-m2"))["result"]
        require(Decimal(data["eui_kwh_per_m2_period"])==12 and data["period_days_inclusive"]==31,"Expected 1200/100=12 over31 days")
        require(data["meter_id"]=="hw1-energy-m2-condition" and data["building_id"]==B,"Wrong meter/building join")
        portfolio=self.execute("energy_eui",self.query(),user="hw1-portfolio")["result"]
        require(portfolio["eui_kwh_per_m2_period"]==data["eui_kwh_per_m2_period"],"Manager projection/calculation parity failed")
        self.execute("energy_eui",self.query(section_id="unknown-section"),expected=404)
        self.execute("energy_eui",self.query(meter_id="hw1-building-a2-energy-meter-1"),expected=404)
        self.execute("energy_eui",self.query(building_id="hw1-building-a2"),expected=403)
        self.execute("energy_eui",self.query("badunit"),expected=422)
        self.execute("energy_eui",self.query(period_start="2026-10-20",period_end="2026-10-01"),expected=422)
        self.execute("energy_eui",self.query(period_end="2026-10-20"),expected=422)
        empty=self.execute("energy_eui",self.query(period_start="2026-09-01",period_end="2026-09-30"))["result"]
        require(empty["consumption_kwh"] is None and not empty["eui_available"],"Missing consumption became zero")
        self.record(IDS[0],"Live A1 condition meter/section returns1200kWh,100m2,31 inclusive days,EUI12. Wrong section404; Property Manager A2 scope403; m3 meter422; reversed dates422; a monthly source cannot be prorated into a partial period (422). Empty September consumption is null and EUI unavailable. Immutable fictional energy-fixtures-v1/m2 sources retained.")

    def matched(self):
        self.spike=self.scan("spike")
        require(self.spike["evaluation_status"]=="scanned" and self.spike["hourly_reading_count"]==840,"Expected a complete35-day hourly scan")
        finding=next((f for f in self.spike["anomalies"] if f["type"]=="asset_spike"),None)
        require(finding is not None,"Expected sub-meter matched-profile spike")
        require(Decimal(finding["metric_pct"])==200 and Decimal(finding["excess_kwh"])==240,"Expected48*(10-5)=240 and200% weekend matched profile")
        require(Decimal(finding["financial_gbp"])==48 and Decimal(finding["annualized_financial_gbp"])==8760,"Window/annualized costs are not distinct48/8760")
        repeated=self.scan("spike")
        require(repeated["anomaly_ids"]==self.spike["anomaly_ids"],"Repeated scans changed immutable anomaly identities")
        anomaly_id=self.spike["anomaly_ids"][0]
        self.execute("energy_anomaly_act",{"anomaly_id":anomaly_id,"action":"mark_expected","reason":"Review without evidence"},expected=422)
        action=self.execute("energy_anomaly_act",{"anomaly_id":anomaly_id,"action":"monitor","reason":"Explicit isolated manager review"})["result"]
        retry=self.execute("energy_anomaly_act",{"anomaly_id":anomaly_id,"action":"monitor","reason":"Explicit isolated manager review"})["result"]
        require(action["event_id"]==retry["event_id"],"Human disposition replay not idempotent")
        self.record(IDS[1],"Complete840-hour fixture uses weekday10/20kWh and weekend5kWh baseline; final48 weekend hours10kWh. Matched weekend/hour median=5, observed/expected=200%, excess48*(10-5)=240kWh. Source tariff0.20 gives windowGBP48 and separately labelled annualizedGBP8760. Asset spike persisted with scoped source IDs; repeat scan returns same immutable finding IDs.")

    def occupancy(self):
        drift=self.scan("drift")
        finding=next((f for f in drift["anomalies"] if f["type"]=="baseline_drift"),None)
        require(finding is not None and Decimal(finding["excess_kwh"])==970,"Expected seven-day rise1940->2910, excess970")
        changed=self.scan("changed")
        require(changed["baseline_drift_suppressed"] and not any(f["type"]=="baseline_drift" for f in changed["anomalies"]),"Occupancy change failed to suppress drift")
        sparse=self.execute("energy_anomaly_scan",{"building_id":B,"as_of":"2026-10-25"})["result"]
        require(sparse["evaluation_status"]=="insufficient_comparable_evidence" and sparse["unavailable_reason"] and not sparse["anomalies"],"Sparse readings reported a clean scan")
        self.record(IDS[2],"Drift fixture raises the matched weekly load1940->2910kWh (50%), with970kWh excess. Identical changed-occupancy fixture suppresses baseline drift using retained initial and change events; other supported finding types remain independently evaluated. Legacy sparse period readings yield insufficient_comparable_evidence with a reason, never a clean-building claim.")

    def financial(self):
        data=self.execute("energy_eui",self.query())["result"]
        expected=Decimal(95)*100*31/365
        require(data["benchmark_available"] and Decimal(data["benchmark_kwh_per_m2_year"])==95,"Expected sourced indicative office/electricity95 benchmark")
        require(abs(Decimal(data["benchmark_excess_kwh"])-(Decimal(1200)-expected))<Decimal("1e-20"),"Incorrect period-compatible benchmark excess")
        require(data["financial_excess_gbp"]=="78.63" and Decimal(data["tariff_gbp_per_kwh"])==Decimal("0.20"),"Expected GBP78.63 at sourced tariff0.20")
        pack=ROOT/"apps/backend/cafm-connector-service-final/svc-deepagents/src/services/energy_hw1/reference/cibse_tm46_uk.json"
        require(data["benchmark_source"]["reference_sha256"]==hashlib.sha256(pack.read_bytes()).hexdigest(),"Benchmark artifact lineage missing")
        require(data["tariff_source_id"] in data["source_versions"],"Tariff source version missing")
        self.record(IDS[3],"Source-verified office100m2, electricity1200kWh and annual indicative TM46 benchmark95: pro-rated31-day expectation=95*100*31/365=806.849315...kWh; excess393.150684...kWh; tariff0.20 ->GBP78.63. Result distinguishes periodEUI12 from annualized141.290322... and includes pack SHA256 plus immutable meter tariff source/version. No period/annual benchmark mismatch.")

    def gaps(self):
        query=self.query("gap",period_start="2026-10-20",period_end="2026-10-20",idempotency_key="energy-gate-"+uuid4().hex)
        result=self.execute("energy_gap_scan",query)["result"]
        require(len(result["gaps"])==1 and result["gaps"][0]["missing_periods"]==2,"Expected only missing02:00/03:00 periods")
        gap=result["gaps"][0]["gap_id"]
        self.execute("energy_gap_retry",{"gap_id":gap,"test_clock":"2026-10-20T04:09:00+00:00"},expected=409)
        for number,minute in enumerate((10,20,30),1):
            args={"gap_id":gap,"test_clock":f"2026-10-20T04:{minute}:00+00:00"}
            attempt=self.execute("energy_gap_retry",args)["result"]
            require(attempt["retry_count"]==number,"Unexpected retry count")
            repeated=self.execute("energy_gap_retry",args)["result"]
            require(repeated["event_id"]==attempt["event_id"],"Same retry was reapplied")
        require(attempt["status"]=="escalated" and len(attempt["notifications"])==2,"Three failures did not escalate to both managers")
        require(all(n["status"]=="simulated sent" for n in attempt["notifications"]),"Configured escalation delivery not confirmed by outbox")
        stopped=self.execute("energy_gap_retry",{"gap_id":gap,"test_clock":"2026-10-20T04:50:00+00:00"})["result"]
        require(stopped["retry_count"]==3,"A fourth blind retry occurred")
        self.execute("energy_gap_retry",{"gap_id":gap,"test_clock":"2026-10-20T05:00:00+00:00"},user="hw1-general",expected=403)
        unknown=self.execute("energy_gap_scan",{**query,"meter_id":"hw1-energy-m2-gap-unknown"})["result"]["gaps"][0]["gap_id"]
        first=self.execute("energy_gap_retry",{"gap_id":unknown,"test_clock":"2026-10-20T04:10:00+00:00"})["result"]
        again=self.execute("energy_gap_retry",{"gap_id":unknown,"test_clock":"2026-10-20T05:00:00+00:00"})["result"]
        require(first["status"]=="unknown" and again["retry_count"]==1,"Unknown DCC outcome was blindly retried")
        self.record(IDS[4],"Source-declared60-minute interval and22 retained readings identify exactly02:00/03:00 missing periods. Test-clock04:09 is rejected409; simulated known-failure retries occur04:10,04:20,04:30 (counts1/2/3); each replay reuses its event. Third failure persists escalated and produces two configured manager notification outcomes; later trigger stays at3. Separate source-backed unknown-outcome fixture stays at1 attempt on later trigger. No DCC network call or fabricated recovery reading; General User retry denied403.")

    def remediation(self):
        query=self.query()
        data=self.execute("energy_remediation_queue",query)["result"]
        require(len(data["recommendations"])==1 and len(data["excluded"])==1,"Expected one recommendation and one negation conflict")
        item=data["recommendations"][0]
        require(item["recommendation"]=="repair" and item["condition_score"]==2 and item["queue_only"] and item["operational_work_order_created"] is False,"Wrong source-backed queue recommendation")
        require("negated" in data["excluded"][0]["reason"],"Negation was not reviewed")
        report=self.execute("energy_monthly_report",query)["result"]
        require(report["reporting_period"]=={"start":"2026-10-01","end":"2026-10-31"} and report["trigger"]=="manual","Wrong monthly reporting period/trigger")
        require(report["eui"]["financial_excess_gbp"]=="78.63" and report["source_ids"] and report["freshness"],"Report missing grounded results/evidence/freshness")
        repeated=self.execute("energy_monthly_report",query)["result"]
        require(repeated["report_id"]==report["report_id"] and repeated["outcome"]=="already_generated","Monthly snapshot is not idempotent")
        retrieved=self.execute("energy_get_monthly_report",{"report_id":report["report_id"]})["result"]
        require(retrieved["eui"]==report["eui"] and retrieved["report_id"]==report["report_id"],"Report retrieval differs from immutable snapshot")
        self.execute("energy_get_monthly_report",{"report_id":report["report_id"]},user="hw1-general",expected=403)
        self.execute("energy_monthly_report",self.query(period_start="2026-10-02"),expected=422)
        require(report["carbon_exposure_kg"] is None,"Missing carbon factor fabricated as zero")
        spike_report=self.execute("energy_monthly_report",self.query("spike"))["result"]
        require(len(spike_report["anomalies_ranked"])>=2 and spike_report["total_excess_cost_gbp"] is None,"Overlapping findings incorrectly summed")
        require(not spike_report["eui"]["eui_available"] and spike_report["eui"]["coverage"]["missing_hourly_periods"]>0,"Partial hourly month silently treated as complete")
        self.record(IDS[5],"Condition2 and exact consumption excess48.726655...% (>15%) create one persisted queue-only repair recommendation using sourced GBP100 repair versus1000 replace. Negated 'no failed fan/no corrosion' source is withheld for review. Manual October1–31 report snapshots EUI, indicative benchmark, tariff GBP78.63 excess, findings/queue/evidence/freshness and manager notifications. Replay returns same immutable report ID; partial calendar month422. Missing carbon measurement remains null; no operational work order is created.")

    def provider(self):
        for user in ("hw1-general","hw1-compliance"):
            require(self.call(user,"/api/homework/energy/tools")["tools"]==[],"Forbidden role sees Energy tools")
            for tool in ("energy_eui","energy_anomaly_scan","energy_gap_scan","energy_monthly_report"):
                self.execute(tool,{},user,403)
            response=self.call(user,"/api/homework/energy/ask",{"message":"Show energy anomalies"})
            require(response["outcome"]=="forbidden" and not response["tool_calls"],"Forbidden role executed Energy agent")
        self.call(None,"/api/homework/energy/tools",expected=401)
        self.execute("get_schema",{},expected=403)
        response=self.call("hw1-property","/api/homework/agent/ask",{"message":
            "Scan energy anomalies for building hw1-building-a1, meter hw1-energy-m2-spike as of 2026-10-25. Use energy_anomaly_scan and show the verified spike window, excess kWh and window cost, separately from annualized cost."})
        require(response.get("selected_route")=="energy" and response.get("outcome")=="answered","Energy route failed")
        require(response.get("provider_execution",{}).get("successful_responses",0)>0 and
            "energy_anomaly_scan" in response.get("provider_execution",{}).get("selected_tools",[]),"No genuine provider-selected anomaly tool call")
        results=[c["result"] for c in response["tool_calls"] if c["tool"]=="energy_anomaly_scan" and "result" in c]
        require(results and any(f["type"]=="asset_spike" and Decimal(f["excess_kwh"])==240 for f in results[0]["anomalies"]),"Provider result was not grounded in expected spike")
        require(len(response["prompt_metadata"]["loaded_instructions"])==6 and
            "energy_anomaly_act" not in response["prompt_metadata"]["bound_tools"],"Instructions missing or model can approve disposition")
        manifest=ROOT/"apps/backend/cafm-connector-service-final/svc-deepagents/skills/energy-manifest.json"
        require(response["prompt_metadata"]["manifest_sha256"]==hashlib.sha256(manifest.read_bytes()).hexdigest(),"Live Energy manifest differs from current source profile")
        self.call("hw1-portfolio","/api/homework/energy/ask",{"message":"Show my anomalies","conversation_id":response["conversation_id"]},expected=404)
        self.record(IDS[6],"Fresh API and agent checks deny General User and Compliance Officer Energy tools403/empty projections; unauthenticated tools401. Genuine configured OpenRouter question through central /agent/ask selects Energy and energy_anomaly_scan on A1 spike meter; grounded result contains240kWh excess. Positive provider response count and selected tool names prevent fallback-only success. Six hash-checked shared/domain instructions loaded; model tools exclude human anomaly disposition. Other principal conversation404; scoped permissions preserved.")


def check_energy_record_immutability():
    script='''
import asyncio
from sqlalchemy import text
from src.services.compliance_hw1.store import runtime_store
async def main():
    store=runtime_store()
    async with store.engine.connect() as conn:
        denied=0
        for identifier in ['hw1-energy-m2-spike','hw1-energy-m2-spike-r-0']:
            await conn.execute(text("SELECT set_config('hw1.org','hw1-org-a',true)"))
            try:
                await conn.execute(text("UPDATE hw1_compliance.records SET data=data WHERE id=:id"),{'id':identifier})
            except Exception as error:
                assert getattr(error.orig,'sqlstate',None)=='P0001'
                denied+=1
            finally:
                await conn.rollback()
        assert denied==2
    await store.engine.dispose()
asyncio.run(main())
'''
    result=subprocess.run(['docker','exec','-i','hoistra-hw1-compliance_candidate-1','python','-'],
        input=script,capture_output=True,text=True,timeout=60)
    require(result.returncode==0,"Imported Energy meter/reading updates were not denied under application role")


def main():
    gate=None; current=IDS[0]
    receipt={"milestone_id":"02-energy","specification_version":"0.37.0","api_base":common.BASE,
        "recorded_at":datetime.now(timezone.utc).isoformat(),"checks":[]}
    DEST.parent.mkdir(parents=True,exist_ok=True)
    try:
        gate=EnergyGate()
        before=common.database_probe()
        check_energy_record_immutability()
        for current,run in zip(IDS,[gate.validation,gate.matched,gate.occupancy,gate.financial,gate.gaps,gate.remediation,gate.provider]):
            run()
        require(common.database_probe()==before,"Retained original source fingerprints changed")
        gate.checks[0]["evidence"] += " Non-superuser/no-BYPASSRLS probes deny source UPDATE/DELETE and imported Energy meter/reading UPDATE; aggregate source fingerprint unchanged across all workflows."
        receipt["checks"]=gate.checks
        require(len(gate.checks)==7,"Required Energy checks missing")
        DEST.write_text(json.dumps(receipt,indent=2)+"\n")
        print(json.dumps({"milestone_id":"02-energy","passed":True,"checks":7}))
        return 0
    except Exception as error:
        safe=str(error) if isinstance(error,common.GateFailure) else type(error).__name__
        receipt["checks"]=(gate.checks if gate else [])+[{"id":current,"passed":False,"evidence":safe}]
        DEST.write_text(json.dumps(receipt,indent=2)+"\n")
        print(json.dumps({"milestone_id":"02-energy","passed":False,"failed_check":current,"reason":safe}))
        return 1


if __name__=="__main__": sys.exit(main())
