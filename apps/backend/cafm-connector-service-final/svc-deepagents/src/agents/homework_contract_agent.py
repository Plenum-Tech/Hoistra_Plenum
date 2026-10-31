"""Bounded Contract Performance planner; all work stays in guarded Python adapters."""
import asyncio
import json
import calendar
import re
from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from ..services.contract_hw1.contracts import (
    ConfigProposal, InvoiceQuery, PerformanceQuery, ScorecardQuery,
)
from ..services.contract_hw1.service import TOOL_ROLES
from ..services.contract_hw1.fixtures import FIXTURE_VERSION
from ..services.compliance_hw1.sessions import append, conversation
from ..observability.homework import (
    invoke_traced_model,
    rendered_prompt_sha256,
    traced_tool_call,
)
from .contract_prompt import allowed_tools, assemble

DESCRIPTIONS = {
    "contract_vendor_performance": "Generate and persist an evidence-backed monthly vendor scorecard for a complete calendar month in assigned scope.",
    "contract_get_scorecard": "Retrieve an existing scorecard in assigned scope by scorecard_id.",
    "contract_invoice_diagnostic": "Read separate source-backed invoice match/clear/flag diagnostics for a date period; this does not change vendor score or approve payment.",
    "contract_scoring_configuration": "Portfolio Manager only: read own-organization vendor-scoring configuration and version.",
    "contract_prepare_scoring_configuration": "Portfolio Manager only: prepare, but never confirm, an exact own-organization scoring configuration preview.",
}
CONTRACTS = {
    "contract_vendor_performance": PerformanceQuery,
    "contract_get_scorecard": ScorecardQuery,
    "contract_invoice_diagnostic": InvoiceQuery,
    "contract_scoring_configuration": None,
    "contract_prepare_scoring_configuration": ConfigProposal,
}


def tool_specs(principal):
    result = []
    for name in allowed_tools(principal):
        schema = CONTRACTS[name].model_json_schema() if CONTRACTS[name] else {
            "type": "object", "properties": {}, "additionalProperties": False}
        result.append({"type": "function", "function": {
            "name": name, "description": DESCRIPTIONS[name], "parameters": schema}})
    return result


def _answer(calls):
    lines = []
    for call in calls:
        result = call.get("output", {}).get("result", {})
        if call.get("error"):
            lines.append("The operation failed: " + call["error"])
        elif "overall_score" in result:
            if result["overall_score"] is None:
                lines.append("No score could be calculated from eligible source reports for "
                             + result["reporting_period"]["start"] + " through "
                             + result["reporting_period"]["end"] + ".")
            else:
                lines.append(f"Vendor scorecard: {result['overall_score']}/100 for "
                             f"{result['reporting_period']['start']} through "
                             f"{result['reporting_period']['end']}; "
                             f"{result['eligible_work_orders']} eligible work orders, "
                             f"{len(result['excluded_work_orders'])} excluded. "
                             f"Configuration version {result['parameter_snapshot']['configuration_version']}. "
                             f"{sum(bool(x['accreditation_cap_applied']) for x in result['work_order_details'].values())} "
                             "work-order contributions have an evidenced date-specific cap. "
                             f"Evidence: {', '.join(result.get('source_ids', []))}. Invoice diagnostics are separate.")
        elif "proposal_id" in result:
            lines.append("Configuration preview " + result["proposal_id"]
                         + " is awaiting explicit confirmation. No settings changed.")
        elif "parameters" in result:
            lines.append("Scoring configuration version " + str(result["version"])
                         + ": " + json.dumps(result["parameters"], sort_keys=True))
        elif "invoices" in result:
            invoices = result["invoices"]
            lines.append(f"Separate invoice diagnostic covers {result['invoice_count']} invoices "
                         f"for {result['reporting_period']['start']} through "
                         f"{result['reporting_period']['end']}. "
                         f"Matched {sum(x['matched_count'] for x in invoices)}, "
                         f"cleared {sum(x['cleared_count'] for x in invoices)}, "
                         f"flagged {sum(x['flagged_count'] for x in invoices)}, "
                         f"unmatched {sum(x['unmatched_count'] for x in invoices)} lines "
                         "in the returned invoices; matched and flagged counts may overlap. "
                         f"Evidence: {', '.join(result.get('source_ids', []))}. "
                         "It does not affect vendor score or authorize payment.")
        elif "scorecard_id" in result or "work_order_details" in result:
            lines.append("Retrieved source-backed scorecard " + str(result.get("scorecard_id", ""))
                         + "; reporting period and parameter snapshot are included.")
    return "\n".join(lines) or "No verified Contract Performance result was returned."


def explicit_contract_request(message):
    """Only route a fully specified monthly score or dated invoice diagnostic."""
    vendors=re.findall(r"\b(hw1-[a-z0-9-]+-vendor-[0-9]+)\b",message,re.I)
    if len(vendors)!=1: return None
    dates=re.findall(r"\b20\d{2}-\d{2}-\d{2}\b",message)
    if len(dates)>=2:
        try: start,end=date.fromisoformat(dates[0]),date.fromisoformat(dates[1])
        except ValueError: return None
    else:
        month=re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b",message,re.I)
        if not month: return None
        month_num=list(calendar.month_name).index(month.group(1).title())
        year=int(month.group(2));start=date(year,month_num,1);end=date(year,month_num,calendar.monthrange(year,month_num)[1])
    buildings=set(re.findall(r"\b(hw1-building-[a-z][0-9]+)\b",message,re.I))
    args={"vendor_id":vendors[0],"period_start":start.isoformat(),"period_end":end.isoformat(),
          "idempotency_key":"provider-fallback-"+uuid4().hex}
    if len(buildings)==1: args["building_ids"]=[next(iter(buildings))]
    if re.search(r"\binvoice\b",message,re.I):
        return "contract_invoice_diagnostic",{k:v for k,v in args.items() if k!="idempotency_key"}
    if re.search(r"\b(scorecard|vendor performance|vendor score|sla)\b",message,re.I):
        return "contract_vendor_performance",args
    return None


async def ask(principal, body, service, model, request_id=None):
    request_id = request_id or str(uuid4())
    prompt, metadata = assemble(principal)
    async with service.store.transaction(principal) as repo:
        session = await conversation(repo, principal, body.conversation_id)
        session_id, context = session["id"], session["data"]
    provider_calls = 0
    provider_tool_calls = []
    provider_failed = False
    if not allowed_tools(principal):
        outcome = "forbidden"
        answer = "Contract Performance is outside your role permissions."
        calls = []
    else:
        messages = [SystemMessage(content=prompt), HumanMessage(content=json.dumps({
            "prior_turns": context["messages"], "request": body.message}))]
        calls = []
        usage_events = []
        control = None
        bound = model.bind_tools(tool_specs(principal))
        for _ in range(5):
            try:
                response = await asyncio.wait_for(
                    invoke_traced_model(
                        bound,
                        messages,
                        rendered_prompt_sha256(prompt, metadata),
                    ),
                    timeout=60,
                )
            except Exception:
                provider_failed = True
                control = "The model provider was unavailable; no additional operation was performed."
                break
            messages.append(response)
            provider_calls += 1
            provider_tool_calls.extend(item["name"] for item in response.tool_calls)
            usage_events.append(getattr(response, "usage_metadata", None))
            if not response.tool_calls:
                # Free-form model prose is not used as evidence or as an action result.
                control = response.content if isinstance(response.content, str) else ""
                break
            if len(calls) + len(response.tool_calls) > 4:
                control = "The four tool-call limit was reached; narrow the request."
                break
            for item in response.tool_calls:
                try:
                    output = await traced_tool_call(
                        principal,
                        item["name"],
                        item["args"],
                        lambda item=item: service.execute(
                            principal, item["name"], item["args"], request_id
                        ),
                        allowed_tools=metadata['bound_tools'],
                    )
                    calls.append({"tool": item["name"], "input": item["args"], "output": output})
                    payload = output
                except HTTPException as exc:
                    error = "Operation unavailable" if exc.status_code in (403, 404) else str(exc.detail)
                    calls.append({"tool": item["name"], "input": {}, "error": error,
                                  "status_code": exc.status_code})
                    payload = {"error": error, "operation_succeeded": False}
                messages.append(ToolMessage(content=json.dumps(payload, default=str),
                                            tool_call_id=item["id"]))
        if not calls:
            fallback=explicit_contract_request(body.message)
            if fallback:
                name,args=fallback
                try:
                    output=await traced_tool_call(
                        principal,
                        name,
                        args,
                        lambda: service.execute(principal,name,args,request_id),
                        allowed_tools=metadata['bound_tools'],
                    )
                    calls.append({"tool":name,"input":args,"output":output,
                                  "route_basis":"explicit scoped Contract Performance request"})
                except HTTPException as exc:
                    error="Operation unavailable" if exc.status_code in (403,404) else str(exc.detail)
                    calls.append({"tool":name,"input":{},"error":error,"status_code":exc.status_code})
        if calls:
            answer = _answer(calls)
            outcome = "failed" if all("error" in call for call in calls) else "answered"
        else:
            answer = "I need a permitted vendor, reporting period, or configuration request to continue."
            outcome = "failed" if provider_failed else "not_enabled"
            if provider_failed:
                answer = "The model provider was unavailable; no verified Contract Performance result was returned."
    async with service.store.transaction(principal) as repo:
        current = await conversation(repo, principal, session_id)
        await append(repo, current, body.message, answer, [])
        await repo.insert("history", "trace", list(principal.building_ids), {
            "actor": principal.user_id, "role": principal.role, "conversation_id": session_id,
            "request_id": request_id, "intent": "contract_performance_request",
            "selected_route": "contract_performance",
            "actual_tools": [{"tool": call["tool"], "succeeded": "error" not in call,
                              "result_keys": sorted(call.get("output", {}).get("result", {}).keys())[:30]}
                             for call in calls],
            "outcome": outcome, "prompt_metadata": metadata,
            "fixture_version": FIXTURE_VERSION, "policy_version": "hw1-contract-v1",
            "provider_calls": provider_calls, "provider_tool_calls": provider_tool_calls,
            "generated_at": datetime.now(timezone.utc).isoformat()})
    if allowed_tools(principal):
        from ..services.usage_hw1.service import record_measurement
        for index, usage in enumerate(usage_events):
            await record_measurement(principal, request_id, "contract_performance", session_id, usage, index)
    return {"outcome": outcome, "answer": answer, "conversation_id": session_id,
            "tool_calls": calls, "prompt_metadata": metadata,
            "provider_execution": {"successful_responses": provider_calls,
                                   "selected_tools": provider_tool_calls,
                                   "provider_failed": provider_failed},
            "data_source": "fictional isolated Homework records"}
