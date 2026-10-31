"""API/model parity for manual configured report triggers and owned reads."""
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from .contracts import MODELS
from .service import UsageService
async def execute(p,body,request_id=None,service=None):
    if set(body)!={'tool','arguments'} or body['tool'] not in MODELS:raise HTTPException(403,'Usage tool denied')
    name=body['tool']
    try:q=MODELS[name].model_validate(body['arguments'])
    except ValidationError:raise HTTPException(422,'Invalid declared usage arguments') from None
    instance=service or UsageService()
    try:
        if name=='usage_get_report':result=await instance.retrieve(p,q.report_id)
        elif name=='usage_weekly_report':result=await instance.weekly_report(p,q.period_start,request_id,q.building_ids)
        else:result=await instance.monthly_report(p,q.period_start,request_id,q.building_ids)
        return {'tool':name,'result':result}
    except SQLAlchemyError:raise HTTPException(503,'Usage store unavailable; no report claimed') from None
