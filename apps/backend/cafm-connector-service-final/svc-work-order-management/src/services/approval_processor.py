"""
Approval processing pipeline for inbound email replies.

Flow:
  1. Detect WO ID in email subject  (regex WO-\\d+)
  2. Match pending wo_approval_requests row for sender email (multi-step chain)
  3. GPT classifies intent: approved | rejected | unclear
  4. approved → ApprovalWorkflowService (step N → notify step N+1, or finalize on last step)
  5. rejected → reject chain via ApprovalWorkflowService
  6. unclear  → log and skip (email stays unread for manual review)
"""
import re
import json
import asyncio
from typing import Any, Dict, Optional

from openai import OpenAI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_workflow import ApprovalWorkflowService
from .ppm_service import check_ppm_for_asset
from .technician_service import find_best_technician
from ..intelligence.smart_scheduler import SmartScheduler
from .journey_service import advance_journey_milestone, record_status_change
from ..models.work_order import WorkOrder
from ..models.journey_log import JourneyLog
from ..config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

_WO_ID_RE = re.compile(r"WO-\d+", re.IGNORECASE)
_EMAIL_IN_ANGLE = re.compile(r"<([^>]+)>")


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_wo_id(subject: str) -> Optional[str]:
    m = _WO_ID_RE.search(subject or "")
    return m.group(0).upper() if m else None


def extract_sender_email(email: Dict[str, Any]) -> str:
    raw = (email.get("from") or "").strip()
    if "<" in raw and ">" in raw:
        m = _EMAIL_IN_ANGLE.search(raw)
        if m:
            return m.group(1).strip().lower()
    return raw.lower()


def is_approval_reply(email: Dict[str, Any]) -> bool:
    """True when the email subject contains a WO ID — i.e. it's a reply to our notification."""
    return bool(_WO_ID_RE.search(email.get("subject") or ""))


async def _find_actionable_approval_request(
    session: AsyncSession,
    work_order_id: str,
    sender_email: str,
) -> Optional[Dict[str, Any]]:
    """
    Pending approval row for this approver that is allowed to act
    (step 1 always; later steps only after unblocked_at is set).
    """
    row = await session.execute(
        text("""
            SELECT request_id, approver, status,
                   step_order, level, unblocked_at
            FROM plenum_cafm.wo_approval_requests
            WHERE work_order_id = :wo_id
              AND status = 'pending'
              AND LOWER(approver) = LOWER(:email)
              AND (
                    unblocked_at IS NOT NULL
                    OR COALESCE(step_order, level, 1) = 1
                  )
            ORDER BY COALESCE(step_order, level, 1) ASC
            LIMIT 1
        """),
        {"wo_id": work_order_id, "email": sender_email},
    )
    found = row.fetchone()
    if not found:
        return None
    m = dict(found._mapping)
    return {
        "request_id": m["request_id"],
        "approver": m["approver"],
        "step": m.get("step_order") or m.get("level") or 1,
    }


# ── GPT intent detection ───────────────────────────────────────────────────────

async def detect_approval_intent(
    body: str,
    work_order_id: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Dict[str, Any]:
    """
    Use GPT to determine whether the email body approves or rejects the WO.
    Returns {intent: 'approved'|'rejected'|'unclear', confidence: float, notes: str}
    """
    prompt = f"""You are analyzing an email reply to a work order approval request.
Work Order: {work_order_id}

Email body:
{body[:2000]}

Determine if this email:
1. APPROVES the work order — look for: approve, approved, go ahead, yes, proceed, confirmed, \
accepted, authorized, ok, looks good, please proceed, green light
2. REJECTS it — look for: reject, rejected, decline, denied, not approved, hold, cancel, \
do not proceed, on hold, not now, defer
3. Is UNCLEAR — asking questions, ambiguous, or unrelated to the approval

Respond in JSON only, no extra text:
{{"intent": "approved", "confidence": 0.95, "notes": "Approver said go ahead"}}"""

    def _call() -> str:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()

    raw = await asyncio.to_thread(_call)
    log.info("approval_processor.intent_raw", work_order_id=work_order_id, raw=raw[:200])

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    try:
        return json.loads(cleaned)
    except Exception:
        text_lower = raw.lower()
        if any(w in text_lower for w in ("approved", "go ahead", "proceed", "yes")):
            return {"intent": "approved", "confidence": 0.7, "notes": raw}
        if any(w in text_lower for w in ("rejected", "reject", "denied", "decline")):
            return {"intent": "rejected", "confidence": 0.7, "notes": raw}
        return {"intent": "unclear", "confidence": 0.5, "notes": raw}


# ── Main entry point ───────────────────────────────────────────────────────────

async def process_approval_reply(
    email: Dict[str, Any],
    session: AsyncSession,
    notification_service,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Dict[str, Any]:
    """
    Full approval reply pipeline. Called from the email poll when a reply
    to one of our WO notification emails is detected.
    """
    wo_id = extract_wo_id(email.get("subject") or "")
    if not wo_id:
        return {"status": "no_wo_id"}

    result = await session.execute(
        select(WorkOrder).where(WorkOrder.work_order_id == wo_id)
    )
    wo = result.scalar_one_or_none()
    if not wo:
        log.warning("approval_processor.wo_not_found", wo_id=wo_id)
        return {"status": "wo_not_found", "work_order_id": wo_id}

    sender = extract_sender_email(email)
    pending_req = await _find_actionable_approval_request(session, wo_id, sender)

    if wo.status != "pending_approval" and not pending_req:
        log.info(
            "approval_processor.skip_already_processed",
            wo_id=wo_id,
            current_status=wo.status,
        )
        return {"status": "already_processed", "work_order_id": wo_id, "wo_status": wo.status}

    body = email.get("body") or email.get("bodyPreview") or ""
    intent_result = await detect_approval_intent(body, wo_id, api_key, model)
    intent = intent_result.get("intent", "unclear")
    notes = intent_result.get("notes", "")

    log.info(
        "approval_processor.intent",
        wo_id=wo_id,
        intent=intent,
        confidence=intent_result.get("confidence"),
        sender=sender,
        approval_request_id=pending_req.get("request_id") if pending_req else None,
    )

    if not pending_req:
        log.warning(
            "approval_processor.no_pending_request_for_sender",
            wo_id=wo_id,
            sender=sender,
        )
        return {
            "status": "no_pending_approval_for_sender",
            "work_order_id": wo_id,
            "sender": sender,
        }

    svc = ApprovalWorkflowService(aimms_api_url=settings.aimms_api_url)

    if intent == "rejected":
        chain_result = await svc.handle_approval_response(
            pending_req["request_id"],
            approved=False,
            notes=notes,
        )
        if chain_result.get("status") == "rejected":
            await _notify_requester_rejected(wo, email, notes, session, notification_service)
        return {
            "status": "rejected",
            "work_order_id": wo_id,
            "approval_request_id": pending_req["request_id"],
            **chain_result,
        }

    if intent == "approved":
        chain_result = await svc.handle_approval_response(
            pending_req["request_id"],
            approved=True,
            notes=notes,
        )
        status = chain_result.get("status")

        if status == "approved_step_complete":
            log.info(
                "approval_processor.step_complete_next_notified",
                wo_id=wo_id,
                completed_step=pending_req.get("step"),
                next_approver=chain_result.get("next_approver"),
                next_request_id=chain_result.get("next_request_id"),
            )
            return {
                "status": "approved_step_complete",
                "work_order_id": wo_id,
                "approval_request_id": pending_req["request_id"],
                "next_approver": chain_result.get("next_approver"),
                "next_request_id": chain_result.get("next_request_id"),
                "message": (
                    f"Step {pending_req.get('step')} approved. "
                    f"Approval email sent to next approver: {chain_result.get('next_approver')}."
                ),
            }

        if status == "approved":
            return {
                "status": "approved",
                "work_order_id": wo_id,
                "approval_request_id": pending_req["request_id"],
                **chain_result,
            }

        return {
            "status": status or "approval_chain_error",
            "work_order_id": wo_id,
            "chain_result": chain_result,
        }

    log.info("approval_processor.unclear", wo_id=wo_id, notes=notes)
    return {"status": "unclear", "work_order_id": wo_id, "notes": notes}


# ── Final approval (all chain steps done) ─────────────────────────────────────

def _notification_service_optional():
    """Outlook notifier when Graph is configured; otherwise None (scheduling still runs)."""
    if not (
        settings.azure_tenant_id
        and settings.azure_client_id
        and settings.azure_client_secret
        and settings.outlook_user_email
    ):
        return None
    from ..integrations.outlook_connector import OutlookConnector
    from .notification_service import NotificationService

    connector = OutlookConnector(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        client_secret=settings.azure_client_secret,
        user_email=settings.outlook_user_email,
    )
    return NotificationService(connector)


async def apply_final_approval_finalization(
    session: AsyncSession,
    wo: WorkOrder,
    *,
    notes: str | None = None,
    approval_request_id: str | None = None,
    approver_name: str = "Approver",
) -> Dict[str, Any]:
    """
    Assign technician + schedule after the last approval step.
    Used by API respond and inbound email approval flows.
    """
    notifier = _notification_service_optional()
    email_ctx = {"from_name": approver_name, "from": approver_name}
    result = await _finalize_work_order_after_full_approval(
        wo, email_ctx, notes or "", session, notifier
    )
    if approval_request_id:
        result["approval_request_id"] = approval_request_id
    return result


async def _finalize_work_order_after_full_approval(
    wo: WorkOrder,
    approver_email: Dict[str, Any],
    notes: str,
    session: AsyncSession,
    notification_service,
) -> Dict[str, Any]:
    """After last approver: assign technician, notify requester & technician."""
    approver_name = (
        approver_email.get("from_name")
        or approver_email.get("from")
        or "Approver"
    )

    if wo.status == "pending_approval":
        await advance_journey_milestone(wo.work_order_id, "preparing", session)
        await record_status_change(
            wo.work_order_id, "pending_approval", "preparing", session,
            changed_by=approver_name, notes=notes,
        )
        wo.status = "preparing"

    jlog_res = await session.execute(
        select(JourneyLog).where(JourneyLog.work_order_id == wo.work_order_id)
    )
    jlog = jlog_res.scalar_one_or_none()
    asset_id = jlog.asset_id if jlog else None
    ppm_info = await check_ppm_for_asset(asset_id, wo.asset, session)

    required_skills: list[str] = []
    if isinstance(wo.manpower, dict):
        required_skills = wo.manpower.get("required_skills") or []
    asset_category = (wo.asset or "General").split()[0]
    technician = await find_best_technician(
        required_skills or [asset_category], asset_category, session
    )

    # Ensure a concrete schedule is present once WO is fully approved.
    # Keep any pre-existing schedule entered earlier in the workflow.
    if not wo.scheduled_date or not wo.scheduled_time or not wo.estimated_duration:
        scheduler = SmartScheduler(settings.aimms_api_url)
        sched = await scheduler.schedule(
            {
                "location": wo.location,
                "criticality": {"criticality_level": (wo.priority or "medium").lower()},
                "warranty_intelligence": {"estimated_duration": int(wo.estimated_duration or 2)},
            }
        )
        wo.scheduled_date = wo.scheduled_date or sched.get("suggested_date")
        wo.scheduled_time = wo.scheduled_time or sched.get("suggested_time")
        wo.estimated_duration = float(
            wo.estimated_duration or sched.get("estimated_duration_hours") or 2
        )

    if jlog and technician:
        jlog.assigned_technician_id = technician["technician_id"]
        jlog.assigned_technician_name = technician["name"]
        jlog.notes = f"Approved by {approver_name}. PPM: {ppm_info.get('recommendation', '')}"

    await session.commit()

    if notification_service and wo.requester_email:
        await notification_service.send_approval_confirmed(
            work_order_id=wo.work_order_id,
            requester_name=wo.requester_name or "Requester",
            requester_email=wo.requester_email,
            asset=wo.asset or "—",
            location=wo.location or "—",
            priority=wo.priority or "medium",
            approver_name=approver_name,
            technician=technician,
            ppm_info=ppm_info,
            scheduled_date=wo.scheduled_date,
            scheduled_time=wo.scheduled_time,
            estimated_duration=wo.estimated_duration,
        )

    if notification_service and technician and technician.get("email"):
        await notification_service.send_technician_assignment(
            work_order_id=wo.work_order_id,
            technician_name=technician["name"],
            technician_email=technician["email"],
            asset=wo.asset or "—",
            location=wo.location or "—",
            priority=wo.priority or "medium",
            issue_description=wo.issue_description or "—",
            ppm_info=ppm_info,
            scheduled_date=wo.scheduled_date,
            scheduled_time=wo.scheduled_time,
            estimated_duration=wo.estimated_duration,
        )

    log.info(
        "approval_processor.fully_approved",
        wo_id=wo.work_order_id,
        technician=technician.get("name") if technician else None,
    )
    return {
        "status": "approved",
        "work_order_id": wo.work_order_id,
        "technician_assigned": technician,
        "scheduled_date": wo.scheduled_date,
        "scheduled_time": wo.scheduled_time,
        "estimated_duration": wo.estimated_duration,
        "ppm_info": ppm_info,
    }


async def _notify_requester_rejected(
    wo: WorkOrder,
    approver_email: Dict[str, Any],
    notes: str,
    session: AsyncSession,
    notification_service,
) -> None:
    approver_name = approver_email.get("from_name") or approver_email.get("from", "Approver")
    if wo.requester_email:
        await notification_service.send_rejection_notice(
            work_order_id=wo.work_order_id,
            requester_name=wo.requester_name or "Requester",
            requester_email=wo.requester_email,
            asset=wo.asset or "—",
            approver_name=approver_name,
            rejection_notes=notes,
        )
    log.info("approval_processor.rejected", wo_id=wo.work_order_id, approver=approver_name)
