"""A2 renewal booking — contractor email + ICS calendar invite (no WO)."""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ...models import ApprovalsQueueItem, ComplianceCertificate
from ...shared.approvals import apply_pm_action_track, send_platform_email, write_audit
from .contractors import resolve_site_display

log = get_logger(__name__)


def _business_days_ahead(start: date, days: int) -> date:
    cur = start
    left = max(days, 1)
    while left > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            left -= 1
    return cur


def build_ics_invite(
    *,
    uid: str,
    summary: str,
    description: str,
    location: str,
    start_date: date,
    end_date: date | None = None,
    organizer_email: str | None = None,
) -> str:
    """Minimal VCALENDAR (all-day) for contractor booking window."""
    dt_end = end_date or (start_date + timedelta(days=1))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    org = (organizer_email or settings.outlook_user_mail or settings.default_pm_email or "").strip()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Plenum Compliance Engine//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{dt_end.strftime('%Y%m%d')}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"LOCATION:{_ics_escape(location)}",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
    ]
    if org:
        lines.append(f"ORGANIZER:mailto:{org}")
    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    return "\r\n".join(lines)


def _ics_escape(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def pick_contractor_contact(payload: dict[str, Any]) -> dict[str, str | None]:
    vendors = payload.get("recommended_vendors") or []
    if isinstance(vendors, list) and vendors:
        top = vendors[0] if isinstance(vendors[0], dict) else {}
        return {
            "vendor_name": str(top.get("vendor_name") or "Contractor"),
            "email": (top.get("email") or top.get("contact_email") or None),
            "accreditation": str(top.get("accreditation") or payload.get("recommended_accreditation") or ""),
        }
    return {
        "vendor_name": "Contractor",
        "email": None,
        "accreditation": str(payload.get("recommended_accreditation") or ""),
    }


async def fulfill_booking_on_approve(
    session: AsyncSession,
    item: ApprovalsQueueItem,
) -> dict[str, Any]:
    """
    After PM approves booking_request:
    - Draft + send contractor booking email
    - Attach ICS calendar invite for suggested 5-business-day window
    - Track on certificate metadata (still no WO)
    """
    payload = dict(item.payload or {})
    contact = pick_contractor_contact(payload)
    to_addr = (contact.get("email") or "").strip() or None
    # Fall back to PM so booking is never silently dropped when vendor email missing
    if not to_addr:
        to_addr = settings.default_pm_email
        contact["vendor_name"] = f"{contact.get('vendor_name')} (via PM — no vendor email on file)"

    window_days = int(payload.get("booking_window_business_days") or 5)
    start = _business_days_ahead(date.today(), 1)
    end = _business_days_ahead(start, window_days)

    site_id = None
    cert = None
    if item.related_entity_id and item.related_entity_type == "compliance_certificate":
        cert = await session.get(ComplianceCertificate, item.related_entity_id)
        if cert:
            site_id = cert.site_id
    site = await resolve_site_display(session, site_id)
    building = site.get("building_name") or "Building"
    address = site.get("address") or "n/a"
    cert_type = (
        (cert.certificate_type_code if cert else None)
        or payload.get("certificate_type")
        or "Inspection"
    )
    inspection = f"{cert_type} renewal / statutory inspection"

    subject = (
        f"[BOOKING REQUEST] {inspection} — {building} — "
        f"{start.isoformat()} to {end.isoformat()}"
    )
    body = (
        f"Dear {contact.get('vendor_name')},\n\n"
        f"The Property Manager has approved a booking request via the Plenum "
        f"Compliance Engine (communication workflow — not a work order).\n\n"
        f"Inspection type: {inspection}\n"
        f"Required accreditation: {contact.get('accreditation') or 'n/a'}\n"
        f"Building: {building}\n"
        f"Address / access: {address}\n"
        f"Suggested window: {start.isoformat()} → {end.isoformat()} "
        f"({window_days} business days)\n"
        f"Access requirements: Coordinate with on-site PM; bring accreditation ID.\n\n"
        f"Please confirm the appointment. An ICS calendar invite is attached.\n\n"
        f"Queue item: {item.id}\n"
        f"Regards,\nPlenum Compliance Engine\n"
    )

    ics = build_ics_invite(
        uid=f"booking-{item.id}@plenum-compliance",
        summary=f"{inspection} — {building}",
        description=body[:1500],
        location=f"{building}, {address}",
        start_date=start,
        end_date=end + timedelta(days=1),
    )
    ics_b64 = base64.b64encode(ics.encode("utf-8")).decode("ascii")

    send_result = await send_platform_email(
        session,
        to_address=to_addr,
        subject=subject,
        body=body,
        organization_id=item.organization_id,
        queue_item_id=item.id,
        cc_address=settings.default_pm_email if to_addr != settings.default_pm_email else None,
        attachments=[
            {
                "name": f"booking-{item.id}.ics",
                "content_type": "text/calendar; method=REQUEST",
                "content_base64": ics_b64,
            }
        ],
        commit=False,
    )

    booking_meta = {
        "booking_request_sent": True,
        "booking_sent_at": datetime.now(timezone.utc).isoformat(),
        "booking_to": to_addr,
        "booking_vendor": contact.get("vendor_name"),
        "booking_window_start": start.isoformat(),
        "booking_window_end": end.isoformat(),
        "booking_email_status": send_result.get("status"),
        "ics_attached": True,
        "no_work_order": True,
    }
    payload.update(booking_meta)
    payload["pm_action_status"] = "Booking request sent to contractor (ICS attached)"
    item.payload = payload

    if cert:
        await apply_pm_action_track(
            session,
            related_entity_type="compliance_certificate",
            related_entity_id=cert.id,
            booking_approved=True,
            queue_item_id=item.id,
        )
        meta = dict(cert.raw_metadata or {})
        meta.update(booking_meta)
        cert.raw_metadata = meta

    await write_audit(
        session,
        actor="system:booking",
        action_type="approvals_queue.booking_request_sent",
        source_feature="A",
        organization_id=item.organization_id,
        output_payload={
            "queue_item_id": str(item.id),
            "to": to_addr,
            "status": send_result.get("status"),
        },
        detail={"subject": subject[:200]},
    )
    log.info(
        "booking.contractor_email",
        queue_item_id=str(item.id),
        to=to_addr,
        status=send_result.get("status"),
    )
    return {"ok": True, "booking": booking_meta, "email": send_result}
