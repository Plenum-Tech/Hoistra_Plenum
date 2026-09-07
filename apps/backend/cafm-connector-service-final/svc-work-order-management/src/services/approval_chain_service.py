"""Persist dynamic approval chains to wo_approval_requests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.approval import ApprovalRequest
from ..services.dynamic_approval_engine import DynamicApprovalEngine
from ..services.notification_service import NotificationService
from ..integrations.outlook_connector import OutlookConnector
from ..config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


async def suggest_approval_for_work_order(
    session: AsyncSession,
    work_order: Dict[str, Any],
    *,
    persist_suggestion: bool = True,
) -> Dict[str, Any]:
    engine = DynamicApprovalEngine(aimms_api_url=settings.aimms_api_url)
    return await engine.suggest_chain(
        session,
        work_order,
        persist=persist_suggestion and bool(work_order.get("work_order_id")),
    )


def work_order_payload_from_model(wo: Any) -> Dict[str, Any]:
    """Build engine payload from a persisted WorkOrder ORM row."""
    asset = getattr(wo, "asset", None) or ""
    return {
        "work_order_id": wo.work_order_id,
        "priority": wo.priority,
        "work_type": wo.request_type,
        "request_type": wo.request_type,
        "location": wo.location,
        "asset": asset,
        "asset_category": getattr(wo, "asset_category", None)
        or (asset.split()[0] if asset else "general"),
        "estimated_cost": float(getattr(wo, "estimated_cost", None) or 0),
        "issue_description": wo.issue_description,
        "title": wo.title,
    }


async def approval_suggestion_after_create(
    session: AsyncSession,
    wo: Any,
) -> Dict[str, Any]:
    """Run dynamic approval engine immediately after WO creation (preview only)."""
    return await suggest_approval_for_work_order(
        session,
        work_order_payload_from_model(wo),
        persist_suggestion=False,
    )


async def _resolve_approver_for_notification(
    session: AsyncSession,
    approval_row: Dict[str, Any],
) -> Dict[str, str]:
    """Map wo_approval_requests.approver (email) to display name from plenum_cafm.users."""
    email_hint = (
        approval_row.get("approver_email")
        or approval_row.get("approver")
        or ""
    ).strip()
    name_hint = (approval_row.get("approver_name") or "").strip()

    if email_hint and "@" not in email_hint and name_hint:
        email_hint, name_hint = name_hint, email_hint

    if email_hint and "@" in email_hint:
        row = await session.execute(
            text("""
                SELECT full_name, email, role
                FROM plenum_cafm.users
                WHERE LOWER(email) = LOWER(:email)
                LIMIT 1
            """),
            {"email": email_hint},
        )
        found = row.fetchone()
        if found:
            return {
                "approver_email": found.email or email_hint,
                "approver_name": found.full_name or name_hint or found.email,
                "approver_role": found.role or "",
            }

    if name_hint and not email_hint:
        row = await session.execute(
            text("""
                SELECT full_name, email, role
                FROM plenum_cafm.users
                WHERE LOWER(full_name) = LOWER(:name)
                   OR LOWER(email) = LOWER(:name)
                LIMIT 1
            """),
            {"name": name_hint},
        )
        found = row.fetchone()
        if found:
            return {
                "approver_email": found.email or email_hint,
                "approver_name": found.full_name or name_hint,
                "approver_role": found.role or "",
            }

    if email_hint and "@" in email_hint:
        local = email_hint.split("@", 1)[0].replace(".", " ").replace("_", " ")
        display = name_hint or local.title()
        return {
            "approver_email": email_hint,
            "approver_name": display,
            "approver_role": approval_row.get("approver_role") or "",
        }

    return {
        "approver_email": email_hint or "—",
        "approver_name": name_hint or email_hint or "Approver",
        "approver_role": approval_row.get("approver_role") or "",
    }


async def _load_existing_approval_chain(
    session: AsyncSession,
    work_order_id: str,
) -> List[Dict[str, Any]]:
    caps = await DynamicApprovalEngine._capabilities(session)
    order_sql = DynamicApprovalEngine._approval_step_order_sql(caps, alias="ar")
    optional: list[str] = []
    if caps.get("ar_step_order"):
        optional.append("ar.step_order")
    if caps.get("ar_level"):
        optional.append("ar.level")
    if caps.get("ar_unblocked_at"):
        optional.append("ar.unblocked_at")
    opt_sql = ", ".join(optional) + "," if optional else ""
    rows = await session.execute(
        text(f"""
            SELECT ar.request_id, ar.approver, ar.status, {opt_sql}
                   ar.requested_at, ar.responded_at,
                   u.full_name AS approver_name, u.email AS approver_email, u.role AS approver_role
            FROM plenum_cafm.wo_approval_requests ar
            LEFT JOIN plenum_cafm.users u
              ON LOWER(u.email) = LOWER(ar.approver)
            WHERE ar.work_order_id = :wo_id
            ORDER BY {order_sql} ASC
        """),
        {"wo_id": work_order_id},
    )
    out: List[Dict[str, Any]] = []
    for r in rows.fetchall():
        m = dict(r._mapping)
        step = m.get("step_order") or m.get("level") or len(out) + 1
        approver_email = m.get("approver_email") or m.get("approver")
        approver_name = m.get("approver_name") or m.get("approver")
        out.append({
            "request_id": m["request_id"],
            "work_order_id": work_order_id,
            "approver": approver_email,
            "approver_email": approver_email,
            "approver_name": approver_name,
            "approver_role": m.get("approver_role"),
            "step": int(step),
            "status": m.get("status"),
            "unblocked_at": (
                m["unblocked_at"].isoformat()
                if m.get("unblocked_at") is not None
                else None
            ),
        })
    return out


async def create_approval_requests_from_suggestion(
    session: AsyncSession,
    work_order_id: str,
    approval_type: str,
    suggestion: Dict[str, Any],
    *,
    notify_first: bool = True,
    work_order_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write one wo_approval_requests row per chain step; notify step 1 only."""
    chain: List[Dict[str, Any]] = suggestion.get("chain") or []
    if not chain:
        log.warning("approval_chain.empty", work_order_id=work_order_id)
        return {
            "success": False,
            "work_order_id": work_order_id,
            "message": "No approvers resolved for this work order.",
            "approval_requests": [],
        }

    existing = await _load_existing_approval_chain(session, work_order_id)
    if existing:
        chain_str = " → ".join(
            f"{s.get('name', '?')} ({s.get('role', '?')})" for s in chain
        )
        log.info(
            "approval_chain.already_exists",
            work_order_id=work_order_id,
            steps=len(existing),
        )
        auto = suggestion.get("auto_suggestion") or {}
        notified = False
        if notify_first and work_order_context and existing:
            notified = await _notify_step(session, existing[0], work_order_context)
        notify_note = (
            " Approval notification email re-sent to step 1."
            if notified
            else ""
        )
        return {
            "success": True,
            "already_exists": True,
            "work_order_id": work_order_id,
            "chain": chain_str,
            "chain_detail": chain,
            "confidence": suggestion.get("confidence"),
            "reason": suggestion.get("reason"),
            "match_score": suggestion.get("match_score"),
            "risk_score": suggestion.get("risk_score"),
            "auto_suggestion": auto,
            "approval_requests": existing,
            "request_id": existing[0]["request_id"],
            "approver": existing[0]["approver"],
            "status": existing[0]["status"],
            "email_sent": notified,
            "message": (
                f"Approval chain is already active for {work_order_id} "
                f"({len(existing)} step(s)). Step 1 approver: {existing[0]['approver']}."
                f"{notify_note}"
            ),
        }

    now = datetime.now(timezone.utc)
    approval_requests: List[Dict[str, Any]] = []
    caps = await DynamicApprovalEngine._capabilities(session)

    for step in chain:
        step_num = int(step.get("step") or 1)
        is_first = step_num == 1
        req_id = f"APR-{work_order_id}-L{step_num}"
        approver_email = (step.get("email") or "").strip()
        approver_name = (step.get("name") or "").strip()
        approver = approver_email or approver_name or str(step.get("user_id") or "")

        row: Dict[str, Any] = {
            "request_id": req_id,
            "work_order_id": work_order_id,
            "approval_type": approval_type,
            "approver": approver,
            "status": "pending",
        }
        if caps.get("ar_level"):
            row["level"] = step_num
        if caps.get("ar_step_order"):
            row["step_order"] = step_num
        if caps.get("ar_risk_score"):
            row["risk_score"] = suggestion.get("risk_score")
        if caps.get("ar_match_score"):
            row["match_score"] = suggestion.get("match_score")
        if caps.get("ar_suggestion_source"):
            row["suggestion_source"] = suggestion.get("source")
        if caps.get("ar_unblocked_at"):
            row["unblocked_at"] = now if is_first else None

        await session.execute(insert(ApprovalRequest.__table__).values(**row))
        approval_requests.append({
            "request_id": req_id,
            "work_order_id": work_order_id,
            "approver": approver,
            "approver_email": approver_email or approver,
            "approver_name": approver_name,
            "approver_role": step.get("role"),
            "step": step_num,
            "status": "pending",
            "unblocked_at": now.isoformat() if is_first else None,
        })

    await session.flush()

    if caps.get("suggestions"):
        await session.execute(
            text("""
                UPDATE plenum_cafm.wo_approval_suggestions
                SET accepted = TRUE
                WHERE suggestion_id = (
                    SELECT suggestion_id FROM plenum_cafm.wo_approval_suggestions
                    WHERE work_order_id = :wo_id
                    ORDER BY created_at DESC
                    LIMIT 1
                )
            """),
            {"wo_id": work_order_id},
        )

    email_sent = False
    if notify_first and work_order_context:
        email_sent = await _notify_step(session, approval_requests[0], work_order_context)

    chain_str = " → ".join(
        f"{s.get('name', '?')} ({s.get('role', '?')})" for s in chain
    )

    log.info(
        "approval_chain.created",
        work_order_id=work_order_id,
        steps=len(chain),
        confidence=suggestion.get("confidence"),
        source=suggestion.get("source"),
        top3=DynamicApprovalEngine._format_top3_log(chain),
    )

    auto = suggestion.get("auto_suggestion") or {}
    return {
        "success": True,
        "work_order_id": work_order_id,
        "chain": chain_str,
        "chain_detail": chain,
        "confidence": suggestion.get("confidence"),
        "reason": suggestion.get("reason"),
        "match_score": suggestion.get("match_score"),
        "risk_score": suggestion.get("risk_score"),
        "previous_approval_processes": suggestion.get("previous_approval_processes"),
        "auto_suggestion": auto,
        "approval_requests": approval_requests,
        "request_id": approval_requests[0]["request_id"] if approval_requests else None,
        "approver": approval_requests[0]["approver"] if approval_requests else None,
        "status": "pending",
        "email_sent": email_sent,
        "message": auto.get("message")
        or (
            f"Approval chain ({len(chain)} steps, {suggestion.get('confidence')} confidence): "
            f"{chain_str}. "
            + (
                "Step 1 approval email sent via Outlook."
                if email_sent
                else "Step 1 saved; configure Outlook to send approval emails."
            )
        ),
    }


async def _notify_step(
    session: AsyncSession,
    approval_row: Dict[str, Any],
    work_order: Dict[str, Any],
) -> bool:
    """Send step approval request via NotificationService / Outlook. Returns True if sent."""
    if not (
        settings.azure_tenant_id
        and settings.azure_client_id
        and settings.azure_client_secret
        and settings.outlook_user_email
    ):
        log.warning(
            "approval_chain.notify_skipped",
            reason="outlook_not_configured",
            work_order_id=work_order.get("work_order_id"),
        )
        return False
    try:
        connector = OutlookConnector(
            tenant_id=settings.azure_tenant_id,
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret,
            user_email=settings.outlook_user_email,
        )
        notifier = NotificationService(connector)
        identity = await _resolve_approver_for_notification(session, approval_row)
        await notifier.send_approval_request(
            work_order_id=work_order.get("work_order_id") or "",
            approver_email=identity["approver_email"],
            approver_name=identity["approver_name"],
            approver_role=identity.get("approver_role") or "",
            asset=work_order.get("asset") or "—",
            location=work_order.get("location") or "—",
            priority=work_order.get("priority") or "medium",
            issue_description=work_order.get("issue_description") or work_order.get("title") or "—",
        )
        log.info(
            "approval_chain.notify_sent",
            work_order_id=work_order.get("work_order_id"),
            approver_email=identity["approver_email"],
            approver_name=identity["approver_name"],
            step=approval_row.get("step"),
        )
        return True
    except Exception as exc:
        log.warning("approval_chain.notify_failed", error=str(exc), exc_info=True)
        return False


async def send_approval_step_email(
    session: AsyncSession,
    work_order_id: str,
    *,
    step_order: int = 1,
) -> Dict[str, Any]:
    """Send approval request email for one chain step (used by agent tool + API)."""
    from ..models.work_order import WorkOrder
    from sqlalchemy import select

    wo_result = await session.execute(
        select(WorkOrder).where(WorkOrder.work_order_id == work_order_id)
    )
    wo = wo_result.scalar_one_or_none()
    if not wo:
        return {
            "success": False,
            "work_order_id": work_order_id,
            "message": f"Work order {work_order_id} not found.",
            "email_sent": False,
        }

    existing = await _load_existing_approval_chain(session, work_order_id)
    if not existing:
        return {
            "success": False,
            "work_order_id": work_order_id,
            "message": (
                "No approval chain found. Call request_approval first, "
                "then send_approval_request_email."
            ),
            "email_sent": False,
        }

    step_row = next((r for r in existing if r.get("step") == step_order), None)
    if not step_row:
        step_row = existing[0]

    wo_context = work_order_payload_from_model(wo)
    email_sent = await _notify_step(session, step_row, wo_context)
    identity = await _resolve_approver_for_notification(session, step_row)
    return {
        "success": email_sent,
        "work_order_id": work_order_id,
        "step": step_row.get("step"),
        "approver": identity["approver_email"],
        "approver_name": identity["approver_name"],
        "request_id": step_row.get("request_id"),
        "email_sent": email_sent,
        "message": (
            f"Approval email sent to {identity['approver_name']} "
            f"({identity['approver_email']}) for {work_order_id} (step {step_row.get('step')})."
            if email_sent
            else (
                f"Could not send approval email for {work_order_id}. "
                "Check Azure/Outlook settings (AZURE_TENANT_ID, AZURE_CLIENT_ID, "
                "AZURE_CLIENT_SECRET, OUTLOOK_USER_EMAIL)."
            )
        ),
    }
