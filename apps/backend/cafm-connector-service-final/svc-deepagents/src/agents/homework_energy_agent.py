"""Bounded model planning with deterministic energy data and caller-owned history."""
import asyncio, json, re
from datetime import date, datetime, timezone
from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from ..services.energy_hw1.contracts import EnergyQuery, AnomalyQuery, AnomalyAction, AnomalyList, GapScan, GapRetry, MonthlyReport, ReportQuery
from ..services.energy_hw1.service import TOOLS
from ..services.compliance_hw1.sessions import append, conversation
from ..observability.homework import (
    invoke_traced_model,
    rendered_prompt_sha256,
    traced_tool_call,
)
from .energy_prompt import allowed_tools, assemble
from .energy_response import render_eui
from ..services.trace_hw1.energy import observation

DESCRIPTIONS={"energy_readings":"Read source-verified compatible kWh readings for an assigned building and period.",
              "energy_eui":"Calculate period and annualized EUI from verified consumption and floor area.",
              "energy_anomaly_scan":"Scan the authorized recent window using comparable occupancy profiles; report insufficient evidence explicitly.",
              "energy_anomaly_list":"List persisted source-backed anomalies and current manager action status for an assigned building.",
              "energy_anomaly_act":"Acknowledge, monitor or mark an anomaly expected; marking expected requires a reason.",
              "energy_benchmarks":"List the bundled indicative TM46 benchmark categories and values."}
DESCRIPTIONS.update({"energy_gap_scan":"Find persisted two-or-more missing hourly period gaps in an assigned meter.",
    "energy_gap_retry":"Manually trigger the configured simulated DCC retry using an explicit timezone-aware test clock; ten-minute spacing and three-retry escalation enforced.",
    "energy_remediation_queue":"Evaluate source-backed condition <=2 and >15% benchmark excess; recommend repair versus replace in a queue only.",
    "energy_monthly_report":"Generate and persist a source-backed monthly Energy report for a complete calendar month; this does not schedule or create work orders."})
DESCRIPTIONS["energy_get_monthly_report"]="Retrieve an immutable monthly Energy report by report_id within assigned building scope."

def specs(principal):
    return [{"type":"function","function":{"name":name,"description":DESCRIPTIONS[name],
            "parameters":({"type":"object","properties":{},"additionalProperties":False} if name == "energy_benchmarks"
                          else GapScan.model_json_schema() if name == "energy_gap_scan"
                          else GapRetry.model_json_schema() if name == "energy_gap_retry"
                          else MonthlyReport.model_json_schema() if name == "energy_monthly_report"
                          else ReportQuery.model_json_schema() if name == "energy_get_monthly_report"
                          else EnergyQuery.model_json_schema() if name == "energy_remediation_queue"
                          else AnomalyQuery.model_json_schema() if name == "energy_anomaly_scan" else AnomalyAction.model_json_schema() if name == "energy_anomaly_act"
                          else EnergyQuery.model_json_schema() if name in {"energy_eui","energy_readings"}
                          else AnomalyList.model_json_schema())}}
            for name in allowed_tools(principal) if name != "energy_anomaly_act"]

def explicit_energy_request(message):
    """Fallback only for an explicit typed read with one building and a complete period."""
    if re.search(r"\b(anomal(?:y|ies)|drift)\b",message,re.I):
        buildings=set(re.findall(r"\b(hw1-building-[a-z0-9-]+)\b",message,re.I))
        dates=re.findall(r"\b20\d{2}-\d{2}-\d{2}\b",message)
        if len(buildings)==1 and dates:
            try: as_of=date.fromisoformat(dates[0])
            except ValueError: return None
            return "energy_anomaly_scan",{"building_id":next(iter(buildings)),"as_of":as_of.isoformat()}
        return None
    if not re.search(r"\b(eui|energy readings?|consumption)\b",message,re.I): return None
    buildings=set(re.findall(r"\b(hw1-building-[a-z0-9-]+)\b",message,re.I))
    dates=re.findall(r"\b20\d{2}-\d{2}-\d{2}\b",message)
    if len(buildings)!=1 or len(dates)<2: return None
    try: start,end=date.fromisoformat(dates[0]),date.fromisoformat(dates[1])
    except ValueError: return None
    tool="energy_eui" if re.search(r"\beui\b",message,re.I) else "energy_readings"
    section=re.search(r"\b(hw1-section-[a-z0-9-]+)\b",message,re.I)
    meter=re.search(r"\bmeter_id\s+([a-zA-Z0-9_-]+)",message)
    return tool,{"building_id":next(iter(buildings)),"period_start":start.isoformat(),"period_end":end.isoformat(),
                 **({"section_id":section.group(1)} if section else {}),
                 **({"meter_id":meter.group(1)} if meter else {})}

async def ask(principal, body, service, model, request_id):
    prompt, metadata=assemble(principal)
    async with service.store.transaction(principal) as repo:
        session=await conversation(repo,principal,body.conversation_id)
        sid,context=session["id"],session["data"]
    calls=[]; outcome="not_enabled"; provider_failed=False; usage_events=[]; provider_tools=[]
    if allowed_tools(principal):
        messages=[SystemMessage(content=prompt),HumanMessage(content=json.dumps({"prior_turns":context["messages"],"request":body.message}))]
        model_tools=model.bind_tools(specs(principal))
        for _ in range(3):
            try:
                response=await asyncio.wait_for(
                    invoke_traced_model(
                        model_tools,
                        messages,
                        rendered_prompt_sha256(prompt, metadata),
                    ),
                    timeout=60,
                )
            except Exception:
                provider_failed=True
                break
            messages.append(response)
            provider_tools.extend(call["name"] for call in response.tool_calls)
            usage_events.append(getattr(response,"usage_metadata",None))
            if not response.tool_calls: break
            if len(calls)+len(response.tool_calls)>2: break
            for call in response.tool_calls:
                try:
                    result=await traced_tool_call(
                        principal,
                        call["name"],
                        call["args"],
                        lambda call=call: service.execute(
                            principal,call["name"],call["args"],request_id
                        ),
                        allowed_tools=metadata["bound_tools"],
                        unbound_reason=(
                            "Anomaly disposition requires an explicit authenticated human API request"
                        ),
                    )
                    calls.append({**observation(principal, call["name"], call["args"], "model", True), "result":result["result"]})
                    messages.append(ToolMessage(content=json.dumps(result,default=str),tool_call_id=call["id"]))
                except HTTPException as exc:
                    calls.append({**observation(principal, call["name"], None, "model", False), "error":"Operation unavailable" if exc.status_code in (403,404) else str(exc.detail)})
                    messages.append(ToolMessage(content=json.dumps({"operation_succeeded":False}),tool_call_id=call["id"]))
        if not calls:
            fallback=explicit_energy_request(body.message)
            if fallback:
                name,args=fallback
                try:
                    result=await traced_tool_call(
                        principal,
                        name,
                        args,
                        lambda: service.execute(principal,name,args,request_id),
                        allowed_tools=metadata["bound_tools"],
                    )
                    calls.append({**observation(principal, name, args, "fallback", True), "result":result["result"],"route_basis":"explicit scoped energy request"})
                except HTTPException as exc:
                    calls.append({**observation(principal, name, None, "fallback", False), "error":"Operation unavailable" if exc.status_code in (403,404) else str(exc.detail)})
    if calls and all("error" in x for x in calls):
        outcome="failed"
        answer="The energy operation failed; no reading or calculation is claimed."
    elif calls:
        outcome="answered"
        last=calls[-1]
        data=last.get("result",{})
        if last.get("tool") == "energy_anomaly_scan":
            answer=(f"Energy anomaly scan for {data.get('window_start')} through {data.get('window_end')}: "
                    f"{data.get('evaluation_status')}. {data.get('unavailable_reason') or str(len(data.get('anomalies',[])))+' findings returned.'}")
            for finding in data.get("anomalies",[]):
                answer += (f" {finding['type']}: {finding['window_start']} through {finding['window_end']}; "
                    f"excess {finding['excess_kwh']} kWh, window cost GBP{finding['financial_gbp']}; "
                    f"annualized cost GBP{finding.get('annualized_financial_gbp')}. ")
            answer += f" Source references: {len(data.get('source_ids',[]))}; meter {data.get('meter_id')}."
        elif last.get("tool") in {"energy_gap_scan","energy_gap_retry","energy_remediation_queue","energy_monthly_report","energy_get_monthly_report"}:
            answer = "Verified Energy workflow result: " + json.dumps(data, default=str)
        elif last.get("tool") == "energy_anomaly_list":
            answer=f"Found {len(data.get('anomalies', []))} {data.get('status')} energy anomalies for {data.get('building_id')}."
        elif last.get("tool") == "energy_anomaly_act":
            answer=f"Energy anomaly {data.get('anomaly_id')} is now {data.get('status')} ({data.get('outcome')})."
        elif last.get("tool") == "energy_benchmarks":
            answer=f"The bundled {data.get('pack')} is indicative. It contains {len(data.get('building_types', {}))} building-type categories ({data.get('unit')})."
        elif last.get("tool") == "energy_eui":
            # This module serves the trusted fictional Homework runtime only.
            answer = render_eui(data, fictional=True)
        else:
            answer=(f"Verified {data.get('consumption_kwh')} {data.get('unit')} across {data.get('reading_count')} readings for "
                f"{data.get('period_start')} through {data.get('period_end')}; latest source reading {data.get('freshness')}. ")
            if "eui_available" in data:
                answer += (f"Period EUI is {data['eui_kwh_per_m2_period']} kWh/m²; annualized EUI is "
                       f"{data.get('annualized_eui_kwh_per_m2_year')} kWh/m²/year." if data["eui_available"] else "EUI unavailable: verified area or readings are missing.")
                if data.get("benchmark_available"):
                    answer += (f" TM46 indicative {data['benchmark_label']} benchmark is {data['benchmark_kwh_per_m2_year']} "
                        f"kWh/m²/year; deviation is {data['deviation_pct']}%. Benchmark excess is {data['benchmark_excess_kwh']} kWh.")
                    answer += (f" Its tariff-derived cost is £{data['financial_excess_gbp']}." if data.get("financial_excess_available") else " Tariff-derived excess cost is unavailable.")
                elif data.get("benchmark_unavailable_reason"):
                    answer += f" Benchmark comparison is unavailable: {data['benchmark_unavailable_reason']}."
    else:
        outcome="failed" if provider_failed else ("not_enabled" if allowed_tools(principal) else "forbidden")
        answer=("The model provider was unavailable; no energy operation was performed." if provider_failed else
                "No energy calculation or reading was returned; provide an assigned building and measurement period." if allowed_tools(principal) else
                "Energy access is outside your role permissions.")
    async with service.store.transaction(principal) as repo:
        current=await conversation(repo,principal,sid)
        await append(repo,current,body.message,answer,[])
        await repo.insert("history","trace",list(principal.building_ids),{"actor":principal.user_id,"role":principal.role,
            "conversation_id":sid,"request_id":request_id,"intent":"energy_question","selected_route":"energy",
            "actual_tools":[observation(principal, x["tool"], x.get("arguments"), x.get("execution_origin"), "error" not in x) for x in calls],"outcome":outcome,
            "prompt_metadata":metadata,"policy_version":"hw1-energy-v1",
            "generated_at":datetime.now(timezone.utc).isoformat()})
    if allowed_tools(principal):
        from ..services.usage_hw1.service import record_measurement
        for index,usage in enumerate(usage_events):
            await record_measurement(principal,request_id,"energy_intelligence",sid,usage,index)
    return {"outcome":outcome,"answer":answer,"conversation_id":sid,"tool_calls":calls,"prompt_metadata":metadata,
        "provider_execution":{"successful_responses":len(usage_events),"selected_tools":provider_tools,"provider_failed":provider_failed}}
