"""CCC Step 4 — verify certificate against compliance_verification_sources."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models import ComplianceCertificate
from . import epc_rating
from ...shared.approvals import enqueue_approval
from .channels import run_public_api_check
from .channels.register_search import deep_link, register_for_certificate_code, run_register_search
from .notify_drafts import verification_human_email_draft
from .register_dump import lookup_register_row
from .verification import build_verify_now_link
from .verification_sources import canonicalize_type_code, get_verification_source

log = get_logger(__name__)

_COMPANY_NUMBER_RE = re.compile(r"\b([0-9]{8}|[A-Z]{2}[0-9]{6})\b", re.I)


def _meta_str(meta: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = meta.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _extract_company_number(meta: dict[str, Any], issuer: str | None) -> str | None:
    direct = _meta_str(
        meta,
        "company_number",
        "companies_house_number",
        "company_no",
        "ch_number",
        "organisation_number",
    )
    if direct:
        return direct
    for candidate in (issuer, meta.get("vendor_name"), meta.get("issuer")):
        if not candidate:
            continue
        m = _COMPANY_NUMBER_RE.search(str(candidate))
        if m:
            return m.group(1).upper()
    return None


async def verify_certificate_type(
    session: AsyncSession,
    *,
    certificate_type_code: str | None,
    certificate_number: str | None = None,
    accreditation_number: str | None = None,
    vendor_name: str | None = None,
    company_number: str | None = None,
    postcode: str | None = None,
    insurer_name: str | None = None,
    certificate_id: UUID | None = None,
    organization_id: UUID | None = None,
    country_code: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Resolve source row → public_api / data_dump check or website Verify-now fallback.
    Persist result into certificate.raw_metadata when certificate_id given.
    """
    canon = canonicalize_type_code(certificate_type_code, country_code)
    source = await get_verification_source(
        session, canon or certificate_type_code, country_code
    )
    checked_at = datetime.now(timezone.utc).isoformat()

    if not source:
        verify_now = await build_verify_now_link(
            session,
            certificate_type_code=certificate_type_code or canon or "",
            accreditation_number=accreditation_number or certificate_number,
            vendor_name=vendor_name,
        )
        result = {
            "ok": True,
            "status": "not_configured",
            "verified": None,
            "channel": "website",
            "source_url": verify_now.get("verification_url"),
            "checked_at": checked_at,
            "evidence": {"message": "No compliance_verification_sources row; Verify-now only"},
            "verify_now": verify_now,
            "canonical_code": canon,
        }
        if persist and certificate_id:
            await _persist(session, certificate_id, result, enqueue_human=False)
        return result

    channel = source["channel"]
    api_available = source["api_available"]
    source_url = source.get("source_url")

    # Self-produced / cert_format — no external check
    if channel in ("self_produced", "cert_format"):
        result = {
            "ok": True,
            "status": "n_a",
            "verified": None,
            "channel": channel,
            "source_url": source_url,
            "checked_at": checked_at,
            "evidence": {"notes": source.get("notes") or "No external register"},
            "source": source,
            "canonical_code": canon,
        }
        if persist and certificate_id:
            await _persist(session, certificate_id, result, enqueue_human=False)
        return result

    # Public API path
    public_config_evidence: dict[str, Any] | None = None
    if channel == "public_api" and api_available == "yes":
        api_result = await run_public_api_check(
            certificate_type_code=canon or certificate_type_code or "",
            certificate_number=certificate_number or accreditation_number,
            company_number=company_number,
            postcode=postcode,
            insurer_name=insurer_name or vendor_name,
            vendor_name=vendor_name,
        )
        if api_result.get("needs_config"):
            public_config_evidence = dict(api_result.get("evidence") or {})
        else:
            verified = api_result.get("verified")
            status = (
                "verified"
                if verified is True
                else "failed"
                if verified is False
                else "inconclusive"
            )
            result = {
                "ok": True,
                "status": status,
                "verified": verified,
                "channel": "public_api",
                "source_url": source_url,
                "checked_at": checked_at,
                "evidence": api_result.get("evidence") or {},
                "source": source,
                "canonical_code": canon,
            }
            if persist and certificate_id:
                await _persist(
                    session,
                    certificate_id,
                    result,
                    enqueue_human=verified is False,
                    organization_id=organization_id,
                )
            return result

    # Data dump — local register rows (§8.2)
    if channel == "data_dump":
        key = accreditation_number or certificate_number or vendor_name
        local = await lookup_register_row(
            session,
            certificate_type_code=canon or certificate_type_code,
            lookup_key=key,
        )
        if local:
            result = {
                "ok": True,
                "status": "verified",
                "verified": True,
                "channel": "data_dump",
                "source_url": source_url,
                "checked_at": checked_at,
                "evidence": {
                    "message": "Matched local weekly dump row",
                    "lookup_key": local.get("lookup_key"),
                    "source_file": local.get("source_file"),
                    "row_id": local.get("id"),
                },
                "source": source,
                "canonical_code": canon,
            }
            if persist and certificate_id:
                await _persist(
                    session,
                    certificate_id,
                    result,
                    enqueue_human=False,
                    organization_id=organization_id,
                )
            return result

        # Miss — bot/auto register search by company name → auto-verify on confident hit
        search_q = (vendor_name or key or "").strip()
        reg = register_for_certificate_code(canon or certificate_type_code)
        search_info: dict[str, Any] = {}
        if reg and search_q:
            try:
                search_info = await run_register_search(
                    session,
                    register=reg,
                    name=search_q,
                    query=search_q,
                    limit=10,
                    mode="auto",
                )
                auto_hit = search_info.get("auto_verify_hit")
                if search_info.get("verified") and auto_hit:
                    match_src = auto_hit.get("match_source") or "register_bot"
                    result = {
                        "ok": True,
                        "status": "verified",
                        "verified": True,
                        "channel": "register_bot"
                        if match_src == "live_bot"
                        else "data_dump",
                        "source_url": auto_hit.get("source_url")
                        or source_url
                        or deep_link(reg, search_q),
                        "checked_at": checked_at,
                        "evidence": {
                            "message": (
                                "Auto-verified via register bot name search"
                                if match_src == "live_bot"
                                else "Auto-verified via dump name search"
                            ),
                            "lookup_key": auto_hit.get("lookup_key"),
                            "company_name": auto_hit.get("company_name"),
                            "register": reg,
                            "match_source": match_src,
                            "match_confidence": auto_hit.get("match_confidence"),
                            "hit": auto_hit,
                        },
                        "source": source,
                        "canonical_code": canon,
                        "register_search": search_info,
                    }
                    if persist and certificate_id:
                        await _persist(
                            session,
                            certificate_id,
                            result,
                            enqueue_human=False,
                            organization_id=organization_id,
                        )
                    return result
            except Exception as exc:  # noqa: BLE001
                log.warning("ccc_verify.register_search_failed", error=str(exc)[:200])

        verify_now = await build_verify_now_link(
            session,
            certificate_type_code=certificate_type_code or canon or "",
            accreditation_number=accreditation_number or certificate_number,
            vendor_name=vendor_name,
        )
        url = (
            (search_info.get("deep_link") if search_info else None)
            or source_url
            or verify_now.get("verification_url")
        )
        result = {
            "ok": True,
            "status": "dump_miss",
            "verified": False,
            "channel": "data_dump",
            "source_url": url,
            "checked_at": checked_at,
            "evidence": {
                "message": (
                    "No confident register name match — "
                    "open register search or verify manually (class 3)"
                ),
                "lookup_key": (key or "").strip().upper() or None,
                "api_available": api_available,
                "register": reg,
                "register_search_deep_link": url,
                "register_search_notes": (search_info or {}).get("notes") or [],
                "local_search_hits": len(
                    (search_info or {}).get("results")
                    or (search_info or {}).get("local_hits")
                    or []
                ),
            },
            "verify_now": verify_now,
            "register_search": search_info or None,
            "source": source,
            "canonical_code": canon,
        }
        if persist and certificate_id:
            await _persist(
                session,
                certificate_id,
                result,
                enqueue_human=True,
                organization_id=organization_id,
            )
        return result

    # Website channel — a locally-ingested register dump (if present) wins before the
    # register-bot / Verify-now fallback. Some website-only registers (e.g. the BAFE Fire
    # Safety Register) can't be scraped server-side, but confirmed rows copied into the local
    # dump should still auto-verify; certs with no dump row keep the Verify-now link below.
    if channel == "website":
        dump_key = accreditation_number or certificate_number or vendor_name
        local = await lookup_register_row(
            session,
            certificate_type_code=canon or certificate_type_code,
            lookup_key=dump_key,
        )
        if local:
            result = {
                "ok": True,
                "status": "verified",
                "verified": True,
                "channel": "data_dump",
                "source_url": source_url,
                "checked_at": checked_at,
                "evidence": {
                    "message": "Matched local register dump row (website register)",
                    "lookup_key": local.get("lookup_key"),
                    "source_file": local.get("source_file"),
                    "row_id": local.get("id"),
                },
                "source": source,
                "canonical_code": canon,
            }
            if persist and certificate_id:
                await _persist(
                    session,
                    certificate_id,
                    result,
                    enqueue_human=False,
                    organization_id=organization_id,
                )
            return result

    # Website channel — try register bot by vendor name before Verify-now only
    reg = register_for_certificate_code(canon or certificate_type_code)
    search_q = (vendor_name or accreditation_number or certificate_number or "").strip()
    if channel == "website" and reg and search_q:
        try:
            search_info = await run_register_search(
                session,
                register=reg,
                name=search_q,
                query=search_q,
                limit=10,
                mode="auto",
            )
            auto_hit = search_info.get("auto_verify_hit")
            if search_info.get("verified") and auto_hit:
                match_src = auto_hit.get("match_source") or "register_bot"
                result = {
                    "ok": True,
                    "status": "verified",
                    "verified": True,
                    "channel": "register_bot"
                    if match_src == "live_bot"
                    else "data_dump",
                    "source_url": auto_hit.get("source_url")
                    or source_url
                    or deep_link(reg, search_q),
                    "checked_at": checked_at,
                    "evidence": {
                        "message": "Auto-verified via register bot name search",
                        "company_name": auto_hit.get("company_name"),
                        "lookup_key": auto_hit.get("lookup_key"),
                        "register": reg,
                        "match_source": match_src,
                        "match_confidence": auto_hit.get("match_confidence"),
                        "hit": auto_hit,
                    },
                    "source": source,
                    "canonical_code": canon,
                    "register_search": search_info,
                }
                if persist and certificate_id:
                    await _persist(
                        session,
                        certificate_id,
                        result,
                        enqueue_human=False,
                        organization_id=organization_id,
                    )
                return result
        except Exception as exc:  # noqa: BLE001
            log.warning("ccc_verify.website_bot_failed", error=str(exc)[:200])

    # Website / partner / public needs_config → Verify-now navigation
    verify_now = await build_verify_now_link(
        session,
        certificate_type_code=certificate_type_code or canon or "",
        accreditation_number=accreditation_number or certificate_number,
        vendor_name=vendor_name,
    )
    url = source_url or verify_now.get("verification_url")
    status = "website_only" if channel == "website" or api_available == "no" else "needs_human"
    if channel == "private_api" and api_available == "partner":
        status = "needs_partner"
    if public_config_evidence is not None:
        status = "needs_config"

    evidence: dict[str, Any] = {
        "message": (
            "Partner API not configured — open register URL (needs_partner)"
            if status == "needs_partner"
            else public_config_evidence.get("message")
            if public_config_evidence
            else "Open register URL to verify (no live scrape in v1)"
        ),
        "api_available": api_available,
    }
    if public_config_evidence:
        evidence.update(public_config_evidence)

    result = {
        "ok": True,
        "status": status,
        "verified": None,
        "channel": channel,
        "source_url": url,
        "checked_at": checked_at,
        "evidence": evidence,
        "verify_now": verify_now,
        "source": source,
        "canonical_code": canon,
    }
    if persist and certificate_id:
        await _persist(
            session,
            certificate_id,
            result,
            enqueue_human=status in ("needs_human", "needs_partner", "failed", "dump_miss"),
            organization_id=organization_id,
        )
    return result


async def verify_stored_certificate(
    session: AsyncSession,
    certificate_id: UUID,
) -> dict[str, Any]:
    cert = await session.get(ComplianceCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "not_found"}
    meta = dict(cert.raw_metadata or {}) if isinstance(cert.raw_metadata, dict) else {}
    company_number = _extract_company_number(meta, cert.issuer)
    postcode = _meta_str(meta, "postcode", "site_postcode", "property_postcode", "address_postcode")
    insurer = (
        _meta_str(meta, "insurer_name", "insurer", "insurance_provider")
        or cert.issuer
    )
    return await verify_certificate_type(
        session,
        certificate_type_code=cert.certificate_type_code or cert.cert_type,
        certificate_number=cert.certificate_number,
        accreditation_number=cert.inspector_accreditation_number,
        vendor_name=_meta_str(meta, "vendor_name", "contractor_name") or cert.issuer,
        company_number=company_number,
        postcode=postcode,
        insurer_name=insurer,
        certificate_id=cert.id,
        organization_id=cert.organization_id or cert.org_id,
        country_code=getattr(cert, "country_code", None),
        persist=True,
    )


async def _persist(
    session: AsyncSession,
    certificate_id: UUID,
    result: dict[str, Any],
    *,
    enqueue_human: bool,
    organization_id: UUID | None = None,
) -> None:
    cert = await session.get(ComplianceCertificate, certificate_id)
    if not cert:
        return
    meta = dict(cert.raw_metadata or {})
    # Merged onto whatever is already there. A register link stored by verify-now lives in
    # this same block, and replacing it wholesale would erase the link while the status line
    # kept rendering — a loss nobody would see.
    verification = dict(meta.get("verification") or {})
    verification.update({
        "channel": result.get("channel"),
        "source_url": result.get("source_url"),
        "checked_at": result.get("checked_at"),
        "verified": result.get("verified"),
        "status": result.get("status"),
        "evidence": result.get("evidence") or {},
        "canonical_code": result.get("canonical_code"),
    })
    meta["verification"] = verification
    cert.raw_metadata = meta
    # B5: the GOV.UK register reports the current band for an EPC it resolves. The register
    # is the issued figure, so it overrides whatever the document said.
    band = epc_rating.rating_from_verification(meta)
    if band and epc_rating.is_epc_type(cert.certificate_type_code or cert.cert_type):
        cert.energy_rating = band
    cert.updated_at = datetime.now(timezone.utc)
    await session.flush()

    if enqueue_human:
        draft = verification_human_email_draft(
            certificate_type_code=cert.certificate_type_code or cert.cert_type,
            status=str(result.get("status") or "needs_human"),
            source_url=result.get("source_url"),
            certificate_id=str(cert.id),
            evidence_message=str((result.get("evidence") or {}).get("message") or ""),
        )
        await enqueue_approval(
            session,
            source_feature="A",
            item_type="verification_human",
            summary=(
                f"Human verification required — "
                f"{cert.certificate_type_code or cert.cert_type or cert.id} "
                f"({result.get('status')})"
            ),
            severity="medium",
            payload={
                "certificate_id": str(cert.id),
                "verification": verification,
                "source_url": result.get("source_url"),
            },
            organization_id=organization_id or cert.organization_id or cert.org_id,
            related_entity_type="compliance_certificate",
            related_entity_id=cert.id,
            email_draft=draft,
        )
    await session.commit()
