"""Model tools can draft and read; only explicit human API calls may commit."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Preview(Contract):
    building_id:str=Field(min_length=1,max_length=160)
    entity_id:str=Field(min_length=1,max_length=160)
    purpose:Literal['team_notification','work_order_request','contract_review','energy_review','certificate_renewal','inspection_booking']
    vendor_id:str|None=Field(default=None,min_length=1,max_length=160)
    contact_ids:list[str]=Field(min_length=1,max_length=20)
    subject:str=Field(min_length=1,max_length=250)
    body:str=Field(min_length=1,max_length=12000)
    source_ids:list[str]=Field(default_factory=list,max_length=20)

class Query(Contract):
    building_id:str=Field(min_length=1,max_length=160)
    limit:int=Field(default=100,ge=1,le=100)
    offset:int=Field(default=0,ge=0,le=1000000)
    after_id: str | None = Field(default=None, min_length=1, max_length=160)
    include_issues: bool = True

    @model_validator(mode='after')
    def cursor_or_offset(self):
        if self.after_id is not None and self.offset:raise ValueError('Use cursor or offset, not both')
        return self

class Confirmation(Contract):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key:str=Field(min_length=8,max_length=160)
    decision:Literal['approve','reject']='approve'

class Recovery(Contract):
    reason:str=Field(min_length=8,max_length=1000)

class ToolRequest(Contract):
    tool:str=Field(min_length=1,max_length=100)
    arguments:dict=Field(default_factory=dict)

MODELS={'communications_preview':Preview,'communications_contacts':Query,
        'communications_outbox':Query,'communications_hoi':Query}
ROLE_TOOLS={role:sorted(MODELS if role in {'property_manager','portfolio_manager'} else
    set(MODELS)-{'communications_hoi'}) for role in
    ['general_user','property_manager','portfolio_manager','compliance_officer']}
