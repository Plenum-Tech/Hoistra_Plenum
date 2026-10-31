"""Manual weekly usage report snapshots. No schedule, quota or billing controls."""
from datetime import date
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from ...services.usage_hw1.service import UsageService

router=APIRouter(prefix="/api/homework/usage",tags=["Isolated usage reports"])

class WeeklyRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    period_start:date
    building_ids:list[str]|None=None

class MonthlyRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    period_start:date
    building_ids:list[str]|None=None

@router.post("/reports/weekly")
async def generate(body:WeeklyRequest,request:Request):
    if not getattr(request.app.state,"hw1_usage_enabled",False): raise HTTPException(503,"Usage reports not enabled")
    try: return await UsageService().weekly_report(request.state.test_principal,body.period_start,request.state.request_id,body.building_ids)
    except SQLAlchemyError: raise HTTPException(503,"Usage store unavailable; no report claimed") from None

@router.post("/reports/monthly")
async def generate_monthly(body:MonthlyRequest,request:Request):
    if not getattr(request.app.state,"hw1_usage_enabled",False): raise HTTPException(503,"Usage reports not enabled")
    try: return await UsageService().monthly_report(request.state.test_principal,body.period_start,request.state.request_id,body.building_ids)
    except SQLAlchemyError: raise HTTPException(503,"Usage store unavailable; no report claimed") from None

@router.get("/reports/{identifier}")
async def retrieve(identifier:str,request:Request):
    if not getattr(request.app.state,"hw1_usage_enabled",False): raise HTTPException(503,"Usage reports not enabled")
    try: return await UsageService().retrieve(request.state.test_principal,identifier)
    except SQLAlchemyError: raise HTTPException(503,"Usage store unavailable; no report claimed") from None


@router.get('/tools')
async def tools(request:Request):
    if not getattr(request.app.state,'hw1_usage_enabled',False):raise HTTPException(503,'Usage reports not enabled')
    from ...services.usage_hw1.contracts import MODELS
    return {'tools':sorted(MODELS)}

@router.post('/tools/execute')
async def execute(body:dict,request:Request):
    if not getattr(request.app.state,'hw1_usage_enabled',False):raise HTTPException(503,'Usage reports not enabled')
    from ...services.usage_hw1.tools import execute
    return await execute(request.state.test_principal,body,request.state.request_id)
