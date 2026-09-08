"""Certificate CRUD + ingest helpers (Building A2 / Vendor A3)."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...shared.vendor_identity import find_vendor_id
from ...core.logging import get_logger
from ...models import ComplianceCertificate
from ...shared.approvals import enqueue_approval, write_audit
from .country_pack import effective_alert_thresholds, get_pack_type, list_pack_types
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

# Brand / trade synonyms → CountryPack certificate_type_code (taxonomy Q&A).
CERT_TYPE_SYNONYMS: dict[str, str] = {
    "gas safe": "GAS_SAFE",
    "gas safe registration": "GAS_SAFE",
    "gassafe": "GAS_SAFE",
    "niceic": "NICEIC",
    "napit": "NAPIT",
    "bpca": "BPCA",
    "bafe": "BAFE",
    "sia": "SIA",
    "cscs": "CSCS",
    "loler": "LOLER",
    "eicr": "EICR",
    "fra": "FIRE_RISK_ASSESSMENT",
    "fire risk assessment": "FIRE_RISK_ASSESSMENT",
    "fire risk": "FIRE_RISK_ASSESSMENT",
}

# Lifecycle statuses that count as "at risk" (valid but in a renewal window).
AT_RISK_STATUSES = {
    STATUS_EXPIRING_SOON,
    STATUS_DUE_FOR_RENEWAL,
    STATUS_OVERDUE,
    STATUS_CRITICAL,
}

# Chat / PM synonyms → ladder status (or special "__at_risk__" mode).
STATUS_QUERY_ALIASES: dict[str, str] = {
    "expired": STATUS_LAPSED,
    "expire": STATUS_LAPSED,
    "expiry": STATUS_LAPSED,
    "past expiry": STATUS_LAPSED,
    "past due": STATUS_LAPSED,
    "lapsed": STATUS_LAPSED,
    "non compliant": STATUS_LAPSED,
    "non-compliant": STATUS_LAPSED,
    "noncompliant": STATUS_LAPSED,
    "non_compliant": STATUS_LAPSED,
    "compliant": STATUS_CURRENT,
    "current": STATUS_CURRENT,
    "active": STATUS_CURRENT,
    "expiring": STATUS_EXPIRING_SOON,
    "expiring soon": STATUS_EXPIRING_SOON,
    "due": STATUS_DUE_FOR_RENEWAL,
    "due for renewal": STATUS_DUE_FOR_RENEWAL,
    "overdue": STATUS_OVERDUE,
    "critical": STATUS_CRITICAL,
    "at risk": "__at_risk__",
    "at-risk": "__at_risk__",
    "atrisk": "__at_risk__",
    "at_risk": "__at_risk__",
}


def resolve_cert_type_code(raw: str | None) -> str | None:
    """Map a brand/trade synonym to a pack type code when possible."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    syn = CERT_TYPE_SYNONYMS.get(s.lower())
    if syn:
        return syn
    # Already a code-looking token
    if s.isupper() or "_" in s:
        return s.upper().replace(" ", "_")
    return s


def normalize_status_query(status: str | None) -> str | None:
    """Map expired/compliant/at-risk chat wording onto ladder statuses."""
    if not status or not str(status).strip():
        return None
    key = (
        str(status)
        .strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
    )
    key = " ".join(key.split())
    if key in STATUS_QUERY_ALIASES:
        return STATUS_QUERY_ALIASES[key]
    # Also try without spaces
    compact = key.replace(" ", "")
    for alias, canon in STATUS_QUERY_ALIASES.items():
        if alias.replace(" ", "").replace("-", "") == compact:
            return canon
    return str(status).strip()


def _row_matches_status(row: dict[str, Any], status_query: str) -> bool:
    """Match enriched (recomputed) lifecycle status — not stale DB-only values."""
    wanted = normalize_status_query(status_query) or status_query
    row_status = (row.get("status") or "").strip()
    if wanted == "__at_risk__":
        return row_status in AT_RISK_STATUSES
    rs = row_status.lower()
    w = wanted.lower()
    return rs == w or rs.startswith(w) or w.startswith(rs)
from .document_forensics import merge_authenticity_warnings
from .verification import soft_authenticity_warning

log = get_logger(__name__)

PASS_FAIL = {"Pass", "Fail", "Advisory", "Satisfactory", "Unsatisfactory"}

_DEFAULT_ORG_CACHE: UUID | None = None


async def default_org_native(session: AsyncSession) -> Any:
    """The platform organization in whatever type the column actually uses.

    resolve_default_org below casts the row to UUID, because its own caller assigns the
    result to a UUID column on the certificate. But plenum_cafm.organizations.id is an
    INTEGER, so that cast always throws and the function always returns None — which meant
    a vendor-scope certificate arriving without an explicit org could never create or link
    its vendor. The certificate landed, the vendor did not exist, nothing was blocked, and
    every vendor-derived number quietly under-counted.

    Vendor creation writes to vendors.organization_id, itself a legacy INTEGER, so it wants
    the value exactly as stored. Keeping the two lookups separate is the point: one feeds a
    UUID column, the other feeds an integer one, and forcing either to serve both is what
    broke this.
    """
    try:
        row = (
            await session.execute(
                text("SELECT id FROM plenum_cafm.organizations ORDER BY id LIMIT 1")
            )
        ).mappings().first()
        return row["id"] if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("cert.default_org_native_failed", error=str(exc)[:200])
        return None


async def resolve_default_org(session: AsyncSession) -> UUID | None:
    """Fallback organization for a record ingested WITHOUT an explicit org — e.g. a compliance
    certificate uploaded on its own. Without an org, ``resolve_or_create_vendor`` can't create the
    vendor (it needs organization_id), so a vendor-scope cert is left orphaned and the risk scan
    skips it (no block, no red lock, no email). Using the platform's organization here lets the
    vendor be created and the block chain work. Uses the single/first organization row; cached
    per process. Returns None only if there are no organizations at all.
    """
    global _DEFAULT_ORG_CACHE
    if _DEFAULT_ORG_CACHE is not None:
        return _DEFAULT_ORG_CACHE
    try:
        row = (
            await session.execute(
                text("SELECT id FROM plenum_cafm.organizations ORDER BY id LIMIT 1")
            )
        ).mappings().first()
        if row and row.get("id"):
            _DEFAULT_ORG_CACHE = UUID(str(row["id"]))
            return _DEFAULT_ORG_CACHE
    except Exception as exc:  # noqa: BLE001 — best-effort fallback
        log.warning("cert.default_org_lookup_failed", error=str(exc))
    return None


def normalize_inspection_result(raw: Any) -> str | None:
    """
    Map UK pack Pass/Refer/Fail and C1/C2 codes onto canonical result enum.

    Fire Door / EICR often use "C1 danger present — immediate action" instead of Fail.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in PASS_FAIL:
        return s
    low = s.lower()
    if re.search(
        r"\bc1\b|danger\s+present|immediate\s+action|\bfail(?:ed|ure)?\b|"
        r"\brefer\b|unsatisfactor",
        low,
    ):
        return "Fail"
    if re.search(r"\bc2\b|advisory|potentially\s+dangerous", low):
        return "Advisory"
    if re.search(r"\bc3\b|improvement", low):
        return "Advisory"
    if re.search(r"\bpass\b|satisfactor", low) and "unsatisfactor" not in low:
        return "Pass"
    return None


async def _enqueue_fail_remedial_alert(
    session: AsyncSession,
    *,
    cert: ComplianceCertificate,
    pack: Any,
    result_val: str,
    defects_found: str | None,
    organization_id: UUID | None,
) -> str | None:
    """
    Critical remedial queue item + email draft for Fail / C1 immediate action.
    Shows in Bell Review → center chat even while certificate is still a draft.
    """
    from ...models import ApprovalsQueueItem
    from ...config import settings
    from .contractors import resolve_site_display

    type_name = (
        (pack.certificate_type_name if pack else None)
        or cert.certificate_type_code
        or "Certificate"
    )
    site_info = await resolve_site_display(session, cert.site_id)
    building = site_info.get("building_name") or "Building"
    defects = (defects_found or "").strip() or result_val
    to_addr = settings.senior_pm_email or settings.default_pm_email
    draft = {
        "to": to_addr,
        "cc_senior": (
            settings.default_pm_email
            if settings.default_pm_email and settings.default_pm_email != to_addr
            else None
        ),
        "subject": (
            f"[URGENT] {type_name} FAIL / C1 — immediate action required — {building}"
        ),
        "body": (
            f"Dear Property Manager,\n\n"
            f"A building certificate inspection has recorded a FAIL / C1 result "
            f"requiring immediate action.\n\n"
            f"Certificate type: {type_name}\n"
            f"Certificate number: {cert.certificate_number or cert.certificate_ref or 'n/a'}\n"
            f"Building: {building}\n"
            f"Address: {site_info.get('address') or 'n/a'}\n"
            f"Result: {result_val}\n"
            f"Defects / findings: {defects}\n"
            f"Remedial status: Open\n\n"
            f"Action required: Acknowledge in Approvals / Bell and arrange remedial "
            f"works via existing FM channel (no work order is auto-created).\n\n"
            f"Certificate id: {cert.id}\n"
            f"Regards,\nPlenum Compliance Engine\n"
        ),
        "status": "Fail",
        "escalation": True,
        "days_to_expiry": cert.days_to_expiry,
    }

    existing = (
        await session.execute(
            select(ApprovalsQueueItem)
            .where(
                ApprovalsQueueItem.status == "pending",
                ApprovalsQueueItem.item_type == "remedial",
                ApprovalsQueueItem.related_entity_type == "compliance_certificate",
                ApprovalsQueueItem.related_entity_id == cert.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    summary = (
        f"Remedial action required — {type_name} result={result_val} "
        f"(immediate action)"
    )
    payload = {
        "certificate_id": str(cert.id),
        "certificate_type_code": cert.certificate_type_code,
        "result": result_val,
        "defects_found": defects,
        "remedial_actions": cert.remedial_actions,
        "status": "Fail",
        "review_before_send": True,
        "note": (
            "PM manages remedial work via existing FM channel — no WO auto-created."
        ),
    }

    if existing:
        existing.summary = summary
        existing.severity = "Critical"
        existing.email_draft = draft
        existing.payload = {**(existing.payload or {}), **payload}
        item = existing
    else:
        item = await enqueue_approval(
            session,
            source_feature="A",
            item_type="remedial",
            summary=summary,
            severity="Critical",
            organization_id=organization_id or cert.organization_id or cert.org_id,
            related_entity_type="compliance_certificate",
            related_entity_id=cert.id,
            email_draft=draft,
            payload=payload,
        )

    # Attach draft to pending confirm so Bell Review opens center-chat track
    confirm = (
        await session.execute(
            select(ApprovalsQueueItem)
            .where(
                ApprovalsQueueItem.status == "pending",
                ApprovalsQueueItem.item_type == "certificate_confirm",
                ApprovalsQueueItem.related_entity_id == cert.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if confirm:
        confirm.severity = "Critical"
        confirm.summary = summary
        confirm.email_draft = draft
        confirm.payload = {
            **(confirm.payload or {}),
            "result": result_val,
            "status": "Fail",
            "review_before_send": True,
            "defects_found": defects,
            "certificate_id": str(cert.id),
        }

    return str(item.id)


async def _attach_draft_to_certificate_confirm(
    session: AsyncSession,
    *,
    cert_id: UUID,
    draft: dict[str, Any],
    life: Any,
    type_name: str,
) -> None:
    """
    Copy ladder email_draft onto pending certificate_confirm for the same cert.

    Bell shows Confirm rows with severity Review; without a draft there is no
    Review button and center-chat inject has nothing to open.
    """
    from ...models import ApprovalsQueueItem

    confirm = (
        await session.execute(
            select(ApprovalsQueueItem)
            .where(
                ApprovalsQueueItem.status == "pending",
                ApprovalsQueueItem.item_type == "certificate_confirm",
                ApprovalsQueueItem.related_entity_type == "compliance_certificate",
                ApprovalsQueueItem.related_entity_id == cert_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if not confirm:
        return
    confirm.email_draft = draft
    if life.status in {STATUS_CRITICAL, STATUS_LAPSED} or life.status == "Fail":
        confirm.severity = "Critical"
        confirm.summary = (
            f"{life.status}: {type_name} — {life.days_to_expiry} days remaining"
            if life.status != "Fail"
            else f"Remedial action required — {type_name} (immediate action)"
        )
    confirm.payload = {
        **(confirm.payload or {}),
        "certificate_id": str(cert_id),
        "status": life.status,
        "days_to_expiry": life.days_to_expiry,
        "review_before_send": life.status == STATUS_CRITICAL,
        "has_ladder_email_draft": True,
    }


async def _enqueue_building_ladder_alert(
    session: AsyncSession,
    *,
    cert: ComplianceCertificate,
    pack: Any,
    life: Any,
    organization_id: UUID | None,
) -> str | None:
    """
    Create Bell/Approvals ladder item with email draft for Review → Send in chat.
    Critical (≤7d) is never auto-sent here — PM must Review then Send.
    """
    from ...models import ApprovalsQueueItem
    from ...config import settings
    from .contractors import recommend_contractors, resolve_asset_display, resolve_site_display
    from .scan import _building_email_draft
    from .tokens import create_approval_token

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
        # Refresh email draft if missing so Review still works
        if not (existing.email_draft or {}).get("body"):
            pass  # fall through to rebuild below by updating existing
        else:
            await _attach_draft_to_certificate_confirm(
                session,
                cert_id=cert.id,
                draft=existing.email_draft or {},
                life=life,
                type_name=(
                    (pack.certificate_type_name if pack else None)
                    or cert.certificate_type_code
                    or "Certificate"
                ),
            )
            return str(existing.id)

    meta = life.alert_meta or {}
    prefix = meta.get("subject_prefix") or "NOTICE"
    contractor = pack.required_contractor_accreditation if pack else None
    type_name = (
        (pack.certificate_type_name if pack else None)
        or cert.certificate_type_code
        or "Certificate"
    )
    site_info = await resolve_site_display(session, cert.site_id)
    asset_info = await resolve_asset_display(session, cert.asset_id)
    recommended = await recommend_contractors(
        session,
        required_accreditation=contractor,
        organization_id=organization_id or cert.organization_id or cert.org_id,
    )
    cert_deep_link = (
        f"{settings.frontend_public_url.rstrip('/')}/ai"
        f"?space=compliance&certId={cert.id}"
    )
    draft = _building_email_draft(
        cert=cert,
        type_name=type_name,
        building_name=site_info.get("building_name") or "Building",
        address=site_info.get("address"),
        days=life.days_to_expiry,
        contractor=contractor,
        recommended_vendors=recommended,
        prefix=prefix,
        status=life.status,
        certificate_link=cert_deep_link,
        asset_reference=asset_info.get("asset_reference"),
    )

    if life.status == STATUS_CRITICAL:
        severity = "Critical"
        item_type = "booking_request"
    elif life.status == STATUS_OVERDUE:
        severity = "Action required"
        item_type = "booking_request"
    elif life.status == STATUS_DUE_FOR_RENEWAL:
        severity = "Info"
        item_type = "booking_request"
    elif life.status == STATUS_LAPSED:
        severity = "Critical"
        item_type = "alert"
    else:
        severity = "Info"
        item_type = "alert"

    if existing:
        item = existing
        item.summary = f"{life.status}: {type_name} — {life.days_to_expiry} days remaining"
        item.severity = severity
        item.item_type = item_type
        item.email_draft = draft
        item.payload = {
            **(item.payload or {}),
            "certificate_id": str(cert.id),
            "status": life.status,
            "days_to_expiry": life.days_to_expiry,
            "booking_window_business_days": 5,
            "recommended_accreditation": contractor,
            "recommended_vendors": recommended,
            "no_work_order": True,
            "review_before_send": life.status == STATUS_CRITICAL,
        }
    else:
        item = await enqueue_approval(
            session,
            source_feature="A",
            item_type=item_type,
            summary=f"{life.status}: {type_name} — {life.days_to_expiry} days remaining",
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
                "no_work_order": True,
                "review_before_send": life.status == STATUS_CRITICAL,
            },
        )

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
    draft = _building_email_draft(
        cert=cert,
        type_name=type_name,
        building_name=site_info.get("building_name") or "Building",
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
    if life.status == STATUS_CRITICAL:
        draft["to"] = settings.senior_pm_email or settings.default_pm_email
        draft["escalation"] = True
        draft["cc_senior"] = (
            settings.default_pm_email
            if settings.default_pm_email and settings.default_pm_email != draft["to"]
            else None
        )
    item.email_draft = draft
    await _attach_draft_to_certificate_confirm(
        session,
        cert_id=cert.id,
        draft=draft,
        life=life,
        type_name=type_name,
    )
    return str(item.id)


async def _safe_exec(session: AsyncSession, coro_factory, *, label: str, default=None):
    """
    Run optional enrichment SQL inside a SAVEPOINT.

    Bare try/except around session.execute leaves Postgres in
    InFailedSQLTransactionError — later queries (e.g. risk snapshots) then 500.
    """
    try:
        async with session.begin_nested():
            return await coro_factory()
    except Exception as exc:  # noqa: BLE001
        log.warning(label, error=str(exc)[:300])
        return default


def cert_to_dict(c: ComplianceCertificate) -> dict[str, Any]:
    doc_id = c.document_id or c.source_document_id
    meta = c.raw_metadata or {}
    return {
        "id": str(c.id),
        "organization_id": str(c.organization_id or c.org_id) if (c.organization_id or c.org_id) else None,
        "certificate_ref": c.certificate_ref,
        "certificate_type_code": c.certificate_type_code or c.cert_type,
        "certificate_number": c.certificate_number,
        "cert_scope": c.cert_scope,
        "asset_id": str(c.asset_id) if c.asset_id else None,
        "site_id": str(c.site_id) if c.site_id else None,
        # The same link when the sites table is not UUID-keyed. Coverage and the dashboard
        # group on whichever of the two is set, so a varchar-keyed portfolio groups per
        # building rather than collapsing into one portfolio-wide bucket.
        "site_ref": getattr(c, "site_ref", None),
        "site_label": meta.get("site_label"),
        "building_name": c.building_name,
        "building_reference": c.building_reference,
        # The resolved graph link, and the basis it was made on.
        "building_id": str(c.building_id) if getattr(c, "building_id", None) else None,
        "building_link": meta.get("building_link"),
        "graph_document_id": meta.get("graph_document_id"),
        "vendor_id": str(c.vendor_id) if c.vendor_id else None,
        "issue_date": c.issue_date.isoformat() if c.issue_date else None,
        "expiry_date": c.expiry_date.isoformat() if c.expiry_date else None,
        "next_due_date": c.next_due_date.isoformat() if c.next_due_date else None,
        "inspection_frequency_months": c.inspection_frequency_months,
        "inspector_name": c.inspector_name,
        "inspector_accreditation_number": c.inspector_accreditation_number,
        "issuer": c.issuer,
        "result": c.result,
        "defects_found": c.defects_found,
        "remedial_actions": c.remedial_actions,
        "remedial_status": c.remedial_status,
        "status": c.status,
        "days_to_expiry": c.days_to_expiry,
        "insurance_risk_flag": c.insurance_risk_flag,
        "authenticity_warning": c.authenticity_warning,
        "country_code": c.country_code,
        # Sub-national grouping, so the dashboard can scope below country level.
        "state": c.state,
        "region": c.region,
        # Whether the location came from the document, was derived, or was assigned.
        # A report that groups by region should be able to say which it is.
        "location_source": meta.get("location_source") or ("document_sourced" if c.state else None),
        "document_id": str(doc_id) if doc_id else None,
        "linked_documents": (
            [{"id": str(doc_id), "label": "Source document", "document_id": str(doc_id)}]
            if doc_id
            else []
        ),
        # Document→entity links the PM can approve/skip when finalising this draft at Review.
        "proposed_links": _proposed_document_links(c),
        "draft": bool(meta.get("draft") or meta.get("requires_pm_confirmation")),
        "requires_pm_confirmation": bool(
            meta.get("requires_pm_confirmation") or meta.get("draft")
        ),
        "confirmed_by_pm": bool(meta.get("confirmed_by_pm")),
        "email_sent_to_pm": bool(meta.get("email_sent_to_pm")),
        "email_sent_to": meta.get("email_sent_to"),
        "email_sent_at": meta.get("email_sent_at"),
        "email_sent_status": meta.get("email_sent_status"),
        "renewal_initiated": bool(meta.get("renewal_initiated")),
        "renewal_initiated_at": meta.get("renewal_initiated_at"),
        "booking_approved": bool(meta.get("booking_approved")),
        "pm_action_status": meta.get("pm_action_status"),
        "pm_action_track": meta.get("pm_action_track") or [],
        # Authenticity / document-forensics, surfaced top-level so "which certs are forged?"
        # style questions have the complete signal without digging into raw_metadata.
        "forensics_verdict": (
            (meta.get("forensics") or {}).get("verdict") or meta.get("forensics_verdict")
        ),
        "forensics_ccc_verdict": (meta.get("forensics") or {}).get("ccc_verdict"),
        "forensics_risk_score": (
            (meta.get("forensics") or {}).get("risk_score")
            if (meta.get("forensics") or {}).get("risk_score") is not None
            else meta.get("forensics_risk_score")
        ),
        "forensics_findings": [
            fi.get("message")
            for fi in ((meta.get("forensics") or {}).get("findings") or [])
            if isinstance(fi, dict) and fi.get("message")
        ],
        "raw_metadata": meta,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


async def _enrich_certificate_rows(
    session: AsyncSession,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach CountryPack + vendor/site display fields for Saved Space UI (A5 / PRD rows)."""
    from ...models import CountryCertificatePack
    from .contractors import resolve_site_display

    if not rows:
        return rows

    # Always recompute days/status from expiry_date so KPIs and badges match today
    # (stored days_to_expiry goes stale between nightly scans).
    today = date.today()
    for r in rows:
        expiry = _parse_date(r.get("expiry_date"))
        life = compute_certificate_status(expiry, thresholds=None, today=today)
        r["days_to_expiry"] = life.days_to_expiry
        if life.status:
            r["status"] = life.status
        r["insurance_risk_flag"] = bool(life.insurance_risk_flag)

    type_codes = {
        r["certificate_type_code"]
        for r in rows
        if r.get("certificate_type_code")
    }
    pack_by_key: dict[tuple[str, str], Any] = {}
    if type_codes:
        pack_rows = list(
            (
                await session.execute(
                    select(CountryCertificatePack).where(
                        CountryCertificatePack.certificate_type_code.in_(type_codes),
                        CountryCertificatePack.is_active.is_(True),
                    )
                )
            ).scalars().all()
        )
        for p in pack_rows:
            key = (p.country_code or "UK", p.certificate_type_code)
            prev = pack_by_key.get(key)
            if prev is None or str(p.pack_version) > str(prev.pack_version):
                pack_by_key[key] = p

    vendor_ids = [UUID(r["vendor_id"]) for r in rows if r.get("vendor_id")]
    vendor_meta: dict[str, dict[str, Any]] = {}
    if vendor_ids:

        async def _load_vendors():
            stmt = text(
                """
                SELECT id, vendor_name, block_state, blocked_accreditation_type,
                       block_reason, block_date
                FROM plenum_cafm.vendors
                WHERE id::text IN :ids
                """
            ).bindparams(bindparam("ids", expanding=True))
            vrows = (
                await session.execute(stmt, {"ids": [str(v) for v in vendor_ids]})
            ).mappings().all()
            meta: dict[str, dict[str, Any]] = {}
            for v in vrows:
                meta[str(v["id"])] = {
                    "vendor_name": v.get("vendor_name"),
                    "vendor_block_state": v.get("block_state") or "Clear",
                    "blocked_accreditation_type": v.get("blocked_accreditation_type"),
                    "block_reason": v.get("block_reason"),
                    "block_date": (
                        v["block_date"].isoformat() if v.get("block_date") else None
                    ),
                }
            return meta

        vendor_meta = (
            await _safe_exec(
                session,
                _load_vendors,
                label="certificates.vendor_enrich_failed",
                default={},
            )
            or {}
        )

    asset_ids = [UUID(r["asset_id"]) for r in rows if r.get("asset_id")]
    asset_codes: dict[str, str] = {}
    if asset_ids:

        async def _load_assets():
            stmt = text(
                """
                SELECT id, asset_code FROM plenum_cafm.assets
                WHERE id IN :ids
                """
            ).bindparams(bindparam("ids", expanding=True))
            arows = (await session.execute(stmt, {"ids": asset_ids})).mappings().all()
            return {
                str(a["id"]): a["asset_code"] for a in arows if a.get("asset_code")
            }

        asset_codes = (
            await _safe_exec(
                session,
                _load_assets,
                label="certificates.asset_enrich_failed",
                default={},
            )
            or {}
        )

    site_cache: dict[str, dict[str, str | None]] = {}
    for r in rows:
        sid = r.get("site_id")
        if sid and sid not in site_cache:
            site_cache[sid] = await resolve_site_display(session, UUID(sid))

    for r in rows:
        country = r.get("country_code") or "UK"
        code = r.get("certificate_type_code") or ""
        pack = pack_by_key.get((country, code)) or pack_by_key.get(("UK", code))
        if pack:
            r["certificate_type_name"] = pack.certificate_type_name
            r["trade_category"] = pack.trade_category
            r["regulation_reference"] = pack.regulation_reference
            r["regulation_url"] = pack.regulation_url
            r["verification_url"] = pack.verification_url
            r["issuing_body"] = pack.issuing_body
            r["required_contractor_accreditation"] = (
                pack.required_contractor_accreditation
            )
            # Re-evaluate with pack-specific thresholds when available
            expiry = _parse_date(r.get("expiry_date"))
            th = getattr(pack, "alert_thresholds", None) or None
            if expiry is not None and th:
                life = compute_certificate_status(expiry, thresholds=th, today=today)
                r["days_to_expiry"] = life.days_to_expiry
                r["status"] = life.status
                r["insurance_risk_flag"] = bool(life.insurance_risk_flag)
        else:
            r.setdefault("certificate_type_name", code)
            r.setdefault("trade_category", None)
            r.setdefault("regulation_reference", None)

        if r.get("vendor_id") and r["vendor_id"] in vendor_meta:
            r.update(vendor_meta[r["vendor_id"]])
            r["vendor_on_record"] = True
        else:
            # Draft / unlinked vendor certs often have the name only in raw_metadata. Also true
            # when vendor_id is dangling (vendor deleted from the table) — not in vendor_meta.
            meta = r.get("raw_metadata") or {}
            fallback_name = (
                meta.get("vendor_name")
                or meta.get("company_name")
                or meta.get("contractor_name")
                or None
            )
            r.setdefault("vendor_name", fallback_name)
            r.setdefault("vendor_block_state", None)
            r["vendor_on_record"] = False

        if r.get("asset_id"):
            r["asset_code"] = asset_codes.get(r["asset_id"])
            r["asset_reference"] = r.get("asset_code") or r["asset_id"]
        else:
            r["asset_code"] = None
            r["asset_reference"] = None

        if r.get("site_id") and r["site_id"] in site_cache:
            site = site_cache[r["site_id"]]
            r["building_name"] = r.get("building_name") or site.get("building_name")
            r["building_address"] = site.get("address")
            r["building_reference"] = (
                r.get("building_reference") or site.get("building_name") or r["site_id"]
            )
        else:
            # No resolvable site FK — use the building_name / building_reference columns captured
            # from the document at ingestion (fall back to raw_metadata for legacy rows) so the
            # row still shows which building it belongs to.
            rmeta = r.get("raw_metadata") if isinstance(r.get("raw_metadata"), dict) else {}
            bn = r.get("building_name") or rmeta.get("building_name") or rmeta.get("site_name")
            r["building_name"] = bn
            r["building_address"] = rmeta.get("building_address") or rmeta.get("address")
            r["building_reference"] = r.get("building_reference") or bn or r.get("site_id")

        risk = vendor_risk_level(r.get("days_to_expiry"))
        r["risk_level"] = risk
        if (r.get("vendor_block_state") or "").lower() == "blocked":
            r["risk_badge"] = "Blocked"
        elif risk == "Lapsed":
            r["risk_badge"] = "Blocked"
        elif risk == "High Risk":
            r["risk_badge"] = "High"
        elif risk == "Medium Risk":
            r["risk_badge"] = "Medium"
        else:
            r["risk_badge"] = r.get("status") or "Clear"

    return rows


def _matches_risk_filter(row: dict[str, Any], risk_filter: str) -> bool:
    rf = risk_filter.strip().lower()
    badge = (row.get("risk_badge") or "").lower()
    risk = (row.get("risk_level") or "").lower()
    block = (row.get("vendor_block_state") or "").lower()
    days = row.get("days_to_expiry")
    if rf in {"blocked", "lapsed"}:
        return block == "blocked" or badge == "blocked" or risk == "lapsed"
    if rf in {"high", "high_risk", "high risk"}:
        return risk == "high risk" or (
            isinstance(days, int) and 0 < days <= 30 and block != "blocked"
        )
    if rf in {"medium", "medium_risk", "medium risk"}:
        return risk == "medium risk" or (
            isinstance(days, int) and 30 < days <= 90
        )
    if rf in {"expiring_90", "<90", "lt90"}:
        return isinstance(days, int) and 0 < days <= 90
    if rf in {"expiring_30", "<30", "lt30"}:
        return isinstance(days, int) and 0 < days <= 30
    if rf == "active":
        return (row.get("status") or "") == "Current" or (
            isinstance(days, int) and days > 90
        )
    return True


_ENTITY_TABLES_READY = False


async def ensure_entity_tables(session: AsyncSession) -> None:
    """Bootstrap the plenum_cafm vendors / sites / assets tables if a tenant schema doesn't have
    them yet, so a compliance document can create its entity even before any UDR migration has
    run. Applies ``migrations/compliance_entity_bootstrap.sql`` — all CREATE TABLE IF NOT EXISTS,
    so it's a no-op when the base schema already has these tables. Runs once per process,
    best-effort (never blocks ingestion)."""
    global _ENTITY_TABLES_READY
    if _ENTITY_TABLES_READY:
        return
    from pathlib import Path

    from ...db import _split_sql, engine

    sql_path = (
        Path(__file__).resolve().parents[2] / "migrations" / "compliance_entity_bootstrap.sql"
    )
    if not sql_path.exists():
        _ENTITY_TABLES_READY = True
        return
    try:
        sql = sql_path.read_text(encoding="utf-8")
        async with engine.begin() as conn:
            for stmt in _split_sql(sql):
                if stmt.strip():
                    await conn.exec_driver_sql(stmt)
        _ENTITY_TABLES_READY = True
        log.info("compliance.entity_tables_ready")
    except Exception as exc:  # noqa: BLE001 — bootstrap must never crash ingestion
        log.warning("compliance.entity_bootstrap_failed", error=str(exc)[:200])


def _first_nonempty(*values: Any) -> Any:
    """First value that is not None and not blank after trimming."""
    for v in values:
        if v is None:
            continue
        if str(v).strip():
            return v
    return None


def _norm_key(k: Any) -> str:
    """Normalise a field label for alias matching: lowercase, punctuation → spaces, collapsed."""
    return re.sub(r"[^a-z0-9]+", " ", str(k).lower()).strip()


def _pick_field(
    sources: tuple[dict[str, Any], ...],
    aliases: tuple[str, ...],
    *,
    exclude: tuple[str, ...] = (),
) -> Any:
    """Find a value whose field label matches one of ``aliases`` (word-boundary, any label the
    document uses). ``aliases`` are tried in priority order; ``exclude`` tokens veto a key
    (e.g. so a building reference never picks up a *certificate* number)."""
    for alias in aliases:
        for src in sources:
            if not isinstance(src, dict):
                continue
            for k, v in src.items():
                if v is None or not str(v).strip():
                    continue
                nk = _norm_key(k)
                if any(x in nk for x in exclude):
                    continue
                if f" {alias} " in f" {nk} ":
                    return v
    return None


def _meta_dict(v: Any) -> dict[str, Any]:
    """raw_metadata may come back from asyncpg as a JSON string — coerce to a dict."""
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str):
        try:
            import json

            parsed = json.loads(v)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


async def _supersede_prior_vendor_certs(
    session: AsyncSession,
    cert: ComplianceCertificate,
    *,
    org_id: Any,
) -> int:
    """Renewal supersedes older same-type certificates for the SAME vendor.

    When a newer accreditation of the same type + same vendor is ingested, mark the older
    row(s) ``superseded_duplicate`` so they drop out of the lists/KPIs and the alert ladder
    (the scan skips superseded rows). Without this, a lapsed prior certificate keeps the
    vendor Blocked even after the renewal has been uploaded. Only supersedes rows whose
    coverage is EARLIER than the current one — never an older re-upload superseding a newer
    row. If the renewal is itself valid and no other live accreditation for the vendor is
    still lapsed, the vendor block is lifted immediately (the scan would also do this).
    """
    type_code = cert.certificate_type_code or cert.cert_type
    if cert.cert_scope != "Vendor" or not type_code:
        return 0
    vendor_name = (_meta_dict(cert.raw_metadata).get("vendor_name") or "").strip().lower()
    if cert.vendor_id is None and not vendor_name:
        return 0

    q = select(ComplianceCertificate).where(
        ComplianceCertificate.certificate_type_code == type_code,
        ComplianceCertificate.cert_scope == "Vendor",
        ComplianceCertificate.id != cert.id,
    )
    if org_id:
        q = q.where(
            (ComplianceCertificate.organization_id == org_id)
            | (ComplianceCertificate.org_id == org_id)
        )

    def _same_vendor(cand: ComplianceCertificate) -> bool:
        if (
            cert.vendor_id is not None
            and cand.vendor_id is not None
            and str(cand.vendor_id) == str(cert.vendor_id)
        ):
            return True
        cand_name = (_meta_dict(cand.raw_metadata).get("vendor_name") or "").strip().lower()
        return bool(vendor_name) and cand_name == vendor_name

    superseded = 0
    for cand in (await session.execute(q)).scalars().all():
        if not _same_vendor(cand):
            continue
        # Only supersede EARLIER coverage. If the current upload has no expiry we can't compare
        # safely, so skip (avoid superseding a newer row with an undated one).
        if not cert.expiry_date:
            continue
        if cand.expiry_date and cand.expiry_date >= cert.expiry_date:
            continue
        meta = _meta_dict(cand.raw_metadata)
        if meta.get("superseded_duplicate"):
            continue
        meta["superseded_duplicate"] = True
        meta["superseded_by"] = str(cert.id)
        meta["superseded_at"] = datetime.now(timezone.utc).isoformat()
        cand.raw_metadata = meta
        cand.updated_at = datetime.now(timezone.utc)
        superseded += 1

    # Lift the block promptly when the renewal is valid and nothing else keeps the vendor blocked.
    current_lapsed = (cert.status or "").strip().lower() in {"lapsed", "expired"} or (
        isinstance(cert.days_to_expiry, int) and cert.days_to_expiry < 0
    )
    if superseded and cert.vendor_id is not None and not current_lapsed:
        others = (
            await session.execute(
                select(ComplianceCertificate).where(
                    ComplianceCertificate.cert_scope == "Vendor",
                    ComplianceCertificate.vendor_id == cert.vendor_id,
                    ComplianceCertificate.id != cert.id,
                )
            )
        ).scalars().all()
        still_lapsed = False
        for o in others:
            if _meta_dict(o.raw_metadata).get("superseded_duplicate"):
                continue
            st = (o.status or "").strip().lower()
            if st in {"lapsed", "expired"} or (
                isinstance(o.days_to_expiry, int) and o.days_to_expiry < 0
            ):
                still_lapsed = True
                break
        if not still_lapsed:
            try:
                from .scan import _clear_vendor_block

                await _clear_vendor_block(
                    session,
                    cert.vendor_id,
                    organization_id=org_id if isinstance(org_id, UUID) else None,
                    accreditation_type=type_code,
                    enqueue_confirm=False,
                )
            except Exception as exc:  # noqa: BLE001 — block-lift is best-effort
                log.warning("compliance.supersede_block_lift_failed", error=str(exc)[:200])

    if superseded:
        log.info(
            "compliance.superseded_prior_certs",
            certificate_id=str(cert.id),
            type_code=type_code,
            count=superseded,
        )
    return superseded


_UK_NATIONS = {
    "england": "England", "eng": "England",
    "scotland": "Scotland", "sct": "Scotland", "scot": "Scotland",
    "wales": "Wales", "cymru": "Wales", "wls": "Wales",
    "northern ireland": "Northern Ireland", "n ireland": "Northern Ireland",
    "ni": "Northern Ireland", "nir": "Northern Ireland",
}


_US_STATES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
    "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
    "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
}

_UAE_EMIRATES = {
    "auh": "Abu Dhabi", "abu dhabi": "Abu Dhabi", "dxb": "Dubai", "dubai": "Dubai",
    "shj": "Sharjah", "sharjah": "Sharjah", "ajm": "Ajman", "ajman": "Ajman",
    "uaq": "Umm Al Quwain", "umm al quwain": "Umm Al Quwain",
    "rak": "Ras Al Khaimah", "ras al khaimah": "Ras Al Khaimah",
    "fuj": "Fujairah", "fujairah": "Fujairah",
}


def _normalize_state(value: Any, country: str | None) -> str | None:
    """Canonical first-level division for the certificate's country, spelled out in full.

    Free text on the way in — an extractor may return "scotland", "SCT", "WA" or
    "Washington" — but a report grouped by state must not show those as separate places,
    and must not show a bare code next to a full name. Every recognised value expands to
    its full name; anything unrecognised is kept as written rather than discarded, since
    a value we cannot map is still better than losing the location entirely.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    cc = (country or "").strip().upper()
    key = raw.lower()
    if cc in ("UK", "GB"):
        return _UK_NATIONS.get(key, raw[:64])
    if cc == "US":
        return _US_STATES.get(key, _US_STATES.get(key.replace(".", ""), raw[:64]))
    if cc in ("UAE", "AE"):
        return _UAE_EMIRATES.get(key, raw[:64])
    return raw[:64]


def _normalize_region(value: Any) -> str | None:
    """City or county below `state` — title-cased so "london" and "London" group as one.

    Deliberately not mapped against a fixed list: regions vary by country and change,
    and an unrecognised region is still useful to report on. Only the casing is
    normalised, and only when the source is all-lower or all-upper, so a value that
    already carries deliberate capitalisation ("Kings Lynn & West Norfolk") survives.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.islower() or raw.isupper():
        raw = raw.title()
    return raw[:64]


async def upsert_certificate(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    confirmed_by_pm: bool = False,
    link_targets: list[str] | None = None,
) -> dict[str, Any]:
    """
    Ingest / update a certificate record.

    Single-door / extract always persists a **draft** when confirmed_by_pm=False
    so Compliance Saved Space and list tools show the record for HITL confirm.
    confirmed_by_pm=True finalises (and can fire remedial Approvals).

    ``link_targets`` (only meaningful with confirmed_by_pm=True) restricts which entity
    types get the source document attached — e.g. ["vendor"] links the vendor record only.
    None links every resolvable target (vendor + site + asset). The document→entity link is
    only written on PM confirm so the PM can approve/skip the proposed links at Review time.
    """
    # Bootstrap vendors/sites/assets tables if a bare tenant schema lacks them, so this
    # certificate's entity can be created even without a prior UDR migration (no-op otherwise).
    await ensure_entity_tables(session)

    from .verification_sources import canonicalize_type_code

    scope = data.get("cert_scope") or data.get("certificate_scope")
    raw_code = data.get("certificate_type_code") or data.get("cert_type")
    type_code = canonicalize_type_code(raw_code) or raw_code
    if scope not in {"Building", "Vendor"}:
        return {"ok": False, "error": "cert_scope must be Building or Vendor"}
    if not type_code:
        return {"ok": False, "error": "certificate_type_code required"}

    country = data.get("country_code") or "UK"
    pack = await get_pack_type(session, type_code, country_code=country)
    if not pack and raw_code and raw_code != type_code:
        # Pack rows are keyed by the pack's own codes (e.g. "BPCA", "HSE_ASBESTOS_LICENCE"),
        # which canonicalize_type_code rewrites to the verify codes ("BPCA_MEMBER",
        # "ASBESTOS_LICENCE"). Fall back to the pre-canonical code the classifier emitted so
        # the pack lookup resolves; verify canonicalizes again internally either way.
        alt = await get_pack_type(session, raw_code, country_code=country)
        if alt:
            pack, type_code = alt, raw_code
    if not pack:
        # Final resolution: the pack keys some types under a code whose CANONICAL form equals
        # the requested one (pack "HSE_ASBESTOS_LICENCE" canonicalizes to the verify code
        # "ASBESTOS_LICENCE", which is exactly what the deep-agent classifier sends). Match on
        # canonical form across the country pack so either code resolves to the pack entry.
        for pt in await list_pack_types(session, country_code=country):
            if canonicalize_type_code(pt.certificate_type_code) == type_code:
                pack, type_code = pt, pt.certificate_type_code
                break
    if not pack:
        return {"ok": False, "error": f"certificate_type_code {type_code} not in {country} pack"}

    expiry = _parse_date(data.get("expiry_date"))
    issue = _parse_date(data.get("issue_date"))
    next_due = _parse_date(data.get("next_due_date"))
    life = compute_certificate_status(
        expiry,
        thresholds=effective_alert_thresholds(pack.alert_thresholds),
        next_due_date=next_due,
    )

    soft_warning = soft_authenticity_warning(
        certificate_type_code=type_code,
        inspector_accreditation_number=data.get("inspector_accreditation_number"),
        register_name=pack.issuing_body if pack.verification_url else None,
    )
    forensics_payload = (
        data.get("forensics")
        or (data.get("raw_metadata") or {}).get("forensics")
    )
    vendor_registration = (
        data.get("vendor_registration")
        or (data.get("raw_metadata") or {}).get("vendor_registration")
    )
    # Vendor registration authenticity is a VENDOR-scope concept. A building certificate
    # (EPC/DEC/TM44) has no contractor/company, so a "Vendor / company name missing from
    # extract" warning must never attach to it — drop it for non-vendor scope.
    if scope != "Vendor":
        vendor_registration = None
    # Prefer vendor_registration authenticity text over bare soft warning when present
    # (it already includes soft register + Verify-now URL).
    warning = merge_authenticity_warnings(
        None if vendor_registration else soft_warning,
        forensics_payload,
        vendor_registration,
    )

    raw_result = data.get("result") or data.get("Pass/Refer/Fail") or data.get("Pass")
    result_val = normalize_inspection_result(raw_result)
    if raw_result and result_val is None and str(raw_result).strip() not in PASS_FAIL:
        # Keep non-canonical text as defects; do not reject the upsert
        if not data.get("defects_found"):
            data["defects_found"] = str(raw_result).strip()

    remedial_status = data.get("remedial_status") or "Closed"
    # Fail / C1 / Advisory → remedial Open as soon as extract lands (draft or confirmed)
    if result_val in {"Fail", "Advisory"}:
        remedial_status = "Open"
    elif confirmed_by_pm and result_val in {"Fail", "Advisory"}:
        remedial_status = "Open"

    org_id = _parse_uuid(data.get("organization_id") or data.get("org_id"))
    # Fall back to the platform's organization when the upload doesn't carry one, so a vendor-scope
    # certificate can still create/link its vendor (vendor creation needs an organization_id) and
    # the risk scan can block a lapsed vendor instead of skipping an orphaned cert.
    if org_id is None:
        org_id = await resolve_default_org(session)
    cert_id = _parse_uuid(data.get("id")) or uuid4()

    existing = await session.get(ComplianceCertificate, cert_id) if data.get("id") else None
    # De-dup: re-ingesting the same certificate must UPDATE the existing row, not stack a new
    # draft each time. Match by certificate_number within scope+org, IGNORING type (so a doc
    # first mis-classified as EPC and later as DEC collapses to one row); when the cert has no
    # number (e.g. SIA ACS), fall back to type + vendor/company name. Newest match wins.
    if existing is None:
        cert_num = (data.get("certificate_number") or "").strip()
        if cert_num:
            match_q = select(ComplianceCertificate).where(
                ComplianceCertificate.certificate_number == cert_num,
                ComplianceCertificate.cert_scope == scope,
            )
            if org_id:
                match_q = match_q.where(
                    (ComplianceCertificate.organization_id == org_id)
                    | (ComplianceCertificate.org_id == org_id)
                )
            existing = (
                await session.execute(
                    match_q.order_by(ComplianceCertificate.created_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
        else:
            company = (
                data.get("vendor_name")
                or data.get("Company name")
                or data.get("company_name")
                or (data.get("raw_metadata") or {}).get("vendor_name")
                or ""
            ).strip().lower()
            if company:
                cand_q = select(ComplianceCertificate).where(
                    ComplianceCertificate.certificate_type_code == type_code,
                    ComplianceCertificate.cert_scope == scope,
                )
                if org_id:
                    cand_q = cand_q.where(
                        (ComplianceCertificate.organization_id == org_id)
                        | (ComplianceCertificate.org_id == org_id)
                    )
                for cand in (
                    await session.execute(
                        cand_q.order_by(ComplianceCertificate.created_at.desc())
                    )
                ).scalars().all():
                    if ((cand.raw_metadata or {}).get("vendor_name") or "").strip().lower() == company:
                        existing = cand
                        break
        if existing:
            cert_id = existing.id

    cert = existing or ComplianceCertificate(id=cert_id)

    cert.organization_id = org_id
    cert.org_id = org_id
    cert.certificate_ref = data.get("certificate_ref") or data.get("certificate_number")
    cert.cert_type = type_code
    cert.certificate_type_code = type_code
    cert.certificate_number = data.get("certificate_number")
    cert.cert_scope = scope
    cert.asset_id = _parse_uuid(data.get("asset_id"))
    cert.site_id = _parse_uuid(data.get("site_id"))
    cert.vendor_id = _parse_uuid(data.get("vendor_id"))
    # Vendor scope: resolve FK from company name when not provided (A3 ingest)
    if scope == "Vendor" and cert.vendor_id is None:
        from .contractors import resolve_or_create_vendor

        company = (
            data.get("vendor_name")
            or data.get("Company name")
            or data.get("company_name")
            or (data.get("raw_metadata") or {}).get("company_name")
            or (data.get("raw_metadata") or {}).get("vendor_name")
        )
        # Fallback: PestGuard / BPCA tokens from source filename
        if not company:
            src = str(
                (data.get("raw_metadata") or {}).get("source_filename")
                or data.get("certificate_number")
                or ""
            )
            m = re.search(
                r"(?:bpca|niceic|napit|bafe|refcom|chas|sia)[\s_-]*([A-Za-z][A-Za-z0-9 &\-]{2,40})",
                src,
                re.I,
            )
            if m:
                company = m.group(1).strip()
            elif re.search(r"pest[\s_-]?guard", src, re.I):
                company = "PestGuard"
        # org_id is the UUID the certificate row stores, and is None whenever the
        # organizations table keys on integers — which it does. Vendor creation needs the
        # org as the vendors table stores it, so it gets its own lookup rather than
        # inheriting a value shaped for a different column.
        vendor_org = org_id if org_id is not None else await default_org_native(session)
        resolved = await resolve_or_create_vendor(
            session,
            company_name=company,
            organization_id=vendor_org,
            create_if_missing=bool(vendor_org and company),
        )
        if resolved:
            cert.vendor_id = resolved
        elif company:
            # A vendor-scope certificate whose vendor could not be resolved is not a
            # neutral outcome: nothing will block, and the vendor KPIs will not count it.
            log.warning(
                "cert.vendor_unresolved",
                company=company[:80],
                certificate_number=str(data.get("certificate_number") or "")[:60],
            )
    cert.issue_date = issue
    cert.expiry_date = expiry
    cert.next_due_date = _parse_date(data.get("next_due_date")) or expiry
    cert.inspection_frequency_months = (
        data.get("inspection_frequency_months") or pack.frequency_months
    )
    cert.inspector_name = data.get("inspector_name")
    cert.inspector_accreditation_number = data.get("inspector_accreditation_number")
    cert.result = result_val
    cert.defects_found = data.get("defects_found")
    cert.remedial_actions = data.get("remedial_actions")
    cert.remedial_status = remedial_status
    cert.document_id = _parse_uuid(data.get("document_id"))
    cert.source_document_id = cert.document_id
    cert.country_code = country
    # Sub-national grouping, broad to narrow. Normalised so "scotland", "SCT" and
    # "Scotland" do not become three separate rows in a report grouped by state, and so a
    # bare "WA" never sits next to a spelled-out "Washington". Only overwrite a stored
    # value when the document actually carried one — a later certificate that states no
    # location must not blank the location an earlier one established.
    state = _normalize_state(data.get("state"), country)
    region = _normalize_region(data.get("region"))
    if state:
        cert.state = state
    if region:
        cert.region = region
    cert.status = life.status
    cert.days_to_expiry = life.days_to_expiry
    cert.insurance_risk_flag = life.insurance_risk_flag if confirmed_by_pm else False
    cert.authenticity_warning = warning
    cert.issuer = data.get("issuer") or pack.issuing_body
    meta = dict(data.get("raw_metadata") or {})
    meta["field_confidence"] = data.get("field_confidence") or meta.get("field_confidence") or {}
    # Persist the CERTIFIED company as vendor_name so ccc_verify matches the register on the
    # accredited firm — not on cert.issuer, which for many vendor certs is the assessing/issuing
    # body (e.g. SIA_ACS lists "SSAIB" as assessor, not the approved contractor).
    if scope == "Vendor" and not meta.get("vendor_name"):
        _company_nm = (
            data.get("vendor_name")
            or data.get("Company name")
            or data.get("company_name")
            or meta.get("company_name")
        )
        if _company_nm:
            meta["vendor_name"] = _company_nm
    if data.get("source_file_path"):
        meta["source_file_path"] = data.get("source_file_path")
    if data.get("source_filename") and not meta.get("source_filename"):
        meta["source_filename"] = data.get("source_filename")
    if forensics_payload:
        meta["forensics"] = forensics_payload
        meta["forensics_verdict"] = forensics_payload.get("verdict")
        meta["forensics_risk_score"] = forensics_payload.get("risk_score")
        if forensics_payload.get("requires_pm_review"):
            meta["requires_forensics_review"] = True
    if vendor_registration:
        meta["vendor_registration"] = vendor_registration
        meta["vendor_platform_compliance"] = vendor_registration.get(
            "platform_compliance"
        )
        meta["vendor_is_compliant"] = vendor_registration.get("is_compliant")
        if vendor_registration.get("verification_url"):
            meta["verify_now_url"] = vendor_registration.get("verification_url")
        if vendor_registration.get("requires_pm_review"):
            meta["requires_vendor_registration_review"] = True
    # Building scope: capture the building name + reference/number from the document into the
    # dedicated columns so the portfolio lists and chat show which building the cert belongs to.
    # Also resolve the site FK best-effort by matching an existing site by name. Never overwrite
    # an existing value with a blank on re-ingest.
    if scope == "Building":
        _rm_in = data.get("raw_metadata") if isinstance(data.get("raw_metadata"), dict) else {}
        _srcs = (data, _rm_in)
        # Building NAME — whatever the document calls it (name first, address last so a first line
        # only becomes the name when no explicit name is present).
        b_name = _pick_field(
            _srcs,
            (
                "building name",
                "premises name",
                "property name",
                "site name",
                "location name",
                "building",
                "premises",
                "property",
                "site",
                "location",
                "building address",
                "premises address",
                "property address",
                "site address",
                "installation address",
                "address of premises",
                "address",
            ),
            exclude=("certificate", "membership", "accreditation", "policy", "inspector", "email"),
        )
        # Building REFERENCE / number — never a certificate/policy number.
        b_ref = _pick_field(
            _srcs,
            (
                "building reference",
                "building ref",
                "building number",
                "building no",
                "property reference",
                "property ref",
                "property number",
                "site reference",
                "site ref",
                "site code",
                "site number",
                "premises reference",
                "premises number",
                "reference number",
                "reference no",
                "ref no",
                "uprn",
                "plot number",
                "plot no",
                "unit number",
                "unit no",
            ),
            exclude=("certificate", "membership", "accreditation", "policy", "inspector"),
        )
        if b_name:
            # If it's a multi-line address, use the first line as the building name.
            cert.building_name = str(b_name).strip().splitlines()[0][:255]
        if b_ref:
            cert.building_reference = str(b_ref).strip()[:120]
        if (cert.building_name or cert.building_reference) and not (
            cert.site_id or cert.site_ref
        ):
            from .site_links import resolve_site_link

            try:
                # A site key is only sometimes a UUID — plenum_cafm.sites keys on
                # site_id VARCHAR(50) here — so the link is stored in whichever column can
                # hold it. Storing it in neither is what left per-site coverage empty.
                link = await resolve_site_link(
                    session,
                    name=cert.building_name,
                    reference=cert.building_reference,
                )
                if link:
                    if link.site_uuid is not None:
                        cert.site_id = link.site_uuid
                    else:
                        cert.site_ref = link.site_ref
                    if link.label:
                        meta["site_label"] = link.label
                    meta["site_link_source"] = f"ingest:{link.matched_on}"
            except Exception as exc:  # noqa: BLE001 — best-effort site FK
                log.warning("compliance.site_resolve_failed", error=str(exc)[:150])

    # ── the building graph ───────────────────────────────────────────────────────────
    # Place the certificate on the graph: resolve the building it belongs to, and record the
    # source file in plenum_cafm.documents so the certificate hangs off a document rather
    # than off nothing. Both are best-effort — a certificate that cannot be placed is still
    # ingested, with the reason stored, because a certificate that exists is worth more than
    # one rejected for want of a building.
    if cert.building_name or cert.building_reference or cert.site_id or cert.site_ref:
        try:
            from ..energy.graph_ingest import attach_to_graph

            graph = await attach_to_graph(
                session,
                document_id=cert.document_id or cert.source_document_id,
                building_name=cert.building_name,
                building_reference=cert.building_reference,
                site_name=meta.get("site_label"),
                site_id=cert.site_ref or (str(cert.site_id) if cert.site_id else None),
                doc_type="compliance_certificate",
                title=cert.certificate_type_code,
                file_name=meta.get("source_file_name"),
            )
            if graph.get("building_id"):
                cert.building_id = graph["building_id"]
            # The basis of the link travels with the record: a match on an exact code and one
            # inferred from a site with a single building are different claims.
            meta["building_link"] = {
                "outcome": graph.get("building_link_outcome"),
                "reason": graph.get("building_link_reason"),
                "building": graph.get("building_label"),
            }
            if graph.get("document_id"):
                meta["graph_document_id"] = graph["document_id"]
        except Exception as exc:  # noqa: BLE001 — the graph must never fail an ingest
            log.warning("compliance.graph_attach_failed", error=str(exc)[:200])

    meta["confirmed_by_pm"] = bool(confirmed_by_pm)
    meta["requires_pm_confirmation"] = not bool(confirmed_by_pm)
    if not confirmed_by_pm:
        meta["draft"] = True
    else:
        meta.pop("draft", None)
    cert.raw_metadata = meta
    cert.updated_at = datetime.now(timezone.utc)

    if not existing:
        session.add(cert)

    # Renewal supersedes older same-type certs for the same vendor (drops them from lists/KPIs
    # and the block ladder, and lifts a now-stale vendor block).
    try:
        await session.flush()
        await _supersede_prior_vendor_certs(session, cert, org_id=org_id)
    except Exception as exc:  # noqa: BLE001 — supersede must never break ingestion
        log.warning("compliance.supersede_failed", error=str(exc)[:200])

    queue_items: list[str] = []
    membership_queue_item_id: str | None = None

    # CCC §9 class 2 — forgery / forensics review → Approvals + email_draft
    verdict = (forensics_payload or {}).get("verdict") if forensics_payload else None
    ccc_verdict = (forensics_payload or {}).get("ccc_verdict") if forensics_payload else None
    if (
        verdict in {"fail", "review", "edited", "suspect"}
        or ccc_verdict in {"edited", "suspect"}
        or (forensics_payload or {}).get("requires_pm_review")
    ):
        from .notify_drafts import forgery_email_draft

        forgery_draft = forgery_email_draft(
            certificate_type_code=type_code,
            certificate_name=pack.certificate_type_name,
            authenticity_warning=warning,
            verdict=ccc_verdict or verdict,
            certificate_id=str(cert.id),
            document_id=str(cert.document_id) if cert.document_id else None,
        )
        forgery_item = await enqueue_approval(
            session,
            source_feature="A",
            item_type="forgery_alert",
            summary=(
                f"Forgery / authenticity review — {pack.certificate_type_name} "
                f"verdict={verdict or 'review'}"
            ),
            severity="Critical" if verdict == "fail" else "Review",
            organization_id=org_id,
            related_entity_type="compliance_certificate",
            related_entity_id=cert.id,
            payload={
                "certificate_id": str(cert.id),
                "certificate_type_code": type_code,
                "forensics_verdict": verdict,
                "authenticity_warning": warning,
                "risk_score": (forensics_payload or {}).get("risk_score"),
                "document_id": str(cert.document_id) if cert.document_id else None,
            },
            email_draft=forgery_draft,
        )
        queue_items.append(str(forgery_item.id))

    # CCC §3.2 — vector membership pending confirmation (hold out of vector until Keep)
    if cert.document_id:
        from .membership import enqueue_membership_pending

        meta = dict(cert.raw_metadata or {})
        rm_in = data.get("raw_metadata") if isinstance(data.get("raw_metadata"), dict) else {}
        source_file_path = (
            meta.get("source_file_path")
            or data.get("source_file_path")
            or (rm_in.get("source_file_path") if isinstance(rm_in, dict) else None)
        )
        mem = await enqueue_membership_pending(
            session,
            document_id=cert.document_id,
            organization_id=org_id,
            certificate_id=cert.id,
            file_name=str(
                data.get("source_filename")
                or meta.get("source_filename")
                or data.get("file_name")
                or ""
            ),
            source_file_path=str(source_file_path) if source_file_path else None,
            # Auto-keep: index the PDF into the vector DB by default (no hold, no
            # "Confirm membership" prompt, no delete that races the doc-rag pipeline).
            # The PM only sees Keep/Remove when they explicitly manage membership.
            hold_until_confirm=False,
        )
        if mem and mem.get("id"):
            membership_queue_item_id = str(mem["id"])
            queue_items.append(membership_queue_item_id)
            meta = dict(cert.raw_metadata or {})
            meta["membership_queue_item_id"] = membership_queue_item_id
            cert.raw_metadata = meta

    if not confirmed_by_pm:
        from ...models import ApprovalsQueueItem

        pending_confirm = (
            await session.execute(
                select(ApprovalsQueueItem)
                .where(
                    ApprovalsQueueItem.source_feature == "A",
                    ApprovalsQueueItem.item_type == "certificate_confirm",
                    ApprovalsQueueItem.status == "pending",
                    ApprovalsQueueItem.related_entity_id == cert.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if pending_confirm:
            pending_confirm.summary = (
                f"Confirm extracted {pack.certificate_type_name} ({type_code}) "
                f"#{data.get('certificate_number') or '—'}"
            )
            pending_confirm.payload = {
                **(pending_confirm.payload or {}),
                "certificate_id": str(cert.id),
                "certificate_type_code": type_code,
                "cert_scope": scope,
                "extracted": {
                    "certificate_number": data.get("certificate_number"),
                    "issue_date": data.get("issue_date"),
                    "expiry_date": data.get("expiry_date"),
                    "inspector_name": data.get("inspector_name"),
                    "result": result_val,
                    "defects_found": data.get("defects_found"),
                },
            }
            if result_val in {"Fail", "Advisory"}:
                pending_confirm.severity = "Critical"
                pending_confirm.summary = (
                    f"Remedial action required — {pack.certificate_type_name} "
                    f"result={result_val} (immediate action)"
                )
            queue_items.append(str(pending_confirm.id))
        else:
            confirm_severity = (
                "Critical" if result_val in {"Fail", "Advisory"} else "Review"
            )
            confirm_summary = (
                (
                    f"Remedial action required — {pack.certificate_type_name} "
                    f"result={result_val} (immediate action)"
                )
                if result_val in {"Fail", "Advisory"}
                else (
                    f"Confirm extracted {pack.certificate_type_name} ({type_code}) "
                    f"#{data.get('certificate_number') or '—'}"
                )
            )
            item = await enqueue_approval(
                session,
                source_feature="A",
                item_type="certificate_confirm",
                summary=confirm_summary,
                severity=confirm_severity,
                organization_id=org_id,
                related_entity_type="compliance_certificate",
                related_entity_id=cert.id,
                payload={
                    "certificate_id": str(cert.id),
                    "certificate_type_code": type_code,
                    "cert_scope": scope,
                    "extracted": {
                        "certificate_number": data.get("certificate_number"),
                        "issue_date": data.get("issue_date"),
                        "expiry_date": data.get("expiry_date"),
                        "inspector_name": data.get("inspector_name"),
                        "result": result_val,
                        "defects_found": data.get("defects_found"),
                    },
                    "note": (
                        "Draft from Single Door extract — confirm in Compliance "
                        "Saved Space Approvals rail (not the header bell)."
                    ),
                },
            )
            queue_items.append(str(item.id))

    # Building ladder: Critical (≤7d) / Overdue / Due / Lapsed / Expiring → Bell alert + email draft
    # (Review in Bell opens draft in center chat; PM confirms Send — not auto-blast for Critical)
    if scope == "Building" and life.status in {
        STATUS_EXPIRING_SOON,
        STATUS_DUE_FOR_RENEWAL,
        STATUS_OVERDUE,
        STATUS_CRITICAL,
        STATUS_LAPSED,
    }:
        ladder_qid = await _enqueue_building_ladder_alert(
            session,
            cert=cert,
            pack=pack,
            life=life,
            organization_id=org_id,
        )
        if ladder_qid:
            queue_items.append(ladder_qid)

    # Vendor risk ladder on ingest (§5 vendor track): Medium → informational queue,
    # High/Lapsed → renewal email draft (+ block_state=Blocked on Lapsed), Clear → lift
    # any block. Fires on the draft upsert so vendor alerts don't wait for a scan — mirrors
    # what the building ladder above already does for building certs.
    if scope == "Vendor" and cert.vendor_id:
        try:
            from .scan import apply_vendor_cert_ladder

            await apply_vendor_cert_ladder(
                session,
                cert,
                life=life,
                organization_id=org_id,
                type_name=pack.certificate_type_name,
                renewal_url=pack.verification_url,
            )
        except Exception as exc:  # noqa: BLE001 — vendor ladder must not block the upsert
            log.warning("certificate.vendor_ladder_failed", error=str(exc)[:200])

    # Fail / C1 immediate action → Critical remedial track + email (even on draft extract)
    if scope == "Building" and result_val in {"Fail", "Advisory"}:
        rem_qid = await _enqueue_fail_remedial_alert(
            session,
            cert=cert,
            pack=pack,
            result_val=result_val,
            defects_found=data.get("defects_found") or cert.defects_found,
            organization_id=org_id,
        )
        if rem_qid:
            queue_items.append(rem_qid)

    if confirmed_by_pm and remedial_status == "Open" and result_val not in {"Fail", "Advisory"}:
        # Remedial Action Required (v1.2 — no WO auto-create) after PM confirm only
        item = await enqueue_approval(
            session,
            source_feature="A",
            item_type="remedial",
            summary=(
                f"Remedial action required — {pack.certificate_type_name} "
                f"({type_code}) result={result_val}"
            ),
            severity="Action required",
            organization_id=org_id,
            related_entity_type="compliance_certificate",
            related_entity_id=cert.id,
            payload={
                "certificate_id": str(cert.id),
                "defects_found": cert.defects_found,
                "remedial_actions": cert.remedial_actions,
                "note": "PM manages remedial work via existing FM channel — no WO auto-created.",
            },
        )
        queue_items.append(str(item.id))

    # A3 — dual-write operative certifications to ResourceSkill when Vendor FK known
    if (
        scope == "Vendor"
        and cert.vendor_id
        and type_code
        in {
            "ACS_CARD",
            "SIA_INDIVIDUAL",
            "ASBESTOS_P402_P403_P404",
            "PA1_PA2_PA6",
            "P402",
            "P403",
            "P404",
            "PA1",
            "PA2",
            "PA6",
        }
    ):
        from .resource_skills import upsert_resource_skill

        try:
            await upsert_resource_skill(
                session,
                {
                    "vendor_id": str(cert.vendor_id),
                    "organization_id": str(org_id) if org_id else None,
                    "skill_type_code": type_code,
                    "certificate_number": cert.certificate_number,
                    "issue_date": data.get("issue_date"),
                    "expiry_date": data.get("expiry_date"),
                    "operative_name": data.get("inspector_name")
                    or data.get("operative_name")
                    or data.get("Company name"),
                },
                commit=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("certificate.operative_skill_upsert_failed", error=str(exc)[:200])

    await write_audit(
        session,
        actor="system",
        action_type="certificate.upsert_draft" if not confirmed_by_pm else "certificate.upsert",
        source_feature="A",
        organization_id=org_id,
        output_payload=cert_to_dict(cert),
    )
    await session.commit()
    await session.refresh(cert)

    # CCC §0 C3 / §8 — auto-verify against register on confirm, or immediately
    # for public_api / data_dump channels (no partner scrape required).
    verification_result: dict[str, Any] | None = None
    try:
        from . import ccc_verify as ccc_verify_svc
        from .verification_sources import get_verification_source

        # Scope the channel lookup to the certificate country so a US/UAE cert is
        # matched against its own register config, not the UK one.
        src = await get_verification_source(
            session, type_code, getattr(cert, "country_code", None)
        )
        channel = (src or {}).get("channel") or ""
        # website is included because website-register types (e.g. BAFE) now resolve
        # against a locally-ingested register dump first (see ccc_verify) — so a confirmed
        # row can auto-verify on ingest instead of only ever returning a Verify-now link.
        should_verify = confirmed_by_pm or channel in {"public_api", "data_dump", "website"}
        if should_verify:
            verification_result = await ccc_verify_svc.verify_stored_certificate(
                session, cert.id
            )
            await session.refresh(cert)
    except Exception as exc:  # noqa: BLE001
        log.warning("certificate.auto_verify_failed", error=str(exc)[:200])
        verification_result = {"ok": False, "error": str(exc)[:200]}

    # Reconcile authenticity once §8 verification is known: a cert that verified True on
    # its official register no longer needs the soft "please verify authenticity" note —
    # drop it, but KEEP genuine forensic/forgery findings and vendor on-platform status.
    if (
        verification_result
        and verification_result.get("verified") is True
        and soft_warning
        and cert.authenticity_warning
    ):
        reconciled = merge_authenticity_warnings(None, forensics_payload, vendor_registration)
        if reconciled != cert.authenticity_warning:
            cert.authenticity_warning = reconciled
            warning = reconciled
            await session.commit()
            await session.refresh(cert)

    # Complete-RAG linkage: attach the source document to its vendor / site / asset record
    # (polymorphic files + asset_documents) so the certificate PDF is retrievable on the
    # entity's record and by entity-scoped RAG. This is a PM-confirmed step: a draft stays
    # UNLINKED so the PM can approve/skip the proposed vendor/site/building links at Review
    # time (confirm_certificate passes link_targets). Auto-verified channels arrive already
    # confirmed_by_pm=True and link every resolvable target. Idempotent, best-effort.
    document_links: list[dict[str, str]] = []
    proposed_links = _proposed_document_links(cert)
    if (
        confirmed_by_pm
        and cert.document_id
        and (cert.vendor_id or cert.site_id or cert.asset_id)
    ):
        try:
            only = set(link_targets) if link_targets is not None else None
            document_links = await _link_document_to_entities(session, cert, only=only)
            if document_links:
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("certificate.document_link_failed_on_confirm", error=str(exc)[:200])

    # Live vendor/site/asset table match for the center-chat "review to link" card. Best-effort
    # (read-only); only meaningful when the cert has a source document to link.
    link_candidates: dict[str, Any] | None = None
    if cert.document_id:
        try:
            link_candidates = await resolve_link_candidates(session, cert)
        except Exception as exc:  # noqa: BLE001
            log.warning("certificate.link_candidates_failed", error=str(exc)[:200])

    out = {
        "ok": True,
        "id": str(cert.id),
        "certificate": cert_to_dict(cert),
        "authenticity_warning": warning,
        "queue_items": queue_items,
        "membership_queue_item_id": membership_queue_item_id,
        "requires_pm_confirmation": not confirmed_by_pm,
        "verification": verification_result,
        "document_links": document_links,
        "proposed_links": proposed_links,
        "link_candidates": link_candidates,
        "pack": {
            "certificate_type_name": pack.certificate_type_name,
            "required_contractor_accreditation": pack.required_contractor_accreditation,
            "verification_url": pack.verification_url,
        },
    }
    if not confirmed_by_pm:
        out["message"] = (
            "Draft certificate saved — PM must confirm or correct fields in "
            "Compliance Saved Space before finalising."
        )
        out["preview"] = {
            "certificate_type_code": type_code,
            "certificate_type_name": pack.certificate_type_name,
            "key_fields_schema": pack.key_fields_schema,
            "field_confidence": data.get("field_confidence") or {},
            "extracted": data,
        }
    return out


async def list_certificates(
    session: AsyncSession,
    *,
    cert_scope: str | None = None,
    status: str | None = None,
    organization_id: UUID | None = None,
    vendor_id: UUID | None = None,
    asset_id: UUID | None = None,
    site_id: UUID | None = None,
    # The site key when plenum_cafm.sites is not UUID-keyed. Without it there is no way to
    # ask for one building's certificates on a varchar-keyed portfolio.
    site_ref: str | None = None,
    risk_filter: str | None = None,
    draft: bool | None = None,
    certificate_type_code: str | None = None,
    trade_category: str | None = None,
    vendor_name: str | None = None,
    expiring_within_days: int | None = None,
    expiry_month: str | None = None,
    include_archived: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    # Fetch a wider window when filtering client-side so deep-links stay accurate.
    # Status is filtered AFTER enrichment (recomputed from expiry) so "expired"/"lapsed"
    # match what the PM sees on /compliance even when the stored status column is stale.
    needs_client_filter = bool(
        risk_filter
        or draft is not None
        or status
        or certificate_type_code
        or trade_category
        or vendor_name
        or expiring_within_days is not None
        or expiry_month
    )
    fetch_limit = min(max(limit * 3, limit), 1000) if needs_client_filter else limit
    q = select(ComplianceCertificate).order_by(
        ComplianceCertificate.expiry_date.asc().nulls_last()
    ).limit(fetch_limit)
    if cert_scope:
        # Case-insensitive scope so "vendor"/"Vendor"/"building" all match.
        q = q.where(func.lower(ComplianceCertificate.cert_scope) == cert_scope.strip().lower())
    if organization_id:
        q = q.where(
            (ComplianceCertificate.organization_id == organization_id)
            | (ComplianceCertificate.org_id == organization_id)
        )
    if vendor_id:
        q = q.where(ComplianceCertificate.vendor_id == vendor_id)
    if asset_id:
        q = q.where(ComplianceCertificate.asset_id == asset_id)
    if site_id:
        q = q.where(ComplianceCertificate.site_id == site_id)
    if site_ref:
        q = q.where(ComplianceCertificate.site_ref == site_ref)
    rows = list((await session.execute(q)).scalars().all())
    # Hide test fixtures / superseded duplicates from Saved Space lists — the same rows the
    # nightly scan already skips — so the PM sees real certificates, not seeded fixtures.
    rows = [
        r
        for r in rows
        if not (
            (r.raw_metadata or {}).get("a1_test_fixture")
            or (r.raw_metadata or {}).get("superseded_duplicate")
            or (not include_archived and (r.raw_metadata or {}).get("archived"))
        )
    ]
    out = await _enrich_certificate_rows(session, [cert_to_dict(r) for r in rows])
    if certificate_type_code:
        out = [r for r in out if _row_matches_type(r, certificate_type_code)]
    if status:
        out = [r for r in out if _row_matches_status(r, status)]
    if risk_filter:
        out = [r for r in out if _matches_risk_filter(r, risk_filter)]
    if trade_category:
        tc = trade_category.strip().lower()
        out = [r for r in out if tc in (r.get("trade_category") or "").lower()]
    if vendor_name:
        vn = vendor_name.strip().lower()
        out = [r for r in out if vn in (r.get("vendor_name") or "").lower()]
    if expiring_within_days is not None:
        # Certs expiring within the window (not already lapsed) — "expiring in N days".
        out = [
            r
            for r in out
            if isinstance(r.get("days_to_expiry"), int)
            and 0 <= r["days_to_expiry"] <= expiring_within_days
        ]
    if expiry_month:
        # Calendar month, e.g. "2026-08" — "certs that expire this month". Accept semantic
        # tokens ("this"/"current"/"next") and resolve them server-side so callers never
        # have to know today's date (the LLM guesses it wrong).
        em = expiry_month.strip().lower()
        today = date.today()
        if em in {"this", "current", "this_month", "this month", "current_month"}:
            em = today.strftime("%Y-%m")
        elif em in {"next", "next_month", "next month"}:
            ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            em = f"{ny:04d}-{nm:02d}"
        else:
            em = expiry_month.strip()
        out = [r for r in out if str(r.get("expiry_date") or "").startswith(em)]
    if draft is not None:
        # `draft` is a metadata flag (unconfirmed extract), not a lifecycle status.
        out = [r for r in out if bool(r.get("draft")) == draft]
    return out[:limit]


async def list_vendors_by_certificate_count(
    session: AsyncSession,
    *,
    min_count: int,
    comparison: str = "gt",
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Group live vendor-scope certificates by company and apply a strict count predicate."""
    if min_count < 0:
        return {"ok": False, "error": "min_count must be zero or greater"}
    if comparison not in {"gt", "gte", "eq"}:
        return {"ok": False, "error": "comparison must be one of: gt, gte, eq"}

    rows = await list_certificates(
        session,
        cert_scope="Vendor",
        organization_id=organization_id,
        limit=1000,
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        vendor_name = str(row.get("vendor_name") or "").strip()
        if not vendor_name:
            continue
        vendor_id = str(row.get("vendor_id") or "").strip() or None
        key = vendor_id or vendor_name.casefold()
        entry = grouped.setdefault(
            key,
            {
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "certificates_by_id": {},
            },
        )
        certificate_id = str(row.get("id") or row.get("certificate_ref") or "").strip()
        if certificate_id:
            entry["certificates_by_id"][certificate_id] = {
                "id": row.get("id"),
                "certificate_type_name": row.get("certificate_type_name")
                or row.get("certificate_type_code"),
                "certificate_number": row.get("certificate_number")
                or row.get("certificate_ref"),
                "status": row.get("status"),
                "issue_date": row.get("issue_date"),
                "expiry_date": row.get("expiry_date"),
                "days_to_expiry": row.get("days_to_expiry"),
                "risk_badge": row.get("risk_badge") or row.get("risk_level"),
                "draft": bool(row.get("draft")),
                "block_reason": row.get("block_reason"),
                "vendor_name": vendor_name,
            }

    def qualifies(count: int) -> bool:
        if comparison == "gt":
            return count > min_count
        if comparison == "gte":
            return count >= min_count
        return count == min_count

    vendors = [
        {
            "vendor_id": entry["vendor_id"],
            "vendor_name": entry["vendor_name"],
            "certificate_count": len(entry["certificates_by_id"]),
            "certificates": list(entry["certificates_by_id"].values()),
        }
        for entry in grouped.values()
        if qualifies(len(entry["certificates_by_id"]))
    ]
    vendors.sort(key=lambda item: (-item["certificate_count"], item["vendor_name"].casefold()))
    return {
        "ok": True,
        "count": len(vendors),
        "vendors": vendors,
        "min_count": min_count,
        "comparison": comparison,
        "certificates_considered": len(rows),
    }


def _row_matches_type(row: dict[str, Any], requested: str) -> bool:
    """Match a certificate row to a requested type code/synonym across pack + CCC codes."""
    from .verification_sources import canonicalize_type_code

    want_raw = (resolve_cert_type_code(requested) or requested or "").strip().upper()
    if not want_raw:
        return True
    want_canon = (canonicalize_type_code(want_raw) or want_raw).upper()
    code = (row.get("certificate_type_code") or "").upper()
    code_canon = (canonicalize_type_code(code) or code).upper()
    name = (row.get("certificate_type_name") or "").lower()
    return (
        code == want_raw
        or code_canon == want_canon
        or requested.strip().lower() in name
    )


def _row_matches_count_filters(
    row: dict[str, Any],
    *,
    cert_type: str | None = None,
    certificate_number: str | None = None,
    inspector_name: str | None = None,
    result: str | None = None,
    remedial_status: str | None = None,
    insurance_risk_flag: bool | None = None,
    country_code: str | None = None,
    issuer_contains: str | None = None,
    vendor_name_contains: str | None = None,
    certificate_name_contains: str | None = None,
) -> bool:
    """AND-filter an enriched certificate row for deterministic attribute counts."""
    if cert_type:
        resolved = resolve_cert_type_code(cert_type) or cert_type
        code = (row.get("certificate_type_code") or "").upper()
        name = (row.get("certificate_type_name") or "").lower()
        needle = resolved.upper()
        if needle not in code and needle.lower() not in name and cert_type.lower() not in name:
            return False
    if certificate_number:
        num = (row.get("certificate_number") or row.get("certificate_ref") or "").lower()
        if certificate_number.strip().lower() not in num:
            return False
    if inspector_name:
        insp = (row.get("inspector_name") or "").lower()
        if inspector_name.strip().lower() not in insp:
            return False
    if result:
        if (row.get("result") or "").strip().lower() != result.strip().lower():
            return False
    if remedial_status:
        if (row.get("remedial_status") or "").strip().lower() != remedial_status.strip().lower():
            return False
    if insurance_risk_flag is not None:
        if bool(row.get("insurance_risk_flag")) != insurance_risk_flag:
            return False
    if country_code:
        if (row.get("country_code") or "UK").strip().upper() != country_code.strip().upper():
            return False
    if issuer_contains:
        issuer = (
            (row.get("issuer") or "")
            + " "
            + (row.get("issuing_body") or "")
            + " "
            + (row.get("inspector_accreditation_number") or "")
        ).lower()
        if issuer_contains.strip().lower() not in issuer:
            return False
    if vendor_name_contains:
        vname = (row.get("vendor_name") or "").lower()
        if vendor_name_contains.strip().lower() not in vname:
            return False
    if certificate_name_contains:
        blob = (
            (row.get("certificate_type_name") or "")
            + " "
            + (row.get("certificate_type_code") or "")
            + " "
            + (row.get("certificate_number") or "")
        ).lower()
        if certificate_name_contains.strip().lower() not in blob:
            return False
    return True


async def count_certificates(
    session: AsyncSession,
    *,
    cert_scope: str | None = None,
    status: str | None = None,
    cert_type: str | None = None,
    certificate_number: str | None = None,
    inspector_name: str | None = None,
    result: str | None = None,
    remedial_status: str | None = None,
    insurance_risk_flag: bool | None = None,
    country_code: str | None = None,
    draft: bool | None = None,
    issuer_contains: str | None = None,
    vendor_name_contains: str | None = None,
    certificate_name_contains: str | None = None,
    organization_id: UUID | None = None,
    risk_filter: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Deterministic filter+count for attribute questions the LLM must not guess.

    Reuses ``list_certificates`` (fixture exclusion + enrichment), then AND-filters
    the small row set in Python. Returns ``{ok, count, matches}``.
    """
    rows = await list_certificates(
        session,
        cert_scope=cert_scope,
        status=status,
        organization_id=organization_id,
        risk_filter=risk_filter,
        draft=draft,
        limit=limit,
    )
    matches = [
        r
        for r in rows
        if _row_matches_count_filters(
            r,
            cert_type=cert_type,
            certificate_number=certificate_number,
            inspector_name=inspector_name,
            result=result,
            remedial_status=remedial_status,
            insurance_risk_flag=insurance_risk_flag,
            country_code=country_code,
            issuer_contains=issuer_contains,
            vendor_name_contains=vendor_name_contains,
            certificate_name_contains=certificate_name_contains,
        )
    ]
    # Compact match preview for the agent (enough to cite, not dump full rows).
    preview = [
        {
            "id": m.get("id"),
            "certificate_type_code": m.get("certificate_type_code"),
            "certificate_type_name": m.get("certificate_type_name"),
            "certificate_number": m.get("certificate_number"),
            "cert_scope": m.get("cert_scope"),
            "status": m.get("status"),
            "issuer": m.get("issuer") or m.get("issuing_body"),
            "inspector_name": m.get("inspector_name"),
            "vendor_name": m.get("vendor_name"),
            "insurance_risk_flag": m.get("insurance_risk_flag"),
            "expiry_date": m.get("expiry_date"),
        }
        for m in matches[:50]
    ]
    return {"ok": True, "count": len(matches), "matches": preview}


async def set_remedial_status(
    session: AsyncSession,
    certificate_id: UUID,
    remedial_status: str,
) -> dict[str, Any]:
    if remedial_status not in {"Open", "In Progress", "Closed"}:
        return {"ok": False, "error": "remedial_status must be Open | In Progress | Closed"}
    cert = await session.get(ComplianceCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "certificate not found"}
    cert.remedial_status = remedial_status
    cert.updated_at = datetime.now(timezone.utc)
    await write_audit(
        session,
        actor="user:pm",
        action_type="certificate.remedial_status",
        source_feature="A",
        organization_id=cert.organization_id or cert.org_id,
        output_payload={"certificate_id": str(cert.id), "remedial_status": remedial_status},
    )
    await session.commit()
    return {"ok": True, "certificate": cert_to_dict(cert)}


async def confirm_certificate(
    session: AsyncSession,
    certificate_id: UUID,
    link_targets: list[str] | None = None,
) -> dict[str, Any]:
    """
    PM confirms a draft certificate — finalise in place (draft flag cleared, insurance
    risk / remedial side-effects fire). Rebuilds the upsert payload from the stored row so
    no fields are lost, then re-runs upsert with confirmed_by_pm=True.

    ``link_targets`` carries the PM's approve/skip choices from the Review panel — the subset
    of ["vendor", "site", "asset"] whose records should get the source document attached.
    None (the default) links every resolvable target; [] links none.
    """
    cert = await session.get(ComplianceCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "certificate not found"}
    if not (cert.certificate_type_code or cert.cert_type) or not cert.cert_scope:
        return {"ok": False, "error": "certificate missing type/scope — cannot confirm"}

    meta = {
        k: v
        for k, v in (cert.raw_metadata or {}).items()
        if k not in ("draft", "requires_pm_confirmation")
    }
    data: dict[str, Any] = {
        "id": str(cert.id),
        "cert_scope": cert.cert_scope,
        "certificate_type_code": cert.certificate_type_code or cert.cert_type,
        "certificate_number": cert.certificate_number,
        "country_code": cert.country_code or "UK",
        "issue_date": cert.issue_date.isoformat() if cert.issue_date else None,
        "expiry_date": cert.expiry_date.isoformat() if cert.expiry_date else None,
        "next_due_date": cert.next_due_date.isoformat() if cert.next_due_date else None,
        "inspection_frequency_months": cert.inspection_frequency_months,
        "inspector_name": cert.inspector_name,
        "inspector_accreditation_number": cert.inspector_accreditation_number,
        "result": cert.result,
        "defects_found": cert.defects_found,
        "remedial_actions": cert.remedial_actions,
        "remedial_status": cert.remedial_status,
        "asset_id": str(cert.asset_id) if cert.asset_id else None,
        "site_id": str(cert.site_id) if cert.site_id else None,
        "vendor_id": str(cert.vendor_id) if cert.vendor_id else None,
        "organization_id": (
            str(cert.organization_id or cert.org_id)
            if (cert.organization_id or cert.org_id)
            else None
        ),
        "document_id": str(cert.document_id) if cert.document_id else None,
        "raw_metadata": meta,
    }
    # upsert_certificate(confirmed_by_pm=True) performs the document→entity link itself,
    # honouring the PM's approve/skip choices via link_targets — no duplicate link here.
    result = await upsert_certificate(
        session, data, confirmed_by_pm=True, link_targets=link_targets
    )
    return result


def _proposed_document_links(cert: ComplianceCertificate) -> list[dict[str, str | None]]:
    """Describe the document→entity links the PM can approve/skip at Review time (no writes).

    One entry per resolvable target with its display name pulled from the cert / raw_metadata,
    so the Review panel can render 'Link document {id} → Vendor X? [OK/Skip]' for vendor, site
    (building) and asset. Empty when there's no source document to link."""
    if not cert.document_id:
        return []
    meta = cert.raw_metadata or {}
    out: list[dict[str, str | None]] = []
    if cert.vendor_id:
        out.append(
            {
                "target": "vendor",
                "entity_id": str(cert.vendor_id),
                "label": meta.get("vendor_name")
                or meta.get("company_name")
                or meta.get("contractor_name"),
            }
        )
    if cert.site_id or getattr(cert, "site_ref", None):
        out.append(
            {
                "target": "site",
                "entity_id": str(cert.site_id or cert.site_ref),
                "label": meta.get("site_label")
                or meta.get("building_name")
                or meta.get("site_name")
                or cert.building_name,
            }
        )
    if cert.asset_id:
        out.append(
            {
                "target": "asset",
                "entity_id": str(cert.asset_id),
                "label": meta.get("asset_code") or meta.get("asset_name"),
            }
        )
    return out


async def _link_document_to_entities(
    session: AsyncSession,
    cert: ComplianceCertificate,
    only: set[str] | None = None,
) -> list[dict[str, str]]:
    """Attach the cert's source document to its mapped vendor/site/asset record. Returns the
    links created (entity_type + entity_id). ``only`` restricts which targets are linked (a
    subset of {"vendor", "site", "asset"}); None links every resolvable one. Legacy
    vendors/assets keep non-UUID ids, and files.entity_id / asset_documents.asset_id are UUID
    columns, so a non-UUID id is skipped."""
    if not cert.document_id:
        return []
    meta = cert.raw_metadata or {}
    fname = str(
        meta.get("source_filename")
        or meta.get("file_name")
        or f"{cert.certificate_type_code or 'certificate'}.pdf"
    )[:255]
    doc_url = f"/backend/deep-agents/api/documents/{cert.document_id}/download"
    try:
        org_val: int | None = (
            int(cert.organization_id or cert.org_id)
            if (cert.organization_id or cert.org_id)
            else None
        )
    except (TypeError, ValueError):
        org_val = None

    def _as_uuid(v: Any) -> str | None:
        try:
            return str(UUID(str(v)))
        except (TypeError, ValueError):
            return None

    links: list[dict[str, str]] = []
    vendor_uuid = _as_uuid(cert.vendor_id) if cert.vendor_id else None
    if vendor_uuid and (only is None or "vendor" in only):
        await session.execute(
            text(
                """
                INSERT INTO plenum_cafm.files
                    (id, organization_id, name, blob_url, entity_type, entity_id, uploaded_at)
                SELECT :id, :org, :name, :url, 'vendor', CAST(:eid AS uuid), now()
                WHERE NOT EXISTS (
                    SELECT 1 FROM plenum_cafm.files
                    WHERE entity_type = 'vendor' AND entity_id = CAST(:eid AS uuid)
                      AND blob_url = :url)
                """
            ),
            {"id": str(uuid4()), "org": org_val, "name": fname, "url": doc_url, "eid": vendor_uuid},
        )
        links.append({"entity_type": "vendor", "entity_id": vendor_uuid, "table": "files"})

    site_uuid = _as_uuid(cert.site_id) if cert.site_id else None
    if site_uuid and (only is None or "site" in only):
        await session.execute(
            text(
                """
                INSERT INTO plenum_cafm.files
                    (id, organization_id, name, blob_url, entity_type, entity_id, uploaded_at)
                SELECT :id, :org, :name, :url, 'site', CAST(:eid AS uuid), now()
                WHERE NOT EXISTS (
                    SELECT 1 FROM plenum_cafm.files
                    WHERE entity_type = 'site' AND entity_id = CAST(:eid AS uuid)
                      AND blob_url = :url)
                """
            ),
            {"id": str(uuid4()), "org": org_val, "name": fname, "url": doc_url, "eid": site_uuid},
        )
        links.append({"entity_type": "site", "entity_id": site_uuid, "table": "files"})

    asset_uuid = _as_uuid(cert.asset_id) if cert.asset_id else None
    if asset_uuid and (only is None or "asset" in only):
        await session.execute(
            text(
                """
                INSERT INTO plenum_cafm.asset_documents
                    (id, asset_id, file_url, document_type, uploaded_at)
                SELECT :id, CAST(:aid AS uuid), :url, :dtype, now()
                WHERE NOT EXISTS (
                    SELECT 1 FROM plenum_cafm.asset_documents
                    WHERE asset_id = CAST(:aid AS uuid) AND file_url = :url)
                """
            ),
            {
                "id": str(uuid4()),
                "aid": asset_uuid,
                "url": doc_url,
                "dtype": cert.certificate_type_code or "compliance",
            },
        )
        links.append({"entity_type": "asset", "entity_id": asset_uuid, "table": "asset_documents"})
    return links


def _match_score(source: str | None, target: str | None) -> float:
    """0..1 similarity between two names (case/space-insensitive), difflib-based (no pg_trgm)."""
    from difflib import SequenceMatcher

    a = re.sub(r"\s+", " ", (source or "").strip().lower())
    b = re.sub(r"\s+", " ", (target or "").strip().lower())
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    base = SequenceMatcher(None, a, b).ratio()
    # Reward token containment (e.g. "Arcus Solutions" ⊂ "Arcus Solutions (Holdings) Ltd").
    if a in b or b in a:
        base = max(base, 0.9)
    return round(base, 3)


async def _rank_rows(
    rows: list[dict[str, Any]], source: str, name_key: str, current_id: str | None, limit: int
) -> list[dict[str, Any]]:
    scored = []
    for r in rows:
        rid = str(r["id"])
        scored.append(
            {
                "id": rid,
                "name": r.get(name_key),
                "code": r.get("code"),
                "score": _match_score(source, r.get(name_key) or r.get("code")),
                "is_current": current_id is not None and rid == current_id,
            }
        )
    scored.sort(key=lambda x: (x["is_current"], x["score"]), reverse=True)
    return scored[:limit]


async def resolve_link_candidates(
    session: AsyncSession, cert: ComplianceCertificate, limit: int = 3
) -> dict[str, Any]:
    """Match a certificate's source document against the vendor / site / asset tables.

    For each target, always run a fresh fuzzy search over the table by the cert's extracted
    name/code and return the top candidates (with the already-linked row flagged is_current),
    so the PM can confirm/pick the row the document_id should auto-link to. Read-only."""
    meta = cert.raw_metadata or {}
    # A vendor cert is "missing" its vendor when it has no vendor_id, OR the vendor_id no longer
    # resolves to a vendors row (e.g. the vendor was deleted from the table — dangling FK). Both
    # cases → offer the "create vendor" form.
    vendor_on_record = False
    if cert.vendor_id:
        vendor_on_record = bool(
            await session.scalar(
                text("SELECT 1 FROM plenum_cafm.vendors WHERE id::text = :vid LIMIT 1"),
                {"vid": str(cert.vendor_id)},
            )
        )
    out: dict[str, Any] = {
        "certificate_id": str(cert.id),
        "document_id": str(cert.document_id) if cert.document_id else None,
        "source_filename": meta.get("source_filename") or meta.get("file_name"),
        "certificate_type_code": cert.certificate_type_code or cert.cert_type,
        "cert_scope": cert.cert_scope,
        # Vendor cert with no vendor on record (null OR dangling vendor_id) → chat shows the
        # "create vendor" form, pre-filled with the extracted name when one exists.
        "vendor_missing": bool(cert.cert_scope == "Vendor" and not vendor_on_record),
        "vendor_suggested_name": (
            meta.get("vendor_name")
            or meta.get("company_name")
            or meta.get("contractor_name")
        ),
        "vendor": None,
        "site": None,
        "asset": None,
    }

    async def _search(sql: str, term: str) -> list[dict[str, Any]]:
        first = term.split()[0] if term.split() else term
        res = await session.execute(
            text(sql), {"q": f"%{term}%", "first": f"%{first}%"}
        )
        return [dict(r) for r in res.mappings().all()]

    # ── Vendor ──
    vname = meta.get("vendor_name") or meta.get("company_name") or meta.get("contractor_name")
    if vname:
        rows = await _safe_exec(
            session,
            lambda: _search(
                """SELECT id::text AS id, vendor_name AS name, NULL AS code
                   FROM plenum_cafm.vendors
                   WHERE vendor_name ILIKE :q OR vendor_name ILIKE :first
                   ORDER BY vendor_name LIMIT 40""",
                vname,
            ),
            label="link_candidates.vendor_failed",
            default=[],
        ) or []
        out["vendor"] = {
            "source_name": vname,
            "current_id": str(cert.vendor_id) if cert.vendor_id else None,
            "candidates": await _rank_rows(
                rows, vname, "name", str(cert.vendor_id) if cert.vendor_id else None, limit
            ),
        }

    # ── Site (building) ──
    sname = meta.get("building_name") or meta.get("site_name") or cert.building_name
    if sname:
        # Sites are searched through site_links so the candidate ids are keys the
        # certificate can actually store — this table is not always UUID-keyed.
        from .site_links import search_sites

        # search_sites runs in its own SAVEPOINT and returns [] on failure, so it needs no
        # second wrapper.
        rows = await search_sites(session, sname)
        out["site"] = {
            "source_name": sname,
            "current_id": (
                str(cert.site_id) if cert.site_id else (cert.site_ref or None)
            ),
            "candidates": await _rank_rows(
                rows,
                sname,
                "name",
                str(cert.site_id) if cert.site_id else (cert.site_ref or None),
                limit,
            ),
        }

    # ── Asset ──
    aname = meta.get("asset_code") or meta.get("asset_name")
    if aname:
        rows = await _safe_exec(
            session,
            lambda: _search(
                """SELECT id::text AS id, asset_name AS name, asset_code AS code
                   FROM plenum_cafm.assets
                   WHERE asset_name ILIKE :q OR asset_code ILIKE :q OR asset_name ILIKE :first
                   ORDER BY asset_name LIMIT 40""",
                aname,
            ),
            label="link_candidates.asset_failed",
            default=[],
        ) or []
        out["asset"] = {
            "source_name": aname,
            "current_id": str(cert.asset_id) if cert.asset_id else None,
            "candidates": await _rank_rows(
                rows, aname, "name", str(cert.asset_id) if cert.asset_id else None, limit
            ),
        }
    return out


async def link_certificate_document(
    session: AsyncSession,
    certificate_id: UUID,
    *,
    vendor_id: str | None = None,
    site_id: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any]:
    """Auto-link the cert's document_id to the PM-chosen vendor/site/asset rows.

    Sets the cert's FK(s) to the picked candidate(s) (which may differ from what was resolved
    at ingest), then attaches the source document to those entity records (polymorphic files /
    asset_documents). Only the targets supplied are touched; others are left as-is."""
    cert = await session.get(ComplianceCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "certificate not found"}
    if not cert.document_id:
        return {"ok": False, "error": "certificate has no source document to link"}

    only: set[str] = set()
    if vendor_id:
        cert.vendor_id = vendor_id
        only.add("vendor")
    if site_id:
        # A non-UUID key used to be assigned straight onto the UUID column, which fails at
        # flush — the PM's pick was lost with an error, not stored. It belongs in site_ref.
        from .site_links import as_uuid

        picked = as_uuid(site_id)
        if picked is not None:
            cert.site_id = picked
            cert.site_ref = None
        else:
            cert.site_ref = str(site_id)[:120]
            cert.site_id = None
        only.add("site")
    if asset_id:
        cert.asset_id = asset_id
        only.add("asset")
    if not only:
        return {"ok": False, "error": "no vendor_id/site_id/asset_id supplied to link"}

    await session.flush()
    links = await _link_document_to_entities(session, cert, only=only)
    await session.commit()
    await session.refresh(cert)
    return {
        "ok": True,
        "certificate_id": str(cert.id),
        "document_id": str(cert.document_id),
        "linked": links,
    }


# Vendor columns the create-vendor form may fill (whitelist — never interpolate arbitrary keys).
_VENDOR_PROFILE_COLUMNS = (
    "trade",
    "address",
    "city",
    "postal_code",
    "country",
    "phone",
    "fax",
    "website",
    "email",
    "specialty",
    "notes",
    "status",
)


async def _download_document_b64(document_id: Any) -> str | None:
    """Fetch the source PDF from Doc-RAG and return base64 (for Claude vision extraction)."""
    import base64

    import httpx

    from ...config import settings

    if not document_id:
        return None
    import os

    # These certificate PDFs are served by svc-deepagents (/api/documents/{id}/download); the
    # Doc-RAG base is a secondary source. Try the deepagents route first, then Doc-RAG variants.
    da = (os.environ.get("DEEPAGENTS_BASE_URL") or "http://svc-deepagents:8008").rstrip("/")
    rag = (settings.doc_rag_base_url or "").rstrip("/")
    urls = [f"{da}/api/documents/{document_id}/download"]
    if rag:
        urls += [
            f"{rag}/doc-rag/documents/{document_id}/download",
            f"{rag}/api/documents/{document_id}/download",
            f"{rag}/documents/{document_id}/download",
        ]
    try:
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            for u in urls:
                try:
                    resp = await client.get(u)
                    if resp.status_code < 400 and resp.content:
                        return base64.b64encode(resp.content).decode("ascii")
                except Exception:  # noqa: BLE001
                    continue
    except Exception as exc:  # noqa: BLE001
        log.warning("vendor_profile.download_failed", error=str(exc)[:200])
    return None


async def extract_vendor_profile(
    session: AsyncSession, cert: ComplianceCertificate
) -> dict[str, Any]:
    """LLM-extract a rich vendor profile from the certificate document to pre-fill the
    center-chat create-vendor form: name, trade, address, city, postcode, country, phone,
    fax, email, website, specialty. Best-effort — falls back to raw_metadata + pack trade on
    any failure. (Uses Claude — this service's document-understanding LLM.)"""
    meta = cert.raw_metadata or {}
    profile: dict[str, Any] = {
        "vendor_name": (
            meta.get("vendor_name") or meta.get("company_name") or meta.get("contractor_name")
        ),
        "status": "active",
    }
    # Pack trade as a sensible default for the trade field.
    try:
        pack = await get_pack_type(
            session,
            cert.certificate_type_code or cert.cert_type or "",
            country_code=cert.country_code or "UK",
        )
        if pack and pack.trade_category:
            profile["trade"] = pack.trade_category
    except Exception:  # noqa: BLE001
        pass

    from ...config import settings

    source_text = meta.get("source_text") or meta.get("extracted_text")
    pdf_b64 = None
    if not source_text:
        pdf_b64 = await _download_document_b64(cert.document_id)
    if not (settings.anthropic_api_key and (source_text or pdf_b64)):
        return profile

    prompt = (
        "Extract the CONTRACTOR / vendor company's own details from this certificate. "
        "Return ONLY JSON with these keys (use null when a field is genuinely absent — never "
        "guess): vendor_name, trade, address, city, postal_code, country, phone, fax, email, "
        "website, specialty. 'trade' is the discipline (e.g. Fire, Electrical, Asbestos). "
        "'address' is the street address only (city/postcode separate). Do not return the "
        "issuing/assessing body — return the company the certificate is ABOUT."
    )
    try:
        import json as _json

        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if pdf_b64:
            content.insert(
                0,
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
            )
        elif source_text:
            content.append({"type": "text", "text": f"\n\nCERTIFICATE TEXT:\n{source_text[:14000]}"})
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": content}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        data = _json.loads(raw) if raw.strip().startswith("{") else {}
        for k in ("vendor_name", *(_VENDOR_PROFILE_COLUMNS)):
            v = data.get(k)
            if isinstance(v, str) and v.strip() and v.strip().lower() not in ("null", "n/a", "none"):
                profile[k] = v.strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("vendor_profile.extract_failed", error=str(exc)[:200])
    return profile


async def create_vendor_for_certificate(
    session: AsyncSession,
    certificate_id: UUID,
    *,
    vendor_name: str,
    trade_category: str | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a vendor row from a PM-filled chat form when a vendor cert had no extractable
    contractor name, then link this certificate (and its document) to the new vendor.

    Matches an existing vendor by name first (avoids duplicates); inserts a new stub otherwise.
    organization_id is optional here (unlike resolve_or_create_vendor) so a nameless-cert vendor
    can still be seeded — the legacy INTEGER org column takes NULL when absent."""
    name = re.sub(r"\s+", " ", (vendor_name or "").strip())
    if len(name) < 2:
        return {"ok": False, "error": "vendor_name is required (min 2 chars)"}
    cert = await session.get(ComplianceCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "certificate not found"}

    # Reuse an existing vendor of the same name; else insert a new stub row.
    # Same identity rule the contract path uses, so "Gough & Kelly Limited" on a certificate
    # and "Gough and Kelly Ltd." on a contract resolve to one vendor rather than two.
    _matched_id = await find_vendor_id(session, name)
    existing = {"id": _matched_id} if _matched_id else None
    # Merge form/profile fields into a whitelist-guarded column set (trade_category → trade).
    prof = dict(profile or {})
    if trade_category and not prof.get("trade"):
        prof["trade"] = trade_category
    extra_cols: dict[str, str] = {}
    for col in _VENDOR_PROFILE_COLUMNS:
        val = prof.get(col)
        if isinstance(val, str) and val.strip():
            extra_cols[col] = val.strip()[:255]

    if existing:
        vendor_id = str(existing["id"])
        created = False
        # Fill only currently-empty profile columns on the matched vendor (never clobber).
        for col, val in extra_cols.items():
            try:
                await session.execute(
                    text(
                        f"UPDATE plenum_cafm.vendors SET {col} = :v "
                        f"WHERE id::text = :id AND ({col} IS NULL OR {col} = '')"
                    ),
                    {"v": val, "id": vendor_id},
                )
            except Exception:  # noqa: BLE001
                pass
    else:
        vendor_id = str(uuid4())
        try:
            org_val: int | None = (
                int(cert.organization_id or cert.org_id)
                if (cert.organization_id or cert.org_id)
                else None
            )
        except (TypeError, ValueError):
            org_val = None
        binds: dict[str, Any] = {"id": vendor_id, "org": org_val, "name": name[:255]}
        col_sql = ["id", "organization_id", "vendor_name", "status", "created_at"]
        val_sql = [":id", ":org", ":name", "'active'", "now()"]
        for col, val in extra_cols.items():
            if col == "status":
                continue  # status forced 'active' on create
            col_sql.append(col)
            val_sql.append(f":{col}")
            binds[col] = val
        await session.execute(
            text(
                f"INSERT INTO plenum_cafm.vendors ({', '.join(col_sql)}) "
                f"VALUES ({', '.join(val_sql)})"
            ),
            binds,
        )
        try:
            await session.execute(
                text(
                    "UPDATE plenum_cafm.vendors SET block_state = 'Clear' "
                    "WHERE id = :id AND block_state IS NULL"
                ),
                {"id": vendor_id},
            )
        except Exception:  # noqa: BLE001
            pass
        created = True

    cert.vendor_id = vendor_id
    meta = dict(cert.raw_metadata or {})
    meta["vendor_name"] = name
    if trade_category:
        meta["trade_category"] = trade_category
    cert.raw_metadata = meta
    await session.flush()

    links: list[dict[str, str]] = []
    if cert.document_id:
        links = await _link_document_to_entities(session, cert, only={"vendor"})
    await session.commit()
    await session.refresh(cert)
    return {
        "ok": True,
        "certificate_id": str(cert.id),
        "vendor_id": vendor_id,
        "vendor_name": name,
        "created": created,
        "linked": links,
    }


async def archive_certificates(
    session: AsyncSession,
    *,
    certificate_ids: list[UUID],
    reason: str | None = None,
    archived_by: UUID | None = None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Soft-archive a chosen set of certificates (reversible; row + audit kept).

    Archived rows are hidden from Saved Space lists, KPI counts, and the alert-ladder
    scan (§3 remove-with-confirmation), but nothing is deleted — ``unarchive_certificates``
    restores them. Each id is handled independently so one bad id can't block the rest.
    """
    if not certificate_ids:
        return {"ok": False, "error": "no certificate_ids supplied"}
    now = datetime.now(timezone.utc)
    actor = f"user:{archived_by}" if archived_by else "user:pm"
    results: list[dict[str, Any]] = []
    archived = 0
    for cid in certificate_ids:
        cert = await session.get(ComplianceCertificate, cid)
        if not cert:
            results.append({"certificate_id": str(cid), "ok": False, "error": "not found"})
            continue
        cert_org = cert.organization_id or cert.org_id
        if organization_id and cert_org and cert_org != organization_id:
            results.append(
                {"certificate_id": str(cid), "ok": False, "error": "wrong organization"}
            )
            continue
        meta = dict(cert.raw_metadata or {})
        if meta.get("archived"):
            results.append(
                {
                    "certificate_id": str(cid),
                    "ok": True,
                    "already_archived": True,
                    "certificate_type_code": cert.certificate_type_code or cert.cert_type,
                }
            )
            continue
        meta["archived"] = True
        meta["archived_at"] = now.isoformat()
        meta["archive_reason"] = reason
        if archived_by:
            meta["archived_by"] = str(archived_by)
        cert.raw_metadata = meta
        cert.updated_at = now
        archived += 1
        results.append(
            {
                "certificate_id": str(cid),
                "ok": True,
                "certificate_type_code": cert.certificate_type_code or cert.cert_type,
                "certificate_number": cert.certificate_number,
            }
        )
        await write_audit(
            session,
            actor=actor,
            action_type="certificate.archive",
            source_feature="A",
            organization_id=cert_org,
            output_payload={
                "certificate_id": str(cid),
                "certificate_type_code": cert.certificate_type_code or cert.cert_type,
                "reason": reason,
            },
        )
    await session.commit()
    return {
        "ok": True,
        "requested": len(certificate_ids),
        "archived": archived,
        "results": results,
    }


async def unarchive_certificates(
    session: AsyncSession,
    *,
    certificate_ids: list[UUID],
    restored_by: UUID | None = None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Reverse a soft-archive — clears the archived flags and restores visibility."""
    if not certificate_ids:
        return {"ok": False, "error": "no certificate_ids supplied"}
    now = datetime.now(timezone.utc)
    actor = f"user:{restored_by}" if restored_by else "user:pm"
    results: list[dict[str, Any]] = []
    restored = 0
    for cid in certificate_ids:
        cert = await session.get(ComplianceCertificate, cid)
        if not cert:
            results.append({"certificate_id": str(cid), "ok": False, "error": "not found"})
            continue
        cert_org = cert.organization_id or cert.org_id
        if organization_id and cert_org and cert_org != organization_id:
            results.append(
                {"certificate_id": str(cid), "ok": False, "error": "wrong organization"}
            )
            continue
        meta = dict(cert.raw_metadata or {})
        if not meta.get("archived"):
            results.append(
                {"certificate_id": str(cid), "ok": True, "not_archived": True}
            )
            continue
        for key in ("archived", "archived_at", "archive_reason", "archived_by"):
            meta.pop(key, None)
        meta["unarchived_at"] = now.isoformat()
        cert.raw_metadata = meta
        cert.updated_at = now
        restored += 1
        results.append({"certificate_id": str(cid), "ok": True})
        await write_audit(
            session,
            actor=actor,
            action_type="certificate.unarchive",
            source_feature="A",
            organization_id=cert_org,
            output_payload={"certificate_id": str(cid)},
        )
    await session.commit()
    return {
        "ok": True,
        "requested": len(certificate_ids),
        "restored": restored,
        "results": results,
    }


async def draft_renewal_email(
    session: AsyncSession,
    *,
    certificate_id: UUID | None = None,
    certificate_number: str | None = None,
) -> dict[str, Any]:
    """
    Draft a renewal email for ONE selected certificate and queue it for PM approval.

    Building scope → a booking-request draft to the PM; Vendor scope → a renewal draft to the
    vendor contact. Returns `email_drafts` so the center chat renders it as a sendable card.
    Nothing is emailed until the PM sends it from the Approvals card (no WO created).
    """
    from ...config import settings
    from .contractors import resolve_site_display
    from .notify_drafts import _mailto

    cert: ComplianceCertificate | None = None
    if certificate_id:
        cert = await session.get(ComplianceCertificate, certificate_id)
    if cert is None and certificate_number:
        cert = (
            await session.execute(
                select(ComplianceCertificate)
                .where(ComplianceCertificate.certificate_number == certificate_number)
                .limit(1)
            )
        ).scalar_one_or_none()
    if cert is None:
        return {"ok": False, "error": "certificate not found (pass certificate_id or certificate_number)"}

    country = cert.country_code or "UK"
    pack = await get_pack_type(session, cert.certificate_type_code or cert.cert_type or "", country_code=country)
    type_name = pack.certificate_type_name if pack else (cert.certificate_type_code or "Certificate")
    days = cert.days_to_expiry
    status = cert.status or ""
    org_id = cert.organization_id or cert.org_id
    critical = status in ("Critical", "Lapsed")

    if cert.cert_scope == "Vendor":
        vendor_name, vendor_email = "Vendor", None
        if cert.vendor_id:
            try:
                async with session.begin_nested():
                    row = (
                        await session.execute(
                            text(
                                "SELECT v.vendor_name, (SELECT vc.email FROM plenum_cafm.vendor_contacts vc "
                                "WHERE vc.vendor_id::text = v.id::text ORDER BY vc.id LIMIT 1) AS email "
                                "FROM plenum_cafm.vendors v WHERE v.id::text = :vid"
                            ),
                            {"vid": str(cert.vendor_id)},
                        )
                    ).mappings().first()
                    if row:
                        vendor_name, vendor_email = row["vendor_name"] or "Vendor", row["email"]
            except Exception:  # noqa: BLE001
                pass
        renewal_url = pack.verification_url if pack else None
        to = vendor_email or settings.default_pm_email
        # Follow the lifecycle ladder: the "must not be assigned regulated work" block line is
        # only true once the accreditation has actually lapsed (past expiry). For a Current cert
        # this is an ADVANCE reminder — the vendor is not barred from work — and for an at-risk
        # (Expiring Soon / Due / Overdue / Critical) cert it is a chase-before-it-lapses notice.
        expired = status == "Lapsed" or (isinstance(days, int) and days < 0)
        at_risk = not expired and status in (
            "Expiring Soon",
            "Due for Renewal",
            "Overdue",
            "Critical",
        )
        if isinstance(days, int):
            days_txt = (
                f"{days} day{'s' if days != 1 else ''} remaining"
                if days >= 0
                else f"{abs(days)} day{'s' if abs(days) != 1 else ''} past expiry"
            )
        else:
            days_txt = "expiry date on file"
        if expired:
            prefix = "URGENT"
            headline = f"Your accreditation ({type_name}) has lapsed and requires renewal."
            gate_line = "Until renewed, you must not be assigned regulated work of this type.\n"
        elif at_risk:
            prefix = "ACTION"
            headline = (
                f"Your accreditation ({type_name}) is approaching expiry and should be renewed."
            )
            gate_line = (
                "Please renew before the expiry date to avoid a lapse that would bar "
                "regulated work of this type.\n"
            )
        else:  # Current — advance reminder, no work restriction
            prefix = "REMINDER"
            headline = (
                f"Advance renewal reminder for your accreditation ({type_name}) — "
                "it is currently valid and no regulated work is restricted."
            )
            gate_line = (
                "No work is blocked today; please plan the renewal ahead of the expiry date "
                "so the accreditation never lapses.\n"
            )
        subject = f"[{prefix}] Accreditation renewal — {type_name} — {vendor_name}"
        body = (
            f"Dear {vendor_name},\n\n"
            f"{headline}\n"
            f"Status: {status}\nExpiry date: {cert.expiry_date} ({days_txt})\n"
            f"Renewal register: {renewal_url or 'contact your issuing body'}\n\n"
            f"{gate_line}"
            "Please confirm the renewal and provide the updated certificate.\n\n"
            "Regards,\nProperty Manager (via Plenum Compliance Engine)\n"
        )
        item_type = "vendor_email"
    else:
        site_info = await resolve_site_display(session, cert.site_id)
        building = site_info.get("building_name") or "Building"
        prefix = "URGENT" if critical else "NOTICE"
        to = (settings.senior_pm_email if critical else settings.default_pm_email) or settings.default_pm_email
        contractor = pack.required_contractor_accreditation if pack else None
        subject = f"[{prefix}] {type_name} renewal — {building} — {days if days is not None else 'n/a'} days remaining"
        body = (
            f"Dear Property Manager,\n\n"
            f"A renewal booking is required for the following building certificate.\n\n"
            f"Certificate: {type_name}\n"
            f"Certificate number: {cert.certificate_number or 'n/a'}\n"
            f"Building: {building}\nStatus: {status}\nExpiry: {cert.expiry_date}\n"
            f"Days remaining: {days if days is not None else 'n/a'}\n"
            f"Required contractor accreditation: {contractor or 'n/a'}\n\n"
            "Action: approve the booking request (suggested 5 business-day window). This is a "
            "communication workflow — no work order is created.\n\n"
            "Regards,\nPlenum Compliance Engine\n"
        )
        item_type = "booking_request"

    draft = {
        "to": to or settings.default_pm_email,
        "subject": subject,
        "body": body,
        "status": status,
        "days_to_expiry": days,
        # mailto: URI so the UI can open the PM's mail client (Outlook) with the draft
        # pre-filled — the frontend Renew handler opens this when present.
        "mailto_uri": _mailto(to or settings.default_pm_email, subject, body),
    }
    approval_summary = f"Renewal email — {type_name} ({cert.certificate_number or '—'})"
    item = await enqueue_approval(
        session,
        source_feature="A",
        item_type=item_type,
        summary=approval_summary,
        severity="Critical" if critical else "Action required",
        organization_id=org_id,
        related_entity_type="compliance_certificate",
        related_entity_id=cert.id,
        email_draft=draft,
        payload={"certificate_id": str(cert.id), "renewal": True, "no_work_order": True},
    )
    await session.commit()
    chat_draft = {
        **draft,
        "queue_item_id": str(item.id),
        "certificate_id": str(cert.id),
        "summary": approval_summary,
        "escalation": critical,
    }
    return {
        "ok": True,
        "certificate_id": str(cert.id),
        "queue_item_id": str(item.id),
        "email_draft": draft,
        "email_drafts": [chat_draft],
        "message": "Renewal email drafted and queued — review and Send from the Approvals card.",
    }


async def building_change_invalidate(
    session: AsyncSession,
    *,
    site_id: UUID,
    change_description: str,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """A2 — material building change flags affected building certificates for PM review."""
    rows = list(
        (
            await session.execute(
                select(ComplianceCertificate).where(
                    ComplianceCertificate.site_id == site_id,
                    ComplianceCertificate.cert_scope == "Building",
                )
            )
        ).scalars().all()
    )
    flagged = []
    for cert in rows:
        meta = dict(cert.raw_metadata or {})
        meta["building_change_review"] = {
            "change": change_description,
            "flagged_at": datetime.now(timezone.utc).isoformat(),
        }
        cert.raw_metadata = meta
        flagged.append(str(cert.id))

    item = await enqueue_approval(
        session,
        source_feature="A",
        item_type="building_change_review",
        summary=f"Building change — review {len(flagged)} certificates (site {site_id})",
        severity="Action required",
        organization_id=organization_id,
        related_entity_type="site",
        related_entity_id=site_id,
        payload={
            "site_id": str(site_id),
            "change_description": change_description,
            "certificate_ids": flagged,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "flagged_certificates": flagged,
        "queue_item_id": str(item.id),
    }


async def saved_space_summary(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Compliance Saved Space summary rows for Building + Vendor sections + WoW trend.

    KPI buckets follow Phase-2 mockups / A1–A3 ladder (exclusive):
      Building
        Active (compliant)  = Current (dte > 90)
        Expiring <90d       = 8 ≤ dte ≤ 90  (Expiring Soon / Due / Overdue)
        Critical <7d        = 0 ≤ dte ≤ 7
        Lapsed              = dte < 0
      Vendor
        Blocked             = unique vendors with block_state=Blocked OR any lapsed cert
        High risk <30d      = 0 < dte ≤ 30 (not already counted as blocked/lapsed)
        Medium <90d         = 31 ≤ dte ≤ 90
        Accreditations      = total vendor certificates
    Days/status are recomputed from expiry_date so counts stay accurate between scans.
    """
    from datetime import date as date_cls, timedelta
    from uuid import uuid4

    from ...models import ComplianceRiskSnapshot
    from .country_pack import list_pack_types
    from .resource_skills import lapsed_operative_flags_for_vendors

    def _agg() -> dict[str, int]:
        return {
            "total": 0,
            "total_active": 0,  # Current / Clear — "Active" / compliant
            "compliant": 0,  # status = Current
            "non_compliant": 0,  # status = Lapsed (+ blocked vendor certs)
            "at_risk": 0,  # Expiring Soon / Due / Overdue / Critical
            "expiring_lt_90": 0,  # 8–90 exclusive of critical
            "expiring_lt_30": 0,  # 8–30 (overdue band)
            "critical_lt_7": 0,
            "lapsed": 0,
            "not_on_record": 0,
            "high_risk": 0,
            "medium_risk": 0,
            "drafts": 0,
        }

    building = _agg()
    vendor = _agg()
    today = date_cls.today()

    q = select(ComplianceCertificate)
    if organization_id:
        q = q.where(
            (ComplianceCertificate.organization_id == organization_id)
            | (ComplianceCertificate.org_id == organization_id)
        )
    rows = list((await session.execute(q)).scalars().all())
    # Exclude seeded test fixtures / superseded duplicates / soft-archived from KPI counts
    # (matches list_certificates).
    rows = [
        c
        for c in rows
        if not (
            (c.raw_metadata or {}).get("a1_test_fixture")
            or (c.raw_metadata or {}).get("superseded_duplicate")
            or (c.raw_metadata or {}).get("archived")
        )
    ]

    # Prefetch vendor block states (case-insensitive) for accurate blocked KPIs
    vendor_block: dict[str, str] = {}
    v_ids = [str(c.vendor_id) for c in rows if c.vendor_id]

    async def _load_block_states():
        if not v_ids:
            return {}
        stmt = text(
            """
            SELECT id::text AS id, block_state
            FROM plenum_cafm.vendors
            WHERE id::text IN :ids
            """
        ).bindparams(bindparam("ids", expanding=True))
        vrows = (await session.execute(stmt, {"ids": v_ids})).mappings().all()
        return {str(v["id"]): (v.get("block_state") or "Clear") for v in vrows}

    vendor_block = (
        await _safe_exec(
            session,
            _load_block_states,
            label="saved_space.vendor_block_states_failed",
            default={},
        )
        or {}
    )

    blocked_vendor_keys: set[str] = set()
    for vid, state in vendor_block.items():
        if str(state).strip().lower() == "blocked":
            blocked_vendor_keys.add(vid)

    dirty = False
    for c in rows:
        scope = (c.cert_scope or "").strip().lower()
        bucket = (
            building
            if scope == "building"
            else vendor
            if scope == "vendor"
            else None
        )
        if not bucket:
            continue

        meta = c.raw_metadata or {}
        is_draft = bool(meta.get("draft") or meta.get("requires_pm_confirmation"))
        if is_draft:
            bucket["drafts"] += 1

        life = compute_certificate_status(c.expiry_date, thresholds=None, today=today)
        dte = life.days_to_expiry
        # Keep stored columns fresh so subsequent list/KPI calls agree
        if c.days_to_expiry != life.days_to_expiry or c.status != life.status:
            c.days_to_expiry = life.days_to_expiry
            c.status = life.status
            c.insurance_risk_flag = bool(life.insurance_risk_flag)
            dirty = True

        bucket["total"] += 1

        if scope == "building":
            if dte is None:
                # No expiry → treat as gap-ish / unknown; don't inflate Active
                continue
            if dte < 0 or life.status == STATUS_LAPSED:
                bucket["lapsed"] += 1
                bucket["non_compliant"] += 1
            elif 0 <= dte <= 7 or life.status == STATUS_CRITICAL:
                bucket["critical_lt_7"] += 1
                bucket["expiring_lt_30"] += 1  # also within 30d window
                bucket["at_risk"] += 1
            elif 8 <= dte <= 30 or life.status == STATUS_OVERDUE:
                bucket["expiring_lt_90"] += 1
                bucket["expiring_lt_30"] += 1
                bucket["at_risk"] += 1
            elif 31 <= dte <= 90 or life.status in {
                STATUS_DUE_FOR_RENEWAL,
                STATUS_EXPIRING_SOON,
            }:
                bucket["expiring_lt_90"] += 1
                bucket["at_risk"] += 1
            else:
                # Current (>90)
                bucket["total_active"] += 1
                bucket["compliant"] += 1
        else:
            # Vendor accreditations
            vid = str(c.vendor_id) if c.vendor_id else None
            block_state = (vendor_block.get(vid or "") or "").strip().lower()
            is_blocked = block_state == "blocked" or (
                dte is not None and dte <= 0
            ) or life.status == STATUS_LAPSED
            if is_blocked:
                bucket["lapsed"] += 1
                bucket["non_compliant"] += 1
                if vid:
                    blocked_vendor_keys.add(vid)
                elif c.certificate_number or c.certificate_type_code:
                    # Orphan lapsed cert without vendor_id — still count as non-compliant
                    blocked_vendor_keys.add(f"orphan:{c.id}")
                continue

            risk = vendor_risk_level(dte)
            if risk == "High Risk":
                bucket["high_risk"] += 1
                bucket["expiring_lt_30"] += 1
                bucket["expiring_lt_90"] += 1
                bucket["at_risk"] += 1
            elif risk == "Medium Risk":
                bucket["medium_risk"] += 1
                bucket["expiring_lt_90"] += 1
                bucket["at_risk"] += 1
            else:
                bucket["total_active"] += 1
                bucket["compliant"] += 1

    # Gaps: pack types with no certificate record — visible, never hidden. Computed for
    # BOTH Building and Vendor scopes so the PM sees the full statutory picture; each gap
    # carries its `scope` so the UI can group them (Building gaps vs Vendor gaps).
    def _present_codes(scope_l: str) -> set[str]:
        return {
            (c.certificate_type_code or c.cert_type)
            for c in rows
            if (c.cert_scope or "").strip().lower() == scope_l
            and (c.certificate_type_code or c.cert_type)
        }

    building_pack = await list_pack_types(session, country_code="UK", scope="Building")
    vendor_pack = await list_pack_types(session, country_code="UK", scope="Vendor")
    building_present = _present_codes("building")
    vendor_present = _present_codes("vendor")
    building_missing = [
        {
            "certificate_type_code": p.certificate_type_code,
            "certificate_type_name": p.certificate_type_name,
            "trade_category": getattr(p, "trade_category", None) or "Uncategorised",
            "scope": "Building",
        }
        for p in building_pack
        if p.certificate_type_code not in building_present
    ]
    vendor_missing = [
        {
            "certificate_type_code": p.certificate_type_code,
            "certificate_type_name": p.certificate_type_name,
            "trade_category": getattr(p, "trade_category", None) or "Uncategorised",
            "scope": "Vendor",
        }
        for p in vendor_pack
        if p.certificate_type_code not in vendor_present
    ]
    missing = building_missing + vendor_missing
    building["not_on_record"] = len(building_missing)
    vendor["not_on_record"] = len(vendor_missing)

    # Prefer unique blocked vendors derived from certs + vendors.block_state.
    # Fall back to a global vendors table count when no vendor-linked certs exist yet.
    blocked = len(blocked_vendor_keys)

    async def _count_blocked_table():
        result = await session.execute(
            text(
                """
                SELECT COUNT(*) FROM plenum_cafm.vendors
                WHERE lower(coalesce(block_state, '')) = 'blocked'
                """
            )
        )
        return int(result.scalar() or 0)

    if blocked == 0:
        blocked = int(
            await _safe_exec(
                session,
                _count_blocked_table,
                label="saved_space.vendors_blocked_count_failed",
                default=0,
            )
            or 0
        )

    high = vendor["high_risk"]
    medium = vendor["medium_risk"]

    # Persist today snapshot + week-on-week delta
    week_ago = today - timedelta(days=7)
    snap_q = select(ComplianceRiskSnapshot).where(
        ComplianceRiskSnapshot.snapshot_date == today
    )
    if organization_id:
        snap_q = snap_q.where(ComplianceRiskSnapshot.organization_id == organization_id)
    else:
        snap_q = snap_q.where(ComplianceRiskSnapshot.organization_id.is_(None))
    snap = (await session.execute(snap_q)).scalar_one_or_none()
    if not snap:
        snap = ComplianceRiskSnapshot(
            id=uuid4(),
            organization_id=organization_id,
            snapshot_date=today,
        )
        session.add(snap)
    snap.vendors_blocked = blocked
    snap.high_risk_lt_30 = high
    snap.medium_risk_lt_90 = medium

    prior_q = select(ComplianceRiskSnapshot).where(
        ComplianceRiskSnapshot.snapshot_date <= week_ago
    )
    if organization_id:
        prior_q = prior_q.where(ComplianceRiskSnapshot.organization_id == organization_id)
    else:
        prior_q = prior_q.where(ComplianceRiskSnapshot.organization_id.is_(None))
    prior = (
        await session.execute(
            prior_q.order_by(ComplianceRiskSnapshot.snapshot_date.desc()).limit(1)
        )
    ).scalar_one_or_none()

    wow = {
        "vendors_blocked_delta": blocked - (prior.vendors_blocked if prior else blocked),
        "high_risk_delta": high - (prior.high_risk_lt_30 if prior else high),
        "medium_risk_delta": medium - (prior.medium_risk_lt_90 if prior else medium),
        "compared_to": prior.snapshot_date.isoformat() if prior else None,
    }

    vendor_ids = list({c.vendor_id for c in rows if c.vendor_id})
    operative_lapsed = await lapsed_operative_flags_for_vendors(session, vendor_ids)

    if dirty:
        await session.flush()
    await session.commit()

    return {
        "building_certificates": building,
        "vendor_certificates": vendor,
        "not_on_record_types": missing[:120],
        "risk_dashboard": {
            "vendors_blocked": blocked,
            "high_risk_lt_30": high,
            "medium_risk_lt_90": medium,
            "building_critical_lt_7": building["critical_lt_7"],
            "building_lapsed": building["lapsed"],
            "building_compliant": building["compliant"],
            "building_non_compliant": building["non_compliant"],
            "building_at_risk": building["at_risk"],
            "vendor_compliant": vendor["compliant"],
            "vendor_non_compliant": vendor["non_compliant"],
            "vendor_at_risk": vendor["at_risk"],
            "week_on_week": wow,
        },
        "operative_lapsed_by_vendor": operative_lapsed,
        "themes": {
            "building": "blue",
            "vendor": "amber_red",
        },
    }


_DATE_FORMATS = (
    "%m/%d/%Y",   # US 05/15/2026
    "%d/%m/%Y",   # UK/EU 15/05/2026
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%m/%d/%y",
    "%d/%m/%y",
    "%d %b %Y",   # 15 May 2026
    "%d %B %Y",
    "%b %d, %Y",  # May 15, 2026
    "%B %d, %Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%Y%m%d",
)


def _parse_date(val: Any) -> date | None:
    """Parse a date from many formats (ISO, US MM/DD/YYYY, UK DD/MM/YYYY, month names).

    Must NEVER raise — an unparseable extracted date returns None so ingestion still saves the
    draft (the field is simply left blank) instead of 500-ing the whole certificate upsert.
    """
    if val is None or val == "":
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()
    if not s:
        return None
    # ISO first (YYYY-MM-DD, optionally with a time component).
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    # Last resort: pull the first date-like triple and disambiguate by range.
    m = re.search(r"(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})", s)
    if m:
        a, b, c = (int(x) for x in m.groups())
        try:
            if a > 31:  # YYYY M D
                return date(a, b, c)
            if c > 31:  # D/M/YYYY or M/D/YYYY — prefer month-first when valid
                month, day = (a, b) if a <= 12 else (b, a)
                return date(c, month, day)
        except ValueError:
            return None
    return None


def _parse_uuid(val: Any) -> UUID | None:
    if val is None or val == "":
        return None
    if isinstance(val, UUID):
        return val
    return UUID(str(val))
