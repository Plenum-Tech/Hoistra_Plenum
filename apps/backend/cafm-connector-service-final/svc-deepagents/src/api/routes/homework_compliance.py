"""Authenticated profile and human confirmation; no legacy specialist routes."""
import os
from fastapi import APIRouter,Request,HTTPException
from sqlalchemy.exc import SQLAlchemyError
from ...services.compliance_hw1.contracts import ToolRequest,ProposalInput,Confirmation,Ask,REVIEWERS
from ...services.compliance_hw1.service import runtime_service
from ...services.compliance_hw1.actions import process_events,emit_expiry_notifications
from ...services.compliance_hw1.authorization import allowed_tools
from ...services.compliance_hw1.sessions import conversation

router=APIRouter(prefix='/api/homework',tags=['Isolated Compliance'])


def service(request):
    if not getattr(request.app.state,'hw1_compliance_enabled',False):
        raise HTTPException(503,'Compliance profile not enabled')
    return runtime_service()


@router.get('/compliance/status')
async def status(request:Request):
    service(request)
    return {'enabled':True,'profile':'hw1-compliance','specification_version':'0.37.0',
            'manifest_version':'2.0.0','max_tool_calls':4,'confirmation':'human API/CLI only',
            'simulation':'fictional fixtures; no real delivery/appointment/register attestation'}


@router.get('/compliance/tools')
async def tools(request:Request):
    service(request)
    return {'tools':allowed_tools(request.state.test_principal),'profile':'hw1-compliance'}


@router.post('/compliance/tools/execute')
async def execute(body:ToolRequest,request:Request):
    return await service(request).execute(request.state.test_principal,body.tool,body.arguments,request.state.request_id)


@router.post('/compliance/proposals')
async def prepare(body:ProposalInput,request:Request):
    return await service(request).execute(request.state.test_principal,'compliance_prepare_proposal',body.model_dump(mode='json'),request.state.request_id)


@router.get('/compliance/proposals/{identifier}')
async def proposal(identifier:str,request:Request):
    return await service(request).execute(request.state.test_principal,'compliance_get_proposal',{'entity_id':identifier},request.state.request_id)


@router.post('/actions/{identifier}/confirm')
async def confirm(identifier:str,body:Confirmation,request:Request):
    return await service(request).confirm(request.state.test_principal,identifier,body.model_dump(),request.state.request_id)


@router.post('/compliance/events/drain')
async def drain(request:Request):
    instance=service(request);p=request.state.test_principal
    if p.role not in REVIEWERS:
        raise HTTPException(403,'Event recovery not permitted')
    await instance.store.audit(p,'event_drain','requested',request_id=request.state.request_id)
    result=await process_events(instance.store,p)
    result['expiry_notifications']=await emit_expiry_notifications(instance.store,p)
    return result


@router.get('/compliance/conversations/{identifier}')
async def get_conversation(identifier:str,request:Request):
    instance=service(request);p=request.state.test_principal
    async with instance.store.transaction(p) as repo:
        row=await conversation(repo,p,identifier)
        await repo.audit('conversation.read','allowed',{'conversation_id':identifier},request.state.request_id)
        return {'conversation_id':identifier,**row['data']}


@router.get('/compliance/traces')
async def traces(request:Request):
    instance=service(request);p=request.state.test_principal
    async with instance.store.transaction(p) as repo:
        rows=[r['data'] for r in await repo.list('history','trace') if r['data']['actor']==p.user_id
              and set(r['buildings'])<=set(p.building_ids)]
        await repo.audit('trace.export','allowed',request_id=request.state.request_id)
        return {'format':'hw1-trace-summary-v1','traces':rows,'private_content':'omitted; owned conversations retain content'}


@router.post('/compliance/ask')
async def ask(body:Ask,request:Request):
    from ...llm_factory import create_chat_model
    from ...agents.homework_compliance_agent import ask as plan
    instance=service(request)
    try:
        return await plan(request.state.test_principal,body,instance,create_chat_model(),request.state.request_id)
    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(503,'Compliance store unavailable') from None
    except Exception:
        raise HTTPException(502,'Compliance planner unavailable; no success claimed') from None
