"""Unified Approvals queue across Features A / B / C."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...shared import approvals as approvals_svc
from ..schemas.compliance import QueueDecisionRequest

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class SendEmailDraftRequest(BaseModel):
    to: str = Field(..., description="PM email address")
    subject: str
    body: str
    cc: str | None = None
    queue_item_id: UUID | None = None
    organization_id: UUID | None = None


@router.get("")
async def list_all_approvals(
    status: str | None = "pending",
    source_feature: str | None = None,
    organization_id: UUID | None = None,
    limit: int = Query(150, le=500),
    session: AsyncSession = Depends(get_session),
):
    """
    Unified Approvals rail — all Phase 2 sources (A Compliance, B Contract, C Energy)
    unless source_feature is set.
    """
    items = await approvals_svc.list_queue(
        session,
        organization_id=organization_id,
        status=status,
        source_feature=source_feature,
        limit=limit,
    )
    return {
        "ok": True,
        "count": len(items),
        "items": [approvals_svc.queue_item_to_dict(i) for i in items],
    }


@router.post("/send-email")
async def send_email_draft(
    body: SendEmailDraftRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Send a compliance alert draft to the PM email entered in the UI."""
    return await approvals_svc.send_approval_email_draft(
        session,
        to_address=body.to,
        subject=body.subject,
        body=body.body,
        cc_address=body.cc,
        queue_item_id=body.queue_item_id,
        organization_id=body.organization_id,
    )


@router.post("/{item_id}/decide")
async def decide_any_approval(
    item_id: UUID,
    body: QueueDecisionRequest,
    session: AsyncSession = Depends(get_session),
):
    """Approve | Edit | Dismiss — same contract for A/B/C."""
    return await approvals_svc.decide_queue_item(
        session,
        item_id,
        decision=body.decision,
        pm_notes=body.pm_notes,
        edited_payload=body.edited_payload,
        decided_by=body.decided_by,
        prepare_email_handoff=body.prepare_email_handoff,
    )
