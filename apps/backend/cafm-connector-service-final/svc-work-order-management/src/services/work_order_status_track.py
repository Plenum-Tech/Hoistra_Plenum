"""Aggregate work order status: approval chain, technician, journey, blockers."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.journey_log import JourneyLog
from ..models.status_history import StatusHistory
from ..models.work_order import WorkOrder
from ..services.dynamic_approval_engine import DynamicApprovalEngine
from ..core.logging import get_logger

log = get_logger(__name__)

_STATUS_LABELS: Dict[str, str] = {
    "pending_approval": "Pending approval",
    "preparing": "Approved — preparing work",
    "prepared": "Prepared — ready to start",
    "active": "In progress",
    "completed": "Completed",
    "closed": "Closed",
    "cancelled": "Cancelled",
}


def _label_status(code: str | None) -> str:
    if not code:
        return "Unknown"
    return _STATUS_LABELS.get(code.lower(), code.replace("_", " ").title())


def _detect_blockers(wo: WorkOrder, jlog: JourneyLog | None) -> List[str]:
    blockers: List[str] = []
    mp = wo.manpower if isinstance(wo.manpower, dict) else {}
    if mp.get("on_hold") or mp.get("hold_reason"):
        blockers.append(str(mp.get("hold_reason") or "Work on hold (manpower)"))
    parts = mp.get("parts_needed") or mp.get("parts_required")
    if parts:
        blockers.append(f"Parts / materials needed: {parts}")
    spec = (wo.special_requirements or "").lower()
    if any(w in spec for w in ("on hold", "waiting for parts", "parts needed", "awaiting parts")):
        blockers.append(wo.special_requirements or "On hold per special requirements")
    if jlog and isinstance(jlog.deviations, list):
        for d in jlog.deviations:
            if isinstance(d, dict) and d.get("type") in ("hold", "parts", "asset"):
                blockers.append(d.get("description") or str(d))
    return blockers


async def _load_approval_steps(
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
                   u.full_name AS approver_name, u.role AS approver_role
            FROM plenum_cafm.wo_approval_requests ar
            LEFT JOIN plenum_cafm.users u ON LOWER(u.email) = LOWER(ar.approver)
            WHERE ar.work_order_id = :wo_id
            ORDER BY {order_sql} ASC
        """),
        {"wo_id": work_order_id},
    )
    steps: List[Dict[str, Any]] = []
    for r in rows.fetchall():
        m = dict(r._mapping)
        step_num = m.get("step_order") or m.get("level") or len(steps) + 1
        email = m.get("approver") or ""
        name = m.get("approver_name") or email
        steps.append({
            "step": int(step_num),
            "request_id": m.get("request_id"),
            "approver_email": email,
            "approver_name": name,
            "approver_role": m.get("approver_role"),
            "status": m.get("status"),
            "requested_at": (
                m["requested_at"].isoformat() if m.get("requested_at") else None
            ),
            "responded_at": (
                m["responded_at"].isoformat() if m.get("responded_at") else None
            ),
            "unblocked_at": (
                m["unblocked_at"].isoformat() if m.get("unblocked_at") else None
            ),
        })
    return steps


def _approval_summary(steps: List[Dict[str, Any]], wo_status: str) -> Dict[str, Any]:
    if not steps:
        return {
            "has_chain": False,
            "summary": (
                "No approval chain started yet."
                if wo_status == "pending_approval"
                else "Approval chain not recorded."
            ),
            "steps": [],
            "approved_count": 0,
            "total_steps": 0,
            "current_pending_step": None,
            "current_pending_approver": None,
        }
    approved = sum(1 for s in steps if s.get("status") == "approved")
    total = len(steps)
    pending = next((s for s in steps if s.get("status") == "pending"), None)
    rejected = next((s for s in steps if s.get("status") == "rejected"), None)
    if rejected:
        summary = f"Approval rejected at step {rejected.get('step')} ({rejected.get('approver_name')})."
    elif approved == total:
        summary = f"All {total} approval step(s) completed."
    elif pending:
        summary = (
            f"Approval in progress: step {pending.get('step')} of {total} pending "
            f"({pending.get('approver_name')} — {pending.get('approver_email')})."
        )
    else:
        summary = f"Approval: {approved} of {total} step(s) complete."
    return {
        "has_chain": True,
        "summary": summary,
        "steps": steps,
        "approved_count": approved,
        "total_steps": total,
        "current_pending_step": pending.get("step") if pending else None,
        "current_pending_approver": (
            {
                "name": pending.get("approver_name"),
                "email": pending.get("approver_email"),
                "role": pending.get("approver_role"),
            }
            if pending
            else None
        ),
    }


def _technician_block(wo: WorkOrder, jlog: JourneyLog | None) -> Dict[str, Any]:
    mp = wo.manpower if isinstance(wo.manpower, dict) else {}
    tech_name = (
        (jlog.assigned_technician_name if jlog else None)
        or mp.get("technician_name")
        or mp.get("assigned_technician")
    )
    tech_id = (
        (jlog.assigned_technician_id if jlog else None)
        or mp.get("technician_id")
    )
    tech_email = mp.get("technician_email") or mp.get("email")
    skills = mp.get("required_skills") or mp.get("skills") or []
    return {
        "assigned": bool(tech_name or tech_id),
        "technician_name": tech_name,
        "technician_id": tech_id,
        "technician_email": tech_email,
        "required_skills": skills,
        "vendor": wo.vendor,
        "score": mp.get("match_score") or mp.get("score"),
    }


def _build_summary_message(
    wo: WorkOrder,
    approval: Dict[str, Any],
    technician: Dict[str, Any],
    blockers: List[str],
    phase: str,
) -> str:
    lines = [
        f"Work order {wo.work_order_id} — {_label_status(wo.status)}.",
        f"Asset: {wo.asset or '—'} at {wo.location or '—'} ({wo.priority or 'medium'} priority).",
    ]
    if approval.get("has_chain"):
        lines.append(f"Approval: {approval.get('summary')}")
    elif wo.status == "pending_approval":
        lines.append("Approval: not started — confirm and request approval chain in chat.")
    if technician.get("assigned"):
        lines.append(
            f"Technician: {technician.get('technician_name') or 'Assigned'} "
            f"({technician.get('vendor') or 'vendor TBD'})."
        )
    elif wo.status == "pending_approval":
        lines.append("Technician: pending final approval.")
    elif wo.status in ("preparing", "prepared", "active"):
        lines.append("Technician: not yet assigned.")
    if blockers:
        lines.append("Hold / blockers: " + "; ".join(blockers))
    else:
        if phase == "execution":
            lines.append("Execution: work is in progress with no recorded holds.")
        elif phase == "completed":
            lines.append("Work is complete.")
    return " ".join(lines)


def format_status_track_markdown(track: Dict[str, Any]) -> str:
    """Markdown status report for chat and APIs."""
    if not track or track.get("error"):
        return track.get("error") or track.get("message") or "Could not load work order status."
    if not track.get("found"):
        return f"Work order {track.get('work_order_id', '?')} was not found."

    lines: List[str] = []
    wo = track.get("work_order") or {}
    lines.append(f"## {track.get('work_order_id')} — {track.get('overall_status')}")
    lines.append("")
    lines.append(f"**Issue:** {wo.get('issue_description') or wo.get('title') or '—'}")
    lines.append(
        f"**Asset / location:** {wo.get('asset') or '—'} @ {wo.get('location') or '—'} "
        f"({wo.get('priority') or 'medium'} priority)"
    )
    if wo.get("requester_name"):
        lines.append(f"**Requester:** {wo.get('requester_name')} ({wo.get('requester_email') or '—'})")

    approval = track.get("approval") or {}
    lines.append("")
    lines.append("### Approval")
    lines.append(approval.get("summary") or "No approval chain.")
    for step in approval.get("steps") or []:
        st = (step.get("status") or "unknown").upper()
        name = step.get("approver_name") or step.get("approver_email") or "—"
        role = step.get("approver_role")
        role_txt = f" ({role})" if role else ""
        lines.append(f"- Step {step.get('step')}: **{st}** — {name}{role_txt}")
        if step.get("responded_at"):
            lines.append(f"  - Responded: {step['responded_at']}")

    tech = track.get("technician") or {}
    lines.append("")
    lines.append("### Technician & vendor")
    if tech.get("assigned"):
        lines.append(
            f"- **{tech.get('technician_name') or 'Assigned'}** "
            f"(vendor: {tech.get('vendor') or 'TBD'})"
        )
        if tech.get("technician_email"):
            lines.append(f"- Email: {tech['technician_email']}")
        if tech.get("required_skills"):
            lines.append(f"- Skills: {', '.join(map(str, tech['required_skills']))}")
    else:
        phase = track.get("phase")
        if phase in ("preparation", "execution"):
            lines.append("- Not yet assigned")
        else:
            lines.append("- Assignment after full approval")

    sched = track.get("scheduling") or {}
    wo_status = (track.get("work_order") or {}).get("status") or track.get("overall_status")
    lines.append("")
    lines.append("### Scheduling")
    if sched.get("scheduled_date"):
        lines.append(
            f"- {sched.get('scheduled_date')} {sched.get('scheduled_time') or ''} "
            f"({sched.get('estimated_duration_hours') or '?'} h est.)"
        )
    elif wo_status == "pending_approval":
        lines.append("- Pending final approval (not scheduled yet)")
    else:
        lines.append("- Not scheduled yet")

    blockers = track.get("blockers") or []
    if blockers:
        lines.append("")
        lines.append("### Holds / blockers")
        for b in blockers:
            lines.append(f"- {b}")

    journey = track.get("journey")
    if journey:
        lines.append("")
        lines.append("### Journey")
        lines.append(
            f"- Progress: {journey.get('completion_percentage', 0)}% "
            f"({journey.get('journey_status') or journey.get('status') or '—'})"
        )
        if journey.get("current_step"):
            lines.append(f"- Current step: {journey['current_step']}")

    hist = track.get("status_history") or []
    if hist:
        lines.append("")
        lines.append("### Status timeline")
        for h in hist[-8:]:
            fr = h.get("from_status") or "—"
            to = h.get("to_status") or "—"
            at = h.get("changed_at") or ""
            lines.append(f"- {at}: {fr} → {to}")

    return "\n".join(lines)


async def build_work_order_status_track(
    session: AsyncSession,
    work_order_id: str,
) -> Dict[str, Any]:
    """Full status snapshot for chat and APIs."""
    wo_result = await session.execute(
        select(WorkOrder).where(WorkOrder.work_order_id == work_order_id)
    )
    wo = wo_result.scalar_one_or_none()
    if not wo:
        return {"found": False, "work_order_id": work_order_id, "error": "Work order not found"}

    jlog_result = await session.execute(
        select(JourneyLog).where(JourneyLog.work_order_id == work_order_id)
    )
    jlog = jlog_result.scalar_one_or_none()

    hist_result = await session.execute(
        select(StatusHistory)
        .where(StatusHistory.work_order_id == work_order_id)
        .order_by(StatusHistory.changed_at.asc())
    )
    history = [
        {
            "from_status": h.from_status,
            "to_status": h.to_status,
            "changed_at": h.changed_at.isoformat() if h.changed_at else None,
            "changed_by": h.changed_by,
            "notes": h.notes,
        }
        for h in hist_result.scalars().all()
    ]

    approval_steps = await _load_approval_steps(session, work_order_id)
    approval = _approval_summary(approval_steps, wo.status or "")
    technician = _technician_block(wo, jlog)
    blockers = _detect_blockers(wo, jlog)

    code = (wo.status or "").lower()
    if code == "pending_approval":
        phase = "approval"
    elif code in ("preparing", "prepared"):
        phase = "preparation"
    elif code == "active":
        phase = "execution"
    elif code in ("completed", "closed"):
        phase = "completed"
    elif code == "cancelled":
        phase = "cancelled"
    else:
        phase = "other"

    overall = _label_status(wo.status)
    if blockers and code == "active":
        overall = f"In progress — on hold ({blockers[0][:80]})"
    elif approval.get("current_pending_approver") and code == "pending_approval":
        pa = approval["current_pending_approver"]
        overall = f"Pending approval — awaiting {pa.get('name')} (step {approval.get('current_pending_step')})"

    track = {
        "found": True,
        "work_order_id": work_order_id,
        "overall_status": overall,
        "overall_status_code": wo.status,
        "phase": phase,
        "work_order": {
            "title": wo.title,
            "asset": wo.asset,
            "location": wo.location,
            "priority": wo.priority,
            "request_type": wo.request_type,
            "issue_description": wo.issue_description,
            "requester_name": wo.requester_name,
            "requester_email": wo.requester_email,
            "vendor": wo.vendor,
            "scheduled_date": wo.scheduled_date,
            "scheduled_time": wo.scheduled_time,
            "estimated_duration": wo.estimated_duration,
            "cmms_work_order_id": wo.cmms_work_order_id,
            "created_at": wo.created_at.isoformat() if wo.created_at else None,
            "approved_at": wo.approved_at.isoformat() if wo.approved_at else None,
        },
        "approval": approval,
        "technician": technician,
        "scheduling": {
            "scheduled_date": wo.scheduled_date,
            "scheduled_time": wo.scheduled_time,
            "estimated_duration_hours": wo.estimated_duration,
        },
        "blockers": blockers,
        "status_history": history,
        "journey": jlog.to_dict() if jlog else None,
    }
    track["summary_message"] = _build_summary_message(wo, approval, technician, blockers, phase)
    track["formatted_summary"] = format_status_track_markdown(track)
    log.info("work_order.status_track", work_order_id=work_order_id, phase=phase, status=wo.status)
    return track
