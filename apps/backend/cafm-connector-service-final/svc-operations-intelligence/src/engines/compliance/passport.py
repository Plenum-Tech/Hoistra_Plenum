"""Pre-engagement vendor passport + shareable link."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from urllib.parse import urlencode

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...shared.approvals import enqueue_approval, write_audit
from . import certificates as cert_svc
from . import resource_skills as skill_svc
from .coverage import vendor_coverage
from .tokens import create_approval_token, peek_approval_token


def _posture(accreditations: list[dict[str, Any]], block_state: str) -> dict[str, Any]:
    blocked = str(block_state or "").lower() == "blocked"
    lapsed = [
        a
        for a in accreditations
        if str(a.get("status") or "").lower() == "lapsed"
        or (isinstance(a.get("days_to_expiry"), int) and a["days_to_expiry"] < 0)
        or str(a.get("risk_badge") or "").lower() == "blocked"
    ]
    at_risk = [
        a
        for a in accreditations
        if str(a.get("status") or "").lower()
        in {"expiring soon", "due for renewal", "overdue", "critical"}
        or (isinstance(a.get("days_to_expiry"), int) and 0 <= a["days_to_expiry"] <= 60)
    ]
    current = [
        a for a in accreditations if str(a.get("status") or "").lower() == "current"
    ]
    if blocked or lapsed:
        status = "not_cleared"
        label = "Not cleared — do not engage for regulated work of the blocked/lapsed trade"
    elif not accreditations:
        status = "incomplete"
        label = "Incomplete — no vendor accreditations on file"
    elif at_risk:
        status = "conditional"
        label = "Conditional — renew at-risk accreditations before engagement"
    elif current:
        status = "cleared"
        label = "Cleared for engagement (subject to trade-specific checks)"
    else:
        status = "incomplete"
        label = "Incomplete — confirm accreditation currency"
    return {
        "status": status,
        "label": label,
        "current_count": len(current),
        "at_risk_count": len(at_risk),
        "lapsed_count": len(lapsed),
        "blocked": blocked,
    }


async def _load_vendor(session: AsyncSession, vendor_id: str) -> dict[str, Any] | None:
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT id::text AS id, vendor_name, vendor_code, status,
                               coalesce(block_state, 'Clear') AS block_state,
                               block_reason, blocked_accreditation_type, block_date,
                               phone, website, city, country, notes
                        FROM plenum_cafm.vendors
                        WHERE id::text = :vid
                           OR vendor_code = :vid
                        LIMIT 1
                        """
                    ),
                    {"vid": str(vendor_id)},
                )
            ).mappings().first()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001
        return None


async def build_vendor_passport(
    session: AsyncSession,
    vendor_id: str,
    *,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    vendor = await _load_vendor(session, vendor_id)
    if not vendor:
        return {"ok": False, "error": "vendor not found"}

    resolved_id = str(vendor["id"])
    # list_certificates expects UUID when column is UUID — fall back via vendor_id filter
    try:
        vid_uuid: UUID | None = UUID(resolved_id)
    except ValueError:
        vid_uuid = None

    accreditations = await cert_svc.list_certificates(
        session,
        cert_scope="Vendor",
        vendor_id=vid_uuid,
        organization_id=organization_id,
        limit=200,
    )
    if not accreditations and not vid_uuid:
        # Filter in Python when vendor PK is not a UUID
        all_v = await cert_svc.list_certificates(
            session,
            cert_scope="Vendor",
            organization_id=organization_id,
            limit=1000,
        )
        accreditations = [
            a
            for a in all_v
            if str(a.get("vendor_id") or "") == resolved_id
            or str(a.get("vendor_code") or "") == str(vendor.get("vendor_code") or "")
            or str(a.get("vendor_name") or "").lower()
            == str(vendor.get("vendor_name") or "").lower()
        ]
    insurance = [
        a
        for a in accreditations
        if "insurance" in str(a.get("certificate_type_code") or "").lower()
        or "insurance" in str(a.get("certificate_type_name") or "").lower()
    ]
    trade_certs = [a for a in accreditations if a not in insurance]

    skills: list[dict[str, Any]] = []
    try:
        if vid_uuid is not None:
            skills = await skill_svc.list_vendor_operative_skills(
                session, vendor_id=vid_uuid, limit=100
            )
    except Exception:  # noqa: BLE001
        skills = []

    cov = await vendor_coverage(session, organization_id=organization_id)
    cov_row = next(
        (
            v
            for v in (cov.get("vendors") or [])
            if str(v.get("vendor_id")) in {resolved_id, str(vendor_id), str(vendor.get("vendor_code") or "")}
        ),
        None,
    )
    posture = _posture(accreditations, str(vendor.get("block_state") or "Clear"))

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vendor": vendor,
        "posture": posture,
        "coverage": cov_row,
        "accreditations": trade_certs,
        "insurance": insurance,
        "skills": skills if isinstance(skills, list) else [],
        "verify_hints": [
            {
                "type": a.get("certificate_type_code"),
                "verification_url": a.get("verification_url"),
            }
            for a in accreditations
            if a.get("verification_url")
        ],
        "companies_house": {
            "checked": False,
            "status": "not_checked",
            "note": "Companies House standing is optional — connector not wired yet.",
        },
        "message": posture["label"],
    }


async def share_vendor_passport(
    session: AsyncSession,
    vendor_id: str,
    *,
    organization_id: UUID | None = None,
    ttl_hours: int = 168,
    recipient: str | None = None,
) -> dict[str, Any]:
    passport = await build_vendor_passport(
        session, str(vendor_id), organization_id=organization_id
    )
    if not passport.get("ok"):
        return passport

    resolved = str((passport.get("vendor") or {}).get("id") or vendor_id)
    vendor_name = (passport.get("vendor") or {}).get("vendor_name") or resolved
    related_uuid: UUID | None
    try:
        related_uuid = UUID(resolved)
    except ValueError:
        related_uuid = None
    item = await enqueue_approval(
        session,
        source_feature="A",
        item_type="passport_share",
        summary=f"Vendor passport shared — {vendor_name}",
        severity="Info",
        organization_id=organization_id,
        related_entity_type="vendor",
        related_entity_id=related_uuid,
        payload={
            "vendor_id": resolved,
            "recipient": recipient,
            "passport_snapshot": {
                "vendor": passport.get("vendor"),
                "posture": passport.get("posture"),
                "generated_at": passport.get("generated_at"),
                "accreditation_count": len(passport.get("accreditations") or []),
                "insurance_count": len(passport.get("insurance") or []),
            },
        },
    )
    token_info = await create_approval_token(
        session,
        item.id,
        action="view_passport",
        ttl_hours=ttl_hours,
    )
    base = (settings.frontend_public_url or "http://localhost:3000").rstrip("/")
    share_url = f"{base}/passport/{token_info['token']}"
    ai_url = f"{base}/ai?{urlencode({'space': 'compliance', 'passportToken': token_info['token']})}"

    await write_audit(
        session,
        actor="system",
        action_type="compliance.passport.share",
        source_feature="A",
        organization_id=organization_id,
        input_payload={"vendor_id": resolved, "recipient": recipient},
        output_payload={"share_url": share_url, "expires_at": token_info.get("expires_at")},
    )
    await session.commit()
    return {
        "ok": True,
        "vendor_id": resolved,
        "token": token_info["token"],
        "share_url": share_url,
        "ai_url": ai_url,
        "expires_at": token_info.get("expires_at"),
        "ttl_hours": ttl_hours,
        "posture": passport.get("posture"),
        "message": f"Shareable passport link created for {vendor_name}",
    }


async def passport_from_share_token(
    session: AsyncSession,
    token: str,
) -> dict[str, Any]:
    peeked = await peek_approval_token(session, token)
    if not peeked.get("ok"):
        return peeked
    item = peeked.get("item") or {}
    if item.get("item_type") != "passport_share":
        return {"ok": False, "error": "token is not a passport share link"}
    payload = item.get("payload") or {}
    vendor_id_raw = payload.get("vendor_id") or item.get("related_entity_id")
    if not vendor_id_raw:
        return {"ok": False, "error": "passport share missing vendor_id"}
    live = await build_vendor_passport(session, str(vendor_id_raw))
    live["share"] = {
        "token": token,
        "expires_at": peeked.get("expires_at"),
        "snapshot": payload.get("passport_snapshot"),
        "recipient": payload.get("recipient"),
    }
    return live
