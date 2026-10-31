"""Reviewed API surface: drafts/read tools plus human-only confirmation/recovery."""
from fastapi import APIRouter,Request,HTTPException
from sqlalchemy.exc import SQLAlchemyError
from ...services.communications_hw1.service import CommunicationsService
from ...services.communications_hw1.contracts import ToolRequest,Preview,Confirmation,Recovery,ROLE_TOOLS
router=APIRouter(prefix='/api/homework/communications',tags=['Isolated communications and HOI'])

def service(request):
    if not getattr(request.app.state,'hw1_communications_enabled',False):raise HTTPException(503,'Communications profile disabled')
    return CommunicationsService()

@router.get('/tools')
async def tools(request:Request):
    service(request)
    return {'tools':ROLE_TOOLS[request.state.test_principal.role],'simulation':'no real transport'}

@router.post('/tools/execute')
async def execute(body:ToolRequest,request:Request):
    return await service(request).execute(request.state.test_principal,body.tool,body.arguments,request.state.request_id)

@router.post('/previews')
async def preview(body:Preview,request:Request):
    return await service(request).execute(request.state.test_principal,'communications_preview',body.model_dump(),request.state.request_id)

@router.post('/proposals/{identifier}/confirm')
async def confirm(identifier:str,body:Confirmation,request:Request):
    try:return await service(request).confirm(request.state.test_principal,identifier,body)
    except SQLAlchemyError:raise HTTPException(503,'Communication transaction unavailable; no success claimed') from None

@router.post('/outbox/{identifier}/recover')
async def recover(identifier:str,body:Recovery,request:Request):
    try:return await service(request).recover(request.state.test_principal,identifier,body.reason)
    except SQLAlchemyError:raise HTTPException(503,'Recovery transaction unavailable; no success claimed') from None


@router.get('/proposals/{identifier}')
async def get_preview(identifier:str,request:Request):
    return await service(request).get_preview(request.state.test_principal,identifier)
