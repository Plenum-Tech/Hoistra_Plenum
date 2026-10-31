"""One authorization/audit boundary for API and model-selected Compliance tools."""
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from .authorization import authorize_tool, allowed_tools, authorize_row
from .contracts import Query, ProposalInput, Confirmation, REVIEWERS, QUERY_KEYS
from .reads import read
from .actions import prepare, confirm, get_proposal, process_events
from .store import runtime_store
from .outbox import now

class ComplianceService:
    def __init__(self,store):
        self.store=store

    async def execute(self,principal,tool,arguments,request_id=None):
        request_id=request_id or str(uuid4())
        try:
            authorize_tool(principal,tool)
            if tool!='compliance_prepare_proposal' and set(arguments)-QUERY_KEYS[tool]:
                raise HTTPException(422,'Unsupported arguments for this declared operation')
            parsed=ProposalInput.model_validate(arguments) if tool=='compliance_prepare_proposal' else Query.model_validate(arguments)
            await self.store.audit(principal,tool,'capability_authorized',request_id=request_id)
            async with self.store.transaction(principal,repeatable=tool!='compliance_prepare_proposal') as repo:
                if tool=='compliance_prepare_proposal':
                    result=await prepare(repo,principal,parsed)
                elif tool=='compliance_get_proposal':
                    if not parsed.entity_id:
                        raise HTTPException(422,'Proposal entity_id required')
                    result=await get_proposal(repo,principal,parsed.entity_id)
                else:
                    result=await read(repo,principal,tool,parsed)
                await repo.audit(tool,'succeeded',request_id=request_id)
            return {'tool':tool,'result':result,'request_id':request_id,'policy_version':'hw1-compliance-v1'}
        except ValidationError:
            await self.store.audit(principal,tool,'invalid_arguments',request_id=request_id)
            raise HTTPException(422,'Invalid declared Compliance arguments') from None
        except HTTPException as exc:
            await self.store.audit(principal,tool,'denied_or_unavailable_'+str(exc.status_code),request_id=request_id)
            raise
        except SQLAlchemyError:
            # If the store/audit is unavailable no result can be safely disclosed.
            raise HTTPException(503,'Compliance store unavailable; no success claimed') from None

    async def confirm(self,principal,identifier,body,request_id=None):
        request_id=request_id or str(uuid4())
        try:
            parsed=Confirmation.model_validate(body)
            await self.store.audit(principal,'confirm','requested',{'proposal_id':identifier},request_id)
            async with self.store.transaction(principal) as repo:
                result=await confirm(repo,principal,identifier,parsed)
            try:
                result['follow_through']=await process_events(self.store,principal)
            except Exception:
                # Primary state has committed; report separate failed follow-through truthfully.
                await self.store.audit(principal,'event_follow_through','failed',{'proposal_id':identifier},request_id)
                result['follow_through']={'status':'failed','recovery':'authorized event drain required'}
            return result
        except ValidationError:
            raise HTTPException(422,'Invalid confirmation contract') from None
        except HTTPException as exc:
            await self.store.audit(principal,'confirm','denied_or_conflict_'+str(exc.status_code),{'proposal_id':identifier},request_id)
            raise
        except SQLAlchemyError:
            raise HTTPException(503,'Compliance transaction unavailable; no success claimed') from None


def runtime_service():
    return ComplianceService(runtime_store())
