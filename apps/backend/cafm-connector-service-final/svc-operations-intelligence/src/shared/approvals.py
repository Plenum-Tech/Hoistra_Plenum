"""Immutable ops audit + Approvals queue + email handoff (PRD Q1)."""
from __future__ import annotations

import hashlib
import json
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.logging import get_logger
from ..models import ApprovalsQueueItem, OpsAuditLog, OpsEmailLog
from .email_graph import graph_configured, send_via_microsoft_graph

log = get_logger(__name__)


async def apply_pm_action_track(
    session: AsyncSession,
    *,
    related_entity_type: str | None,
    related_entity_id: UUID | None,
    email_sent_to: str | None = None,
    email_status: str | None = None,
    renewal_initiated: bool = False,
    booking_approved: bool = False,
    queue_item_id: UUID | None = None,
) -> None:
    """
    Persist PM action trail on the related ComplianceCertificate:
    - email sent to PM for action
    - renewal initiated (Lapsed acknowledge)
    """
    if related_entity_type != "compliance_certificate" or not related_entity_id:
        return
    from ..models import ComplianceCertificate

    cert = await session.get(ComplianceCertificate, related_entity_id)
    if not cert:
        return
    meta = dict(cert.raw_metadata or {})
    now = datetime.now(timezone.utc).isoformat()
    if email_sent_to:
        meta["email_sent_to_pm"] = True
        meta["email_sent_to"] = email_sent_to
        meta["email_sent_at"] = now
        if email_status:
            meta["email_sent_status"] = email_status
        if queue_item_id:
            meta["email_queue_item_id"] = str(queue_item_id)
        meta["pm_action_status"] = "Email sent to PM for action"
        track = list(meta.get("pm_action_track") or [])
        track.append(
            {
                "at": now,
                "event": "email_sent_to_pm",
                "to": email_sent_to,
                "status": email_status,
                "queue_item_id": str(queue_item_id) if queue_item_id else None,
            }
        )
        meta["pm_action_track"] = track[-20:]
    if renewal_initiated:
        meta["renewal_initiated"] = True
        meta["renewal_initiated_at"] = now
        meta["pm_action_status"] = "Renewal initiated"
        if not cert.remedial_status or cert.remedial_status in {"Closed", "None"}:
            cert.remedial_status = "In Progress"
        track = list(meta.get("pm_action_track") or [])
        track.append(
            {
                "at": now,
                "event": "renewal_initiated",
                "queue_item_id": str(queue_item_id) if queue_item_id else None,
            }
        )
        meta["pm_action_track"] = track[-20:]
    if booking_approved:
        meta["booking_approved"] = True
        meta["booking_approved_at"] = now
        if not meta.get("renewal_initiated"):
            meta["pm_action_status"] = "Booking approved — renewal in progress"
        track = list(meta.get("pm_action_track") or [])
        track.append(
            {
                "at": now,
                "event": "booking_approved",
                "queue_item_id": str(queue_item_id) if queue_item_id else None,
            }
        )
        meta["pm_action_track"] = track[-20:]
    cert.raw_metadata = meta
    cert.updated_at = datetime.now(timezone.utc)


def build_email_handoff(draft: dict[str, Any]) -> dict[str, Any]:
    """
    PRD Q1 — platform generates the draft; PM sends from their own email client.
    Returns mailto URI + copy fields. OAuth integration is a later enhancement.
    """
    to_addr = (draft.get("to") or "").strip()
    subject = draft.get("subject") or ""
    body = draft.get("body") or ""
    cc = (draft.get("cc_senior") or draft.get("cc") or "").strip() or None
    mailto = f"mailto:{quote(to_addr, safe='@.+_-')}?subject={quote(subject)}&body={quote(body)}"
    if cc:
        mailto += f"&cc={quote(cc, safe='@.+_-')}"
    return {
        "mode": "handoff",
        "to": to_addr,
        "cc": cc,
        "subject": subject,
        "body": body,
        "mailto_uri": mailto,
        "message": (
            "Open mailto_uri in the PM email client, or copy subject/body. "
            "Platform does not send vendor/PM mail directly in this phase."
        ),
    }


def _hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def write_audit(
    session: AsyncSession,
    *,
    actor: str,
    action_type: str,
    source_feature: str | None = "A",
    organization_id: UUID | None = None,
    input_payload: Any = None,
    output_payload: Any = None,
    detail: dict[str, Any] | None = None,
) -> OpsAuditLog:
    row = OpsAuditLog(
        id=uuid4(),
        organization_id=organization_id,
        actor=actor,
        action_type=action_type,
        source_feature=source_feature,
        input_hash=_hash_payload(input_payload) if input_payload is not None else None,
        output_hash=_hash_payload(output_payload) if output_payload is not None else None,
        detail=detail or {},
    )
    session.add(row)
    await session.flush()
    return row


async def enqueue_approval(
    session: AsyncSession,
    *,
    source_feature: str,
    item_type: str,
    summary: str,
    severity: str,
    payload: dict[str, Any],
    organization_id: UUID | None = None,
    related_entity_type: str | None = None,
    related_entity_id: UUID | None = None,
    email_draft: dict[str, Any] | None = None,
) -> ApprovalsQueueItem:
    item = ApprovalsQueueItem(
        id=uuid4(),
        organization_id=organization_id,
        source_feature=source_feature,
        item_type=item_type,
        summary=summary,
        severity=severity,
        status="pending",
        payload=payload,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        email_draft=email_draft,
    )
    session.add(item)
    await session.flush()
    await write_audit(
        session,
        actor="system",
        action_type="approvals_queue.enqueue",
        source_feature=source_feature,
        organization_id=organization_id,
        output_payload={"queue_item_id": str(item.id), "item_type": item_type},
        detail={"severity": severity, "summary": summary},
    )
    return item


async def list_queue(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    status: str | None = "pending",
    source_feature: str | None = None,
    limit: int = 100,
) -> list[ApprovalsQueueItem]:
    q = select(ApprovalsQueueItem).order_by(ApprovalsQueueItem.created_at.desc()).limit(limit)
    if organization_id:
        q = q.where(ApprovalsQueueItem.organization_id == organization_id)
    if status:
        q = q.where(ApprovalsQueueItem.status == status)
    if source_feature:
        q = q.where(ApprovalsQueueItem.source_feature == source_feature)
    return list((await session.execute(q)).scalars().all())


async def decide_queue_item(
    session: AsyncSession,
    item_id: UUID,
    *,
    decision: str,
    pm_notes: str | None = None,
    edited_payload: dict[str, Any] | None = None,
    decided_by: UUID | None = None,
    prepare_email_handoff: bool = True,
) -> dict[str, Any]:
    item = await session.get(ApprovalsQueueItem, item_id)
    if not item:
        return {"ok": False, "error": "queue item not found"}

    decision_l = decision.lower()
    if decision_l not in {"approve", "edit", "dismiss"}:
        return {"ok": False, "error": "decision must be approve | edit | dismiss"}

    # Edit = save PM corrections and KEEP pending (do not vanish from Approvals rail).
    if decision_l == "edit":
        if edited_payload:
            item.payload = {**(item.payload or {}), **edited_payload}
            if edited_payload.get("extracted") and isinstance(edited_payload["extracted"], dict):
                prev_ex = dict((item.payload or {}).get("extracted") or {})
                item.payload["extracted"] = {**prev_ex, **edited_payload["extracted"]}
            # Flatten common cert fields into payload.extracted for the rail
            ex = dict((item.payload or {}).get("extracted") or {})
            for key in (
                "certificate_number",
                "issue_date",
                "expiry_date",
                "inspector_name",
                "result",
                "defects_found",
                "remedial_actions",
            ):
                if edited_payload.get(key) is not None:
                    ex[key] = edited_payload[key]
            if ex:
                item.payload = {**(item.payload or {}), "extracted": ex}
            if edited_payload.get("email_draft"):
                item.email_draft = edited_payload["email_draft"]
        item.status = "pending"
        item.pm_notes = pm_notes or item.pm_notes
        item.updated_at = datetime.now(timezone.utc)
        # Apply field corrections to draft certificate without finalising
        if (
            item.source_feature == "A"
            and item.item_type == "certificate_confirm"
            and item.related_entity_id
            and edited_payload
        ):
            from ..models import ComplianceCertificate
            from ..engines.compliance.lifecycle import compute_certificate_status
            from datetime import date as date_cls

            cert = await session.get(ComplianceCertificate, item.related_entity_id)
            if cert:
                if edited_payload.get("certificate_number"):
                    cert.certificate_number = str(edited_payload["certificate_number"])
                    cert.certificate_ref = cert.certificate_number
                if edited_payload.get("inspector_name"):
                    cert.inspector_name = str(edited_payload["inspector_name"])
                for date_key, attr in (("issue_date", "issue_date"), ("expiry_date", "expiry_date")):
                    raw = edited_payload.get(date_key)
                    if not raw:
                        continue
                    try:
                        parsed = date_cls.fromisoformat(str(raw)[:10])
                        setattr(cert, attr, parsed)
                    except ValueError:
                        pass
                if cert.expiry_date:
                    life = compute_certificate_status(cert.expiry_date)
                    cert.status = life.status
                    cert.days_to_expiry = life.days_to_expiry
                cert.updated_at = datetime.now(timezone.utc)
        await write_audit(
            session,
            actor=f"user:{decided_by}" if decided_by else "user:pm",
            action_type="approvals_queue.edit",
            source_feature=item.source_feature,
            organization_id=item.organization_id,
            output_payload={"queue_item_id": str(item.id), "status": item.status},
            detail={"pm_notes": pm_notes, "edited_payload": edited_payload},
        )
        await session.commit()
        return {
            "ok": True,
            "queue_item_id": str(item.id),
            "status": item.status,
            "message": "Edits saved — item remains pending until Approve or Dismiss.",
        }

    if decision_l == "dismiss":
        item.status = "dismissed"
    else:
        item.status = "approved"

    item.pm_notes = pm_notes
    item.decided_by = decided_by
    item.decided_at = datetime.now(timezone.utc)
    item.updated_at = datetime.now(timezone.utc)

    # Ladder acknowledge / booking approve → PM action trail on certificate
    if decision_l == "approve" and item.source_feature == "A" and item.related_entity_id:
        ladder_status = str(
            (item.payload or {}).get("status")
            or (item.email_draft or {}).get("status")
            or ""
        )
        payload = dict(item.payload or {})
        is_lapsed = ladder_status == "Lapsed" or (
            item.item_type == "alert" and "lapsed" in (item.summary or "").lower()
        )
        if is_lapsed:
            await apply_pm_action_track(
                session,
                related_entity_type=item.related_entity_type,
                related_entity_id=item.related_entity_id,
                renewal_initiated=True,
                queue_item_id=item.id,
            )
            payload["renewal_initiated"] = True
            payload["renewal_initiated_at"] = datetime.now(timezone.utc).isoformat()
            payload["pm_action_status"] = "Renewal initiated"
            item.payload = payload
        elif ladder_status in {"Due for Renewal", "Overdue", "Critical"} or item.item_type in {
            "booking_request",
        }:
            await apply_pm_action_track(
                session,
                related_entity_type=item.related_entity_type,
                related_entity_id=item.related_entity_id,
                booking_approved=True,
                renewal_initiated=ladder_status == "Critical",
                queue_item_id=item.id,
            )
            payload["booking_approved"] = True
            if ladder_status == "Critical":
                payload["renewal_initiated"] = True
                payload["pm_action_status"] = "Renewal initiated"
            else:
                payload["pm_action_status"] = "Booking approved — renewal in progress"
            item.payload = payload
            # Send contractor booking email + ICS (communication workflow, not a WO)
            if item.item_type == "booking_request" or ladder_status in {
                "Due for Renewal",
                "Overdue",
                "Critical",
            }:
                from ..engines.compliance.booking import fulfill_booking_on_approve

                booking_result = await fulfill_booking_on_approve(session, item)
                payload = dict(item.payload or {})
                payload["booking_fulfillment"] = booking_result.get("booking")
                item.payload = payload
        else:
            item.payload = payload

    # Feature A: approving a draft extract finalises the certificate (HITL gate).
    if (
        decision_l == "approve"
        and item.source_feature == "A"
        and item.item_type == "certificate_confirm"
        and item.related_entity_id
    ):
        from ..models import ComplianceCertificate

        cert = await session.get(ComplianceCertificate, item.related_entity_id)
        if cert:
            meta = dict(cert.raw_metadata or {})
            meta["confirmed_by_pm"] = True
            meta["requires_pm_confirmation"] = False
            meta.pop("draft", None)
            if edited_payload:
                if edited_payload.get("certificate_number"):
                    cert.certificate_number = str(edited_payload["certificate_number"])
                    cert.certificate_ref = cert.certificate_number
                if edited_payload.get("inspector_name"):
                    cert.inspector_name = str(edited_payload["inspector_name"])
                if edited_payload.get("result"):
                    cert.result = str(edited_payload["result"])
                if edited_payload.get("defects_found") is not None:
                    cert.defects_found = str(edited_payload["defects_found"])
                if edited_payload.get("remedial_actions") is not None:
                    cert.remedial_actions = str(edited_payload["remedial_actions"])
                from datetime import date as date_cls
                from ..engines.compliance.lifecycle import compute_certificate_status

                for date_key, attr in (("issue_date", "issue_date"), ("expiry_date", "expiry_date")):
                    raw = edited_payload.get(date_key)
                    if not raw:
                        continue
                    try:
                        setattr(cert, attr, date_cls.fromisoformat(str(raw)[:10]))
                    except ValueError:
                        pass
                if cert.expiry_date:
                    life = compute_certificate_status(cert.expiry_date)
                    cert.status = life.status
                    cert.days_to_expiry = life.days_to_expiry
                    if not meta.get("confirmed_by_pm"):
                        pass
                    else:
                        cert.insurance_risk_flag = life.insurance_risk_flag
            cert.raw_metadata = meta
            cert.updated_at = datetime.now(timezone.utc)
            # Fail/Advisory after confirm → open remedial + queue Action Required
            result_val = cert.result
            if result_val in {"Fail", "Advisory"}:
                cert.remedial_status = "Open"
                rem = await enqueue_approval(
                    session,
                    source_feature="A",
                    item_type="remedial",
                    summary=(
                        f"Remedial action required — {cert.certificate_type_code or cert.cert_type} "
                        f"result={result_val}"
                    ),
                    severity="Action required",
                    organization_id=item.organization_id or cert.organization_id or cert.org_id,
                    related_entity_type="compliance_certificate",
                    related_entity_id=cert.id,
                    payload={
                        "certificate_id": str(cert.id),
                        "defects_found": cert.defects_found,
                        "remedial_actions": cert.remedial_actions,
                        "note": "PM manages remedial via existing FM channel — no WO auto-created.",
                    },
                )
                log.info(
                    "approvals.certificate_confirm.remedial_opened",
                    certificate_id=str(cert.id),
                    queue_item_id=str(rem.id),
                )

    email_result = None
    already_sent = bool((item.payload or {}).get("email_sent"))
    # Ladder alerts send email via Send-to-PM first; don't overwrite approved → handoff_ready
    if (
        decision_l in {"approve", "edit"}
        and prepare_email_handoff
        and item.email_draft
        and not already_sent
        and item.item_type in {
            "certificate_confirm",
            "vendor_email",
            "forgery_alert",
            "verification_human",
        }
    ):
        mode = (settings.email_delivery_mode or "handoff").lower()
        if mode == "platform_send":
            # Legacy / future ops path — not the Phase 2 product default (PRD Q1).
            email_result = await send_platform_email(
                session,
                organization_id=item.organization_id,
                queue_item_id=item.id,
                to_address=item.email_draft.get("to") or settings.default_pm_email,
                subject=item.email_draft.get("subject") or item.summary,
                body=item.email_draft.get("body") or "",
            )
            if email_result.get("ok"):
                item.status = "sent"
        else:
            email_result = build_email_handoff(item.email_draft)
            item.status = "handoff_ready"
            await session.flush()
            session.add(
                OpsEmailLog(
                    id=uuid4(),
                    organization_id=item.organization_id,
                    queue_item_id=item.id,
                    to_address=email_result.get("to") or "",
                    subject=email_result.get("subject") or "",
                    body=email_result.get("body") or "",
                    status="handoff",
                    sent_at=datetime.now(timezone.utc),
                )
            )

    await write_audit(
        session,
        actor=f"user:{decided_by}" if decided_by else "user:pm",
        action_type=f"approvals_queue.{decision_l}",
        source_feature=item.source_feature,
        organization_id=item.organization_id,
        output_payload={"queue_item_id": str(item.id), "status": item.status},
        detail={"pm_notes": pm_notes, "email": email_result},
    )
    await session.commit()
    return {
        "ok": True,
        "queue_item_id": str(item.id),
        "status": item.status,
        "email": email_result,
        "renewal_initiated": bool((item.payload or {}).get("renewal_initiated")),
        "pm_action_status": (item.payload or {}).get("pm_action_status"),
        "email_sent": bool((item.payload or {}).get("email_sent")),
    }


async def send_platform_email(
    session: AsyncSession,
    *,
    to_address: str,
    subject: str,
    body: str,
    organization_id: UUID | None = None,
    queue_item_id: UUID | None = None,
    cc_address: str | None = None,
    attachments: list[dict[str, str]] | None = None,
    commit: bool = True,
    log_body: str | None = None,
) -> dict[str, Any]:
    """Platform sends email — Microsoft Graph (preferred) or SMTP.

    ``log_body`` is what gets written to ops_email_log in place of the message itself.
    Every email is recorded here, which is right for an audit trail and wrong for a
    message whose entire content is a live credential: a one-time code stored in a table
    anyone with read access can query is not one-time in any useful sense, and it outlives
    the ten minutes it was supposed to exist for. Callers sending a secret pass a redacted
    stand-in, so the record still proves an email went out and no longer contains the
    thing it was carrying.
    """
    row = OpsEmailLog(
        id=uuid4(),
        organization_id=organization_id,
        queue_item_id=queue_item_id,
        to_address=to_address,
        subject=subject,
        body=log_body if log_body is not None else body,
        status="queued",
    )
    session.add(row)
    await session.flush()

    use_graph = graph_configured()
    use_smtp = bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)

    async def _persist() -> None:
        if commit:
            await session.commit()
        else:
            await session.flush()

    if settings.email_dry_run or (not use_graph and not use_smtp):
        row.status = "dry_run"
        row.sent_at = datetime.now(timezone.utc)
        await _persist()
        log.info(
            "email.dry_run",
            to=to_address,
            cc=cc_address,
            subject=subject,
            email_id=str(row.id),
            graph_configured=use_graph,
            smtp_configured=use_smtp,
            attachments=len(attachments or []),
        )
        return {
            "ok": True,
            "status": "dry_run",
            "email_id": str(row.id),
            "to": to_address,
            "cc": cc_address,
            "subject": subject,
            "message": (
                f"Email queued to {to_address} (dry-run — set EMAIL_DRY_RUN=false and "
                "Azure Graph or SMTP credentials for live delivery)."
            ),
        }

    try:
        if use_graph:
            result = await send_via_microsoft_graph(
                to_address=to_address,
                subject=subject,
                body=body,
                cc_address=cc_address,
                attachments=attachments,
            )
            from_addr = str(result.get("from") or settings.outlook_user_mail)
        else:
            import base64 as _b64

            user = (settings.smtp_user or "").strip()
            password = settings.smtp_password or ""
            msg = EmailMessage()
            from_addr = (settings.smtp_from or settings.outlook_user_mail or user).strip()
            if from_addr.endswith(".local") or not from_addr:
                from_addr = user
            msg["From"] = from_addr
            msg["To"] = to_address
            if cc_address:
                msg["Cc"] = cc_address
            msg["Subject"] = subject
            msg.set_content(body)
            for att in attachments or []:
                raw = att.get("content_base64") or ""
                if not raw:
                    continue
                data = _b64.b64decode(raw)
                ctype = att.get("content_type") or "application/octet-stream"
                maintype, _, subtype = ctype.partition("/")
                msg.add_attachment(
                    data,
                    maintype=maintype or "application",
                    subtype=(subtype.split(";")[0] or "octet-stream"),
                    filename=att.get("name") or "attachment",
                )
            recipients = [to_address]
            if cc_address:
                recipients.extend(
                    [p.strip() for p in cc_address.split(",") if p.strip()]
                )
            use_ssl = bool(settings.smtp_use_ssl) or int(settings.smtp_port) == 465
            if use_ssl:
                with smtplib.SMTP_SSL(
                    settings.smtp_host, settings.smtp_port, timeout=30
                ) as smtp:
                    smtp.login(user, password)
                    smtp.send_message(msg, to_addrs=recipients)
            else:
                with smtplib.SMTP(
                    settings.smtp_host, settings.smtp_port, timeout=30
                ) as smtp:
                    if settings.smtp_use_tls:
                        smtp.ehlo()
                        smtp.starttls()
                        smtp.ehlo()
                    smtp.login(user, password)
                    smtp.send_message(msg, to_addrs=recipients)
            result = {"ok": True, "provider": "smtp", "from": from_addr}

        row.status = "sent"
        row.sent_at = datetime.now(timezone.utc)
        await _persist()
        provider = result.get("provider") or "email"
        return {
            "ok": True,
            "status": "sent",
            "email_id": str(row.id),
            "to": to_address,
            "cc": cc_address,
            "subject": subject,
            "provider": provider,
            "message": f"Email sent to {to_address} via {provider} (from {from_addr})",
        }
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:1000]
        hint = ""
        if "5.7.57" in err or "not authenticated" in err.lower():
            hint = (
                " | Prefer Azure Graph: AZURE_TENANT_ID / AZURE_CLIENT_ID / "
                "AZURE_CLIENT_SECRET / OUTLOOK_USER_MAIL with Mail.Send."
            )
        if "Authorization_RequestDenied" in err or "ErrorAccessDenied" in err:
            hint = (
                " | Grant Microsoft Graph application permission Mail.Send "
                "and admin consent for this app registration."
            )
        row.status = "failed"
        row.error_message = (err + hint)[:1000]
        await _persist()
        log.error("email.send_failed", error=err, email_id=str(row.id))
        return {
            "ok": False,
            "status": "failed",
            "error": err + hint,
            "email_id": str(row.id),
        }


async def send_approval_email_draft(
    session: AsyncSession,
    *,
    to_address: str,
    subject: str,
    body: str,
    cc_address: str | None = None,
    queue_item_id: UUID | None = None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Send (or dry-run) a compliance alert draft to a PM email entered in the UI."""
    to_addr = (to_address or "").strip()
    if not to_addr or "@" not in to_addr:
        return {"ok": False, "error": "Enter a valid PM email address"}
    if not (subject or "").strip():
        return {"ok": False, "error": "Subject is required"}
    if not (body or "").strip():
        return {"ok": False, "error": "Body is required"}

    org_id = organization_id
    item = None
    if queue_item_id:
        item = await session.get(ApprovalsQueueItem, queue_item_id)
        if item:
            org_id = org_id or item.organization_id
            # Persist chosen recipient on the draft for audit
            draft = dict(item.email_draft or {})
            draft["to"] = to_addr
            if cc_address:
                draft["cc_senior"] = cc_address
            draft["last_sent_at"] = datetime.now(timezone.utc).isoformat()
            item.email_draft = draft
            item.updated_at = datetime.now(timezone.utc)

    result = await send_platform_email(
        session,
        to_address=to_addr,
        subject=subject.strip(),
        body=body,
        cc_address=(cc_address or "").strip() or None,
        organization_id=org_id,
        queue_item_id=queue_item_id,
    )
    await write_audit(
        session,
        actor="user:pm",
        action_type="approvals_queue.email_send",
        source_feature=item.source_feature if item else "A",
        organization_id=org_id,
        output_payload={
            "queue_item_id": str(queue_item_id) if queue_item_id else None,
            "to": to_addr,
            "cc": cc_address,
            "status": result.get("status"),
            "email_id": result.get("email_id"),
        },
        detail={"subject": subject[:200]},
    )
    if item and result.get("ok"):
        # Keep pending so PM can still Acknowledge in Approvals; mark payload sent
        payload = dict(item.payload or {})
        sent_at = datetime.now(timezone.utc).isoformat()
        payload["email_sent"] = True
        payload["email_sent_status"] = result.get("status")
        payload["email_sent_to"] = to_addr
        payload["email_sent_at"] = sent_at
        payload["pm_action_status"] = "Email sent to PM for action"
        payload["pm_action_track"] = list(payload.get("pm_action_track") or []) + [
            {
                "at": sent_at,
                "event": "email_sent_to_pm",
                "to": to_addr,
                "status": result.get("status"),
            }
        ]
        item.payload = payload
        await apply_pm_action_track(
            session,
            related_entity_type=item.related_entity_type,
            related_entity_id=item.related_entity_id,
            email_sent_to=to_addr,
            email_status=str(result.get("status") or ""),
            queue_item_id=item.id,
        )
        # Sync siblings (confirm + ladder) for same certificate so Bell Review state matches
        if item.related_entity_id is not None:
            siblings = (
                await session.execute(
                    select(ApprovalsQueueItem).where(
                        ApprovalsQueueItem.status == "pending",
                        ApprovalsQueueItem.related_entity_type == item.related_entity_type,
                        ApprovalsQueueItem.related_entity_id == item.related_entity_id,
                        ApprovalsQueueItem.id != item.id,
                    )
                )
            ).scalars().all()
            for sib in siblings:
                sp = dict(sib.payload or {})
                sp["email_sent"] = True
                sp["email_sent_status"] = result.get("status")
                sp["email_sent_to"] = to_addr
                sp["email_sent_at"] = sent_at
                sp["pm_action_status"] = "Email sent to PM for action"
                sib.payload = sp
                sd = dict(sib.email_draft or {})
                if sd:
                    sd["last_sent_at"] = sent_at
                    sd["to"] = to_addr
                    sib.email_draft = sd
                sib.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return {
        **result,
        "pm_action_status": "Email sent to PM for action" if result.get("ok") else None,
        "email_sent_to": to_addr if result.get("ok") else None,
    }


def queue_item_to_dict(item: ApprovalsQueueItem) -> dict[str, Any]:
    payload = item.payload or {}
    return {
        "id": str(item.id),
        "organization_id": str(item.organization_id) if item.organization_id else None,
        "source_feature": item.source_feature,
        "item_type": item.item_type,
        "summary": item.summary,
        "severity": item.severity,
        "status": item.status,
        "payload": payload,
        "related_entity_type": item.related_entity_type,
        "related_entity_id": str(item.related_entity_id) if item.related_entity_id else None,
        "email_draft": item.email_draft,
        "pm_notes": item.pm_notes,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        # Convenience for FE track strip
        "email_sent": bool(payload.get("email_sent")),
        "email_sent_to": payload.get("email_sent_to"),
        "email_sent_at": payload.get("email_sent_at")
        or (item.email_draft or {}).get("last_sent_at"),
        "email_sent_status": payload.get("email_sent_status"),
        "renewal_initiated": bool(payload.get("renewal_initiated")),
        "pm_action_status": payload.get("pm_action_status"),
    }
