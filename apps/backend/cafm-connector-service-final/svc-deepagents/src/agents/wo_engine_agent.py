"""
WO Engine agent tools — wraps svc-work-order-management (port 8007).

Two categories of tools:
  - Intelligent pipeline  : create_intelligent_work_order, trigger_ppm_work_order,
                            process_email_work_order
    These go through POST /api/chat/ which runs the full 15-step AI assessment
    (criticality, safety, compliance, asset intelligence, vendor scoring, scheduling,
    resource allocation, workspace pinning, journey log creation).

  - Dynamic approval      : suggest_approval_chain, request_approval_chain,
                            get_approval_chain, customize_approval_chain,
                            respond_to_approval_step
  - CRUD + lifecycle      : create_work_order, get_work_order, update_work_order,
                            list_work_orders, approve_work_order, close_work_order,
                            transition_work_order, get_work_order_history
    Direct REST endpoints — no AI pipeline, used when the user already has a WO ID
    or wants to bypass the assessment.

  - Reference lookups     : search_assets, get_asset_details, search_locations,
                            find_ppm_schedules, get_dashboard_stats
    Read-only helpers for pre-filling WO fields and answering asset questions.
"""
import json
import re
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_TIMEOUT = 180.0      # intelligent pipeline can take >60s under load; avoid timeout/retry duplicates
_SERVICE = "wo_management"
_LAST_WO_CHAT_SESSION_ID: str | None = None
_LAST_WORK_ORDER_ID: str | None = None
_SESSION_WORK_ORDER_MAP: dict[str, str] = {}
_LAST_CREATE_ARGS: dict[str, Any] | None = None
_SESSION_CREATE_ARGS_MAP: dict[str, dict[str, Any]] = {}
_PENDING_APPROVAL_CONFIRM: str | None = None
_ORCH_TO_WO_CHAT_SESSION: dict[str, str] = {}


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text or ""
        log.error(
            "wo.tool.http_error",
            operation=op,
            status_code=exc.response.status_code,
            url=str(exc.request.url) if exc.request else None,
            body=body[:2000],
        )
        return {"error": body[:2000], "status_code": exc.response.status_code}
    log.error(
        "wo.tool.error",
        operation=op,
        error=str(exc),
        exc_type=type(exc).__name__,
        exc_info=True,
    )
    return {"error": str(exc)[:2000]}


def _orch_session_id() -> str | None:
    from .meta_tools import get_session_context

    sid = get_session_context()
    if sid and sid != "shared":
        return sid
    return None


def _resolve_wo_chat_session(session_id: str | None) -> str | None:
    """Map orchestrator session id to WO /api/chat session id when they differ."""
    if not session_id:
        return _LAST_WO_CHAT_SESSION_ID
    return _ORCH_TO_WO_CHAT_SESSION.get(session_id) or session_id


def _mirror_for_orchestrator_session(
    *,
    wo_chat_session: str | None = None,
    work_order_id: str | None = None,
    create_args: dict[str, Any] | None = None,
    awaiting_approval: bool = False,
) -> None:
    orch = _orch_session_id()
    if not orch:
        return
    if wo_chat_session:
        _ORCH_TO_WO_CHAT_SESSION[orch] = wo_chat_session
    if work_order_id:
        _SESSION_WORK_ORDER_MAP[orch] = work_order_id
    if create_args is not None:
        _SESSION_CREATE_ARGS_MAP[orch] = create_args
    if awaiting_approval:
        _mark_awaiting_approval_confirm(orch)


def _extract_work_order_id(payload: dict[str, Any]) -> str | None:
    """Best-effort extract of work_order_id from tool payload shapes."""
    wo = payload.get("work_order")
    if isinstance(wo, dict):
        wo_id = wo.get("work_order_id")
        if isinstance(wo_id, str) and wo_id:
            return wo_id
    wo_id = payload.get("work_order_id")
    if isinstance(wo_id, str) and wo_id:
        return wo_id
    return None


def _remember_context(payload: dict[str, Any], preferred_session_id: str | None = None) -> None:
    """Persist last WO chat session and WO ID for same-thread follow-up turns."""
    global _LAST_WO_CHAT_SESSION_ID, _LAST_WORK_ORDER_ID

    sid_payload = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
    sid_pref = preferred_session_id if isinstance(preferred_session_id, str) and preferred_session_id else None
    if sid_pref:
        _LAST_WO_CHAT_SESSION_ID = sid_pref
    elif sid_payload:
        _LAST_WO_CHAT_SESSION_ID = sid_payload

    wo_id = _extract_work_order_id(payload)
    if wo_id:
        _LAST_WORK_ORDER_ID = wo_id
        if sid_pref:
            _SESSION_WORK_ORDER_MAP[sid_pref] = wo_id
        if sid_payload:
            _SESSION_WORK_ORDER_MAP[sid_payload] = wo_id
        _mirror_for_orchestrator_session(
            wo_chat_session=sid_payload or sid_pref,
            work_order_id=wo_id,
        )


def _mark_awaiting_approval_confirm(session_id: str | None) -> None:
    global _PENDING_APPROVAL_CONFIRM
    orch = _orch_session_id()
    _PENDING_APPROVAL_CONFIRM = orch or session_id


def _clear_create_draft(session_id: str | None) -> None:
    global _LAST_CREATE_ARGS
    if session_id and session_id in _SESSION_CREATE_ARGS_MAP:
        del _SESSION_CREATE_ARGS_MAP[session_id]
    if not _SESSION_CREATE_ARGS_MAP:
        _LAST_CREATE_ARGS = None


def _remember_create_args(
    *,
    source: str,
    asset: str,
    location: str,
    issue_description: str,
    priority: str,
    request_type: str,
    requester_name: str,
    requester_email: str,
    requester_phone: str | None,
    session_id: str | None,
) -> None:
    """Cache last WO draft so a plain 'Yes' can create in same session."""
    global _LAST_CREATE_ARGS
    args = {
        "source": source,
        "asset": asset,
        "location": location,
        "issue_description": issue_description,
        "priority": priority,
        "request_type": request_type,
        "requester_name": requester_name,
        "requester_email": requester_email,
        "requester_phone": requester_phone,
    }
    _LAST_CREATE_ARGS = args
    if session_id:
        _SESSION_CREATE_ARGS_MAP[session_id] = args
    _mirror_for_orchestrator_session(wo_chat_session=session_id, create_args=args)


# ── Intelligent pipeline tools ────────────────────────────────────────────────

def _build_wo_chat_message(
    *,
    source: str,
    asset: str,
    location: str,
    issue_description: str,
    priority: str,
    request_type: str,
    requester_name: str,
    requester_email: str,
    requester_phone: str | None,
    assess_only: bool,
) -> str:
    header = (
        "ASSESSMENT ONLY — Run the full intelligence pipeline (search_assets, "
        "assess_criticality, safety, compliance, scheduling, allocate_resources, "
        "score_vendors). Do NOT call create_work_order or request_approval.\n"
        "Present the pre-create summary template and end with: "
        "'Would you like to proceed with creating this work order?'"
        if assess_only
        else (
            "USER CONFIRMED — Create the work order now with create_work_order "
            "using all assessed details from this session.\n"
            "Then present the post-create confirmation template including "
            "work_order_id, status, vendor, and auto_suggestion approval chain "
            "(names, roles, confidence_label, risk_score). Do NOT call "
            "suggest_approval_chain."
        )
    )
    parts = [
        header,
        f"Source: {source}",
        f"Asset: {asset}",
        f"Location: {location}",
        f"Issue: {issue_description}",
        f"Priority: {priority}",
        f"Request type: {request_type}",
        f"Requester: {requester_name} ({requester_email})",
    ]
    if requester_phone:
        parts.append(f"Phone: {requester_phone}")
    return "\n".join(parts)


async def _post_intelligent_chat(payload: dict[str, Any], op: str) -> dict:
    try:
        resp = await _request(
            "POST",
            settings.wo_management_base_url,
            "/api/chat/",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json=payload,
        )
        result = resp.json()
        if isinstance(result, dict):
            sid = result.get("session_id") if isinstance(result.get("session_id"), str) else None
            if not sid and isinstance(payload.get("session_id"), str):
                sid = payload["session_id"]
            _remember_context(result, preferred_session_id=sid)
            if result.get("work_order") or _extract_work_order_id(result):
                _mark_awaiting_approval_confirm(_orch_session_id() or sid)
        return result
    except Exception as exc:
        return _err(exc, op)


def capture_work_order_from_tool_output(
    orchestrator_session_id: str,
    output: Any,
) -> None:
    """Persist WO id from any tool result so follow-up 'yes' / approval works in WS chat."""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            m = re.search(r"(WO-\d+)", output, re.IGNORECASE)
            if m:
                _mirror_for_orchestrator_session(work_order_id=m.group(1).upper())
            return
    if not isinstance(output, dict):
        return
    _remember_context(output, preferred_session_id=orchestrator_session_id)
    wo_id = _extract_work_order_id(output)
    if wo_id:
        _mirror_for_orchestrator_session(
            wo_chat_session=output.get("session_id"),
            work_order_id=wo_id,
        )
        _mark_awaiting_approval_confirm(orchestrator_session_id)


def extract_work_order_id_from_text(text: str) -> str | None:
    m = re.search(r"(WO-\d{14,})", text or "", re.IGNORECASE)
    return m.group(1).upper() if m else None


@tool
async def prepare_intelligent_work_order(
    source: str,
    asset: str,
    location: str,
    issue_description: str,
    priority: str = "medium",
    request_type: str = "repair",
    requester_name: str = "System",
    requester_email: str = "system@plenum-tech.com",
    requester_phone: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Run the full AI assessment and return a summary — do NOT create a work order yet.

    **Always call this first** when the user reports a new maintenance issue.
    Returns `session_id` (save it) and `reply` with asset, scheduling, vendor, compliance,
    and safety details. Ask the user to confirm before calling `create_intelligent_work_order`.

    Args:
        source: email, ppm, manual, tenant, internal, remediation, or chat.
        asset: Asset name or code (e.g. 'Central Chiller Unit #2', 'CHILLER-102').
        location: Building/zone (e.g. 'Building B - Basement 2').
        issue_description: Fault or maintenance need in plain language.
        priority: low, medium, high, urgent, or critical.
        request_type: repair, maintenance, inspection, or installation.
        requester_name: Defaults to System for orchestrator flows.
        requester_email: Defaults to system@plenum-tech.com.
        requester_phone: Optional phone.
        session_id: Optional existing WO chat session to continue.
    """
    message = _build_wo_chat_message(
        source=source,
        asset=asset,
        location=location,
        issue_description=issue_description,
        priority=priority,
        request_type=request_type,
        requester_name=requester_name,
        requester_email=requester_email,
        requester_phone=requester_phone,
        assess_only=True,
    )
    payload: dict[str, Any] = {"message": message}
    resolved_session_id = session_id or _LAST_WO_CHAT_SESSION_ID
    if resolved_session_id:
        payload["session_id"] = resolved_session_id
    result = await _post_intelligent_chat(payload, "prepare_intelligent")
    if isinstance(result, dict) and result.get("session_id"):
        _remember_context(result, preferred_session_id=resolved_session_id)
        sid = result.get("session_id") if isinstance(result.get("session_id"), str) else resolved_session_id
        _remember_create_args(
            source=source,
            asset=asset,
            location=location,
            issue_description=issue_description,
            priority=priority,
            request_type=request_type,
            requester_name=requester_name,
            requester_email=requester_email,
            requester_phone=requester_phone,
            session_id=sid,
        )
        result["phase"] = "pre_create_summary"
        result["next_step"] = (
            "Present reply to the user and ask to confirm creation. "
            "On yes/proceed, call confirm_intelligent_work_order_creation (or create_intelligent_work_order with same session_id)."
        )
    return result


@tool
async def create_intelligent_work_order(
    source: str,
    asset: str,
    location: str,
    issue_description: str,
    priority: str = "medium",
    request_type: str = "repair",
    requester_name: str = "System",
    requester_email: str = "system@plenum-tech.com",
    requester_phone: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Create a work order after the user confirmed the pre-create summary.

    Call ONLY after `prepare_intelligent_work_order` and explicit user confirmation
    (yes, proceed, create it, go ahead). Pass the same `session_id` from the prepare step.

    Returns work_order with approval_suggestion / auto_suggestion — present the full
    post-create confirmation to the user. Do not call suggest_approval_chain.

    Args:
        source: Origin of the request — one of: email, ppm, manual, tenant, internal, remediation, chat.
        asset: Asset name or code the issue concerns (e.g. 'MOB-AHU-001', 'Chiller-3').
        location: Building or zone where the asset is located (e.g. 'Level 2 Plant Room').
        issue_description: Clear description of the fault or maintenance need.
        priority: Urgency — one of: low, medium, high, urgent, critical (default 'medium').
        request_type: Type of work — one of: repair, maintenance, inspection, installation.
        requester_name: Full name of the person raising the request.
        requester_email: Email address of the requester.
        requester_phone: Phone number of the requester (optional).
        session_id: **Required** — session_id returned by prepare_intelligent_work_order.
    """
    message = _build_wo_chat_message(
        source=source,
        asset=asset,
        location=location,
        issue_description=issue_description,
        priority=priority,
        request_type=request_type,
        requester_name=requester_name,
        requester_email=requester_email,
        requester_phone=requester_phone,
        assess_only=False,
    )
    payload: dict[str, Any] = {"message": message}
    resolved_session_id = session_id or _LAST_WO_CHAT_SESSION_ID
    if resolved_session_id:
        payload["session_id"] = resolved_session_id

    result = await _post_intelligent_chat(payload, "create_intelligent")
    if isinstance(result, dict):
        _remember_context(result, preferred_session_id=resolved_session_id)
    if isinstance(result, dict) and result.get("work_order"):
        result["phase"] = "post_create_confirmation"
        wo_sid = resolved_session_id or result.get("session_id")
        _clear_create_draft(wo_sid)
        _clear_create_draft(_orch_session_id())
        _mark_awaiting_approval_confirm(wo_sid if isinstance(wo_sid, str) else None)
        _mirror_for_orchestrator_session(
            wo_chat_session=wo_sid if isinstance(wo_sid, str) else None,
            awaiting_approval=True,
        )
    return result


@tool
async def confirm_intelligent_work_order_creation(
    session_id: str | None = None,
) -> dict:
    """Create WO from the cached draft in the same chat session.

    Use this for short confirmations like "yes", "proceed", "create it".
    It reuses details captured by prepare_intelligent_work_order.
    """
    sid = _resolve_wo_chat_session(session_id) or _LAST_WO_CHAT_SESSION_ID
    orch = session_id or _orch_session_id()
    args = _SESSION_CREATE_ARGS_MAP.get(orch or "") if orch else None
    args = args or (_SESSION_CREATE_ARGS_MAP.get(sid or "") if sid else None)
    args = args or _LAST_CREATE_ARGS
    if not args:
        return {
            "error": (
                "No pending work order draft found in this session. "
                "Please provide the work order details again."
            )
        }
    message = _build_wo_chat_message(
        source=args["source"],
        asset=args["asset"],
        location=args["location"],
        issue_description=args["issue_description"],
        priority=args["priority"],
        request_type=args["request_type"],
        requester_name=args["requester_name"],
        requester_email=args["requester_email"],
        requester_phone=args.get("requester_phone"),
        assess_only=False,
    )
    payload: dict[str, Any] = {"message": message}
    if sid:
        payload["session_id"] = sid
    result = await _post_intelligent_chat(payload, "confirm_intelligent")
    if isinstance(result, dict):
        _remember_context(result, preferred_session_id=sid)
    if isinstance(result, dict) and result.get("work_order"):
        result["phase"] = "post_create_confirmation"
        _clear_create_draft(sid)
        _clear_create_draft(_orch_session_id())
        _mark_awaiting_approval_confirm(sid)
        _mirror_for_orchestrator_session(wo_chat_session=sid, awaiting_approval=True)
    return result


@tool
async def trigger_ppm_work_order(
    schedule_id: str,
    asset_id: str,
    asset_name: str,
    description: str,
    maintenance_type: str | None = None,
    next_due_date: str | None = None,
    frequency: str | None = None,
) -> dict:
    """Trigger a Planned Preventive Maintenance (PPM) work order from a schedule.

    Called when a PPM schedule is due. The AI agent looks up the asset, runs
    scheduling and resource tools, and creates the work order automatically.
    Returns the created work order along with the agent reply.

    Args:
        schedule_id: UUID of the PPM schedule that triggered this run.
        asset_id: Asset identifier (UUID or asset_code).
        asset_name: Human-readable asset name (e.g. 'AHU-Level-3').
        description: Description of the planned maintenance task.
        maintenance_type: Type of maintenance (e.g. 'quarterly_service').
        next_due_date: ISO date when this task is due (YYYY-MM-DD).
        frequency: Schedule frequency (e.g. 'monthly', 'quarterly').
    """
    payload: dict[str, Any] = {
        "schedule_id": schedule_id,
        "asset_id": asset_id,
        "asset_name": asset_name,
        "description": description,
    }
    if maintenance_type:
        payload["maintenance_type"] = maintenance_type
    if next_due_date:
        payload["next_due_date"] = next_due_date
    if frequency:
        payload["frequency"] = frequency

    try:
        resp = await _request(
            "POST", settings.wo_management_base_url, "/api/chat/ppm",
            service=_SERVICE, timeout=_TIMEOUT, json=payload,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "trigger_ppm")


@tool
async def process_email_work_order(
    subject: str | None = None,
    body: str | None = None,
    sender_name: str | None = None,
    sender_email: str | None = None,
    asset: str | None = None,
    location: str | None = None,
) -> dict:
    """Create a work order by processing an incoming maintenance request email.

    The AI agent extracts the issue, looks up the asset, and either creates a work
    order automatically or asks clarifying questions. Provide as much context as
    available from the email.

    Args:
        subject: Email subject line.
        body: Full email body text.
        sender_name: Name of the person who sent the email.
        sender_email: Email address of the sender.
        asset: Asset name/code if already known or mentioned in the email.
        location: Location if already known or mentioned in the email.
    """
    payload: dict[str, Any] = {}
    if subject:
        payload["subject"] = subject
    if body:
        payload["body"] = body
    if sender_name:
        payload["sender_name"] = sender_name
    if sender_email:
        payload["sender_email"] = sender_email
    if asset:
        payload["asset"] = asset
    if location:
        payload["location"] = location

    try:
        resp = await _request(
            "POST", settings.wo_management_base_url, "/api/chat/email",
            service=_SERVICE, timeout=_TIMEOUT, json=payload,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "process_email")


# ── Dynamic approval tools ───────────────────────────────────────────────────

@tool
async def suggest_approval_chain(
    work_type: str,
    priority: str,
    location: str | None = None,
    estimated_cost: float = 0,
    asset_category: str | None = None,
    work_order_id: str | None = None,
) -> dict:
    """Preview the approval chain for an existing WO or a what-if (no WO created).

    Do NOT call before create_work_order or create_intelligent_work_order — those tools
    already return approval_suggestion / auto_suggestion after the WO is created.

    Use only when: (1) the user asks who would approve without creating a WO, or
    (2) refreshing the chain for an existing work_order_id.

    Args:
        work_type: Request type — repair, maintenance, inspection, hvac, etc.
        priority: low, medium, high, urgent, or critical.
        location: Building or zone name.
        estimated_cost: Estimated cost in AED (default 0).
        asset_category: Asset category for matching (e.g. HVAC, Electrical).
        work_order_id: WO ID after create (recommended) or omit for preview-only.
    """
    payload: dict[str, Any] = {
        "work_type": work_type,
        "priority": priority,
        "location": location or "",
        "estimated_cost": estimated_cost,
        "asset_category": asset_category or work_type,
        "work_order_id": work_order_id,
    }
    try:
        resp = await _request(
            "POST", settings.wo_management_base_url, "/api/work-orders/suggest-approval",
            service=_SERVICE, timeout=_TIMEOUT, json=payload,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "suggest_approval_chain")


@tool
async def request_approval_chain(
    work_order_id: str | None = None,
    approval_type: str = "preparation",
    session_id: str | None = None,
) -> dict:
    """Commit the dynamic multi-step approval chain for a pending work order.

    Creates one approval request per step; only step 1 is notified immediately.
    Later steps unblock when the prior approver approves via respond_to_approval_step.

    Call after create_work_order / create_intelligent_work_order once the user confirms the
    auto_suggestion chain from the create result.

    Args:
        work_order_id: Work order in pending_approval status. Optional when session_id has context.
        approval_type: preparation, final, or simple (default preparation).
        session_id: Optional WO chat session ID to resolve latest WO automatically.
    """
    resolved_wo_id = work_order_id
    if not resolved_wo_id:
        sid = _resolve_wo_chat_session(session_id) or _LAST_WO_CHAT_SESSION_ID
        orch = session_id or _orch_session_id()
        if orch:
            resolved_wo_id = _SESSION_WORK_ORDER_MAP.get(orch)
        if not resolved_wo_id and sid:
            resolved_wo_id = _SESSION_WORK_ORDER_MAP.get(sid)
    if not resolved_wo_id:
        resolved_wo_id = _LAST_WORK_ORDER_ID
    if not resolved_wo_id:
        return {
            "error": (
                "No work order ID available in session context. "
                "Provide work_order_id, or create a work order first in the same chat session."
            )
        }
    try:
        resp = await _request(
            "POST",
            settings.wo_management_base_url,
            f"/api/work-orders/{resolved_wo_id}/request-approval",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"approval_type": approval_type},
        )
        result = resp.json()
        if isinstance(result, dict):
            _remember_context(result, preferred_session_id=session_id or _LAST_WO_CHAT_SESSION_ID)
            result.setdefault("work_order_id", resolved_wo_id)
            global _PENDING_APPROVAL_CONFIRM
            _PENDING_APPROVAL_CONFIRM = None
        return result
    except Exception as exc:
        return _err(exc, "request_approval_chain")


@tool
async def send_approval_request_email(
    work_order_id: str | None = None,
    step_order: int = 1,
    session_id: str | None = None,
) -> dict:
    """Send the Outlook approval-request email to the designated approver.

    Uses the same NotificationService / Microsoft Graph pipeline as the WO email agent.
    Call after `request_approval_chain` when the user confirms the chain and asks to
    email the approver (e.g. Carlos Garcia).

    Args:
        work_order_id: Work order ID. Optional if session_id maps to a recent WO.
        step_order: Chain step to notify (default 1 = first approver).
        session_id: Orchestrator or WO chat session for context lookup.
    """
    resolved_wo_id = work_order_id
    if not resolved_wo_id:
        sid = _resolve_wo_chat_session(session_id) or _LAST_WO_CHAT_SESSION_ID
        orch = session_id or _orch_session_id()
        if orch:
            resolved_wo_id = _SESSION_WORK_ORDER_MAP.get(orch)
        if not resolved_wo_id and sid:
            resolved_wo_id = _SESSION_WORK_ORDER_MAP.get(sid)
    if not resolved_wo_id:
        resolved_wo_id = _LAST_WORK_ORDER_ID
    if not resolved_wo_id:
        return {
            "error": (
                "No work order ID in session. Provide work_order_id or create/confirm a WO first."
            )
        }
    try:
        resp = await _request(
            "POST",
            settings.wo_management_base_url,
            f"/api/work-orders/{resolved_wo_id}/send-approval-email?step_order={step_order}",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        result = resp.json()
        if isinstance(result, dict):
            result.setdefault("work_order_id", resolved_wo_id)
        return result
    except Exception as exc:
        return _err(exc, "send_approval_request_email")


@tool
async def get_approval_chain(work_order_id: str) -> dict:
    """Get the full multi-step approval chain for a work order (all steps and statuses).

    Args:
        work_order_id: Work order ID.
    """
    try:
        resp = await _request(
            "GET",
            settings.wo_management_base_url,
            f"/api/work-orders/{work_order_id}/approval-chain",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_approval_chain")


def format_work_order_status_track(track: dict[str, Any]) -> str:
    """Human-readable status report (uses API formatted_summary when present)."""
    if isinstance(track, dict) and track.get("formatted_summary"):
        return str(track["formatted_summary"])
    if not track or track.get("error"):
        return track.get("error") or track.get("message") or "Could not load work order status."
    if not track.get("found"):
        return f"Work order {track.get('work_order_id', '?')} was not found."
    return track.get("summary_message") or "Status loaded."


@tool
async def get_work_order_status_track(work_order_id: str) -> dict:
    """Full work order tracking: lifecycle status, multi-step approval, technician, scheduling, holds, history.

    Use this (not get_work_order alone) when the user asks for status, progress, track, or where a WO stands.

    Args:
        work_order_id: Work order ID (e.g. WO-202605271502250185).
    """
    try:
        resp = await _request(
            "GET",
            settings.wo_management_base_url,
            f"/api/work-orders/{work_order_id}/status-track",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        track = resp.json()
        if isinstance(track, dict) and track.get("found"):
            track["formatted_summary"] = format_work_order_status_track(track)
        return track
    except Exception as exc:
        return _err(exc, "get_work_order_status_track")


@tool
async def customize_approval_chain(
    work_order_id: str,
    chain: list[dict],
) -> dict:
    """Override pending approval chain steps before any approver has acted.

    Args:
        work_order_id: Work order ID.
        chain: List of {"step": 1, "email": "user@company.com"} overrides per step.
    """
    try:
        resp = await _request(
            "PATCH",
            settings.wo_management_base_url,
            f"/api/work-orders/{work_order_id}/customize-chain",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"chain": chain},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "customize_approval_chain")


@tool
async def respond_to_approval_step(
    approval_request_id: str,
    approved: bool,
    notes: str | None = None,
) -> dict:
    """Approve or reject one step in a multi-step approval chain.

    On approve: unblocks the next approver if more steps remain; otherwise WO moves to preparing.
    On reject: closes the entire work order.

    Args:
        approval_request_id: e.g. APR-WO-20260101-L1 (from get_approval_chain or request_approval_chain).
        approved: True to approve this step, False to reject.
        notes: Optional approver notes.
    """
    params: dict[str, Any] = {"approved": approved}
    if notes:
        params["notes"] = notes
    try:
        resp = await _request(
            "POST",
            settings.wo_management_base_url,
            f"/api/work-orders/approvals/{approval_request_id}/respond",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "respond_approval_step")


# ── CRUD + lifecycle tools ────────────────────────────────────────────────────

@tool
async def create_work_order(
    source: str,
    asset: str,
    location: str,
    issue_description: str,
    requester_name: str,
    requester_email: str,
    priority: str = "medium",
    request_type: str = "repair",
    requester_phone: str | None = None,
) -> dict:
    """Create a work order directly without the AI assessment pipeline.

    Use when you already know all details. For new user requests, prefer
    create_intelligent_work_order. On success returns approval_suggestion and auto_suggestion
    for the new WO — show the user before request_approval_chain.

    Args:
        source: Origin — one of: email, ppm, manual, tenant, internal, remediation.
        asset: Asset name or code (e.g. 'MOB-AHU-001').
        location: Location of the asset (e.g. 'Level 2 Plant Room').
        issue_description: Description of the fault or task.
        requester_name: Full name of requester.
        requester_email: Email of requester.
        priority: One of: low, medium, high, urgent, critical.
        request_type: One of: repair, maintenance, inspection, installation.
        requester_phone: Phone number (optional).
    """
    payload: dict[str, Any] = {
        "source": source,
        "asset": asset,
        "location": location,
        "issue_description": issue_description,
        "requester_name": requester_name,
        "requester_email": requester_email,
        "priority": priority,
        "request_type": request_type,
    }
    if requester_phone:
        payload["requester_phone"] = requester_phone

    try:
        resp = await _request(
            "POST", settings.wo_management_base_url, "/api/work-orders/",
            service=_SERVICE, timeout=_TIMEOUT, json=payload,
        )
        result = resp.json()
        if isinstance(result, dict):
            _remember_context(result, preferred_session_id=_LAST_WO_CHAT_SESSION_ID)
            wo_id = _extract_work_order_id(result)
            if wo_id:
                _clear_create_draft(_orch_session_id())
                _mark_awaiting_approval_confirm(_orch_session_id())
                _mirror_for_orchestrator_session(work_order_id=wo_id, awaiting_approval=True)
        return result
    except Exception as exc:
        return _err(exc, "create")


@tool
async def get_work_order(work_order_id: str) -> dict:
    """Get full details of a single work order by its ID.

    Returns all fields: status, priority, asset, location, issue description,
    requester, vendor, scheduled date/time, CMMS ID, journey log ID, and timestamps.

    Args:
        work_order_id: Work order ID (e.g. 'WO-20240115143022123456').
    """
    try:
        resp = await _request(
            "GET", settings.wo_management_base_url, f"/api/work-orders/{work_order_id}",
            service=_SERVICE, timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get")


@tool
async def update_work_order(
    work_order_id: str,
    vendor: str | None = None,
    scheduled_date: str | None = None,
    scheduled_time: str | None = None,
    estimated_duration: float | None = None,
    inspection_required: bool | None = None,
    special_requirements: str | None = None,
    cmms_work_order_id: str | None = None,
) -> dict:
    """Update editable fields on an existing work order.

    Only pass the fields you want to change. All fields are optional.
    This does not change the work order status — use transition_work_order for that.

    Args:
        work_order_id: ID of the work order to update.
        vendor: Assigned vendor name or company.
        scheduled_date: Planned date for the work (ISO format YYYY-MM-DD).
        scheduled_time: Planned time for the work (HH:MM format).
        estimated_duration: Estimated work duration in hours (must be positive).
        inspection_required: Whether a post-work inspection is required.
        special_requirements: Any special access, permits, or PPE requirements.
        cmms_work_order_id: ID from the external CMMS system (Maximo/SAP).
    """
    payload: dict[str, Any] = {}
    if vendor is not None:
        payload["vendor"] = vendor
    if scheduled_date is not None:
        payload["scheduled_date"] = scheduled_date
    if scheduled_time is not None:
        payload["scheduled_time"] = scheduled_time
    if estimated_duration is not None:
        payload["estimated_duration"] = estimated_duration
    if inspection_required is not None:
        payload["inspection_required"] = inspection_required
    if special_requirements is not None:
        payload["special_requirements"] = special_requirements
    if cmms_work_order_id is not None:
        payload["cmms_work_order_id"] = cmms_work_order_id

    try:
        resp = await _request(
            "PATCH", settings.wo_management_base_url, f"/api/work-orders/{work_order_id}",
            service=_SERVICE, timeout=_TIMEOUT, json=payload,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "update")


@tool
async def list_work_orders(
    status: str | None = None,
    priority: str | None = None,
    source: str | None = None,
    asset: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> list[dict]:
    """List work orders with optional filters. Returns a paginated list of WO summaries.

    Args:
        status: Filter by status — one of: pending_approval, preparing, prepared, active, completed, closed.
        priority: Filter by priority — one of: low, medium, high, urgent, critical.
        source: Filter by source — one of: email, ppm, manual, tenant, internal, remediation.
        asset: Filter to WOs for a specific asset (partial match on asset name/code).
        from_date: Start of date range in ISO format (YYYY-MM-DD).
        to_date: End of date range in ISO format (YYYY-MM-DD).
        page: Page number (default 1).
        limit: Records per page — max 200 (default 50).
    """
    params: dict[str, Any] = {"page": page, "limit": min(limit, 200)}
    if status:
        params["status"] = status
    if priority:
        params["priority"] = priority
    if source:
        params["source"] = source
    if asset:
        params["asset"] = asset
    if from_date:
        params["from_date"] = from_date
    if to_date:
        params["to_date"] = to_date

    try:
        resp = await _request(
            "GET", settings.wo_management_base_url, "/api/work-orders/",
            service=_SERVICE, timeout=_TIMEOUT, params=params,
        )
        return resp.json()
    except Exception as exc:
        return [_err(exc, "list")]


@tool
async def transition_work_order(
    work_order_id: str,
    new_status: str,
    notes: str | None = None,
) -> dict:
    """Transition a work order to a new status.

    Valid transitions (state machine):
      pending_approval → preparing | closed
      preparing        → prepared | closed
      prepared         → active | preparing | closed
      active           → completed | closed
      completed        → closed

    Args:
        work_order_id: ID of the work order to transition.
        new_status: Target status — one of: pending_approval, preparing, prepared, active, completed, closed.
        notes: Optional notes explaining the status change (written to history).
    """
    payload: dict[str, Any] = {"new_status": new_status}
    if notes:
        payload["notes"] = notes

    try:
        resp = await _request(
            "PATCH", settings.wo_management_base_url, f"/api/work-orders/{work_order_id}/status",
            service=_SERVICE, timeout=_TIMEOUT, json=payload,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "transition")


@tool
async def approve_work_order(work_order_id: str) -> dict:
    """Fast-path approve: single-step legacy endpoint (pending_approval → preparing).

    Prefer request_approval_chain + respond_to_approval_step when using dynamic
    multi-step approval. Use this only for simple single-approver WOs without a chain.

    Args:
        work_order_id: ID of the work order to approve.
    """
    try:
        resp = await _request(
            "POST", settings.wo_management_base_url, f"/api/work-orders/{work_order_id}/approve",
            service=_SERVICE, timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "approve")


@tool
async def close_work_order(work_order_id: str, notes: str | None = None) -> dict:
    """Close a work order from any open status.

    Sets status to 'closed'. Closing is a terminal state — the work order
    cannot be reopened. Use this when work is complete or cancelled.

    Args:
        work_order_id: ID of the work order to close.
        notes: Optional closing notes or reason (e.g. 'Work completed, asset operational').
    """
    payload: dict[str, Any] = {"new_status": "closed"}
    if notes:
        payload["notes"] = notes

    try:
        resp = await _request(
            "PATCH", settings.wo_management_base_url, f"/api/work-orders/{work_order_id}/status",
            service=_SERVICE, timeout=_TIMEOUT, json=payload,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "close")


@tool
async def get_work_order_history(work_order_id: str) -> list[dict]:
    """Get the full status change history of a work order.

    Returns a chronological list of all status transitions with timestamps,
    actor notes, and milestone information. Useful for audit trails and
    understanding how a WO progressed.

    Args:
        work_order_id: ID of the work order to get history for.
    """
    try:
        resp = await _request(
            "GET", settings.wo_management_base_url, f"/api/work-orders/{work_order_id}/history",
            service=_SERVICE, timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return [_err(exc, "history")]


# ── Reference lookup tools ────────────────────────────────────────────────────

@tool
async def search_assets(query: str, limit: int = 20) -> list[dict]:
    """Search for assets by name, code, category, or location.

    Returns matching assets with their code, name, category, location, status,
    and make/model. Use this to find the correct asset before raising a work order.

    Args:
        query: Search text — partial match on asset name, code, or description.
        limit: Maximum results to return (default 20, max 100).
    """
    try:
        resp = await _request(
            "GET", settings.wo_management_base_url, "/api/assets",
            service=_SERVICE, timeout=_TIMEOUT,
            params={"search": query, "limit": min(limit, 100)},
        )
        return resp.json()
    except Exception as exc:
        return [_err(exc, "search_assets")]


@tool
async def get_asset_details(asset_id: str) -> dict:
    """Get full details of a specific asset by its code or UUID.

    Returns all asset fields including category, make, model, serial number,
    location, installation date, warranty information, and open work order count.

    Args:
        asset_id: Asset code (e.g. 'MOB-AHU-001') or UUID.
    """
    try:
        resp = await _request(
            "GET", settings.wo_management_base_url, f"/api/assets/{asset_id}",
            service=_SERVICE, timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_asset")


@tool
async def search_locations(query: str | None = None) -> list[dict]:
    """List or search facility locations available for work orders.

    Returns locations with their codes and full names. Use this to find the
    correct location value before creating a work order.

    Args:
        query: Optional search text to filter locations by name (omit for full list).
    """
    params: dict[str, Any] = {}
    if query:
        params["search"] = query

    try:
        resp = await _request(
            "GET", settings.wo_management_base_url, "/api/locations",
            service=_SERVICE, timeout=_TIMEOUT, params=params,
        )
        return resp.json()
    except Exception as exc:
        return [_err(exc, "search_locations")]


@tool
async def find_ppm_schedules(
    asset_id: str | None = None,
    overdue_only: bool = False,
) -> list[dict]:
    """Find Planned Preventive Maintenance schedules, optionally for a specific asset.

    Returns PPM schedules with their frequency, next due date, and last completion.
    Use this to identify which assets have upcoming or overdue maintenance.

    Args:
        asset_id: Filter to schedules for a specific asset (optional).
        overdue_only: If True, return only overdue schedules (default False).
    """
    params: dict[str, Any] = {}
    if asset_id:
        params["asset_id"] = asset_id
    if overdue_only:
        params["overdue_only"] = "true"

    try:
        resp = await _request(
            "GET", settings.wo_management_base_url, "/api/ppm/schedules",
            service=_SERVICE, timeout=_TIMEOUT, params=params,
        )
        return resp.json()
    except Exception as exc:
        return [_err(exc, "find_ppm")]


@tool
async def get_dashboard_stats() -> dict:
    """Get aggregate work order statistics for the dashboard.

    Returns counts by status and priority, recent activity, overdue work orders,
    and asset health summary. Use this to answer high-level questions about the
    current state of maintenance operations.
    """
    try:
        resp = await _request(
            "GET", settings.wo_management_base_url, "/api/dashboard/stats",
            service=_SERVICE, timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "dashboard")
