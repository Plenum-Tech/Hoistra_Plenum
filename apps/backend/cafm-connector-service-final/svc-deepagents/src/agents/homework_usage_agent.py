"""Bounded preview/search agent. Human confirmation routes are never model tools."""
import asyncio,json,re
from datetime import date,datetime,timezone
from fastapi import HTTPException
from langchain_core.messages import HumanMessage,SystemMessage,ToolMessage
from .usage_prompt import assemble
from ..services.usage_hw1.contracts import MODELS
from ..services.compliance_hw1.sessions import conversation,append

def generation_declined(message):
    return bool(re.search(r"\b(?:do not|don't|never)\s+(?:generate|run|trigger|send)\b",message,re.I)
        or re.match(r'\s*(explain|describe|what\s+(is|does)|how\s+(does|do))\b',message,re.I))

def explicit_report(message):
    """Honor a named manual report only when the user supplied one valid period."""
    if generation_declined(message) or '?' in message:return None
    if not (re.search(r'\b(generate|run|trigger)\b',message,re.I) or re.match(r'\s*usage_(weekly|monthly)_report\b',message)):
        return None
    names=[n for n in ('usage_weekly_report','usage_monthly_report') if re.search(r'\b'+n+r'\b',message)]
    dates=set(re.findall(r'\b\d{4}-\d{2}-\d{2}\b',message))
    if len(names)!=1 or len(dates)!=1:return None
    raw=next(iter(dates))
    try:
        start=date.fromisoformat(raw)
        if names[0]=='usage_weekly_report' and start.weekday()!=0:return None
        if names[0]=='usage_monthly_report' and start.day!=1:return None
    except ValueError:return None
    return {'tool':names[0],'period_start':raw}

async def ask(principal,body,service,model,request_id):
    prompt,metadata=assemble(principal)
    async with service.store.transaction(principal) as repo:
        session=await conversation(repo,principal,body.conversation_id)
    descriptions={'usage_weekly_report':'Generate/retrieve an immutable measured weekly snapshot and configured scoped simulated digest; Monday start in Asia/Dubai.',
        'usage_monthly_report':'Generate/retrieve a measured calendar month snapshot and configured scoped simulated digest.',
        'usage_get_report':'Read a previously generated immutable usage snapshot with same role/content/building guards.'}
    tools=[{"type":"function","function":{"name":name,"description":descriptions[name],
        "parameters":MODELS[name].model_json_schema()}} for name in metadata["bound_tools"]]
    calls=[];provider_tools=[];responses=0;failed=False;usage_events=[]
    if tools:
        messages=[SystemMessage(content=prompt),HumanMessage(content=json.dumps({"prior_turns":session["data"]["messages"],"request":body.message}))]
        bound=model.bind_tools(tools)
        requested=explicit_report(body.message)
        if requested and requested['tool'] not in metadata['bound_tools']:requested=None
        initial=(model.bind_tools(tools,tool_choice={'type':'function','function':{'name':requested['tool']}}) if requested else bound)
        for step in range(3):
            try: response=await asyncio.wait_for((initial if step==0 else bound).ainvoke(messages),timeout=60)
            except Exception: failed=True;break
            responses+=1;provider_tools.extend(c["name"] for c in response.tool_calls)
            usage_events.append(getattr(response,"usage_metadata",None))
            messages.append(response)
            if not response.tool_calls or len(calls)+len(response.tool_calls)>2: break
            for call in response.tool_calls:
                try:
                    if call["name"] not in metadata["bound_tools"]:raise HTTPException(403,"Tool not permitted")
                    if call['name'] in {'usage_weekly_report','usage_monthly_report'} and generation_declined(body.message):
                        raise HTTPException(422,'Manual generation/delivery was explicitly declined')
                    if requested and (call['name']!=requested['tool'] or str(call['args'].get('period_start'))!=requested['period_start']):
                        raise HTTPException(422,'Requested report tool and period must match exactly')
                    from ..services.usage_hw1.tools import execute
                    output=await execute(principal,{'tool':call['name'],'arguments':call['args']},request_id,service)
                    calls.append({"tool":call["name"],"input":call["args"],"result":output["result"]})
                    payload=output
                except HTTPException as error:
                    payload={"error":"Operation unavailable","status":error.status_code}
                    calls.append({"tool":call["name"],"error":"Operation unavailable","status":error.status_code})
                messages.append(ToolMessage(content=json.dumps(payload,default=str),tool_call_id=call["id"]))
    good=[c for c in calls if "result" in c]
    outcome="answered" if good else "failed" if failed or calls else "needs_clarification" if tools else "forbidden"
    answer=("Verified usage result: "+json.dumps(good[-1]["result"],default=str) if good else
        "Provide a Monday weekly start date or a first-of-month reporting start date. The report uses only your permitted role scope." if tools and not failed else
        "Usage is outside your role permissions." if not tools else "The provider was unavailable; no successful usage is claimed.")
    async with service.store.transaction(principal) as repo:
        current=await conversation(repo,principal,session["id"])
        await append(repo,current,body.message,answer,[])
        await repo.insert("history","trace",list(principal.building_ids),{"actor":principal.user_id,"role":principal.role,"category":"compliance" if principal.role=="compliance_officer" else "agent",
            "conversation_id":session["id"],"request_id":request_id,"intent":"usage_request","selected_route":"usage",
            "actual_tools":[{"tool":c["tool"],"succeeded":"result" in c} for c in calls],"outcome":outcome,
            "prompt_metadata":metadata,"generated_at":datetime.now(timezone.utc).isoformat()})
    from ..services.usage_hw1.service import record_measurement
    for index,usage in enumerate(usage_events):
        await record_measurement(principal,request_id,"compliance" if principal.role=="compliance_officer" else "usage",
            session["id"],usage,index)
    return {"outcome":outcome,"answer":answer,"conversation_id":session["id"],"tool_calls":calls,
        "prompt_metadata":metadata,"provider_execution":{"successful_responses":responses,"selected_tools":provider_tools}}
