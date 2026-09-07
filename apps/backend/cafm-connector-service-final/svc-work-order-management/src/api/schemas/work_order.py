from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from typing import Any, Dict, Literal, Optional
from datetime import datetime

# ── Enum constants ────────────────────────────────────────────────────────────
Priority     = Literal["low", "medium", "high", "urgent", "critical"]
RequestType  = Literal["repair", "maintenance", "inspection", "installation"]
Source       = Literal["email", "ppm", "manual", "tenant", "internal", "remediation"]
WOStatus     = Literal[
    "pending_approval", "preparing", "prepared",
    "active", "completed", "closed",
]


# ── Request models ────────────────────────────────────────────────────────────

class WorkOrderCreate(BaseModel):
    source:            Source
    asset:             str
    location:          str
    issue_description: str
    priority:          Priority    = "medium"
    request_type:      RequestType = "repair"
    requester_name:    str
    requester_email:   EmailStr
    requester_phone:   Optional[str] = None

    @field_validator("asset", "location", "issue_description", "requester_name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must not be blank")
        return v.strip()


class WorkOrderUpdate(BaseModel):
    vendor:               Optional[str]   = None
    scheduled_date:       Optional[str]   = None
    scheduled_time:       Optional[str]   = None
    estimated_duration:   Optional[float] = None
    inspection_required:  Optional[bool]  = None
    special_requirements: Optional[str]   = None
    cmms_work_order_id:   Optional[str]   = None

    @field_validator("estimated_duration")
    @classmethod
    def positive_duration(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("estimated_duration must be positive")
        return v


class StatusUpdate(BaseModel):
    new_status: WOStatus
    notes:      Optional[str] = None


# ── Response models ───────────────────────────────────────────────────────────

class WorkOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_order_id:      str
    # Legacy plenum_cafm.work_orders rows may have NULL source/priority when not
    # created through this service — keep optional so list/detail responses validate.
    source:             Optional[str]      = None
    status:             Optional[str]      = None
    priority:           Optional[str]      = None
    asset:              Optional[str]      = None
    location:           Optional[str]      = None
    issue_description:  Optional[str]      = None
    request_type:       Optional[str]      = None
    requester_name:     Optional[str]      = None
    requester_email:    Optional[str]      = None
    vendor:             Optional[str]      = None
    scheduled_date:     Optional[str]      = None
    scheduled_time:     Optional[str]      = None
    cmms_work_order_id: Optional[str]      = None
    journey_log_id:     Optional[str]      = None
    created_at:         Optional[datetime] = None
    approved_at:        Optional[datetime] = None
    prepared_at:        Optional[datetime] = None


class WorkOrderCreateResponse(WorkOrderResponse):
    """Create response includes dynamic approval suggestion for the new WO."""

    approval_suggestion: Optional[Dict[str, Any]] = None
    auto_suggestion: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class ErrorDetail(BaseModel):
    """Standardised error envelope returned on 4xx / 5xx."""
    code:    str
    message: str
    field:   Optional[str] = None   # populated for validation errors


class ErrorResponse(BaseModel):
    """Top-level error wrapper — always returned as JSON."""
    success: bool  = False
    errors:  list[ErrorDetail] = []
