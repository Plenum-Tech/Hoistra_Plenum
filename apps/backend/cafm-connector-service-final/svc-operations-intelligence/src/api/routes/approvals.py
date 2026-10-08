"""Unified Approvals queue across Features A / B / C."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...shared import approvals as approvals_svc
from ...engines.auth import access
from .auth import scope
from ..schemas.compliance import QueueDecisionRequest

router = APIRouter(prefix="/api/approvals", tags=["approvals"],
                   # Every route here needs a signed-in caller, and a company named in
                   # the query string must be the caller's own (or the caller a
                   # superadmin). Before this, every endpoint was open and tenancy
                   # was whatever organization_id the client chose to send.
                   dependencies=[Depends(scope)])


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
    s: access.Scope = Depends(scope),
):
    """
    Unified Approvals rail — all Phase 2 sources (A Compliance, B Contract, C Energy)
    unless source_feature is set.
    """
    organization_id = access.organization_for(s, organization_id)
    items = await approvals_svc.list_queue(
        session,
        organization_id=organization_id,
        status=status,
        source_feature=source_feature,
        limit=limit,
        scope=s,
    )
    return {
        "ok": True,
        "count": len(items),
        "items": [approvals_svc.queue_item_to_dict(i) for i in items],
    }


@router.get("/sent-emails")
async def sent_emails(
    subject: str = Query(..., min_length=3, max_length=500),
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
) -> dict[str, Any]:
    """Whether this company already sent this request, and when - so the dock offers a
    reminder instead of a fresh copy. Matched on the subject a draft is given (and its
    "Reminder: " copies); the body is never returned - the log holds what went out."""
    org_id = access.organization_for(s, organization_id)
    return await approvals_svc.sent_email_history(session, subject=subject, organization_id=org_id)


@router.get("/vendor-contact")
async def vendor_contact(
    vendor_id: str = Query(..., min_length=1, max_length=100),
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
) -> dict[str, Any]:
    """Where a draft to this vendor may be sent: its primary contact, or its only one. Several
    and none marked primary is no address, with the candidates for the reader to choose from -
    never a guess, because the Decision queue's drafts are sent for real. Reads only.

    404 for a vendor outside the caller's company (or, for a user restricted to some buildings,
    one with no footprint on them); 503 when the vendor record could not be read just now."""
    org_id = access.organization_for(s, organization_id)
    out = await approvals_svc.vendor_contact(
        session, vendor_id=vendor_id, organization_id=org_id, building_ids=s.building_ids,
        is_superadmin=s.is_superadmin,
    )
    if not out.get("ok"):
        unreadable = out.get("reason") == "unreadable"
        raise HTTPException(status_code=503 if unreadable else 404, detail={
            "ok": False,
            "error": ("The vendor record could not be read just now." if unreadable
                      else "No such vendor in your company."),
            "reason": out.get("reason") or "not_found",
        })
    return out


@router.post("/send-email")
async def send_email_draft(
    body: SendEmailDraftRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
) -> dict[str, Any]:
    """Send a compliance alert draft to the PM email entered in the UI."""
    org_id = access.organization_for(s, body.organization_id)
    return await approvals_svc.send_approval_email_draft(
        session,
        to_address=body.to,
        subject=body.subject,
        body=body.body,
        cc_address=body.cc,
        queue_item_id=body.queue_item_id,
        organization_id=org_id,
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
