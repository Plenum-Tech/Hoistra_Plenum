"""A5 — Accreditation verification + vendor registration/compliance search.

Live register scrape/API is out of scope (PRD Q3) — Verify-now is navigation only.
Platform *does* search internal vendor + certificate records to report whether the
vendor is registered on-platform and currently compliant (or blocked/lapsed).
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID
from urllib.parse import urlencode

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from .country_pack import get_pack_type, load_pack_json

log = get_logger(__name__)


def list_verification_registers() -> list[dict[str, Any]]:
    """Authoritative Verify-now register table from the UK Country Pack."""
    pack = load_pack_json()
    raw = pack.get("verification_registers") or []
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        # Legacy shape: { name: url }
        for name, url in raw.items():
            out.append(
                {
                    "register": name,
                    "trade": None,
                    "prefills_number": True,
                    "verification_url": url,
                    "aliases": [name],
                }
            )
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "register": item.get("register"),
                "trade": item.get("trade"),
                "prefills_number": bool(item.get("prefills_number")),
                "verification_url": item.get("verification_url"),
                "aliases": list(item.get("aliases") or []),
            }
        )
    return out


def resolve_verification_register(
    *,
    certificate_type_code: str | None = None,
    issuing_body: str | None = None,
    verification_url: str | None = None,
) -> dict[str, Any] | None:
    """Match a pack type to a verification register (URL / alias / issuing body)."""
    registers = list_verification_registers()
    code = (certificate_type_code or "").strip().upper()
    body = (issuing_body or "").strip().lower()
    url = (verification_url or "").strip().rstrip("/")

    # 1) Exact URL match
    if url:
        for reg in registers:
            ru = (reg.get("verification_url") or "").strip().rstrip("/")
            if ru and ru == url:
                return reg

    # 2) Alias / type-code match
    if code:
        for reg in registers:
            aliases = {str(a).strip().upper() for a in (reg.get("aliases") or [])}
            if code in aliases:
                return reg

    # 3) Issuing-body contains register name (or vice versa)
    if body:
        for reg in registers:
            name = (reg.get("register") or "").strip().lower()
            if not name:
                continue
            if name in body or body in name:
                return reg
            for alias in reg.get("aliases") or []:
                a = str(alias).strip().lower()
                if a and (a in body or body in a):
                    return reg
    return None


async def build_verify_now_link(
    session: AsyncSession,
    *,
    certificate_type_code: str,
    accreditation_number: str | None = None,
    country_code: str = "UK",
    vendor_name: str | None = None,
) -> dict[str, Any]:
    """
    Opens the relevant register in a new tab.
    Pre-fills accreditation number only when the register supports it
    (see verification_registers.prefills_number in the UK pack).
    Navigation action only — no scraping or automation.
    """
    pack = await get_pack_type(
        session, certificate_type_code, country_code=country_code
    )
    if not pack:
        # Fall back to register aliases when pack row missing
        reg = resolve_verification_register(
            certificate_type_code=certificate_type_code
        )
        if not reg or not reg.get("verification_url"):
            return {
                "ok": False,
                "error": f"Unknown certificate type: {certificate_type_code}",
                "verification_url": None,
            }
        base_url = reg["verification_url"]
        prefills = bool(reg.get("prefills_number"))
        url = _append_register_search_params(
            base_url,
            accreditation_number=accreditation_number,
            vendor_name=vendor_name,
            prefills_number=prefills,
        )
        return {
            "ok": True,
            "certificate_type_code": certificate_type_code,
            "certificate_type_name": None,
            "issuing_body": reg.get("register"),
            "trade": reg.get("trade"),
            "prefills_number": prefills,
            "accreditation_number": accreditation_number,
            "vendor_name": vendor_name,
            "verification_url": url,
            "register_url": base_url,
            "mode": "navigation",
            "message": (
                "Open this URL in a new tab to verify against the live register. "
                "Platform does not scrape or auto-verify in v1.2."
            ),
        }

    reg = resolve_verification_register(
        certificate_type_code=certificate_type_code,
        issuing_body=pack.issuing_body,
        verification_url=pack.verification_url,
    )
    base_url = pack.verification_url or (reg or {}).get("verification_url")
    if not base_url:
        return {
            "ok": True,
            "certificate_type_code": certificate_type_code,
            "certificate_type_name": pack.certificate_type_name,
            "issuing_body": pack.issuing_body,
            "verification_url": None,
            "prefills_number": False,
            "mode": "manual",
            "message": "No public verification register URL configured for this type.",
        }

    prefills = bool((reg or {}).get("prefills_number")) if reg else True
    url = _append_register_search_params(
        base_url,
        accreditation_number=accreditation_number,
        vendor_name=vendor_name,
        prefills_number=prefills,
    )

    return {
        "ok": True,
        "certificate_type_code": certificate_type_code,
        "certificate_type_name": pack.certificate_type_name,
        "issuing_body": (reg or {}).get("register") or pack.issuing_body,
        "trade": (reg or {}).get("trade") or pack.trade_category,
        "prefills_number": prefills,
        "accreditation_number": accreditation_number,
        "vendor_name": vendor_name,
        "verification_url": url,
        "register_url": base_url,
        "mode": "navigation",
        "message": (
            "Open this URL in a new tab to verify against the live register. "
            + (
                "Accreditation / membership number pre-filled from the certificate. "
                if prefills and (accreditation_number or vendor_name)
                else (
                    "This register does not support number pre-fill — search manually. "
                    if not prefills
                    else ""
                )
            )
            + "Platform does not scrape or auto-verify in v1.2."
        ),
    }


def _append_register_search_params(
    base_url: str,
    *,
    accreditation_number: str | None,
    vendor_name: str | None,
    prefills_number: bool,
) -> str:
    """Append register-specific search params when prefill is supported."""
    url = base_url
    if not prefills_number:
        return url
    number = (accreditation_number or "").strip()
    name = (vendor_name or "").strip()
    if not number and not name:
        return url

    # BAFE Salesforce company verify form fields:
    #   j_id0:j_id3:registration  = Organisation ID or Scheme Certificate No.
    #   j_id0:j_id3:name          = Organisation Name
    if "bafe.my.salesforce-sites.com" in url.lower() or "wbcompanyverify" in url.lower():
        params: dict[str, str] = {}
        if number:
            params["j_id0:j_id3:registration"] = number
        elif name:
            params["j_id0:j_id3:name"] = name
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{urlencode(params)}"

    search_term = number or name
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode({'q': search_term, 'search': search_term})}"


def soft_authenticity_warning(
    *,
    certificate_type_code: str,
    inspector_accreditation_number: str | None,
    register_name: str | None,
) -> str | None:
    """
    Soft warning only (PRD open Q2). PM retains override authority.
    Live register API is out of scope (Q3) — warn when number missing or
    when PM should manually verify against the named register.
    """
    if not register_name:
        return None
    if not inspector_accreditation_number or not inspector_accreditation_number.strip():
        return (
            f"Inspector accreditation number not found in [{register_name}] — "
            f"please verify certificate authenticity. "
            f"PM retains override authority (soft warning only)."
        )
    return (
        f"Inspector accreditation number not verified live against [{register_name}] "
        f"in this phase — please Verify now. "
        f"PM retains override authority (soft warning only)."
    )


async def check_vendor_registration_compliance(
    session: AsyncSession,
    *,
    vendor_name: str | None,
    certificate_type_code: str,
    accreditation_number: str | None = None,
    cert_scope: str | None = "Vendor",
    country_code: str = "UK",
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """
    Search whether the vendor is registered on-platform and currently compliant
    for this accreditation type, plus build the external Verify-now register URL.

    Does NOT scrape Gas Safe / NICEIC / etc. (PRD Q3). Soft authenticity gate.
    """
    name = re.sub(r"\s+", " ", (vendor_name or "").strip())
    pack = await get_pack_type(
        session, certificate_type_code, country_code=country_code
    )
    if not pack:
        return {
            "ok": False,
            "error": f"Unknown certificate type: {certificate_type_code}",
            "vendor_found": False,
            "platform_compliance": "unknown",
            "is_compliant": None,
            "verification_url": None,
        }

    # For Building certs, also search against required contractor accreditation
    search_type = certificate_type_code
    required_acc = getattr(pack, "required_contractor_accreditation", None)
    if (cert_scope or "").title() == "Building" and required_acc:
        search_type = str(required_acc).strip() or certificate_type_code

    verify_type = search_type
    if not await get_pack_type(session, search_type, country_code=country_code):
        verify_type = certificate_type_code

    verify = await build_verify_now_link(
        session,
        certificate_type_code=verify_type,
        accreditation_number=accreditation_number,
        country_code=country_code,
        vendor_name=name or None,
    )

    vendor_row: dict[str, Any] | None = None
    cert_row: dict[str, Any] | None = None
    if name and len(name) >= 2:
        try:
            async with session.begin_nested():
                params: dict[str, Any] = {
                    "name": name,
                    "like": f"%{name[:40]}%",
                    "acc": search_type,
                    "acc_like": f"%{search_type}%",
                }
                org_clause = ""
                if organization_id:
                    org_clause = "AND v.organization_id = :org"
                    params["org"] = str(organization_id)
                vendor_row = (
                    await session.execute(
                        text(
                            f"""
                            SELECT v.id, v.vendor_name, v.vendor_code,
                                   COALESCE(v.block_state, 'Clear') AS block_state,
                                   v.blocked_accreditation_type, v.status
                            FROM plenum_cafm.vendors v
                            WHERE (
                                   LOWER(TRIM(v.vendor_name)) = LOWER(TRIM(:name))
                                OR v.vendor_name ILIKE :like
                            )
                            {org_clause}
                            ORDER BY
                              CASE WHEN LOWER(TRIM(v.vendor_name)) = LOWER(TRIM(:name))
                                   THEN 0 ELSE 1 END
                            LIMIT 1
                            """
                        ),
                        params,
                    )
                ).mappings().first()
                if vendor_row:
                    cert_row = (
                        await session.execute(
                            text(
                                """
                                SELECT cc.id, cc.certificate_type_code, cc.certificate_number,
                                       cc.expiry_date, cc.status, cc.days_to_expiry,
                                       cc.inspector_accreditation_number
                                FROM plenum_cafm.compliance_certificates cc
                                WHERE cc.vendor_id = :vid
                                  AND cc.cert_scope = 'Vendor'
                                  AND (
                                        cc.certificate_type_code ILIKE :acc
                                     OR cc.cert_type ILIKE :acc
                                     OR CAST(cc.raw_metadata AS text) ILIKE :acc_like
                                  )
                                ORDER BY
                                  CASE WHEN cc.status = 'Lapsed' THEN 1 ELSE 0 END,
                                  cc.expiry_date DESC NULLS LAST
                                LIMIT 1
                                """
                            ),
                            {
                                "vid": str(vendor_row["id"]),
                                "acc": search_type,
                                "acc_like": f"%{search_type}%",
                            },
                        )
                    ).mappings().first()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "verification.vendor_registration_search_failed",
                error=str(exc)[:200],
                vendor=name[:80],
            )

    block_state = (vendor_row or {}).get("block_state") if vendor_row else None
    vendor_found = vendor_row is not None
    has_cert = cert_row is not None
    cert_status = (cert_row or {}).get("status") if cert_row else None
    days = (cert_row or {}).get("days_to_expiry") if cert_row else None

    if not name:
        platform_compliance = "vendor_name_missing"
        is_compliant = None
    elif not vendor_found:
        platform_compliance = "unknown_vendor"
        is_compliant = False
    elif str(block_state or "").lower() == "blocked":
        platform_compliance = "blocked"
        is_compliant = False
    elif not has_cert:
        platform_compliance = "no_accreditation_on_record"
        is_compliant = False
    elif str(cert_status or "").lower() == "lapsed" or (
        isinstance(days, int) and days <= 0
    ):
        platform_compliance = "lapsed"
        is_compliant = False
    else:
        platform_compliance = "compliant"
        is_compliant = True

    register_name = pack.issuing_body or verify.get("issuing_body")
    soft = soft_authenticity_warning(
        certificate_type_code=certificate_type_code,
        inspector_accreditation_number=accreditation_number,
        register_name=register_name if pack.verification_url else None,
    )

    display_name = (vendor_row or {}).get("vendor_name") or name
    if platform_compliance == "compliant":
        status_msg = (
            f"Vendor [{display_name}] is registered on-platform and currently "
            f"compliant for {search_type}"
            + (
                f" (status {cert_status}, days_to_expiry={days})."
                if cert_status or days is not None
                else "."
            )
        )
    elif platform_compliance == "blocked":
        status_msg = (
            f"Vendor [{display_name}] is BLOCKED on-platform — "
            f"not compliant for assignment."
        )
    elif platform_compliance == "lapsed":
        status_msg = (
            f"Vendor [{display_name}] has a LAPSED {search_type} accreditation "
            f"on-platform — not currently compliant."
        )
    elif platform_compliance == "no_accreditation_on_record":
        status_msg = (
            f"Vendor [{display_name}] is registered on-platform but has no current "
            f"{search_type} accreditation on record."
        )
    elif platform_compliance == "unknown_vendor":
        status_msg = (
            f"Vendor [{name}] was not found on-platform — treat as not yet registered; "
            f"confirm against [{register_name or 'the public register'}] via Verify now."
        )
    else:
        status_msg = (
            "Vendor / company name missing from extract — cannot confirm "
            "on-platform registration or compliance."
        )

    verify_url = verify.get("verification_url")
    authenticity_parts = [status_msg]
    if soft:
        authenticity_parts.append(soft)
    if verify_url:
        authenticity_parts.append(f"Verify now: {verify_url}")
    authenticity_parts.append("PM retains override authority (soft authenticity gate).")

    return {
        "ok": True,
        "vendor_found": vendor_found,
        "vendor_id": str(vendor_row["id"]) if vendor_row else None,
        "vendor_name": display_name or None,
        "block_state": block_state,
        "platform_compliance": platform_compliance,
        "is_compliant": is_compliant,
        "accreditation_type_checked": search_type,
        "accreditation_number": accreditation_number,
        "on_record_certificate": (
            {
                "id": str(cert_row["id"]),
                "certificate_type_code": cert_row.get("certificate_type_code"),
                "certificate_number": cert_row.get("certificate_number"),
                "status": cert_row.get("status"),
                "expiry_date": str(cert_row["expiry_date"])
                if cert_row.get("expiry_date")
                else None,
                "days_to_expiry": cert_row.get("days_to_expiry"),
            }
            if cert_row
            else None
        ),
        "issuing_body": register_name,
        "verification_url": verify_url,
        "verify_mode": verify.get("mode"),
        "certificate_type_code": certificate_type_code,
        "cert_scope": cert_scope,
        "status_message": status_msg,
        "authenticity_warning": " ".join(authenticity_parts),
        "requires_pm_review": not bool(is_compliant),
        "mode": "platform_search_plus_register_navigation",
        "message": (
            "Platform searched internal vendor/accreditation records. "
            "Live register confirmation is via Verify-now navigation only (no scrape)."
        ),
    }
