"""Email → platform deep-link tokens for Approvals (A2 ladder)."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import ApprovalActionToken, ApprovalsQueueItem
from ...shared.approvals import decide_queue_item, queue_item_to_dict


def _platform_approval_url(
    *,
    token: str,
    queue_item_id: UUID,
    action: str,
    ladder_status: str | None = None,
) -> str:
    """
    Deep-link into Orchestrator Compliance space so the PM lands in-platform
    and can Approve booking / Acknowledge (not a raw API JSON response).
    """
    base = (settings.frontend_public_url or "http://localhost:3000").rstrip("/")
    q = {
        "space": "compliance",
        "approvalToken": token,
        "approvalItem": str(queue_item_id),
        "approvalAction": action,
    }
    if ladder_status:
        q["ladderStatus"] = ladder_status
    return f"{base}/ai?{urlencode(q)}"


async def create_approval_token(
    session: AsyncSession,
    queue_item_id: UUID,
    *,
    action: str = "approve",
    ladder_status: str | None = None,
    ttl_hours: int | None = None,
) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    ttl = ttl_hours if ttl_hours is not None else settings.approval_token_ttl_hours
    row = ApprovalActionToken(
        id=uuid4(),
        queue_item_id=queue_item_id,
        token=token,
        action=action,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl),
    )
    session.add(row)
    await session.flush()
    platform_url = _platform_approval_url(
        token=token,
        queue_item_id=queue_item_id,
        action=action,
        ladder_status=ladder_status,
    )
    # Legacy API path — redirects browser to platform; ?redeem=1 still auto-decides
    api_url = (
        f"{settings.public_base_url.rstrip('/')}/api/compliance/approvals/one-click/{token}"
    )
    return {
        "token": token,
        "one_click_url": platform_url,
        "api_url": api_url,
        "expires_at": row.expires_at.isoformat(),
        "action": action,
        "ladder_status": ladder_status,
    }


async def peek_approval_token(session: AsyncSession, token: str) -> dict[str, Any]:
    """Return queue item + token meta without redeeming (for platform UI)."""
    row = (
        await session.execute(
            select(ApprovalActionToken).where(ApprovalActionToken.token == token)
        )
    ).scalar_one_or_none()
    if not row:
        return {"ok": False, "error": "invalid_token"}
    if row.used_at:
        return {"ok": False, "error": "token_already_used"}
    if row.expires_at < datetime.now(timezone.utc):
        return {"ok": False, "error": "token_expired"}

    item = await session.get(ApprovalsQueueItem, row.queue_item_id)
    if not item:
        return {"ok": False, "error": "queue_item_not_found"}

    payload = item.payload or {}
    status = payload.get("status") or (item.email_draft or {}).get("status")
    return {
        "ok": True,
        "token": token,
        "action": row.action,
        "expires_at": row.expires_at.isoformat(),
        "queue_item_id": str(item.id),
        "ladder_status": status,
        "item": queue_item_to_dict(item),
        "cta": _cta_for_status(str(status or ""), row.action),
    }


def _cta_for_status(status: str, action: str) -> dict[str, str]:
    """Map PRD ladder row → button labels shown in platform."""
    s = status.strip()
    if s in {"Due for Renewal", "Overdue", "Critical"}:
        return {
            "primary": "Approve booking",
            "secondary": "Dismiss",
            "title": f"{s} — booking request",
            "help": "PM approves the booking request from this platform page "
            "(same action as one-click from email).",
        }
    if s == "Lapsed":
        return {
            "primary": "Acknowledge",
            "secondary": "Dismiss",
            "title": "Lapsed — acknowledge & initiate renewal",
            "help": "Acknowledge the lapsed certificate and confirm renewal is initiated.",
        }
    if s == "Expiring Soon":
        return {
            "primary": "Acknowledge",
            "secondary": "Dismiss",
            "title": "Expiring Soon — informational",
            "help": "No booking required — acknowledge for PM awareness.",
        }
    primary = "Acknowledge" if action == "acknowledge" else "Approve"
    return {
        "primary": primary,
        "secondary": "Dismiss",
        "title": "Compliance approval",
        "help": "Review and decide this Approvals queue item.",
    }


async def redeem_approval_token(
    session: AsyncSession,
    token: str,
    *,
    decision: str | None = None,
    pm_notes: str | None = None,
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(ApprovalActionToken).where(ApprovalActionToken.token == token)
        )
    ).scalar_one_or_none()
    if not row:
        return {"ok": False, "error": "invalid_token"}
    if row.used_at:
        return {"ok": False, "error": "token_already_used"}
    if row.expires_at < datetime.now(timezone.utc):
        return {"ok": False, "error": "token_expired"}

    decided = decision or row.action
    # Map acknowledge → approve on the queue item
    if decided in {"acknowledge", "ack"}:
        decided = "approve"
    if decided not in {"approve", "dismiss", "edit"}:
        decided = "approve"

    notes = pm_notes
    if not notes:
        if (decision or row.action) in {"acknowledge", "ack"}:
            notes = "Acknowledged via platform email deep-link"
        else:
            notes = "Approved via platform email deep-link"
    result = await decide_queue_item(
        session,
        row.queue_item_id,
        decision=decided,
        pm_notes=notes,
        prepare_email_handoff=True,
    )
    row.used_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True, "token_redeemed": True, **result}
