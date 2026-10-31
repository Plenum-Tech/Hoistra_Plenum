"""Read-only identity and scope checks; these do not execute business actions."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..homework_identity import check_building_scope

router = APIRouter(prefix="/api/homework", tags=["Homework test identity"])


class ScopeCheck(BaseModel):
    # Reject identity overrides instead of silently accepting misleading client fields.
    model_config = ConfigDict(extra="forbid")
    building_ids: list[str] = Field(min_length=1, max_length=100)
    purpose: str = "building_read"


@router.get("/identity")
async def identity(request: Request):
    principal = request.state.test_principal
    return {"user_id": principal.user_id, "role": principal.role,
            "organization_id": principal.organization_id,
            "building_ids": list(principal.building_ids), "identity_source": "server_test_session"}


@router.post("/scope-check")
async def scope_check(body: ScopeCheck, request: Request):
    principal = request.state.test_principal
    check_building_scope(principal, body.building_ids, body.purpose)
    return {"allowed": True, "user_id": principal.user_id,
            "building_ids": body.building_ids, "purpose": body.purpose,
            "business_action_executed": False}
