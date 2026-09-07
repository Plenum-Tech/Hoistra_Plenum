"""Nightly / on-demand compliance scan — Planner + parallel Workers (A2 + A3)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ...db import AsyncSessionLocal
from ...models import ApprovalsQueueItem, ComplianceCertificate, ComplianceScanRun
from ...shared.approvals import apply_pm_action_track, enqueue_approval, send_platform_email, write_audit
from ...swarm.adversary import (
    validate_block_state_change,
    validate_lapsed_flag,
    validate_vendor_email_draft,
)
from ...swarm.planner import packet_to_dict, plan_compliance_scan
from ...swarm.worker import WorkerResult, merge_worker_results
from .country_pack import (
    effective_alert_thresholds,
    get_pack_type,
    resolve_site_pack_context,
)
from .contractors import recommend_contractors, resolve_asset_display, resolve_site_display
from .lifecycle import (
    STATUS_CRITICAL,
    STATUS_CURRENT,
    STATUS_DUE_FOR_RENEWAL,
    STATUS_EXPIRING_SOON,
    STATUS_LAPSED,
    STATUS_OVERDUE,
    compute_certificate_status,
    vendor_risk_level,
)
from .tokens import create_approval_token
from .resource_skills import lapsed_operative_flags_for_vendors

log = get_logger(__name__)


def _building_email_draft(
    *,
    cert: ComplianceCertificate,
    type_name: str,
    building_name: str,
    address: str | None,
    days: int | None,
    contractor: str | None,
    recommended_vendors: list[dict[str, Any]],
    prefix: str,
    status: str,
    one_click_url: str | None = None,
    certificate_link: str | None = None,
    asset_reference: str | None = None,
) -> dict[str, Any]:
    days_s = "unknown" if days is None else str(days)
    # Critical / Lapsed escalate to senior; Overdue stays with operational PM
    escalate = status in {STATUS_CRITICAL, STATUS_LAPSED} or prefix == "URGENT"
    # Risk level in the subject = the Building Alert Ladder band (Expiring Soon / Due for
    # Renewal / Overdue / Critical / Lapsed) so the PM sees severity at a glance.
    days_phrase = (
        "expired"
        if (days is not None and days < 0)
        else (f"{days_s} days remaining" if days is not None else "expiry unknown")
    )
    subject = f"[{status}] {type_name} — {building_name} — {days_phrase}"

    vendor_lines = "\n".join(
        f"  - {v.get('vendor_name')} ({v.get('accreditation')}) exp {v.get('expiry_date')}"
        for v in recommended_vendors
    ) or "  - see approved vendor list filtered by required accreditation"

    action_line = {
        STATUS_EXPIRING_SOON: "No action required — informational awareness for the PM.",
        STATUS_DUE_FOR_RENEWAL: (
            "Booking prompt: review the contractor recommendation and arrange "
            "renewal (5 business-day window)."
        ),
        STATUS_OVERDUE: (
            "PM approval required for the booking request — one click from this "
            "email or the Approvals queue."
        ),
        STATUS_CRITICAL: (
            "ESCALATION TO SENIOR CONTACT: certificate is within ≤7 days of expiry. "
            "Immediate PM action required — approve booking / initiate renewal now."
        ),
        STATUS_LAPSED: (
            "Certificate is PAST EXPIRY. Acknowledge immediately and initiate renewal. "
            "Insurance risk flag is set. Platform alert raised."
        ),
    }.get(status, "Please review certificate status in Compliance Saved Space.")

    to_addr = settings.default_pm_email
    cc_senior = None
    if status in {STATUS_CRITICAL, STATUS_LAPSED}:
        # Critical / Lapsed → senior PM primary, operational PM on CC when set
        to_addr = settings.senior_pm_email or settings.default_pm_email
        if settings.default_pm_email and settings.default_pm_email != to_addr:
            cc_senior = settings.default_pm_email
    elif escalate and settings.senior_pm_email:
        cc_senior = settings.senior_pm_email

    asset_line = asset_reference or "n/a"
    deep_link = certificate_link or (
        f"{settings.frontend_public_url.rstrip('/')}/ai"
        f"?space=compliance&certId={cert.id}"
    )

    body = (
        f"Dear Property Manager,\n\n"
        f"This is an automated Building Certificate Alert Ladder notification "
        f"from the Plenum Compliance Engine.\n\n"
        f"Status: {status}\n"
        f"Certificate type: {type_name}\n"
        f"Building: {building_name}\n"
        f"Address: {address or 'n/a'}\n"
        f"Asset reference: {asset_line}\n"
        f"Certificate number: {cert.certificate_number or cert.certificate_ref or 'n/a'}\n"
        f"Expiry date: {cert.expiry_date}\n"
        f"Days remaining: {days_s}\n"
        f"Required contractor accreditation: {contractor or 'n/a'}\n"
        f"Recommended contractors:\n{vendor_lines}\n\n"
        f"Action required:\n{action_line}\n\n"
        f"Certificate record (Compliance Saved Space):\n{deep_link}\n"
        f"Certificate id: {cert.id}\n"
    )
    # Platform deep-link CTA by ladder status
    if one_click_url:
        if status in {STATUS_DUE_FOR_RENEWAL, STATUS_OVERDUE, STATUS_CRITICAL}:
            body += (
                f"\nOpen in Plenum to approve booking:\n{one_click_url}\n"
            )
        elif status == STATUS_LAPSED:
            body += (
                f"\nOpen in Plenum to acknowledge & initiate renewal:\n{one_click_url}\n"
            )
        elif status == STATUS_EXPIRING_SOON:
            body += f"\nOpen in Plenum (informational):\n{one_click_url}\n"
    body += (
        "\nThis is a communication workflow — no work order is auto-created.\n"
        "Regards,\nPlenum Compliance Engine\n"
    )
    return {
        "to": to_addr,
        "subject": subject,
        "body": body,
        "cc_senior": cc_senior,
        "status": status,
        "days_to_expiry": days,
        "escalation": escalate,
        "one_click_url": one_click_url,
        "recommended_vendors": recommended_vendors,
        "ladder_action": action_line,
    }


def _vendor_email_draft(
    *,
    vendor_name: str,
    vendor_email: str,
    accreditation_type: str,
    expiry_date: Any,
    renewal_url: str | None,
    risk_level: str | None = None,
) -> dict[str, Any]:
    # Risk level in the subject = the Vendor risk band (Medium Risk / High Risk /
    # Lapsed / Blocked) so the PM sees severity at a glance.
    subject = (
        f"[{risk_level or 'High Risk'}] Accreditation renewal required — "
        f"{accreditation_type} — {vendor_name}"
    )
    body = (
        f"Dear {vendor_name},\n\n"
        f"Your accreditation ({accreditation_type}) is due for renewal.\n"
        f"Expiry date: {expiry_date}\n"
        f"Renewal register: {renewal_url or 'contact your issuing body'}\n\n"
        "Until renewed, you must not be assigned regulated work of this type.\n"
        "Please confirm renewal and provide the updated certificate.\n\n"
        "Regards,\nProperty Manager (via Plenum Compliance Engine)\n"
    )
    return {"to": vendor_email, "subject": subject, "body": body}


async def _fetch_vendor_contact(
    session: AsyncSession, vendor_id: UUID
) -> tuple[str, str | None]:
    """Return (vendor_name, primary_email).

    ``plenum_cafm.vendors.id`` is varchar (Fiix codes) while ``vendor_contacts.vendor_id``
    is uuid, so every join is cast to ::text. SAVEPOINT so a residual type error can't
    abort the whole vendor-worker transaction.
    """
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT v.vendor_name,
                               COALESCE(
                                 (SELECT vc.email FROM plenum_cafm.vendor_contacts vc
                                  WHERE vc.vendor_id::text = v.id::text
                                  ORDER BY vc.id LIMIT 1),
                                 NULL
                               ) AS email
                        FROM plenum_cafm.vendors v
                        WHERE v.id::text = :vid
                        """
                    ),
                    {"vid": str(vendor_id)},
                )
            ).mappings().first()
        if row:
            return row["vendor_name"] or "Vendor", row["email"]
    except Exception as exc:  # noqa: BLE001
        log.warning("scan.vendor_lookup_failed", error=str(exc))
    return "Vendor", None


async def _set_vendor_block(
    session: AsyncSession,
    vendor_id: UUID,
    *,
    accreditation_type: str,
    reason: str,
) -> bool:
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    UPDATE plenum_cafm.vendors
                    SET block_state = 'Blocked',
                        block_reason = :reason,
                        block_date = now(),
                        blocked_accreditation_type = :acct
                    WHERE id::text = :vid
                    """
                ),
                {"vid": str(vendor_id), "reason": reason, "acct": accreditation_type},
            )
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("scan.block_failed", vendor_id=str(vendor_id), error=str(exc))
        return False


async def _clear_vendor_block(
    session: AsyncSession,
    vendor_id: UUID,
    *,
    organization_id: UUID | None = None,
    accreditation_type: str | None = None,
    enqueue_confirm: bool = True,
) -> bool:
    """Clear Blocked state. Returns True if a row was updated (block lifted)."""
    from ...shared.approvals import enqueue_approval

    result = await session.execute(
        text(
            """
            UPDATE plenum_cafm.vendors
            SET block_state = 'Clear',
                block_reason = NULL,
                block_date = NULL,
                blocked_accreditation_type = NULL
            WHERE id::text = :vid AND COALESCE(block_state, 'Clear') = 'Blocked'
            RETURNING vendor_name
            """
        ),
        {"vid": str(vendor_id)},
    )
    row = result.mappings().first()
    if not row:
        return False
    if enqueue_confirm:
        vname = row.get("vendor_name") or "Vendor"
        acct = accreditation_type or "accreditation"
        await enqueue_approval(
            session,
            source_feature="A",
            item_type="block_lift",
            summary=f"Block lifted — {vname} ({acct})",
            severity="Info",
            organization_id=organization_id,
            related_entity_type="vendor",
            related_entity_id=vendor_id,
            payload={
                "block_state": "Clear",
                "accreditation_type": acct,
                "note": (
                    "Renewed certificate verified with future expiry. "
                    "Vendor may again be considered for regulated work of this type."
                ),
            },
        )
    return True


def _tally_adversary(adv: Any, wr: WorkerResult) -> None:
    if adv.approved:
        wr.adversary_passed += 1
    else:
        wr.adversary_failed += 1
        if getattr(adv, "outcome", None) == "flagged":
            wr.adversary_flagged += 1
        elif getattr(adv, "outcome", None) == "escalated":
            wr.adversary_escalated += 1


async def _enqueue_adversary_gate(
    session: AsyncSession,
    *,
    adv: Any,
    organization_id: UUID | None,
    related_entity_type: str,
    related_entity_id: UUID | None,
    summary: str,
    payload: dict[str, Any],
) -> None:
    """Flagged → Worker return path (queue for correction); Escalated → PM."""
    outcome = getattr(adv, "outcome", "flagged")
    tag = "ESCALATED" if outcome == "escalated" else "FLAGGED"
    await enqueue_approval(
        session,
        source_feature="A",
        item_type="adversary_gate",
        summary=f"[{tag}] {summary}",
        severity="Critical" if outcome == "escalated" else "Action required",
        organization_id=organization_id,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        payload={
            **payload,
            "adversary_outcome": outcome,
            "adversary_reasons": list(getattr(adv, "reasons", []) or []),
            "adversary_checks": dict(getattr(adv, "checks", {}) or {}),
            "no_work_order": True,
        },
    )


async def _process_building_certs(
    session: AsyncSession,
    certs: list[ComplianceCertificate],
    *,
    organization_id: UUID | None,
) -> WorkerResult:
    wr = WorkerResult(worker="building_cert_worker", stream="building", ok=True)
    for cert in certs:
        wr.scanned += 1
        meta0 = cert.raw_metadata or {}
        # Skip test fixtures / superseded duplicates / soft-archived from the alert ladder.
        # Normal PM-confirmation drafts still alert when Lapsed/expiring.
        if (
            meta0.get("a1_test_fixture")
            or meta0.get("superseded_duplicate")
            or meta0.get("archived")
        ):
            continue
        site_ctx = await resolve_site_pack_context(
            session,
            cert.site_id,
            fallback_country=cert.country_code or settings.default_country_code,
        )
        country = site_ctx["country_code"]
        # Align certificate country with building pack assignment (multi-country)
        if cert.site_id and site_ctx.get("source") == "building_country_pack":
            cert.country_code = country
        pack = await get_pack_type(
            session,
            cert.certificate_type_code or cert.cert_type or "",
            country_code=country,
            pack_version=site_ctx.get("pack_version"),
        )
        if pack is None and site_ctx.get("pack_version"):
            # Fall back to latest version if pinned version missing this type
            pack = await get_pack_type(
                session,
                cert.certificate_type_code or cert.cert_type or "",
                country_code=country,
            )
        thresholds = effective_alert_thresholds(pack.alert_thresholds if pack else None)
        life = compute_certificate_status(cert.expiry_date, thresholds=thresholds)
        cert.status = life.status
        cert.days_to_expiry = life.days_to_expiry
        cert.insurance_risk_flag = life.insurance_risk_flag
        cert.updated_at = datetime.now(timezone.utc)

        type_name = pack.certificate_type_name if pack else (cert.certificate_type_code or "Certificate")
        contractor = pack.required_contractor_accreditation if pack else None

        if life.status == STATUS_CURRENT:
            continue

        # Insurance risk adversary only for true Lapsed (past expiry)
        if life.status == STATUS_LAPSED or life.insurance_risk_flag:
            adv = await validate_lapsed_flag(session, certificate=cert)
            _tally_adversary(adv, wr)
            if not adv.approved:
                wr.details.append(
                    {
                        "certificate_id": str(cert.id),
                        "stream": "building",
                        "adversary": adv.outcome,
                        "reasons": adv.reasons,
                    }
                )
                cert.insurance_risk_flag = False
                await _enqueue_adversary_gate(
                    session,
                    adv=adv,
                    organization_id=organization_id or cert.organization_id or cert.org_id,
                    related_entity_type="compliance_certificate",
                    related_entity_id=cert.id,
                    summary=f"Lapsed flag blocked — {type_name}",
                    payload={"certificate_id": str(cert.id), "status": life.status},
                )
                # Still enqueue the Lapsed PM email — successor only blocks the
                # insurance_risk_flag, not visibility of the expired certificate.
            else:
                cert.insurance_risk_flag = True

        meta = life.alert_meta
        prefix = meta.get("subject_prefix") or "NOTICE"
        site_info = await resolve_site_display(session, cert.site_id)
        asset_info = await resolve_asset_display(session, cert.asset_id)
        cert_deep_link = (
            f"{settings.frontend_public_url.rstrip('/')}/ai"
            f"?space=compliance&certId={cert.id}"
        )
        recommended = await recommend_contractors(
            session,
            required_accreditation=contractor,
            organization_id=organization_id or cert.organization_id or cert.org_id,
        )
        draft = _building_email_draft(
            cert=cert,
            type_name=type_name,
            building_name=site_info["building_name"] or "Building",
            address=site_info.get("address"),
            days=life.days_to_expiry,
            contractor=contractor,
            recommended_vendors=recommended,
            prefix=prefix,
            status=life.status,
            certificate_link=cert_deep_link,
            asset_reference=asset_info.get("asset_reference"),
        )
        if meta.get("escalate_senior") and settings.senior_pm_email:
            draft["to"] = settings.senior_pm_email
            draft["escalation"] = True
            if settings.default_pm_email and settings.default_pm_email != draft["to"]:
                draft["cc_senior"] = settings.default_pm_email

        severity = meta.get("severity") or "Info"
        if life.status == STATUS_CRITICAL:
            severity = "Critical"
        item_type = (
            "booking_request"
            if meta.get("booking_draft") or meta.get("booking_prompt")
            else "alert"
        )

        # Channel routing per PRD ladder
        # Expiring Soon: email only (still create Bell/platform card as Info, no queue action)
        # Due: email + platform notification (queue item)
        # Overdue / Critical / Lapsed: email + Approvals queue
        should_enqueue = life.status in {
            STATUS_EXPIRING_SOON,
            STATUS_DUE_FOR_RENEWAL,
            STATUS_OVERDUE,
            STATUS_CRITICAL,
            STATUS_LAPSED,
        }
        if should_enqueue:
            if life.status == STATUS_EXPIRING_SOON:
                severity = "Info"
                item_type = "alert"
            elif life.status == STATUS_DUE_FOR_RENEWAL:
                severity = "Info"
                item_type = "booking_request"
            elif life.status == STATUS_OVERDUE:
                severity = "Action required"
                item_type = "booking_request"
            elif life.status == STATUS_CRITICAL:
                severity = "Critical"
                item_type = "booking_request"
            elif life.status == STATUS_LAPSED:
                severity = "Critical"
                item_type = "alert"

            # Avoid duplicate pending alerts for the same certificate + status
            existing = (
                await session.execute(
                    select(ApprovalsQueueItem).where(
                        ApprovalsQueueItem.status == "pending",
                        ApprovalsQueueItem.related_entity_type == "compliance_certificate",
                        ApprovalsQueueItem.related_entity_id == cert.id,
                        ApprovalsQueueItem.item_type.in_(["alert", "booking_request"]),
                    )
                )
            ).scalars().first()
            if existing and (existing.payload or {}).get("status") == life.status:
                continue

            item = await enqueue_approval(
                session,
                source_feature="A",
                item_type=item_type,
                summary=(
                    f"{life.status}: {type_name} — "
                    f"{life.days_to_expiry} days remaining"
                ),
                severity=severity,
                organization_id=organization_id or cert.organization_id or cert.org_id,
                related_entity_type="compliance_certificate",
                related_entity_id=cert.id,
                email_draft=draft,
                payload={
                    "certificate_id": str(cert.id),
                    "status": life.status,
                    "days_to_expiry": life.days_to_expiry,
                    "booking_window_business_days": 5,
                    "recommended_accreditation": contractor,
                    "recommended_vendors": recommended,
                    "ladder_channel": meta.get("channel"),
                    "ladder_action": meta.get("action"),
                    "no_work_order": True,
                },
            )
            # Platform deep-link for ladder actions (email → /ai Approvals UI)
            one_click = None
            if life.status in {
                STATUS_EXPIRING_SOON,
                STATUS_DUE_FOR_RENEWAL,
                STATUS_OVERDUE,
                STATUS_CRITICAL,
                STATUS_LAPSED,
            }:
                action = (
                    "acknowledge"
                    if life.status in {STATUS_LAPSED, STATUS_EXPIRING_SOON}
                    else "approve"
                )
                token_info = await create_approval_token(
                    session,
                    item.id,
                    action=action,
                    ladder_status=life.status,
                )
                draft["one_click_url"] = token_info["one_click_url"]
                draft["platform_action"] = action
                # Re-build body with deep link now that URL exists
                draft = _building_email_draft(
                    cert=cert,
                    type_name=type_name,
                    building_name=site_info["building_name"] or "Building",
                    address=site_info.get("address"),
                    days=life.days_to_expiry,
                    contractor=contractor,
                    recommended_vendors=recommended,
                    prefix=prefix,
                    status=life.status,
                    one_click_url=token_info["one_click_url"],
                    certificate_link=cert_deep_link,
                    asset_reference=asset_info.get("asset_reference"),
                )
                if meta.get("escalate_senior") and settings.senior_pm_email:
                    draft["to"] = settings.senior_pm_email
                    draft["escalation"] = True
                    if settings.default_pm_email and settings.default_pm_email != draft["to"]:
                        draft["cc_senior"] = settings.default_pm_email
                item.email_draft = draft
                one_click = token_info["one_click_url"]

            # Auto-blast ladder email on scan — except Critical (≤7d): PM must Review → Send
            if (
                settings.auto_send_ladder_emails
                and draft.get("to")
                and life.status != STATUS_CRITICAL
            ):
                send_res = await send_platform_email(
                    session,
                    to_address=str(draft["to"]),
                    subject=str(draft.get("subject") or item.summary),
                    body=str(draft.get("body") or ""),
                    cc_address=(draft.get("cc_senior") or None),
                    organization_id=item.organization_id,
                    queue_item_id=item.id,
                    commit=False,
                )
                if send_res.get("ok"):
                    payload = dict(item.payload or {})
                    sent_at = datetime.now(timezone.utc).isoformat()
                    payload["email_sent"] = True
                    payload["email_sent_status"] = send_res.get("status")
                    payload["email_sent_to"] = draft.get("to")
                    payload["email_sent_at"] = sent_at
                    payload["pm_action_status"] = "Email sent to PM for action"
                    payload["auto_sent_on_scan"] = True
                    item.payload = payload
                    draft["last_sent_at"] = sent_at
                    item.email_draft = draft
                    await apply_pm_action_track(
                        session,
                        related_entity_type="compliance_certificate",
                        related_entity_id=cert.id,
                        email_sent_to=str(draft.get("to")),
                        email_status=str(send_res.get("status") or ""),
                        queue_item_id=item.id,
                    )
            wr.alerts_created += 1
            wr.details.append(
                {
                    "certificate_id": str(cert.id),
                    "stream": "building",
                    "queue_item_id": str(item.id),
                    "status": life.status,
                    "one_click_url": one_click,
                    "channel": meta.get("channel"),
                    "action": meta.get("action"),
                    "email_auto_sent": bool(
                        settings.auto_send_ladder_emails
                        and (item.payload or {}).get("email_sent")
                    ),
                    "email_sent_status": (item.payload or {}).get("email_sent_status"),
                }
            )
    return wr


async def apply_vendor_cert_ladder(
    session: AsyncSession,
    cert: ComplianceCertificate,
    *,
    life: Any,
    organization_id: UUID | None,
    type_name: str,
    renewal_url: str | None,
) -> dict[str, Any]:
    """CCC §5 vendor risk ladder for ONE certificate.

    Medium Risk → informational queue item; High Risk / Lapsed → vendor renewal email
    draft (+ block_state=Blocked on Lapsed); Clear → lift any block. Reused by the nightly
    scan AND by ingestion (draft upsert) so vendor alerts fire the moment a certificate is
    ingested — not only on a scan. Returns tally counts + adversary outcomes for the caller.
    """
    result: dict[str, Any] = {
        "alerts_created": 0,
        "blocks_set": 0,
        "details": [],
        "adversaries": [],
    }
    risk = vendor_risk_level(life.days_to_expiry)
    vendor_id = cert.vendor_id
    if not vendor_id:
        return result

    vendor_name, vendor_email = await _fetch_vendor_contact(session, vendor_id)

    if risk == "Clear":
        if life.days_to_expiry is not None and life.days_to_expiry > 0:
            lifted = await _clear_vendor_block(
                session,
                vendor_id,
                organization_id=organization_id or cert.organization_id,
                accreditation_type=type_name,
            )
            if lifted:
                result["alerts_created"] += 1
        return result

    if risk == "Medium Risk":
        item = await enqueue_approval(
            session,
            source_feature="A",
            item_type="vendor_risk",
            summary=f"Medium Risk: {vendor_name} — {type_name} ({life.days_to_expiry}d)",
            severity="Info",
            organization_id=organization_id or cert.organization_id,
            related_entity_type="vendor",
            related_entity_id=vendor_id,
            payload={
                "certificate_id": str(cert.id),
                "risk": risk,
                "accreditation_type": type_name,
            },
        )
        result["alerts_created"] += 1
        result["details"].append(
            {
                "certificate_id": str(cert.id),
                "stream": "vendor",
                "risk": risk,
                "queue_item_id": str(item.id),
            }
        )
        return result

    to_email = vendor_email or settings.default_pm_email
    draft = _vendor_email_draft(
        vendor_name=vendor_name,
        vendor_email=to_email,
        accreditation_type=type_name,
        expiry_date=cert.expiry_date,
        renewal_url=renewal_url,
        risk_level=risk,
    )
    adv_email = validate_vendor_email_draft(
        vendor_contact_email=to_email,
        accreditation_type=type_name,
        renewal_url=renewal_url,
        draft=draft,
    )
    result["adversaries"].append(adv_email)
    if not adv_email.approved:
        result["details"].append(
            {
                "certificate_id": str(cert.id),
                "stream": "vendor",
                "adversary": adv_email.outcome,
                "reasons": adv_email.reasons,
            }
        )
        await _enqueue_adversary_gate(
            session,
            adv=adv_email,
            organization_id=organization_id or cert.organization_id,
            related_entity_type="vendor",
            related_entity_id=vendor_id,
            summary=f"Vendor email blocked — {vendor_name} ({type_name})",
            payload={"certificate_id": str(cert.id), "risk": risk},
        )
    else:
        await enqueue_approval(
            session,
            source_feature="A",
            item_type="vendor_email",
            summary=f"{risk}: {vendor_name} — {type_name}",
            severity="Critical" if risk == "Lapsed" else "Action required",
            organization_id=organization_id or cert.organization_id,
            related_entity_type="vendor",
            related_entity_id=vendor_id,
            email_draft=draft,
            payload={
                "certificate_id": str(cert.id),
                "risk": risk,
                "accreditation_type": type_name,
                "renewal_url": renewal_url,
            },
        )
        result["alerts_created"] += 1

    if risk == "Lapsed":
        adv_block = validate_block_state_change(
            accreditation_type=type_name,
            expiry_date=cert.expiry_date,
            proposed_block_state="Blocked",
        )
        result["adversaries"].append(adv_block)
        if adv_block.approved:
            ok = await _set_vendor_block(
                session,
                vendor_id,
                accreditation_type=type_name,
                reason=f"Accreditation lapsed: {type_name}",
            )
            if ok:
                result["blocks_set"] += 1
                await enqueue_approval(
                    session,
                    source_feature="A",
                    item_type="block_ack",
                    summary=f"Vendor blocked — {vendor_name} ({type_name})",
                    severity="Critical",
                    organization_id=organization_id or cert.organization_id,
                    related_entity_type="vendor",
                    related_entity_id=vendor_id,
                    payload={
                        "block_state": "Blocked",
                        "accreditation_type": type_name,
                        "note": (
                            "Governance state only — future WO Engine will enforce "
                            "at allocation. No WO suspension in Phase 2."
                        ),
                    },
                )
                result["alerts_created"] += 1
        else:
            result["details"].append(
                {
                    "certificate_id": str(cert.id),
                    "stream": "vendor",
                    "adversary": adv_block.outcome,
                    "reasons": adv_block.reasons,
                }
            )
            await _enqueue_adversary_gate(
                session,
                adv=adv_block,
                organization_id=organization_id or cert.organization_id,
                related_entity_type="vendor",
                related_entity_id=vendor_id,
                summary=f"Block blocked by Adversary — {vendor_name}",
                payload={"certificate_id": str(cert.id), "risk": risk},
            )
    return result


async def _process_vendor_certs(
    session: AsyncSession,
    certs: list[ComplianceCertificate],
    *,
    organization_id: UUID | None,
) -> WorkerResult:
    wr = WorkerResult(worker="vendor_cert_worker", stream="vendor", ok=True)
    for cert in certs:
        wr.scanned += 1
        country = cert.country_code or settings.default_country_code
        pack = await get_pack_type(
            session,
            cert.certificate_type_code or cert.cert_type or "",
            country_code=country,
        )
        thresholds = effective_alert_thresholds(pack.alert_thresholds if pack else None)
        life = compute_certificate_status(cert.expiry_date, thresholds=thresholds)
        cert.status = life.status
        cert.days_to_expiry = life.days_to_expiry
        cert.insurance_risk_flag = life.insurance_risk_flag
        cert.updated_at = datetime.now(timezone.utc)

        type_name = pack.certificate_type_name if pack else (cert.certificate_type_code or "Certificate")
        renewal_url = pack.verification_url if pack else None

        ladder = await apply_vendor_cert_ladder(
            session,
            cert,
            life=life,
            organization_id=organization_id,
            type_name=type_name,
            renewal_url=renewal_url,
        )
        wr.alerts_created += ladder["alerts_created"]
        wr.blocks_set += ladder["blocks_set"]
        wr.details.extend(ladder["details"])
        for adv in ladder["adversaries"]:
            _tally_adversary(adv, wr)
    return wr


async def _run_stream_session(
    stream: str,
    cert_ids: list[UUID],
    organization_id: UUID | None,
) -> WorkerResult:
    """Each Worker gets its own AsyncSession (sessions are not concurrency-safe)."""
    if not cert_ids:
        return WorkerResult(
            worker=f"{stream}_cert_worker",
            stream=stream,  # type: ignore[arg-type]
            ok=True,
        )
    async with AsyncSessionLocal() as session:
        try:
            certs = list(
                (
                    await session.execute(
                        select(ComplianceCertificate).where(
                            ComplianceCertificate.id.in_(cert_ids)
                        )
                    )
                ).scalars().all()
            )
            if stream == "building":
                wr = await _process_building_certs(
                    session, certs, organization_id=organization_id
                )
            else:
                wr = await _process_vendor_certs(
                    session, certs, organization_id=organization_id
                )
            await session.commit()
            return wr
        except Exception as exc:  # noqa: BLE001
            log.error("compliance.worker.failed", stream=stream, error=str(exc))
            await session.rollback()
            return WorkerResult(
                worker=f"{stream}_cert_worker",
                stream=stream,  # type: ignore[arg-type]
                ok=False,
                error=str(exc)[:500],
            )


async def run_compliance_scan(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    scope: str = "all",
    site_id: UUID | None = None,
    certificate_type_code: str | None = None,
    reverify: bool = True,
    reverify_limit: int = 100,
) -> dict[str, Any]:
    """
    Orchestrator → Planner → parallel Workers (Building A2 + Vendor A3) → Adversary.
    Workers use separate DB sessions so asyncio.gather is safe.
    """
    packet = plan_compliance_scan(
        scope=scope,
        organization_id=organization_id,
        site_id=site_id,
        certificate_type_code=certificate_type_code,
    )

    run = ComplianceScanRun(
        id=uuid4(),
        organization_id=organization_id,
        scope=scope,
        scope_filter={
            "site_id": str(site_id) if site_id else None,
            "certificate_type_code": certificate_type_code,
            "planner_packet_id": packet.packet_id,
        },
        status="running",
    )
    session.add(run)
    await session.flush()

    q = select(ComplianceCertificate)
    if organization_id:
        q = q.where(
            (ComplianceCertificate.organization_id == organization_id)
            | (ComplianceCertificate.org_id == organization_id)
        )
    if site_id:
        q = q.where(ComplianceCertificate.site_id == site_id)
    if certificate_type_code:
        q = q.where(ComplianceCertificate.certificate_type_code == certificate_type_code)
    if scope == "building":
        q = q.where(ComplianceCertificate.cert_scope == "Building")
    elif scope == "vendor":
        q = q.where(ComplianceCertificate.cert_scope == "Vendor")

    certs = list((await session.execute(q)).scalars().all())
    building_ids = [c.id for c in certs if (c.cert_scope or "") == "Building"]
    vendor_ids = [c.id for c in certs if (c.cert_scope or "") == "Vendor"]

    # Release parent row locks before parallel workers mutate the same certificates
    run_id = run.id
    await session.commit()

    worker_results: list[WorkerResult] = []
    tasks = []
    for task in packet.tasks:
        if task.stream == "building":
            tasks.append(_run_stream_session("building", building_ids, organization_id))
        elif task.stream == "vendor":
            tasks.append(_run_stream_session("vendor", vendor_ids, organization_id))

    if tasks:
        if packet.parallel and len(tasks) > 1:
            worker_results = list(await asyncio.gather(*tasks))
        else:
            for coro in tasks:
                worker_results.append(await coro)

    merged = merge_worker_results(worker_results)

    vendor_id_set = list({
        c.vendor_id for c in certs if c.cert_scope == "Vendor" and c.vendor_id
    })
    operative_flags = await lapsed_operative_flags_for_vendors(session, vendor_id_set)

    run = await session.get(ComplianceScanRun, run_id)
    if not run:
        return {"ok": False, "error": "scan run lost after workers", "scan_run_id": str(run_id)}

    run.building_scanned = merged["building_scanned"]
    run.vendor_scanned = merged["vendor_scanned"]
    run.alerts_created = merged["alerts_created"]
    run.blocks_set = merged["blocks_set"]
    run.adversary_passed = merged["adversary_passed"]
    run.adversary_failed = merged["adversary_failed"]
    run.status = "completed" if all(r.ok for r in worker_results) else "completed_with_errors"
    run.finished_at = datetime.now(timezone.utc)
    run.detail = {
        "items": merged["details"][:500],
        "streams": merged["streams"],
        "planner": packet_to_dict(packet),
        "operative_lapsed_by_vendor": operative_flags,
        "adversary_flagged": merged["adversary_flagged"],
        "adversary_escalated": merged["adversary_escalated"],
        "workers": [r.to_dict() for r in worker_results],
    }

    await write_audit(
        session,
        actor="system",
        action_type="compliance.scan",
        source_feature="A",
        organization_id=organization_id,
        output_payload={
            "scan_run_id": str(run.id),
            "building_scanned": merged["building_scanned"],
            "vendor_scanned": merged["vendor_scanned"],
            "alerts_created": merged["alerts_created"],
            "blocks_set": merged["blocks_set"],
            "adversary_flagged": merged["adversary_flagged"],
            "adversary_escalated": merged["adversary_escalated"],
            "operative_vendors_flagged": len(operative_flags),
            "planner_packet_id": packet.packet_id,
        },
    )
    await session.commit()

    # CCC §8.6 — every rescan also runs the 90-day re-verification: any active certificate
    # whose last register check is >90 days old (or never checked) is re-verified against its
    # source; failures raise a class-3 "human verification required" alert. Non-fatal so a
    # verification hiccup never fails the scan.
    reverification: dict[str, Any] | None = None
    if reverify:
        try:
            from .reverify import reverify_due_certificates

            reverification = await reverify_due_certificates(
                session,
                organization_id=organization_id,
                limit=reverify_limit,
            )
            log.info(
                "compliance.scan.reverify",
                scan_id=str(run.id),
                due=reverification.get("due_count"),
                processed=reverification.get("processed"),
            )
        except Exception as exc:  # noqa: BLE001 — reverify must not fail the scan
            log.warning("compliance.scan.reverify_failed", error=str(exc)[:200])
            reverification = {"ok": False, "error": str(exc)[:200]}

    log.info(
        "compliance.scan.complete",
        scan_id=str(run.id),
        building=merged["building_scanned"],
        vendor=merged["vendor_scanned"],
        alerts=merged["alerts_created"],
        blocks=merged["blocks_set"],
        parallel=packet.parallel,
    )
    return {
        "ok": True,
        "scan_run_id": str(run.id),
        "building_scanned": merged["building_scanned"],
        "vendor_scanned": merged["vendor_scanned"],
        "alerts_created": merged["alerts_created"],
        "blocks_set": merged["blocks_set"],
        "adversary_passed": merged["adversary_passed"],
        "adversary_failed": merged["adversary_failed"],
        "adversary_flagged": merged["adversary_flagged"],
        "adversary_escalated": merged["adversary_escalated"],
        "planner": packet_to_dict(packet),
        "operative_lapsed_by_vendor": operative_flags,
        "details": merged["details"],
        "reverification": reverification,
    }
