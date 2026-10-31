"""Bounded preview/search agent. Human confirmation routes are never model tools."""
import asyncio,json
from datetime import datetime,timezone
from fastapi import HTTPException
from langchain_core.messages import HumanMessage,SystemMessage,ToolMessage
from .communications_prompt import assemble
from ..services.communications_hw1.contracts import MODELS
from ..services.compliance_hw1.sessions import conversation,append
from ..observability.homework import (
    invoke_traced_model,
    rendered_prompt_sha256,
    traced_tool_call,
)

async def ask(principal,body,service,model,request_id):
    prompt,metadata=assemble(principal)
    async with service.store.transaction(principal) as repo:
        session=await conversation(repo,principal,body.conversation_id)
    descriptions={"communications_preview":"Prepare the exact requested simulated email preview with recipients, subject, body and source attachments; never send or confirm.",
        "communications_contacts":"List authorized fictional contacts and their permitted purposes in an assigned building.",
        "communications_outbox":"Read permitted simulated outcomes and pending/failure issues in an assigned building.",
        "communications_hoi":"Managers only: read canonical ten-group HOI completeness and evidence. History uses limit/offset pagination; for a current-score question use limit 1. A partial history page does not make the current score partial."}
    tools=[{"type":"function","function":{"name":name,"description":descriptions[name],
        "parameters":MODELS[name].model_json_schema()}} for name in metadata["bound_tools"]]
    calls=[];provider_tools=[];responses=0;failed=False;usage_events=[]
    if tools:
        messages=[SystemMessage(content=prompt),HumanMessage(content=json.dumps({"prior_turns":session["data"]["messages"],"request":body.message}))]
        bound=model.bind_tools(tools)
        for _ in range(3):
            try:
                response=await asyncio.wait_for(
                    invoke_traced_model(
                        bound,
                        messages,
                        rendered_prompt_sha256(prompt, metadata),
                    ),
                    timeout=60,
                )
            except Exception: failed=True;break
            responses+=1;provider_tools.extend(c["name"] for c in response.tool_calls)
            usage_events.append(getattr(response,"usage_metadata",None))
            messages.append(response)
            if not response.tool_calls or len(calls)+len(response.tool_calls)>2: break
            for call in response.tool_calls:
                try:
                    output=await traced_tool_call(
                        principal,
                        call["name"],
                        call["args"],
                        lambda call=call: service.execute(
                            principal,call["name"],call["args"],request_id
                        ),
                        allowed_tools=metadata["bound_tools"],
                        unbound_reason="Tool not permitted",
                    )
                    calls.append({"tool":call["name"],"input":call["args"],"result":output["result"]})
                    payload=output
                except HTTPException as error:
                    payload={"error":"Operation unavailable","status":error.status_code}
                    calls.append({"tool":call["name"],"error":"Operation unavailable","status":error.status_code})
                messages.append(ToolMessage(content=json.dumps(payload,default=str),tool_call_id=call["id"]))
    good=[c for c in calls if "result" in c]
    outcome="answered" if good else "failed" if failed or calls else "needs_clarification" if tools else "forbidden"
    answer=("Verified communications result: "+json.dumps(good[-1]["result"],default=str) if good else
        "Provide the assigned building and typed source/evidence details for an enabled preview or search." if tools and not failed else
        "Communications is outside your role permissions." if not tools else "The provider was unavailable; no successful communications is claimed.")
    async with service.store.transaction(principal) as repo:
        current=await conversation(repo,principal,session["id"])
        await append(repo,current,body.message,answer,[])
        await repo.insert("history","trace",list(principal.building_ids),{"actor":principal.user_id,"role":principal.role,"category":"compliance" if principal.role=="compliance_officer" else "agent",
            "conversation_id":session["id"],"request_id":request_id,"intent":"communications_request","selected_route":"communications",
            "actual_tools":[{"tool":c["tool"],"succeeded":"result" in c} for c in calls],"outcome":outcome,
            "prompt_metadata":metadata,"generated_at":datetime.now(timezone.utc).isoformat()})
    from ..services.usage_hw1.service import record_measurement
    for index,usage in enumerate(usage_events):
        await record_measurement(principal,request_id,"compliance" if principal.role=="compliance_officer" else "communications",
            session["id"],usage,index)
    return {"outcome":outcome,"answer":answer,"conversation_id":session["id"],"tool_calls":calls,
        "prompt_metadata":metadata,"provider_execution":{"successful_responses":responses,"selected_tools":provider_tools}}
