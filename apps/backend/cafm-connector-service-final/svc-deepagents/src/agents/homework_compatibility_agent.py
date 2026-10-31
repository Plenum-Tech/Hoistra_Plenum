"""One bounded typed comparison; the model cannot invent a join or commit state."""
import asyncio,json
from langchain_core.messages import SystemMessage,HumanMessage
from fastapi import HTTPException
from .unified_prompt import augment
from ..services.unified_hw1.compatibility import Compatibility,compare
from ..services.compliance_hw1.store import runtime_store
from ..services.compliance_hw1.sessions import conversation,append
from ..observability.homework import (
    invoke_traced_model,
    rendered_prompt_sha256,
    traced_tool_call,
)
async def ask(p,body,model,request_id):
    prompt,metadata=augment('Compare only explicitly supplied immutable source IDs using the typed compatibility tool. Missing metadata never establishes a join.',
        {'profile':'hw1-unified-support','bound_tools':['unified_source_compatibility'],'max_tool_calls':1},p)
    tool={'type':'function','function':{'name':'unified_source_compatibility','description':'Validate explicit same-building entity, period and unit compatibility; no inferred joins or calculations.',
        'parameters':Compatibility.model_json_schema()}}
    calls=[];selected=[];responses=0;usage=None
    try:
        bound_model = model.bind_tools([tool])
        response=await asyncio.wait_for(
            invoke_traced_model(
                bound_model,
                [SystemMessage(content=prompt), HumanMessage(content=body.message)],
                rendered_prompt_sha256(prompt, metadata),
            ),
            60,
        )
        responses=1;usage=getattr(response,'usage_metadata',None);selected=[c['name'] for c in response.tool_calls]
        if len(response.tool_calls)==1:
            c=response.tool_calls[0]
            result=await traced_tool_call(
                p,
                c['name'],
                c['args'],
                lambda: compare(p,Compatibility.model_validate(c['args'])),
                allowed_tools={'unified_source_compatibility'},
            )
            calls.append({'tool':c['name'],'input':c['args'],'result':result})
        outcome='answered' if calls else 'needs_clarification'
    except HTTPException as error:
        calls=[{'tool':'unified_source_compatibility','error':'Source comparison unavailable','status':error.status_code}];outcome='failed'
    except Exception:
        outcome='failed'
    answer='Verified source compatibility: '+json.dumps(calls[0]['result']) if calls and 'result' in calls[0] else 'Provide permitted source IDs and their assigned building; no compatibility or join is claimed.'
    async with runtime_store().transaction(p) as repo:
        current=await conversation(repo,p,body.conversation_id);await append(repo,current,body.message,answer,[])
    if responses:
        from ..services.usage_hw1.service import record_measurement
        await record_measurement(p,request_id,'source_compatibility',body.conversation_id,usage)
    return {'outcome':outcome,'answer':answer,'tool_calls':calls,'conversation_id':body.conversation_id,
        'prompt_metadata':metadata,'provider_execution':{'successful_responses':responses,'selected_tools':selected}}
