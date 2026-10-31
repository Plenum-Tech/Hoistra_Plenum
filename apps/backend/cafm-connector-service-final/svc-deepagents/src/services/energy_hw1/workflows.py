"""Scoped manual Energy workflows; retained sources and event history are append-only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from uuid import uuid5, NAMESPACE_URL

from fastapi import HTTPException
from sqlalchemy import text

from .contracts import EnergyQuery, GapScan, GapRetry, MonthlyReport, ReportQuery
from ..compliance_hw1.outbox import notify_managers


def identifier(*values):
    return uuid5(NAMESPACE_URL, ":".join(str(value) for value in values)).hex


async def lock(repo, key):
    # Serialize stateful retries/actions without granting arbitrary SQL to any tool.
    await repo.session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key":key})


async def meter_source(service, repo, principal, query):
    meter_id = query.meter_id or query.building_id + "-energy-meter-1"
    meter = await repo.get("records", meter_id)
    if meter["kind"] != "energy_meter" or meter["buildings"] != [query.building_id]:
        raise HTTPException(404, "Energy meter unavailable")
    source = await service._source(repo, principal, meter, query.building_id)
    if query.section_id and query.section_id != meter["data"].get("section_id"):
        raise HTTPException(404, "Energy section unavailable")
    if meter["data"].get("unit") != "kWh":
        raise HTTPException(422, "Compatible kWh meter required")
    return meter, source


async def execute_workflow(service, principal, tool, raw, request_id):
    schema = {"energy_gap_scan":GapScan,"energy_gap_retry":GapRetry,
              "energy_remediation_queue":EnergyQuery,"energy_monthly_report":MonthlyReport,
              "energy_get_monthly_report":ReportQuery}[tool]
    try:
        query = schema.model_validate(raw)
    except ValueError:
        raise HTTPException(422, "Invalid Energy workflow scope, period or clock") from None
    if tool=="energy_get_monthly_report":
        async with service.store.transaction(principal) as repo:
            row=await repo.get("history",query.report_id)
            if row["kind"]!="energy_monthly_report" or not row["buildings"] or not set(row["buildings"])<=set(principal.building_ids):
                raise HTTPException(404,"Monthly Energy report unavailable")
            await repo.audit("energy_get_monthly_report","succeeded",{"report_id":query.report_id},request_id)
            return {"result":row["data"]}
    if tool == "energy_gap_retry":
        return {"result":await retry_gap(service,principal,query,request_id)}
    if query.building_id not in principal.building_ids:
        raise HTTPException(403,"Building access denied")
    if tool == "energy_gap_scan":
        return {"result":await scan_gaps(service,principal,query,request_id)}
    eui = (await service.execute(principal,"energy_eui",query.model_dump(mode="json"),request_id))["result"]
    if tool == "energy_remediation_queue":
        return {"result":await remediation(service,principal,query,eui,request_id)}
    queue = await remediation(service,principal,query,eui,request_id)
    async with service.store.transaction(principal) as repo:
        meter_id = eui["meter_id"]
        await lock(repo,"energy-report:"+meter_id+":"+str(query.period_start))
        anomalies = [r["data"] for r in await repo.list("history","energy_anomaly")
            if r["buildings"]==[query.building_id] and r["data"].get("meter_id")==meter_id
            and str(query.period_start) <= r["data"]["finding"]["window_start"][:10] <= str(query.period_end)]
        ranked = sorted(anomalies,key=lambda r:Decimal(r["finding"].get("financial_gbp") or 0),reverse=True)
        report_id = identifier("energy-month-v2",meter_id,query.period_start,
            ",".join(sorted(eui["source_ids"])),",".join(sorted(r["anomaly_id"] for r in ranked)))
        previous = next((r for r in await repo.list("history","energy_monthly_report") if r["id"]==report_id),None)
        if previous: return {"result":{**previous["data"],"outcome":"already_generated"}}
        data = {"report_id":report_id,"report_version":"energy-month-v2","building_id":query.building_id,"meter_id":meter_id,
            "reporting_period":{"start":str(query.period_start),"end":str(query.period_end)},
            "trigger":"manual","generated_at":datetime.now(timezone.utc).isoformat(),
            "eui":eui,"remediation":queue,"anomalies_ranked":ranked,
            "total_excess_cost_gbp":ranked[0]["finding"]["financial_gbp"] if len(ranked)==1 else None,
            "anomaly_evaluation_status":"persisted_findings" if ranked else "no_persisted_anomaly_evaluation",
            "cost_aggregation_note":"Overlapping anomaly windows are not additive; benchmark excess cost is separate.",
            "carbon_exposure_kg":None,"carbon_unavailable_reason":"No verified carbon factor in this fixture",
            "source_ids":sorted(set(eui["source_ids"]+queue["source_ids"])),
            "freshness":eui["freshness"],"actor":principal.user_id,"outcome":"generated"}
        data["notifications"] = await notify_managers(repo,query.building_id,"energy-report-"+report_id,
            {"report_id":report_id,"reporting_period":data["reporting_period"],"meter_id":meter_id},"energy_report")
        await repo.insert("history","energy_monthly_report",[query.building_id],data,report_id)
        await repo.audit("energy_monthly_report","generated",{"report_id":report_id},request_id)
        return {"result":data}


async def scan_gaps(service, principal, query, request_id):
    async with service.store.transaction(principal) as repo:
        meter, source = await meter_source(service,repo,principal,query)
        if meter["data"].get("expected_interval_minutes") != 60:
            raise HTTPException(422,"Source-backed hourly interval is required for gap detection")
        await lock(repo,"energy-gap:"+meter["id"])
        begin = datetime.combine(query.period_start,datetime.min.time(),timezone.utc)
        stop = datetime.combine(query.period_end+timedelta(days=1),datetime.min.time(),timezone.utc)
        stamps = set(); source_ids = [source["id"]]
        for row in await repo.list("records","energy_reading"):
            if row["buildings"]!=[query.building_id] or row["data"].get("meter_id")!=meter["id"]: continue
            evidence = await service._source(repo,principal,row,query.building_id)
            stamp = datetime.fromisoformat(row["data"]["timestamp"])
            if begin<=stamp<stop:
                if row["data"].get("interval")!="hour" or row["data"].get("unit")!="kWh" or stamp in stamps:
                    raise HTTPException(409,"Gap source intervals are incompatible or duplicated")
                stamps.add(stamp); source_ids.append(evidence["id"])
        runs=[]; run=[]
        for n in range(int((stop-begin).total_seconds()/3600)+1):
            stamp=begin+timedelta(hours=n)
            if stamp<stop and stamp not in stamps: run.append(stamp)
            elif run:
                if len(run)>=2: runs.append(run)
                run=[]
        existing={r["id"] for r in await repo.list("history","energy_gap")}
        result=[]
        for run in runs:
            gap_id=identifier("energy-gap",meter["id"],run[0],run[-1],query.idempotency_key)
            data={"gap_id":gap_id,"building_id":query.building_id,"meter_id":meter["id"],
                "gap_start":run[0].isoformat(),"gap_end_exclusive":(run[-1]+timedelta(hours=1)).isoformat(),
                "missing_periods":len(run),"source_ids":source_ids,"retry_count":0,"status":"open",
                "first_retry_due":(run[-1]+timedelta(hours=1,minutes=10)).isoformat(),
                "dcc_simulation":meter["data"].get("dcc_simulation"),"actor":principal.user_id}
            data["generated_at"]=datetime.now(timezone.utc).isoformat()
            if gap_id not in existing:
                await repo.insert("history","energy_gap",[query.building_id],data,gap_id)
            result.append(data)
        await repo.audit("energy_gap_scan","succeeded",{"meter_id":meter["id"],"gap_count":len(result)},request_id)
        return {"building_id":query.building_id,"meter_id":meter["id"],"gaps":result,"source_ids":source_ids,
                "rule":"two or more missing hourly periods; three retries ten minutes apart then escalation"}


async def retry_gap(service, principal, query, request_id):
    async with service.store.transaction(principal) as repo:
        await lock(repo,"energy-gap-retry:"+query.gap_id)
        row=await repo.get("history",query.gap_id)
        if row["kind"]!="energy_gap" or not set(row["buildings"])<=set(principal.building_ids):
            raise HTTPException(404,"Energy gap unavailable")
        gap=row["data"]
        events=[r["data"] for r in await repo.list("history","energy_gap_retry") if r["data"].get("gap_id")==query.gap_id]
        prior=max(events,key=lambda r:r["retry_count"],default=None)
        if prior and (prior["test_clock"]==query.test_clock.isoformat() or prior["status"] in {"escalated","unknown"}):
            return {**prior,"outcome":"already_recorded"}
        due=datetime.fromisoformat(prior["next_retry_due"] if prior else gap["first_retry_due"])
        if query.test_clock<due: raise HTTPException(409,"Retry is not due; ten-minute spacing is enforced")
        count=len(events)+1
        mode=gap.get("dcc_simulation")
        if mode not in {"known_failure","unknown"}:
            raise HTTPException(503,"No configured simulated DCC response; no recovery claimed")
        status="unknown" if mode=="unknown" else "escalated" if count==3 else "retry_pending"
        event_id=identifier("energy-retry",query.gap_id,count)
        data={"event_id":event_id,"gap_id":query.gap_id,"retry_count":count,"status":status,
            "test_clock":query.test_clock.isoformat(),"next_retry_due":(query.test_clock+timedelta(minutes=10)).isoformat(),
            "transport":"simulated DCC response; no external call or fabricated reading", "actor":principal.user_id,
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "source_ids":gap["source_ids"],"outcome":"recorded"}
        if status in {"escalated","unknown"}:
            data["notifications"]=await notify_managers(repo,row["buildings"][0],"energy-gap-"+event_id,
                {"gap_id":query.gap_id,"status":status,"attempts":count},"energy_escalation")
        await repo.insert("history","energy_gap_retry",row["buildings"],data,event_id)
        await repo.audit("energy_gap_retry",status,{"gap_id":query.gap_id,"attempt":count},request_id)
        return data


async def remediation(service,principal,query,eui,request_id):
    async with service.store.transaction(principal) as repo:
        await lock(repo,"energy-remediation:"+eui["meter_id"]+":"+str(query.period_start))
        results=[]; excluded=[]; source_ids=list(eui["source_ids"])
        for row in await repo.list("records","energy_asset_profile"):
            if row["buildings"]!=[query.building_id] or row["data"].get("meter_id")!=eui["meter_id"]: continue
            source=await service._source(repo,principal,row,query.building_id)
            source_ids.append(source["id"])
            data=row["data"]
            if re.search(r"\b(no|not|without)\b.{0,25}\b(failed|failure|corrosion|defects?)\b",data.get("inspection_text",""),re.I):
                excluded.append({"asset_id":data["asset_id"],"reason":"negated inspection text conflicts with poor-condition evidence; review required"})
                continue
            if not eui.get("benchmark_available") or not eui.get("eui_available"):
                excluded.append({"asset_id":data["asset_id"],"reason":"verified consumption/benchmark unavailable"}); continue
            condition = Decimal(str(data["condition_score"]))
            if not condition.is_finite() or not 1<=condition<=5:
                raise HTTPException(409,"Condition source score is invalid")
            # Classify using exact annualized values, not the rounded display percentage.
            over_threshold = Decimal(eui["annualized_eui_kwh_per_m2_year"]) > Decimal(eui["benchmark_kwh_per_m2_year"])*Decimal("1.15")
            if condition<=2 and over_threshold:
                repair=Decimal(data["repair_cost_gbp"]); replace=Decimal(data["replace_cost_gbp"])
                if not repair.is_finite() or not replace.is_finite() or min(repair,replace)<0:
                    raise HTTPException(409,"Recommendation cost source is invalid")
                event_id=identifier("energy-remediation",row["id"],query.period_start,query.period_end)
                item={"recommendation_id":event_id,"asset_id":data["asset_id"],"condition_score":data["condition_score"],
                    "consumption_vs_benchmark_pct":eui["deviation_pct"],"recommendation":"repair" if repair<replace else "replace",
                    "repair_cost_gbp":str(repair),"replace_cost_gbp":str(replace),"source_id":source["id"],
                    "actor":principal.user_id,"generated_at":datetime.now(timezone.utc).isoformat(),
                    "queue_only":True,"operational_work_order_created":False}
                if not any(r["id"]==event_id for r in await repo.list("history","energy_recommendation")):
                    await repo.insert("history","energy_recommendation",[query.building_id],item,event_id)
                results.append(item)
        await repo.audit("energy_remediation_queue","succeeded",{"building_id":query.building_id,"count":len(results)},request_id)
        return {"building_id":query.building_id,"recommendations":results,"excluded":excluded,"source_ids":source_ids}
