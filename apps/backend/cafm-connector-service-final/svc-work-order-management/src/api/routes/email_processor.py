"""
Email intake routes:
  POST /api/email/process          — process a single email dict
  POST /api/email/process/sample   — process the hardcoded sample email
  POST /api/email/poll             — poll Outlook inbox, process all unread emails
  GET  /api/email/status           — verify Outlook token is working
  GET  /api/email/inbox            — list recent Inbox messages (email-inbox UI)
  GET  /api/email/watch/{wo_id}    — SSE: watch a WO for approval/rejection
"""
import json
import asyncio
import uuid as _uuid_mod
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any

from ...core.logging import get_logger
from ...db import get_session, AsyncSessionLocal
from ...config import settings
from ...services.work_order_flow import WorkOrderFlow
from ...services.notification_service import NotificationService
from ...services.approval_router import get_approver
from ...services.approval_processor import is_approval_reply, process_approval_reply
from ...integrations.outlook_connector import OutlookConnector
from ...models.work_order import WorkOrder
from ...models.journey_log import JourneyLog

router = APIRouter()
log = get_logger(__name__)

SAMPLE_EMAIL_COMPLETE = {
    "subject": "Urgent - HVAC-301 making grinding noise",
    "body": (
        "Hi Facilities Team,\n\n"
        "Asset HVAC-301 located at Building A - Roof Level has been making a loud grinding noise "
        "since this morning and cooling capacity has dropped significantly.\n\n"
        "Please send a technician urgently.\n\n"
        "Regards,\n"
        "AIMMS Test User\n"
        "Phone: +971-50-123-4567"
    ),
}

SAMPLE_EMAIL_MISSING = {
    "subject": "Urgent - maintenance issue",
    "body": (
        "Hi Team,\n\n"
        "Something is wrong and needs urgent repair.\n\n"
        "Please help quickly.\n\n"
        "Regards,\n"
        "AIMMS Test User"
    ),
}


# ── Dependency factories ──────────────────────────────────────────────────────

def _flow() -> WorkOrderFlow:
    return WorkOrderFlow(api_key=settings.openai_api_key, model=settings.openai_model)


def _outlook() -> OutlookConnector:
    if not (settings.azure_tenant_id and settings.azure_client_id and settings.azure_client_secret):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "outlook_not_configured",
                "message": (
                    "AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET must be set. "
                    "Register an app in Azure Entra ID and grant Mail.Read, Mail.Send, "
                    "Mail.ReadWrite Application permissions with admin consent."
                ),
            },
        )
    return OutlookConnector(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        client_secret=settings.azure_client_secret,
        user_email=settings.outlook_user_email,
    )


def _notifications(connector: OutlookConnector) -> NotificationService:
    return NotificationService(
        connector=connector,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )


# ── Background approval poller ────────────────────────────────────────────────

async def _approval_poller(interval: int = 60) -> None:
    """
    Background task started at app boot.
    Polls the Outlook inbox every `interval` seconds for unread emails whose
    subject contains a WO ID — i.e. approval replies from the facility manager.
    Runs the full process_approval_reply() pipeline for each reply found:
      approved → PPM check, technician assign, 2 outbound emails, DB commit
      rejected → cancel WO, notify requester, DB commit
    """
    if not settings.approval_email_poll_enabled:
        log.info("approval_poller.not_started", reason="APPROVAL_EMAIL_POLL_ENABLED=false")
        return

    log.info("approval_poller.started", interval_seconds=interval)
    while True:
        await asyncio.sleep(interval)
        if not settings.approval_email_poll_enabled:
            log.info("approval_poller.stopped", reason="APPROVAL_EMAIL_POLL_ENABLED=false")
            return
        if not (settings.azure_tenant_id and settings.azure_client_id and settings.azure_client_secret):
            log.debug("approval_poller.skip_no_credentials")
            continue
        try:
            connector = OutlookConnector(
                tenant_id=settings.azure_tenant_id,
                client_id=settings.azure_client_id,
                client_secret=settings.azure_client_secret,
                user_email=settings.outlook_user_email,
            )
            emails = await connector.get_unread_emails(max_count=20)
            approval_emails = [e for e in emails if is_approval_reply(e)]
            if not approval_emails:
                log.debug("approval_poller.no_replies")
                continue

            log.info("approval_poller.found_replies", count=len(approval_emails))
            async with AsyncSessionLocal() as session:
                notif = _notifications(connector)
                for email in approval_emails:
                    try:
                        result = await process_approval_reply(
                            email, session, notif,
                            api_key=settings.openai_api_key,
                            model=settings.openai_model,
                        )
                        st = result.get("status")
                        log.info(
                            "approval_poller.processed",
                            email_id=email.get("id"),
                            wo_id=result.get("work_order_id"),
                            status=st,
                        )
                        if st in ("approved", "rejected", "approved_step_complete"):
                            await connector.move_to_folder(email["id"], "AIMMS-Processed")
                    except Exception as exc:
                        log.error("approval_poller.email_error",
                                  email_id=email.get("id"), exc_info=exc)
        except Exception as exc:
            log.error("approval_poller.cycle_error", exc_info=exc)


# ── Shared helper: poll DB until WO leaves pending_approval ──────────────────

async def _wait_for_approval(
    wo_id: str,
    poll_interval: int = 10,
):
    """
    Async generator that polls the DB every poll_interval seconds.
    Yields SSE event strings until the WO status changes (approved / rejected / other).
    Runs indefinitely — no timeout — so the stream stays open until the manager responds.
    Uses a fresh DB session per poll so committed changes from the background poller
    are immediately visible.
    """
    elapsed = 0
    yield f"data: {json.dumps({'step': 'waiting_approval', 'status': 'running', 'message': 'Waiting for facility manager to respond…', 'data': {'wo_id': wo_id}})}\n\n"

    while True:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        async with AsyncSessionLocal() as poll_session:
            wo_res = await poll_session.execute(
                select(WorkOrder).where(WorkOrder.work_order_id == wo_id)
            )
            wo = wo_res.scalar_one_or_none()

            if not wo:
                yield f"data: {json.dumps({'step': 'waiting_approval', 'status': 'error', 'message': f'Work order {wo_id} not found in database.'})}\n\n"
                return

            if wo.status != "pending_approval":
                if wo.status in ("preparing", "active", "in_progress", "open"):
                    jlog_res = await poll_session.execute(
                        select(JourneyLog).where(JourneyLog.work_order_id == wo_id)
                    )
                    jlog = jlog_res.scalar_one_or_none()
                    tech_name = (
                        (jlog.assigned_technician_name if jlog else None) or "Being arranged"
                    )
                    yield f"data: {json.dumps({'step': 'waiting_approval', 'status': 'complete', 'message': 'Facility manager approved the work order!'})}\n\n"
                    yield f"data: {json.dumps({'step': 'technician_assigned', 'status': 'complete', 'message': f'Technician assigned: {tech_name}', 'data': {'technician_name': tech_name, 'wo_status': wo.status}})}\n\n"
                    yield f"data: {json.dumps({'step': 'notifications_sent', 'status': 'complete', 'message': 'Confirmation sent to requester. Assignment sent to technician.'})}\n\n"
                elif wo.status == "cancelled":
                    yield f"data: {json.dumps({'step': 'waiting_approval', 'status': 'error', 'message': 'Work order was rejected by the facility manager. Requester has been notified.'})}\n\n"
                else:
                    yield f"data: {json.dumps({'step': 'waiting_approval', 'status': 'complete', 'message': f'Status updated to: {wo.status}', 'data': {'wo_status': wo.status}})}\n\n"
                return

        # Still pending — heartbeat so client connection stays alive
        yield f"data: {json.dumps({'step': 'waiting_approval', 'status': 'running', 'message': f'Still waiting for manager response… ({elapsed}s)', 'data': {'elapsed_seconds': elapsed, 'wo_id': wo_id}})}\n\n"


async def _send_to_self_and_fetch_unread(
    connector: OutlookConnector,
    sample: Dict[str, str],
    max_wait_seconds: int = 25,
) -> Dict[str, Any]:
    me = await connector.check_connection()
    if not me.get("connected"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "outlook_not_connected", "message": me.get("error", "Outlook not connected")},
        )

    to_addr = me.get("email") or settings.outlook_user_email
    if not to_addr:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "outlook_no_mailbox", "message": "Unable to resolve mailbox email for self-test flow."},
        )

    marker = f"AIMMS-SELFTEST-{_uuid_mod.uuid4().hex[:10]}"
    subject = f"[{marker}] {sample['subject']}"
    await connector.send_email(to=to_addr, subject=subject, body=sample["body"])
    log.info("email.self_test.sent", to=to_addr, marker=marker)

    deadline = asyncio.get_event_loop().time() + max_wait_seconds
    while asyncio.get_event_loop().time() < deadline:
        unread = await connector.get_unread_emails(max_count=25)
        for msg in unread:
            if marker in (msg.get("subject") or ""):
                log.info("email.self_test.received", email_id=msg.get("id"), marker=marker)
                return msg
        await asyncio.sleep(2)

    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail={
            "code": "self_test_inbox_timeout",
            "message": "Sent test email to self but did not find it unread in Inbox within timeout.",
        },
    )


# ── Shared helper: fire approval request email after WO creation ──────────────

async def _send_approval_request_if_needed(
    result: Dict[str, Any],
    notif: NotificationService,
    session: AsyncSession,
) -> None:
    """After a WO is created, look up the approver and send the approval request email."""
    if result.get("status") != "created":
        return
    try:
        assessment = result.get("full_assessment") or {}
        asset_type = (
            (assessment.get("asset_intelligence") or {}).get("asset_type")
            or result.get("asset", "")
        )
        approver = await get_approver(asset_type, session)
        if not approver:
            log.warning(
                "email.approval_request.no_approver",
                work_order_id=result.get("work_order_id"),
                asset_type=asset_type,
            )
            return
        await notif.send_approval_request(
            work_order_id=result["work_order_id"],
            asset=asset_type,
            location=assessment.get("location") or "—",
            issue_description=(assessment.get("issue_description") or
                               (assessment.get("criticality") or {}).get("description") or "—"),
            priority=result.get("priority", "medium"),
            approver_email=approver["approver_email"],
            approver_name=approver["approver_name"],
            requester_name=result.get("requester_name", ""),
            assessment_summary=result.get("assessment_summary"),
        )
        log.info(
            "email.approval_request.sent",
            work_order_id=result["work_order_id"],
            to=approver["approver_email"],
        )
    except Exception as exc:
        log.warning(
            "email.approval_request.failed",
            work_order_id=result.get("work_order_id"),
            exc_info=exc,
        )


# ── Connectivity check ────────────────────────────────────────────────────────

@router.get("/status")
async def outlook_status():
    connector = _outlook()
    result = await connector.check_connection()
    if result.get("connected"):
        log.info("outlook.status.ok", display_name=result.get("display_name"), email=result.get("email"))
    else:
        log.warning("outlook.status.failed", error=result.get("error"))
    return result


@router.get("/inbox")
async def list_inbox(max_count: int = 30):
    """Recent Inbox messages for the frontend email-inbox UI."""
    connector = _outlook()
    try:
        emails = await connector.list_inbox_messages(max_count=max_count)
        return emails
    except Exception as exc:
        log.warning("outlook.inbox.failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "outlook_inbox_failed", "message": str(exc)},
        ) from exc


# ── Single email (manual / test) ──────────────────────────────────────────────

@router.post("/process")
async def process_email(
    email: dict,
    session: AsyncSession = Depends(get_session),
):
    """
    Accepts a raw email dict, runs the full pipeline.
    After WO creation: sends approval request to the routing-table approver.
    """
    log.info(
        "email.process.start",
        email_id=email.get("id"),
        subject=email.get("subject"),
        from_addr=email.get("from"),
    )
    flow = _flow()
    result = await flow.create_from_email(email, session)
    log.info(
        "email.process.result",
        email_id=email.get("id"),
        status=result.get("status"),
        work_order_id=result.get("work_order_id"),
    )

    if settings.azure_client_id:
        connector = _outlook()
        notif = _notifications(connector)
        await _send_notifications(result, email, notif)
        await _send_approval_request_if_needed(result, notif, session)

    return result


@router.post("/process/sample/stream")
async def process_sample_email_stream(session: AsyncSession = Depends(get_session)):
    """Stream the sample email pipeline as Server-Sent Events."""
    flow = _flow()
    connector = _outlook()

    async def event_gen():
        result: Dict[str, Any] = {}
        try:
            live_email = await _send_to_self_and_fetch_unread(connector, SAMPLE_EMAIL_COMPLETE)
            async for event in flow.stream_from_email(live_email, session):
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("step") == "done" and event.get("result"):
                    result = event["result"]

            if result.get("status") == "created":
                # Confirmation email to requester
                if settings.azure_client_id:
                    yield f"data: {json.dumps({'step': 'notification', 'status': 'running', 'message': 'Sending requester confirmation…'})}\n\n"
                    try:
                        notif = _notifications(connector)
                        await _send_notifications(result, live_email, notif)
                        _to = live_email.get("from", "")
                        yield f"data: {json.dumps({'step': 'notification', 'status': 'complete', 'message': f'Confirmation sent to {_to}'})}\n\n"
                    except Exception as exc:
                        yield f"data: {json.dumps({'step': 'notification', 'status': 'warning', 'message': f'Notification failed: {exc}'})}\n\n"

                # Approval request to facility manager
                yield f"data: {json.dumps({'step': 'approval_request', 'status': 'running', 'message': 'Routing approval request to facility manager…'})}\n\n"
                try:
                    notif = _notifications(connector)
                    await _send_approval_request_if_needed(result, notif, session)
                    yield f"data: {json.dumps({'step': 'approval_request', 'status': 'complete', 'message': 'Approval request sent to facility manager'})}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({'step': 'approval_request', 'status': 'warning', 'message': f'Approval routing failed: {exc}'})}\n\n"

                # Wait for the manager to reply — background poller will process it
                # and update the WO status; we watch the DB and stream the result
                wo_id = result.get("work_order_id")
                if wo_id:
                    async for evt in _wait_for_approval(wo_id):
                        yield evt

        except Exception as exc:
            yield f"data: {json.dumps({'step': 'error', 'status': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/process/sample/missing-info/stream")
async def process_sample_missing_info_stream(session: AsyncSession = Depends(get_session)):
    """Self-test: incomplete email → verify missing-info reply flow."""
    flow = _flow()
    connector = _outlook()

    async def event_gen():
        result: Dict[str, Any] = {}
        try:
            live_email = await _send_to_self_and_fetch_unread(connector, SAMPLE_EMAIL_MISSING)
            async for event in flow.stream_from_email(live_email, session):
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("step") == "done" and event.get("result"):
                    result = event["result"]

            if result.get("status") == "missing_info":
                try:
                    notif = _notifications(connector)
                    await _send_notifications(result, live_email, notif)
                    _to = live_email.get("from", "")
                    yield f"data: {json.dumps({'step': 'notification', 'status': 'complete', 'message': f'Missing-info reply sent to {_to}'})}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({'step': 'notification', 'status': 'warning', 'message': f'Notification failed: {exc}'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'step': 'error', 'status': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/process/sample")
async def process_sample_email(session: AsyncSession = Depends(get_session)):
    """Non-stream fallback for self-mail complete sample flow."""
    log.info("email.process_sample.start")
    connector = _outlook()
    live_email = await _send_to_self_and_fetch_unread(connector, SAMPLE_EMAIL_COMPLETE)
    flow = _flow()
    result = await flow.create_from_email(live_email, session)
    log.info("email.process_sample.result", status=result.get("status"), work_order_id=result.get("work_order_id"))

    notif = _notifications(connector)
    await _send_notifications(result, live_email, notif)
    await _send_approval_request_if_needed(result, notif, session)

    return result


@router.post("/process/sample/missing-info")
async def process_sample_missing_info(session: AsyncSession = Depends(get_session)):
    """Non-stream fallback for missing-info self-test flow."""
    connector = _outlook()
    live_email = await _send_to_self_and_fetch_unread(connector, SAMPLE_EMAIL_MISSING)
    flow = _flow()
    result = await flow.create_from_email(live_email, session)
    notif = _notifications(connector)
    await _send_notifications(result, live_email, notif)
    return result


# ── Inbox poll ────────────────────────────────────────────────────────────────

@router.post("/poll")
async def poll_inbox(
    max_emails: int = 20,
    session: AsyncSession = Depends(get_session),
):
    """
    Poll the Outlook inbox for unread emails.

    For each email:
      • If subject contains a WO ID → treat as approval reply, run approval pipeline
      • Otherwise → run normal WO creation pipeline
        - On WO created: send requester confirmation + approver approval request
    """
    connector = _outlook()
    notif     = _notifications(connector)
    flow      = _flow()

    emails = await connector.get_unread_emails(max_count=max_emails)
    log.info("email.poll.fetched", count=len(emails))

    summary: Dict[str, Any] = {
        "fetched":      len(emails),
        "created":      0,
        "approved":     0,
        "rejected":     0,
        "missing_info": 0,
        "skipped":      0,
        "errors":       0,
        "work_orders":  [],
    }

    for email in emails:
        email_id = email.get("id")
        subject  = email.get("subject")
        try:
            # ── Approval reply branch ─────────────────────────────────────────
            if is_approval_reply(email):
                log.info("email.poll.approval_reply", email_id=email_id, subject=subject)
                result = await process_approval_reply(
                    email, session, notif,
                    api_key=settings.openai_api_key,
                    model=settings.openai_model,
                )
                poll_status = result.get("status")
                if poll_status == "approved":
                    summary["approved"] += 1
                    summary["work_orders"].append(result.get("work_order_id"))
                    await connector.move_to_folder(email_id, "AIMMS-Processed")
                elif poll_status == "rejected":
                    summary["rejected"] += 1
                    await connector.mark_as_read(email_id)
                else:
                    # unclear / already processed / not found — leave for manual review
                    log.info("email.poll.approval_unclear",
                             email_id=email_id, poll_status=poll_status)
                continue

            # ── New work order branch ─────────────────────────────────────────
            log.info("email.poll.processing", email_id=email_id, subject=subject,
                     from_addr=email.get("from"))
            result = await flow.create_from_email(email, session)

            if result["status"] == "not_maintenance":
                summary["skipped"] += 1
                await connector.mark_as_read(email["id"])
                continue

            await _send_notifications(result, email, notif)

            if result["status"] == "created":
                summary["created"] += 1
                summary["work_orders"].append(result["work_order_id"])
                await _send_approval_request_if_needed(result, notif, session)
                await connector.move_to_folder(email["id"], "AIMMS-Processed")
            else:
                summary["missing_info"] += 1
                await connector.mark_as_read(email["id"])

        except Exception as exc:
            summary["errors"] += 1
            summary.setdefault("error_details", []).append({
                "email_id": email_id,
                "subject":  subject,
                "error":    str(exc),
            })
            log.error("email.poll.error", email_id=email_id, subject=subject, exc_info=exc)
            try:
                await connector.mark_as_read(email["id"])
            except Exception:
                pass

    log.info(
        "email.poll.complete",
        fetched=summary["fetched"],
        created=summary["created"],
        approved=summary["approved"],
        rejected=summary["rejected"],
        missing_info=summary["missing_info"],
        skipped=summary["skipped"],
        errors=summary["errors"],
    )
    return summary


# ── Approval watch (standalone SSE) ──────────────────────────────────────────

@router.get("/watch/{wo_id}")
async def watch_approval(wo_id: str):
    """
    SSE stream that watches any work order for an approval decision.
    Connect immediately after WO creation — yields events when the background
    poller detects the manager's reply and updates the WO status.
    Closes automatically once approved, rejected, or after 10 minutes.
    """
    async def event_gen():
        try:
            async for evt in _wait_for_approval(wo_id):
                yield evt
        except Exception as exc:
            yield f"data: {json.dumps({'step': 'error', 'status': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Helper ────────────────────────────────────────────────────────────────────

async def _send_notifications(
    result: Dict[str, Any],
    original_email: Dict[str, Any],
    notif: NotificationService,
) -> None:
    """Fire requester-facing outbound emails based on flow result."""
    try:
        if result["status"] == "missing_info":
            await notif.send_missing_info_email(
                original_email=original_email,
                missing_fields=result["missing_fields"],
            )
        elif result["status"] == "created":
            requester_email = original_email.get("from", "")
            requester_name  = original_email.get("from_name", "Requester")
            if requester_email:
                await notif.send_wo_created_confirmation(
                    work_order_id=result["work_order_id"],
                    priority=result.get("priority", "medium"),
                    asset=result.get("full_assessment", {})
                               .get("asset_intelligence", {})
                               .get("asset_type", "—"),
                    location="—",
                    requester_email=requester_email,
                    requester_name=requester_name,
                    original_email_id=original_email.get("id"),
                )
    except Exception as exc:
        log.warning(
            "email.notification.failed",
            work_order_id=result.get("work_order_id"),
            exc_info=exc,
        )
