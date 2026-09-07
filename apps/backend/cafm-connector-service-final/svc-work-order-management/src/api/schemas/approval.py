from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ApprovalRequest(BaseModel):
    request_id: str
    work_order_id: str
    approval_type: str
    approver: str
    status: str
    requested_at: datetime


class ApprovalResponse(BaseModel):
    approved: bool
    notes: Optional[str] = None
