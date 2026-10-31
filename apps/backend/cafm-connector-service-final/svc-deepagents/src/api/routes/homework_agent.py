"""Single entry point for the enabled, authenticated Homework agent profiles."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from ...agents.homework_router import ask as route_request

router = APIRouter(prefix="/api/homework/agent", tags=["Homework orchestrator"])


class AgentAsk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=160)


@router.post("/ask")
async def ask(body: AgentAsk, request: Request):
    try:
        result=await route_request(request, body)
        if result.get('outcome')=='failed':
            from ...services.usage_hw1.events import record_failure
            await record_failure(request.state.test_principal,request.state.request_id,result.get('selected_route','agent'),502)
        return result
    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(503, "Homework agent data store unavailable; no result claimed") from None
    except Exception:
        raise HTTPException(502, "Homework agent router unavailable; no result claimed") from None


@router.get('/status')
async def status(request:Request):
    from ...agents.unified_prompt import validate,enabled_for,ROOT,PROFILES
    from ...agents.homework_router import permitted
    import json
    p=request.state.test_principal;registry,_=validate(enabled_for(request.app))
    capabilities={name:json.loads((ROOT/PROFILES[name][0]).read_text())['role_tools'][p.role]
        for name in registry['enabled_profiles'] if permitted(p,name)}
    if 'energy' in capabilities:capabilities['energy']=[t for t in capabilities['energy'] if t!='energy_anomaly_act']
    return {'registry':registry,'role':p.role,'permitted_profile_tools':capabilities,
        'business_confirmation':'separate authenticated human API; no model commit tool'}

@router.get('/conversations/{identifier}')
async def owned_conversation(identifier:str,request:Request):
    from ...services.compliance_hw1.sessions import conversation
    from ...services.compliance_hw1.store import runtime_store
    p=request.state.test_principal
    async with runtime_store().transaction(p) as repo:
        row=await conversation(repo,p,identifier)
        await repo.audit('unified_conversation_read','allowed',{'conversation_id':identifier},request.state.request_id)
        return {'conversation_id':identifier,**row['data']}

@router.get('/evaluations/{identifier}')
async def evaluation_metadata(identifier:str,request:Request):
    from ...services.trace_hw1.service import TraceService
    data=await TraceService().export(request.state.test_principal,identifier,100)
    evaluations=[]
    for t in data['traces']:
        bound=(t.get('prompt_metadata') or {}).get('bound_tools',[])
        evaluations.append({'trace_id':t['trace_id'],'outcome':t['outcome'],
            'provider_response_observed':bool((t.get('provider_execution') or {}).get('successful_responses')),
            'executed_tools':[x['tool'] for x in t['actual_tools']],
            'registered_bound_tools_only':all(x['tool'] in bound for x in t['actual_tools']),
            'semantic_judgement':'not inferred from tool success; compare actual results to scenario expectations'})
    return {'conversation_id':identifier,'evaluations':evaluations,'raw_content_included':False}


from ...services.unified_hw1.compatibility import Compatibility
@router.post('/source-compatibility')
async def compatibility(body:Compatibility,request:Request):
    from ...services.unified_hw1.compatibility import compare
    return await compare(request.state.test_principal,body)
