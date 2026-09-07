"""CCC §8.4 — public/government API verification (EPC/DEC/TM44, FCA, Companies House).

- EPC/DEC/TM44: no key — public GOV.UK "Find an energy certificate" reference-number lookup.
- FCA (insurance) + Companies House (ESOS): live APIs; when their keys are unset the check
  returns needs_config so the caller falls back to website Verify-now.
"""
from __future__ import annotations

import difflib
import re
from typing import Any
from urllib.parse import quote

import httpx

from ....config import settings
from ....core.logging import get_logger

log = get_logger(__name__)

# GOV.UK "Find an energy certificate" — public, no-auth citizen service. We verify an
# EPC/DEC by its certificate reference number (RRN) against the public search-by-reference
# pages (no API key or token needed). A real certificate lands on a certificate detail page
# with a "Valid until" date + rating; a non-existent RRN renders an empty shell / error.
# Two separate registers — DOMESTIC and NON-DOMESTIC (DEC / non-domestic EPC / air-con) —
# so we try both and accept the first that resolves to a real certificate.
_EPC_UA = "Mozilla/5.0 (PlenumComplianceEPCVerifier)"
_EPC_HOST = "https://find-energy-certificate.service.gov.uk"
_EPC_REF_SEARCHES: list[tuple[str, str]] = [
    ("domestic", f"{_EPC_HOST}/find-a-certificate/search-by-reference-number"),
    ("non_domestic", f"{_EPC_HOST}/find-a-non-domestic-certificate/search-by-reference-number"),
]


def _epc_field(html: str, pattern: str) -> str | None:
    m = re.search(pattern, html, re.I | re.S)
    return m.group(1).strip() if m else None


async def _epc_ref_lookup(
    client: httpx.AsyncClient, search_url: str, rrn: str
) -> dict[str, Any]:
    """Look an RRN up against one register's reference-number search. Returns
    ``{found, register, ...evidence}``. Never raises — caller aggregates across registers.
    """
    resp = await client.get(f"{search_url}?reference_number={quote(rrn)}")
    html = resp.text or ""
    final = str(resp.url)
    # A real certificate redirects to a detail page (…/energy-certificate/… or
    # …/display-energy-certificate/…); the search form re-renders on error/not-found.
    on_cert = "certificate/" in final and "search-by" not in final
    valid_until = _epc_field(
        html, r"[Vv]alid until[\s\S]{0,160}?([0-9]{1,2}\s+[A-Za-z]+\s+\d{4})"
    )
    rating = _epc_field(
        html, r"(?:current energy rating|energy rating)[\s\S]{0,200}?>\s*([A-G])\s*<"
    )
    kind = _epc_field(html, r"<title>\s*([^<]+?)\s*(?:[–-]\s*Find|</title>)")
    # A genuine certificate resolves to a detail page (…/energy-certificate/…) carrying a
    # "Valid until" date, with the searched RRN reflected in the final URL. Air-conditioning
    # inspection certificates (TM44) resolve exactly this way but do NOT carry an A–G energy
    # rating or a "Certificate number"-labelled echo — so requiring those (as before) wrongly
    # rejected every TM44. The RRN-in-final-URL guard rejects the empty-shell error pages that
    # a non-existent RRN (e.g. 0000-0000-0000-0000-0000) redirects to.
    rrn_resolved = rrn.replace(" ", "") in final.replace(" ", "")
    found = bool(on_cert and valid_until and rrn_resolved)
    return {
        "found": found,
        "certificate_kind": kind,
        "current_energy_rating": rating,
        "valid_until": valid_until,
        "certificate_number": rrn,
        "source_url": final if on_cert else None,
        "status_code": resp.status_code,
    }


async def run_public_api_check(
    *,
    certificate_type_code: str,
    certificate_number: str | None = None,
    company_number: str | None = None,
    postcode: str | None = None,
    insurer_name: str | None = None,
    vendor_name: str | None = None,
) -> dict[str, Any]:
    code = (certificate_type_code or "").upper()

    if code in ("EPC", "TM44", "DEC"):
        return await _govuk_energy(
            code=code,
            certificate_number=certificate_number,
            postcode=postcode,
        )
    if code in (
        "EL_INSURANCE",
        "CONTRACTOR_EL",
        "CONTRACTOR_PL",
        "CONTRACTOR_EL_INSURANCE",
        "CONTRACTOR_PL_INSURANCE",
    ):
        return await _fca_register(insurer_name=insurer_name or certificate_number)
    if code == "ESOS":
        return await _companies_house(company_number=company_number or certificate_number)
    # US vendor entity verification via the federal SAM.gov register. There is no national
    # US API for insurance carriers (NAIC is web-only), so for a US COI / workers-comp
    # certificate we verify the INSURED CONTRACTOR entity is SAM-registered and not excluded.
    if code in ("US_GL_INSURANCE", "US_WORKERS_COMP", "US_LEAD_RRP"):
        # The entity to verify is the INSURED CONTRACTOR (vendor), not the carrier.
        return await _sam_gov_entity(
            vendor_name=vendor_name or insurer_name,
            certificate_number=certificate_number,
        )

    return {
        "ok": False,
        "verified": None,
        "needs_config": False,
        "channel": "public_api",
        "evidence": {"error": f"no_public_api_handler_for_{code}"},
    }


async def _govuk_energy(
    *,
    code: str,
    certificate_number: str | None,
    postcode: str | None,
) -> dict[str, Any]:
    """GOV.UK Find an energy certificate — PUBLIC reference-number lookup (no key/token).

    Verifies an EPC/DEC by its certificate reference number (RRN) against the public,
    no-auth citizen service. A real certificate lands on ``/energy-certificate/{RRN}`` with
    a "Valid until" date + energy rating; a non-existent RRN renders an empty shell / error.
    """
    rrn = (certificate_number or "").strip()
    if not rrn:
        return {
            "ok": True,
            "verified": None,
            "needs_config": False,
            "channel": "public_api",
            "evidence": {
                "register": "GOV.UK Find an energy certificate",
                "message": (
                    "certificate_number (RRN, e.g. 0568-5012-5301-1519-8254) required "
                    "for EPC/DEC lookup"
                ),
            },
        }
    # DEC-type certs are non-domestic; a plain EPC may be domestic OR non-domestic. Try both
    # registers and accept the first that resolves the RRN to a real certificate.
    order = list(_EPC_REF_SEARCHES)
    if code in ("DEC", "TM44"):  # non-domestic first for these types
        order = list(reversed(order))
    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, headers={"User-Agent": _EPC_UA}
        ) as client:
            last: dict[str, Any] = {}
            for register, search_url in order:
                res = await _epc_ref_lookup(client, search_url, rrn)
                last = {**res, "register": register}
                if res["found"]:
                    return {
                        "ok": True,
                        "verified": True,
                        "needs_config": False,
                        "channel": "public_api",
                        "evidence": {
                            "register": f"GOV.UK Find an energy certificate ({register})",
                            "match_mode": "reference_number",
                            "certificate_kind": res.get("certificate_kind"),
                            "certificate_number": res["certificate_number"],
                            "current_energy_rating": res["current_energy_rating"],
                            "valid_until": res["valid_until"],
                            "source_url": res["source_url"],
                            "status_code": res["status_code"],
                        },
                    }
            # Neither register resolved the RRN → not found.
            return {
                "ok": True,
                "verified": False,
                "needs_config": False,
                "channel": "public_api",
                "evidence": {
                    "register": "GOV.UK Find an energy certificate",
                    "match_mode": "reference_number",
                    "certificate_number": rrn,
                    "registers_tried": [r for r, _ in order],
                    "status_code": last.get("status_code"),
                    "message": "No certificate found for this reference number",
                },
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("govuk_epc.public_lookup_failed", error=str(exc)[:200])
        return {
            "ok": False,
            "verified": None,
            "needs_config": False,
            "channel": "public_api",
            "evidence": {
                "register": "GOV.UK Find an energy certificate",
                "error": str(exc)[:200],
            },
        }


def _norm_firm(s: str | None) -> str:
    """Normalise an FCA firm name for comparison: drop the ``(Postcode: …)`` suffix
    the Register API appends, strip punctuation, collapse whitespace, lower-case."""
    s = (s or "").lower()
    s = re.sub(r"\(postcode:[^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _fca_authorised(status: str | None) -> bool:
    """True only for a live authorisation. ``No longer authorised`` / ``Not authorised``
    must NOT count as verified for a compliance check."""
    s = (status or "").strip().lower()
    if "no longer" in s or "not authorised" in s or "cancelled" in s:
        return False
    return "authorised" in s or "registered" in s


_FCA_NAME_MATCH_MIN = 0.60


async def _fca_register(*, insurer_name: str | None) -> dict[str, Any]:
    """FCA Register search — free API key + email."""
    api_key = (settings.fca_api_key or "").strip()
    api_email = (settings.fca_api_email or "").strip()
    if not api_key:
        return {
            "ok": True,
            "verified": None,
            "needs_config": True,
            "channel": "public_api",
            "evidence": {
                "register": "FCA",
                "message": "Set FCA_API_KEY to enable live insurer authorisation checks",
                "config_hint": "Missing env: FCA_API_KEY"
                + ("" if api_email else ", FCA_API_EMAIL (recommended)"),
                "query": insurer_name,
            },
        }
    if not insurer_name:
        return {
            "ok": True,
            "verified": None,
            "needs_config": False,
            "channel": "public_api",
            "evidence": {
                "message": "insurer_name or certificate_number required",
                "register": "FCA",
            },
        }
    url = "https://register.fca.org.uk/services/V0.1/Search"
    headers = {"X-Auth-Key": api_key}
    if api_email:
        headers["X-Auth-Email"] = api_email
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                url,
                params={"q": insurer_name, "type": "firm"},
                headers=headers,
            )
            if resp.status_code >= 400:
                return {
                    "ok": True,
                    "verified": False,
                    "needs_config": False,
                    "channel": "public_api",
                    "evidence": {
                        "status_code": resp.status_code,
                        "register": "FCA",
                        "detail": resp.text[:300],
                    },
                }
            data = resp.json() if resp.content else {}
            hits = data.get("Data") or data.get("data") or []
            if not isinstance(hits, list):
                hits = []
            hits = [h for h in hits if isinstance(h, dict)]

            # Rank hits by name similarity to the queried firm, then prefer an
            # authorised match on ties. The FCA `q` search returns many related
            # entities (branches, historical, cross-border) — blindly taking the
            # first hit both mis-identifies the firm and (worse) marks a de-authorised
            # firm as verified. Select the closest name and report its true status.
            query_norm = _norm_firm(insurer_name)
            scored: list[tuple[float, bool, dict[str, Any]]] = []
            for h in hits:
                name = h.get("Name") or h.get("name") or ""
                st = h.get("Status") or h.get("status") or h.get("Organisation Status")
                ratio = difflib.SequenceMatcher(None, query_norm, _norm_firm(name)).ratio()
                scored.append((ratio, _fca_authorised(st), h))
            scored.sort(key=lambda t: (t[0], t[1]), reverse=True)

            if scored:
                name_ratio, authorised, best = scored[0]
            else:
                name_ratio, authorised, best = 0.0, False, {}
            best_status = (
                best.get("Status")
                or best.get("status")
                or best.get("Organisation Status")
            )
            name_ok = name_ratio >= _FCA_NAME_MATCH_MIN
            verified = bool(hits) and authorised and name_ok
            return {
                "ok": True,
                "verified": verified,
                "needs_config": False,
                "channel": "public_api",
                "evidence": {
                    "register": "FCA",
                    "hit_count": len(hits),
                    "query": insurer_name,
                    "firm_name": best.get("Name") or best.get("name"),
                    "firm_status": best_status,
                    "authorised": authorised,
                    "name_match_score": round(name_ratio, 3),
                    "frn": best.get("Reference Number") or best.get("FRN"),
                    "reason": (
                        None
                        if verified
                        else (
                            "no_match" if not hits
                            else "name_below_threshold" if not name_ok
                            else "firm_not_authorised"
                        )
                    ),
                },
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("fca.check_failed", error=str(exc)[:200])
        return {
            "ok": False,
            "verified": None,
            "needs_config": True,
            "channel": "public_api",
            "evidence": {
                "error": str(exc)[:200],
                "config_hint": "FCA API call failed — check network and FCA_API_KEY / FCA_API_EMAIL",
            },
        }


async def _companies_house(*, company_number: str | None) -> dict[str, Any]:
    api_key = (settings.companies_house_api_key or "").strip()
    if not api_key:
        return {
            "ok": True,
            "verified": None,
            "needs_config": True,
            "channel": "public_api",
            "evidence": {
                "register": "Companies House",
                "message": "Set COMPANIES_HOUSE_API_KEY to enable live company checks",
                "config_hint": "Missing env: COMPANIES_HOUSE_API_KEY",
                "company_number": company_number,
            },
        }
    if not company_number:
        return {
            "ok": True,
            "verified": None,
            "needs_config": False,
            "channel": "public_api",
            "evidence": {
                "message": "company_number required",
                "register": "Companies House",
            },
        }
    url = (
        "https://api.company-information.service.gov.uk/company/"
        f"{company_number.strip()}"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, auth=(api_key, ""))
            if resp.status_code == 200:
                body = resp.json()
                status = (body.get("company_status") or "").lower()
                return {
                    "ok": True,
                    "verified": status in ("active", "open"),
                    "needs_config": False,
                    "channel": "public_api",
                    "evidence": {
                        "register": "Companies House",
                        "company_name": body.get("company_name"),
                        "company_status": body.get("company_status"),
                        "company_number": body.get("company_number") or company_number,
                    },
                }
            return {
                "ok": True,
                "verified": False,
                "needs_config": False,
                "channel": "public_api",
                "evidence": {
                    "status_code": resp.status_code,
                    "register": "Companies House",
                    "company_number": company_number,
                },
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("companies_house.check_failed", error=str(exc)[:200])
        return {
            "ok": False,
            "verified": None,
            "needs_config": True,
            "channel": "public_api",
            "evidence": {
                "error": str(exc)[:200],
                "config_hint": "Companies House API call failed — check COMPANIES_HOUSE_API_KEY",
            },
        }


async def _sam_gov_entity(*, vendor_name: str | None, certificate_number: str | None) -> dict[str, Any]:
    """SAM.gov Entity Management API - official US federal entity register.

    Verifies that the firm named on a US vendor certificate is a registered US entity and
    is not excluded/debarred. This is the closest US federal equivalent to the UK Companies
    House / FCA authorisation check; there is no national US API for insurance carriers
    (NAIC is web-only) or for state contractor licences.

    Free API key: https://sam.gov -> Account Details -> Request Public API Key.
    """
    api_key = (settings.sam_gov_api_key or "").strip()
    if not api_key:
        return {
            "ok": True,
            "verified": None,
            "needs_config": True,
            "channel": "public_api",
            "evidence": {
                "register": "SAM.gov",
                "message": "Set SAM_GOV_API_KEY to enable live US entity verification",
                "config_hint": "Missing env: SAM_GOV_API_KEY",
                "query": vendor_name or certificate_number,
            },
        }
    query = (vendor_name or "").strip()
    if not query:
        return {
            "ok": True,
            "verified": None,
            "needs_config": False,
            "channel": "public_api",
            "evidence": {"register": "SAM.gov", "message": "vendor_name required"},
        }
    url = "https://api.sam.gov/entity-information/v3/entities"
    params = {
        "api_key": api_key,
        "legalBusinessName": query,
        "includeSections": "entityRegistration,coreData",
        "registrationStatus": "A",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
        # SAM.gov's gateway answers an invalid/unauthorised key with 404 (verified against the
        # live endpoint), so treat 404 as a key problem rather than "entity not found".
        if resp.status_code in (401, 403, 404):
            return {
                "ok": True,
                "verified": None,
                "needs_config": True,
                "channel": "public_api",
                "evidence": {
                    "register": "SAM.gov",
                    "message": f"SAM.gov rejected the request (HTTP {resp.status_code})",
                    "config_hint": (
                        "Check SAM_GOV_API_KEY is a valid SAM.gov Public API key "
                        "(sam.gov -> Account Details -> Request Public API Key)"
                    ),
                    "endpoint": url,
                    "query": query,
                },
            }
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - never crash the verify path
        return {
            "ok": False,
            "verified": None,
            "needs_config": False,
            "channel": "public_api",
            "evidence": {"register": "SAM.gov", "error": str(exc)[:200], "query": query},
        }

    entities = data.get("entityData") or []
    total = data.get("totalRecords")
    if not entities:
        return {
            "ok": True,
            "verified": False,
            "needs_config": False,
            "channel": "public_api",
            "evidence": {
                "register": "SAM.gov",
                "message": "No active SAM.gov registration found for this entity",
                "query": query,
                "total_records": total,
            },
        }
    reg = (entities[0] or {}).get("entityRegistration") or {}
    status = str(reg.get("registrationStatus") or "").upper()
    exclusion = str(reg.get("exclusionStatusFlag") or "").upper()
    # Active registration and NOT excluded/debarred.
    verified = status == "A" and exclusion != "Y"
    return {
        "ok": True,
        "verified": verified,
        "needs_config": False,
        "channel": "public_api",
        "evidence": {
            "register": "SAM.gov",
            "message": (
                "Active SAM.gov registration, no exclusion"
                if verified
                else "Entity found but registration inactive or excluded"
            ),
            "legal_business_name": reg.get("legalBusinessName"),
            "uei": reg.get("ueiSAM"),
            "cage_code": reg.get("cageCode"),
            "registration_status": status,
            "exclusion_flag": exclusion or "N",
            "expiration_date": reg.get("registrationExpirationDate"),
            "query": query,
            "total_records": total,
        },
    }
