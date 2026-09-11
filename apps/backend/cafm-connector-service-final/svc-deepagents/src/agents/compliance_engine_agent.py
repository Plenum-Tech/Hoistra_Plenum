"""
Compliance Engine agent tools — wraps svc-operations-intelligence (port 8009).

PRD tool agent: compliance-engine → Orchestrator agent id `compliance` (Feature A).
Intent keywords (SSOT: phase2_intents.COMPLIANCE_INTENT_KEYWORDS):
  certificate, expiry, cert due, inspection, LOLER, EICR, gas safety, compliance,
  accreditation, Gas Safe, NICEIC, lapsed, renewal, statutory, fire risk,
  vendor cert, building cert.

Phase 2 Feature A (A1–A5):
  - run_compliance_scan
  - list_building_certificates / list_vendor_accreditations
  - upsert_compliance_certificate
  - set_remedial_status
  - get_compliance_saved_space_summary
  - list_country_pack / seed_uk_country_pack
  - verify_accreditation_now
  - run_document_forensics (authenticity gate on upload)
  - list_compliance_approvals / decide_compliance_approval
"""
from __future__ import annotations

import difflib
import re
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request
from .document_forensics import run_document_forensics

log = structlog.get_logger(__name__)

_TIMEOUT = 60.0
_SERVICE = "operations_intelligence"


def _base() -> str:
    return settings.operations_intelligence_base_url.rstrip("/")


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text or ""
        log.error(
            "compliance_engine.http_error",
            operation=op,
            status_code=exc.response.status_code,
            body=body[:2000],
        )
        return {"error": body[:2000], "status_code": exc.response.status_code}
    log.error("compliance_engine.error", operation=op, error=str(exc), exc_info=True)
    return {"error": str(exc)[:2000]}


# ---------------------------------------------------------------------------------------------
# Tolerant matching of a user-typed name against register values.
#
# The data service filters by exact substring of the whole phrase, which misses "Limited" vs
# "Ltd", "C & H" vs "C&H", "Kurt Lesker" vs "Kurt J. Lesker", "building5" vs "Building 5",
# "fire safety" vs "Fire", and any typo. So when a name is given the tools fetch the whole
# scope and match here. Python fetches and matches; the model judges what the match means.
# ---------------------------------------------------------------------------------------------

# Words that say what kind of company it is, not which company.
_LEGAL_FORM_WORDS = {
    "ltd", "limited", "llc", "inc", "incorporated", "plc", "co", "corp", "corporation",
    "company", "the", "and", "holdings", "group", "subsidiary", "companies",
}
# Words people add around a building name that are not the name.
_BUILDING_FILLER_WORDS = {"the", "building", "bldg", "block", "site", "property", "at", "in", "for"}
# What a user may call a trade, mapped onto the register's trade_category vocabulary.
_TRADE_ALIASES = {
    "fire": "fire", "firesafety": "fire", "alarm": "fire", "sprinkler": "fire",
    "electric": "electrical", "electrical": "electrical", "electricity": "electrical", "eicr": "electrical",
    "gas": "gas", "boiler": "gas", "heating": "gas",
    "lift": "loler", "lifts": "loler", "lifting": "loler", "loler": "loler", "hoist": "loler",
    "hvac": "hvac", "aircon": "hvac", "airconditioning": "hvac", "refrigeration": "hvac", "fgas": "hvac", "cooling": "hvac",
    "asbestos": "asbestos",
    "energy": "energy", "epc": "energy", "dec": "energy", "tm44": "energy",
    "insurance": "insurance", "liability": "insurance", "insured": "insurance",
    "safety": "h&s", "healthandsafety": "h&s", "hs": "h&s", "h&s": "h&s", "hse": "h&s",
    "water": "water", "legionella": "water",
    "general": "general", "quality": "general", "iso": "general", "environmental": "general",
}


def _tokens(text: str, drop: set[str] = frozenset()) -> list[str]:
    """Lower-case words, with runs of initials joined: "R.D.M. Electrical" -> rdm, electrical;
    "C & H" -> ch. So initials written with dots, spaces or an ampersand match a register name
    that writes them together."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    words = [t for t in cleaned.split() if t and t not in drop]
    out: list[str] = []
    run: list[str] = []
    for w in words + [""]:
        if len(w) == 1 and w.isalpha():
            run.append(w)
            continue
        if run:
            out.append("".join(run))
            run = []
        if w:
            out.append(w)
    return out


def _word_matches(qw: str, cw: str) -> bool:
    """A query word matches a candidate word it starts, is inside, or is a near-misspelling of.

    "electric" ~ "electrical", "electrcal" ~ "electrical" (a letter missing), "solutons" ~
    "solutions". Words shorter than four letters must match exactly, so "aib" cannot drift.
    """
    if qw == cw:
        return True
    if len(qw) < 4 or qw.isdigit() or len(cw) < 4 or cw.isdigit():
        return False
    if cw.startswith(qw) or qw in cw or cw in qw:
        return True
    return difflib.SequenceMatcher(None, qw, cw).ratio() >= 0.8


def _tokens_match(q: list[str], c: list[str], joined_ratio: float = 0.85) -> bool:
    if not q:
        return False
    # A number is never "nearly" another number: "Building 9" must not find "Building 5".
    if any(t.isdigit() and t not in c for t in q):
        return False
    if all(any(_word_matches(qw, cw) for cw in c) for qw in q):
        return True
    jq, jc = "".join(q), "".join(c)  # "Bright Spark" ~ "BrightSpark", "building5" ~ "Building 5"
    if len(jq) < 4 or not jc:  # "ch" or "5" alone would be inside half the register
        return False
    return jq in jc or difflib.SequenceMatcher(None, jq, jc).ratio() >= joined_ratio


def _name_matches(query: str, candidate: str) -> bool:
    """Company name: case, punctuation, spacing and legal suffixes ignored; typos tolerated."""
    q = _tokens(query, _LEGAL_FORM_WORDS)
    if not q:  # someone asked for "Ltd"
        return (query or "").strip().lower() in (candidate or "").lower()
    return _tokens_match(q, _tokens(candidate, _LEGAL_FORM_WORDS))


def _building_matches(query: str, candidate: str) -> bool:
    """Building name: "building 5", "Bldg5", "bishopsgate", "the town hall" all find their row."""
    q = _tokens(query, _BUILDING_FILLER_WORDS)
    c = _tokens(candidate, _BUILDING_FILLER_WORDS)
    if q and _tokens_match(q, c):
        return True
    # With filler kept, so "building5" (one token) meets "Building 5" joined as "building5".
    return _tokens_match(_tokens(query), _tokens(candidate))


def _trade_matches(query: str, candidate: str) -> bool:
    """Trade category: "fire safety", "electric", "lifts", "air conditioning" find Fire, Electrical, LOLER, HVAC."""
    cand = (candidate or "").strip().lower()
    if not cand:
        return False
    raw = re.sub(r"[^a-z0-9&]+", "", (query or "").lower())
    whole = _TRADE_ALIASES.get(raw) or _TRADE_ALIASES.get(raw.replace("&", ""))
    if whole:
        return whole == cand or whole in cand or cand in whole
    for qw in _tokens(query):
        alias = _TRADE_ALIASES.get(qw)
        if alias and (alias == cand or alias in cand or cand in alias):
            return True
        if len(qw) >= 3 and (cand.startswith(qw) or qw.startswith(cand) or _word_matches(qw, cand)):
            return True
    return False


_FIELD_MATCHERS = {
    "vendor_name": _name_matches,
    "building_name": _building_matches,
    "trade_category": _trade_matches,
}


def _filter_by_field(payload: dict[str, Any], field: str, query: str) -> dict[str, Any]:
    """Keep the rows whose `field` matches `query` tolerantly, and say what matched.

    `<field>_match` tells the model which register values were matched so it can report them,
    or notice that several did. When NOTHING matches, every row is returned together with the
    register's distinct values for that field and `no_match: true`, so the model can decide
    whether one of them is what the user meant, or answer that it is not on record.
    """
    if not isinstance(payload, dict):
        return payload
    rows = payload.get("certificates")
    if not isinstance(rows, list):
        return payload
    matcher = _FIELD_MATCHERS[field]
    kept = [r for r in rows if isinstance(r, dict) and matcher(query, str(r.get(field) or ""))]
    all_values = sorted({str(r.get(field)) for r in rows if isinstance(r, dict) and r.get(field)})
    out = dict(payload)
    match: dict[str, Any] = {
        "query": query,
        "matched_values": sorted({str(r.get(field)) for r in kept}),
        "rows": len(kept),
        "how": ("case-insensitive; punctuation, spacing and filler words (Ltd, Limited, LLC, Inc, plc, "
                "'building', 'the') ignored; one-letter typos and missing letters tolerated; trade words "
                "mapped onto the register's categories (fire safety->Fire, lifts->LOLER, air conditioning->HVAC)"),
    }
    if kept:
        out["certificates"] = kept
        if "count" in out:
            out["count"] = len(kept)
    else:
        match["no_match"] = True
        match["all_values"] = all_values
        match["note"] = (f"No {field} matched the query. ALL rows of this scope are returned unfiltered; decide "
                         f"yourself whether one of all_values is what the user meant (then use only its rows and "
                         f"say which value you took), or answer that it is not on record and offer the closest values.")
    out[f"{field}_match"] = match
    return out


def _apply_tolerant_filters(data: dict[str, Any], **queries: str | None) -> dict[str, Any]:
    for field, query in queries.items():
        if query:
            data = _filter_by_field(data, field, query)
    return data


def _email_drafts_for_chat(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten Approvals items into chat-ready email draft cards."""
    out: list[dict[str, Any]] = []
    for item in items:
        draft = item.get("email_draft") or {}
        if not draft.get("subject") and not draft.get("body"):
            continue
        out.append(
            {
                "queue_item_id": item.get("id"),
                "item_type": item.get("item_type"),
                "severity": item.get("severity"),
                "summary": item.get("summary"),
                "to": draft.get("to"),
                "cc": draft.get("cc_senior") or draft.get("cc"),
                "subject": draft.get("subject"),
                "body": draft.get("body"),
                "escalation": bool(draft.get("escalation")),
                "status": draft.get("status") or (item.get("payload") or {}).get("status"),
                "one_click_url": draft.get("one_click_url"),
                "mailto_uri": draft.get("mailto_uri"),
            }
        )
    return out


def _chat_markdown_for_drafts(drafts: list[dict[str, Any]]) -> str:
    if not drafts:
        return ""
    parts = ["### Compliance alert email drafts\n"]
    for i, d in enumerate(drafts, 1):
        esc = " · **ESCALATION**" if d.get("escalation") else ""
        parts.append(f"**{i}. {d.get('summary') or d.get('subject')}{esc}**\n")
        parts.append(f"- **To:** `{d.get('to') or '—'}`")
        if d.get("cc"):
            parts.append(f"- **Cc:** `{d.get('cc')}`")
        parts.append(f"- **Subject:** {d.get('subject') or '—'}\n")
        parts.append("**Email body:**\n```")
        parts.append((d.get("body") or "").rstrip())
        parts.append("```\n")
    parts.append(
        "_Open each draft from the notification Bell or Approvals rail to send via "
        "your mail client (PM handoff)._"
    )
    return "\n".join(parts)


@tool
async def run_compliance_scan(
    scope: str = "all",
    site_id: str | None = None,
    certificate_type_code: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """Run the Compliance Engine scan (A1–A3).

    Computes certificate lifecycle status, building alert ladder, vendor risk /
    block_state, and enqueues Approvals items with email drafts for the PM.
    Returns email_drafts + chat_markdown so the center chat can show Subject/Body.
    scope: all | building | vendor. Does NOT create work orders.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/compliance/scan",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "scope": scope,
                "site_id": site_id,
                "certificate_type_code": certificate_type_code,
                "organization_id": organization_id,
            },
        )
        result = resp.json()
        # Pull freshly enqueued drafts for center-chat display
        params: dict[str, Any] = {"status": "pending", "source_feature": "A"}
        if organization_id:
            params["organization_id"] = organization_id
        appr = await _request(
            "GET",
            _base(),
            "/api/compliance/approvals",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        items = (appr.json() or {}).get("items") or []
        drafts = _email_drafts_for_chat(items)
        result["email_drafts"] = drafts
        result["chat_markdown"] = _chat_markdown_for_drafts(drafts)
        result["message"] = (
            f"Scan complete. {len(drafts)} email draft(s) ready for PM "
            f"(escalations use senior PM when configured)."
        )
        return result
    except Exception as exc:
        return _err(exc, "run_compliance_scan")


@tool
async def list_building_certificates(
    status: str | None = None,
    site_id: str | None = None,
    asset_id: str | None = None,
    draft: bool | None = None,
    trade_category: str | None = None,
    expiring_within_days: int | None = None,
    expiry_month: str | None = None,
    vendor_name: str | None = None,
    building_name: str | None = None,
    limit: int = 100,
) -> dict:
    """List building-scope compliance certificates (portfolio — same as /compliance).

    status: Lapsed / expired / Current / Expiring Soon / Due for Renewal / Overdue / Critical.
    "expired" == "lapsed". Do NOT pass status='compliant', 'at_risk', 'Blocked', or 'draft'.
    Pass draft=true only for unconfirmed extracts.

    Filters for natural-language queries:
    - `trade_category` — "building certs by trade" / "electrical certs" / "fire safety
      certificates". Pass the trade as the user said it; matching maps everyday words onto
      the register's categories (fire safety → Fire, electric → Electrical, lifts → LOLER,
      air conditioning → HVAC, insurance, energy, gas, asbestos) and tolerates typos.
    - `building_name` — "certificates for Building 5" / "what does Bishopsgate hold". Pass
      the name as the user wrote it; "building5", "bldg 5", "bishopsgate", "the town hall"
      all find their building. Prefer this over `site_id` when the user names a building.
    - `expiring_within_days` — "which certs expire in the next 30/60/90 days" → 30/60/90.
    - `expiry_month` — "certs that expire this month" → pass the literal token
      `"this_month"` (or `"next_month"`); the server resolves it to the real calendar month.
      Do NOT compute a YYYY-MM yourself — you do not reliably know today's date.
    - `vendor_name` — the contractor named on a building certificate as its issuer. Pass
      the name as the user wrote it; case, punctuation, Ltd/Limited and single-letter typos
      do not matter. A question about a named company needs BOTH this call
      and `list_vendor_accreditations(vendor_name=...)`: the vendor call returns what the
      company HOLDS (its own accreditations), this call returns what the company ISSUED
      (EICRs, gas certificates, FRAs it signed for a building). A firm can appear on a
      building certificate while holding no accreditation of its own — RDM Electrical
      Services Ltd is on Building 5's EICR and nowhere else.

    Every name-filtered result carries `<field>_match` (matched_values, rows, and when nothing
    matched `no_match: true` with ALL rows and `all_values` returned so you can decide).
    """
    try:
        # Names are matched here, tolerantly, not by the service's exact-substring filter —
        # so fetch the whole scope when any name is given.
        named = bool(vendor_name or building_name or trade_category)
        params: dict[str, Any] = {"cert_scope": "Building", "limit": max(limit, 500) if named else limit}
        if status:
            params["status"] = status
        if draft is not None:
            params["draft"] = str(draft).lower()
        if site_id:
            params["site_id"] = site_id
        if asset_id:
            params["asset_id"] = asset_id
        if expiring_within_days is not None:
            params["expiring_within_days"] = expiring_within_days
        if expiry_month:
            params["expiry_month"] = expiry_month
        resp = await _request(
            "GET",
            _base(),
            "/api/compliance/certificates",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return _apply_tolerant_filters(
            resp.json(), vendor_name=vendor_name, building_name=building_name, trade_category=trade_category
        )
    except Exception as exc:
        return _err(exc, "list_building_certificates")


@tool
async def list_vendor_accreditations(
    status: str | None = None,
    vendor_id: str | None = None,
    vendor_name: str | None = None,
    draft: bool | None = None,
    risk_filter: str | None = None,
    trade_category: str | None = None,
    expiring_within_days: int | None = None,
    expiry_month: str | None = None,
    limit: int = 100,
) -> dict:
    """List vendor-scope accreditation certificates (portfolio — same as /compliance).

    Pass draft=true for unconfirmed drafts only. 'Draft' is not a lifecycle status.
    For blocked vendors: risk_filter='blocked' (never status='Blocked'). Also 'high'|'medium'.

    Filters for natural-language queries:
    - `vendor_name` — "show all certs for <company>" / "accreditations for AIB Solutions".
      Pass the company name as the user wrote it. Matching ignores case, punctuation,
      spacing and legal suffixes (Ltd/Limited/LLC/Inc/plc), tolerates a missing or wrong
      letter, and needs only the distinctive words: "rdm electrical", "RDM Electrical
      Services Limited" and "R.D.M. Electrcal" all find "RDM Electrical Services Ltd". Read
      `vendor_name_match.matched_values` to see which companies matched. If `no_match` is
      true, ALL rows came back with `all_values`: pick the company the user meant yourself,
      or say the name is not on record. This returns only what the company HOLDS. For "all
      certificates belonging to / for <company>" also call
      `list_building_certificates(vendor_name=...)` for the building certificates the
      company ISSUED; zero rows here does not mean the company is unknown.
    - `trade_category` — "vendor certs by trade" / "fire contractors" / "electricians".
      Pass the trade as the user said it; everyday words are mapped onto the register's
      categories (fire safety → Fire, electric → Electrical, lifts → LOLER, air conditioning
      → HVAC) and typos are tolerated.
    - `expiring_within_days` — "vendor accreditations expiring in the next 30/60/90 days".
    - `expiry_month` — "vendor certs that expire this month" → the calendar month "YYYY-MM".
    """
    try:
        named = bool(vendor_name or trade_category)
        params: dict[str, Any] = {"cert_scope": "Vendor", "limit": max(limit, 500) if named else limit}
        if status:
            params["status"] = status
        if draft is not None:
            params["draft"] = str(draft).lower()
        if vendor_id:
            params["vendor_id"] = vendor_id
        # vendor_name and trade_category are matched in Python after the fetch.
        if risk_filter:
            params["risk_filter"] = risk_filter
        if expiring_within_days is not None:
            params["expiring_within_days"] = expiring_within_days
        if expiry_month:
            params["expiry_month"] = expiry_month
        resp = await _request(
            "GET",
            _base(),
            "/api/compliance/certificates",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return _apply_tolerant_filters(resp.json(), vendor_name=vendor_name, trade_category=trade_category)
    except Exception as exc:
        return _err(exc, "list_vendor_accreditations")


@tool
async def list_vendors_by_certificate_count(
    min_count: int,
    comparison: str = "gt",
) -> dict:
    """Group vendors by certificate count.

    Use this for "more than N" (comparison="gt"), "at least N" ("gte"), or
    "exactly N" ("eq") vendor-certificate questions. Do not use the flat list tool.
    """
    try:
        resp = await _request(
            "GET",
            _base(),
            "/api/compliance/vendors/certificate-counts",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params={"min_count": min_count, "comparison": comparison},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_vendors_by_certificate_count")


@tool
async def count_compliance_certificates(
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
    risk_filter: str | None = None,
) -> dict:
    """Deterministic count of certificates matching attribute filters (portfolio-wide).

    Use for "how many have …". The `count` field IS the answer — never guess.
    status synonyms: expired == lapsed. Compliant/Blocked are NOT status values.
    Brand synonyms work for cert_type (Gas Safe → GAS_SAFE). issuer_contains for SIA/BAFE.
    """
    try:
        params: dict[str, Any] = {}
        if cert_scope:
            params["cert_scope"] = cert_scope
        if status:
            params["status"] = status
        if cert_type:
            params["cert_type"] = cert_type
        if certificate_number:
            params["certificate_number"] = certificate_number
        if inspector_name:
            params["inspector_name"] = inspector_name
        if result:
            params["result"] = result
        if remedial_status:
            params["remedial_status"] = remedial_status
        if insurance_risk_flag is not None:
            params["insurance_risk_flag"] = str(insurance_risk_flag).lower()
        if country_code:
            params["country_code"] = country_code
        if draft is not None:
            params["draft"] = str(draft).lower()
        if issuer_contains:
            params["issuer_contains"] = issuer_contains
        if vendor_name_contains:
            params["vendor_name_contains"] = vendor_name_contains
        if certificate_name_contains:
            params["certificate_name_contains"] = certificate_name_contains
        if risk_filter:
            params["risk_filter"] = risk_filter
        resp = await _request(
            "GET",
            _base(),
            "/api/compliance/certificates/count",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params or None,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "count_compliance_certificates")


@tool
async def upsert_compliance_certificate(
    cert_scope: str,
    certificate_type_code: str,
    confirmed_by_pm: bool = False,
    certificate_number: str | None = None,
    expiry_date: str | None = None,
    issue_date: str | None = None,
    asset_id: str | None = None,
    site_id: str | None = None,
    vendor_id: str | None = None,
    inspector_name: str | None = None,
    inspector_accreditation_number: str | None = None,
    result: str | None = None,
    defects_found: str | None = None,
    remedial_actions: str | None = None,
    organization_id: str | None = None,
    country_code: str = "UK",
) -> dict:
    """Upsert a building or vendor certificate (A2/A3 ingest).

    confirmed_by_pm=False still persists a draft (appears in lists / Saved Space).
    Set confirmed_by_pm=True after the PM reviews extracted fields to finalise.
    Fail/Advisory opens remedial_status only after confirm (no WO auto-created).
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/compliance/certificates",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "cert_scope": cert_scope,
                "certificate_type_code": certificate_type_code,
                "confirmed_by_pm": confirmed_by_pm,
                "certificate_number": certificate_number,
                "expiry_date": expiry_date,
                "issue_date": issue_date,
                "asset_id": asset_id,
                "site_id": site_id,
                "vendor_id": vendor_id,
                "inspector_name": inspector_name,
                "inspector_accreditation_number": inspector_accreditation_number,
                "result": result,
                "defects_found": defects_found,
                "remedial_actions": remedial_actions,
                "organization_id": organization_id,
                "country_code": country_code,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "upsert_compliance_certificate")


@tool
async def set_remedial_status(certificate_id: str, remedial_status: str) -> dict:
    """Update remedial_status on a certificate: Open | In Progress | Closed."""
    try:
        resp = await _request(
            "PATCH",
            _base(),
            f"/api/compliance/certificates/{certificate_id}/remedial-status",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"remedial_status": remedial_status},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "set_remedial_status")


@tool
async def draft_certificate_renewal(
    certificate_id: str | None = None,
    certificate_number: str | None = None,
) -> dict:
    """Draft a renewal email for ONE selected compliance certificate (pass certificate_id OR
    certificate_number) and queue it for PM approval.

    Building certificates → a booking-request draft to the PM; vendor accreditations → a renewal
    draft to the vendor contact. Returns `email_drafts`, which the center chat renders as a sendable
    email card (Subject/To/Body). Use this when the PM wants to renew, chase, or re-issue a specific
    certificate — e.g. "draft a renewal email for the lapsed BPCA certificate". Nothing is emailed
    until the PM sends it from the card; no work order is created.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/compliance/certificates/renewal-email",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"certificate_id": certificate_id, "certificate_number": certificate_number},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "draft_certificate_renewal")


@tool
async def get_compliance_saved_space_summary() -> dict:
    """Compliance Saved Space summary: Building + Vendor KPIs (portfolio — same as /compliance).

    Returns posture: compliant (= Current), non_compliant (= Lapsed/expired),
    at_risk (= Expiring Soon / Due / Overdue / Critical). These are NOT status filters.
    Call for overall status questions so the UI can render accurate KPI tiles.
    """
    try:
        resp = await _request(
            "GET",
            _base(),
            "/api/compliance/saved-space/summary",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=None,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_compliance_saved_space_summary")


@tool
async def list_country_pack(
    country_code: str = "UK",
    scope: str | None = None,
    trade_category: str | None = None,
) -> dict:
    """List Country Certificate Pack types (A4 taxonomy).

    Source of truth for TAXONOMY questions independent of certificates on file —
    e.g. "is Gas Safe building or vendor?", "which building certificates for Fire?".
    scope: Building | Vendor | omit for all. trade_category: Fire | Gas | Electrical | …
    Zero on-record certificates does NOT mean a type is neither building nor vendor.
    """
    try:
        params: dict[str, Any] = {"country_code": country_code}
        if scope:
            params["scope"] = scope
        if trade_category:
            params["trade_category"] = trade_category
        resp = await _request(
            "GET",
            _base(),
            "/api/compliance/country-pack",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_country_pack")


@tool
async def seed_uk_country_pack(force: bool = False) -> dict:
    """Seed UK Compliance Certification Pack v1.1 into the UDR (A4)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/compliance/country-pack/seed-uk?force={str(force).lower()}",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "seed_uk_country_pack")


@tool
async def list_verification_registers() -> dict:
    """List UK verification registers (Gas Safe, NICEIC, BAFE, BPCA, SIA, …).

    Returns register name, trade, whether accreditation number pre-fills, and URL.
    """
    try:
        resp = await _request(
            "GET",
            _base(),
            "/api/compliance/verification-registers",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_verification_registers")


@tool
async def verify_accreditation_now(
    certificate_type_code: str,
    accreditation_number: str | None = None,
    country_code: str = "UK",
    vendor_name: str | None = None,
) -> dict:
    """A5 — Build a Verify-now navigation URL for the live register (no scraping).

    Prefills accreditation number only when the register supports it
    (e.g. Gas Safe / NICEIC yes; BPCA / SSIP / LEIA no).
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/compliance/verify-now",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "certificate_type_code": certificate_type_code,
                "accreditation_number": accreditation_number,
                "country_code": country_code,
                "vendor_name": vendor_name,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "verify_accreditation_now")


@tool
async def check_vendor_registration_compliance(
    certificate_type_code: str,
    vendor_name: str | None = None,
    accreditation_number: str | None = None,
    cert_scope: str = "Vendor",
    country_code: str = "UK",
    organization_id: str | None = None,
) -> dict:
    """Search whether a vendor is registered on-platform and currently compliant.

    Checks internal vendor + accreditation records (block_state, current vs lapsed)
    and returns the Verify-now public register URL. Does not scrape Gas Safe/NICEIC.
    Soft authenticity gate — PM retains override.
    """
    try:
        body: dict[str, Any] = {
            "certificate_type_code": certificate_type_code,
            "vendor_name": vendor_name,
            "accreditation_number": accreditation_number,
            "cert_scope": cert_scope,
            "country_code": country_code,
        }
        if organization_id:
            body["organization_id"] = organization_id
        resp = await _request(
            "POST",
            _base(),
            "/api/compliance/vendor-registration-check",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json=body,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "check_vendor_registration_compliance")


@tool
async def list_compliance_approvals(
    status: str = "pending",
    organization_id: str | None = None,
) -> dict:
    """List Compliance Approvals & Notifications queue items (Feature A).

    Includes email_drafts and chat_markdown so the center chat can display
    Subject / To / Body for expiry escalations to the PM.
    """
    try:
        params: dict[str, Any] = {"status": status, "source_feature": "A"}
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/compliance/approvals",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        result = resp.json()
        items = result.get("items") or []
        drafts = _email_drafts_for_chat(items)
        result["email_drafts"] = drafts
        result["chat_markdown"] = _chat_markdown_for_drafts(drafts)
        return result
    except Exception as exc:
        return _err(exc, "list_compliance_approvals")


@tool
async def decide_compliance_approval(
    item_id: str,
    decision: str,
    pm_notes: str | None = None,
    prepare_email_handoff: bool = True,
) -> dict:
    """PM decision on a Compliance queue item: approve | edit | dismiss.

    On approve/edit with an email draft, returns a mailto handoff for the PM's
    own email client (PRD Q1). Platform does not send the email directly.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/compliance/approvals/{item_id}/decide",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "decision": decision,
                "pm_notes": pm_notes,
                "prepare_email_handoff": prepare_email_handoff,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "decide_compliance_approval")


@tool
async def flag_building_change(
    site_id: str,
    change_description: str,
    organization_id: str | None = None,
) -> dict:
    """Flag all Building certificates for a site after a material UDR building change.

    Use when the PM logs structural alteration, change of use, fire safety system
    modification, or similar. Opens Approvals review items — does not create a WO.
    """
    try:
        body: dict[str, Any] = {
            "site_id": site_id,
            "change_description": change_description,
        }
        if organization_id:
            body["organization_id"] = organization_id
        resp = await _request(
            "POST",
            _base(),
            "/api/compliance/building-change",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json=body,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "flag_building_change")


@tool
async def get_compliance_coverage(
    scope: str = "both",
    trade_category: str | None = None,
) -> dict:
    """Per-building and/or per-vendor CountryPack coverage %.

    scope: building | vendor | both. Optional trade_category filters vendor pack types
    (e.g. Fire, Pest, Gas). Use for coverage-gap questions.
    """
    try:
        out: dict[str, Any] = {"ok": True}
        scope_l = (scope or "both").lower()
        if scope_l in {"building", "both"}:
            resp = await _request(
                "GET",
                _base(),
                "/api/compliance/coverage/buildings",
                service=_SERVICE,
                timeout=_TIMEOUT,
            )
            out["buildings"] = resp.json()
        if scope_l in {"vendor", "both"}:
            params: dict[str, Any] = {}
            if trade_category:
                params["trade_category"] = trade_category
            resp = await _request(
                "GET",
                _base(),
                "/api/compliance/coverage/vendors",
                service=_SERVICE,
                timeout=_TIMEOUT,
                params=params or None,
            )
            out["vendors"] = resp.json()
        return out
    except Exception as exc:
        return _err(exc, "get_compliance_coverage")


@tool
async def generate_compliance_evidence_pack(
    site_id: str | None = None,
    vendor_id: str | None = None,
) -> dict:
    """Point the PM to the one-click compliance evidence PDF pack (insurers / auditors / BSA)."""
    try:
        summary = await _request(
            "GET",
            _base(),
            "/api/compliance/saved-space/summary",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        cov_b = await _request(
            "GET",
            _base(),
            "/api/compliance/coverage/buildings",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return {
            "ok": True,
            "message": (
                "Download the PDF from /compliance → Evidence pack. "
                "API: GET /api/compliance/evidence-pack"
            ),
            "download_hint": "/compliance",
            "api": "/api/compliance/evidence-pack",
            "summary": summary.json(),
            "building_coverage": cov_b.json(),
            "filters": {"site_id": site_id, "vendor_id": vendor_id},
        }
    except Exception as exc:
        return _err(exc, "generate_compliance_evidence_pack")


@tool
async def get_vendor_passport(vendor_id: str) -> dict:
    """Pre-engagement vendor passport: accreditations, insurance, block posture, coverage."""
    try:
        resp = await _request(
            "GET",
            _base(),
            f"/api/compliance/vendors/{vendor_id}/passport",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_vendor_passport")


@tool
async def share_vendor_passport(
    vendor_id: str,
    ttl_hours: int = 168,
    recipient: str | None = None,
) -> dict:
    """Create a time-limited shareable vendor passport link for tender / site induction."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/compliance/vendors/{vendor_id}/passport/share",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"ttl_hours": ttl_hours, "recipient": recipient},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "share_vendor_passport")


_MEMBERSHIP_STOPWORDS = {
    "the", "a", "an", "report", "certificate", "cert", "certificates", "from", "vector",
    "db", "database", "remove", "removing", "delete", "add", "adding", "keep", "membership",
    "please", "inspection", "of", "for", "want", "would", "like", "to", "my", "this", "that",
    "document", "doc", "pdf", "file",
}
# Common English phrasings → a distinctive token that appears in the cert type code/name.
_MEMBERSHIP_ALIASES = {
    "air conditioning": "tm44",
    "air con": "tm44",
    "aircon": "tm44",
    "display energy": "dec",
    "asbestos": "asbestos",
    "security industry": "sia",
    "approved contractor": "sia",
}


def _match_membership_query(q: str, cert: dict[str, Any]) -> bool:
    """True when the free-text query matches this certificate's type / vendor / filename.

    Empty query matches everything (manage all). Otherwise: whole-phrase match, a known
    alias (e.g. 'air conditioning' → TM44), or every significant query word present.
    """
    meta = cert.get("raw_metadata") or {}
    hay = " ".join(
        str(x or "").lower()
        for x in (
            cert.get("certificate_type_name"),
            cert.get("certificate_type_code"),
            cert.get("vendor_name"),
            meta.get("source_filename"),
            meta.get("company_name"),
        )
    )
    ql = (q or "").strip().lower()
    if not ql:
        return True
    if ql in hay:
        return True
    for phrase, code in _MEMBERSHIP_ALIASES.items():
        if phrase in ql and code in hay:
            return True
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", ql)
        if w not in _MEMBERSHIP_STOPWORDS and len(w) > 2
    ]
    return bool(words) and all(w in hay for w in words)


@tool
async def manage_vector_membership(query: str = "") -> dict:
    """List indexed compliance PDFs' vector-DB membership for an explicit Keep/Remove decision.

    Call this ONLY when the user explicitly asks to add, remove, or manage vector-DB
    membership for a compliance document — e.g. "remove the air conditioning inspection
    report from the vector DB", "manage vector membership for TM44", "take the DEC out of
    the vector database". NEVER call it on a plain ingest — ingested PDFs are auto-kept and
    must not prompt.

    ``query`` narrows the list to the document(s) the user named — by certificate type
    ("air conditioning inspection report" / "TM44" / "DEC" / "asbestos"), by vendor, or by
    filename. Leave it empty ONLY when the user asked to manage ALL memberships.

    Returns ``{items: [{document_id, file_name, certificate_type_name, vendor_name,
    membership_pending: true, kind: "vector_membership"}], count, query}``. The chat renders
    a Keep/Remove confirm list from these — nothing is removed until the PM clicks Remove.
    """
    try:
        certs: list[dict[str, Any]] = []
        for scope in ("Building", "Vendor"):
            resp = await _request(
                "GET",
                _base(),
                "/api/compliance/certificates",
                service=_SERVICE,
                timeout=_TIMEOUT,
                params={"cert_scope": scope, "limit": 200},
            )
            body = resp.json()
            certs.extend(body.get("certificates") or body.get("items") or [])

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for c in certs:
            did = c.get("document_id")
            if not did:
                continue  # not indexed → no vector membership to manage
            did = str(did)
            if did in seen or not _match_membership_query(query, c):
                continue
            seen.add(did)
            meta = c.get("raw_metadata") or {}
            items.append(
                {
                    "document_id": did,
                    "file_name": meta.get("source_filename")
                    or c.get("vendor_name")
                    or did,
                    "certificate_type_name": c.get("certificate_type_name")
                    or c.get("certificate_type_code"),
                    "vendor_name": c.get("vendor_name"),
                    "membership_pending": True,
                    "kind": "vector_membership",
                }
            )

        if query and not items:
            summary = (
                f"No indexed compliance document matched '{query}'. Try the certificate "
                f"type (e.g. TM44 / DEC), the vendor name, or the filename."
            )
        elif query:
            summary = (
                f"{len(items)} document(s) matched '{query}'. Confirm Keep or Remove below "
                f"— nothing is removed until you click Remove."
            )
        else:
            summary = (
                f"{len(items)} indexed compliance document(s). Confirm Keep or Remove below "
                f"— nothing is removed until you click Remove."
            )
        return {
            "kind": "vector_membership_list",
            "items": items,
            "count": len(items),
            "query": query,
            "summary": summary,
        }
    except Exception as exc:
        return _err(exc, "manage_vector_membership")


@tool
async def get_pack_facts(country_code: str = "UK") -> dict:
    """Computed counts of the country pack against the register — use these numbers verbatim.

    Returns, per scope (Building / Vendor): required_types, trade_categories, types_held,
    types_with_nothing_on_record, by_trade, codes_held, codes_with_nothing_on_record — plus the
    register rows that match no pack type and any country codes other than the pack's.

    Call this whenever an answer states a total, a count of required types, or how many have
    nothing on record. Do NOT tally the pack yourself: counting 55 rows in prose produced
    "31 vendor types" against a 28-type pack. The counts here are computed by code from the
    same data; your job is to reason about them, not re-derive them.
    """
    try:
        from .compliance_facts import compute_pack_facts

        pack = await _request(
            "GET", _base(), "/api/compliance/country-pack",
            service=_SERVICE, timeout=_TIMEOUT, params={"country_code": country_code},
        )
        certs: list[dict[str, Any]] = []
        for scope in ("Building", "Vendor"):
            resp = await _request(
                "GET", _base(), "/api/compliance/certificates",
                service=_SERVICE, timeout=_TIMEOUT, params={"cert_scope": scope, "limit": 500},
            )
            body = resp.json()
            certs.extend(body.get("certificates") or body.get("items") or [])
        pj = pack.json()
        facts = compute_pack_facts(pj.get("types") or [], certs, pj.get("country_code") or country_code)
        return facts or {"ok": False, "error": "pack returned no types"}
    except Exception as exc:
        return _err(exc, "get_pack_facts")


@tool
async def get_mees_summary(building_id: str | None = None) -> dict:
    """A — MEES from the EPC register: per building the EPC band (A–G) and score, how many sit
    below E (unlettable now) and below B over 1,000 m² (proposed 2030), EPCs current / expiring
    within 12 months. UK only; an unknown band is reported as unknown, never as compliant."""
    try:
        params = {"building_id": building_id} if building_id else {}
        resp = await _request("GET", _base(), "/api/compliance/mees", service=_SERVICE, timeout=_TIMEOUT,
                              params=params)
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_mees_summary")


@tool
async def record_regulatory_filing(
    building_id: str,
    scheme: str,
    period_year: int,
    status: str = "filed",
    filed_at: str | None = None,
    reference: str | None = None,
    certification_level: str | None = None,
    valid_until: str | None = None,
    submitted_by: str | None = None,
) -> dict:
    """A — Record that a filing was made for a building and compliance year. scheme: LL84 (NYC
    benchmarking, due 1 May of the following year), BCA_BENCHMARKING (Singapore annual return),
    GREEN_MARK (certification: level Certified | Gold | GoldPlus | Platinum, valid 3 years).
    Idempotent on (building, scheme, year)."""
    try:
        resp = await _request("POST", _base(), "/api/compliance/filings", service=_SERVICE, timeout=_TIMEOUT,
                              json={"building_id": building_id, "scheme": scheme, "period_year": period_year,
                                    "status": status, "filed_at": filed_at, "reference": reference,
                                    "certification_level": certification_level, "valid_until": valid_until,
                                    "submitted_by": submitted_by})
        return resp.json()
    except Exception as exc:
        return _err(exc, "record_regulatory_filing")


@tool
async def list_regulatory_filings(building_id: str | None = None, scheme: str | None = None,
                                  country_code: str | None = None) -> dict:
    """A — Filings on record (LL84 / BCA / Green Mark) for the caller's buildings; with
    country_code (US | SG) also the position per scheme: filed / due / overdue, certified / lapsed."""
    try:
        params = {k: v for k, v in {"building_id": building_id, "scheme": scheme}.items() if v}
        out = (await _request("GET", _base(), "/api/compliance/filings", service=_SERVICE, timeout=_TIMEOUT,
                              params=params)).json()
        if country_code:
            pos = await _request("GET", _base(), "/api/compliance/filings/position", service=_SERVICE,
                                 timeout=_TIMEOUT, params={**params, "country_code": country_code})
            out["position"] = pos.json()
        return out
    except Exception as exc:
        return _err(exc, "list_regulatory_filings")


COMPLIANCE_ENGINE_TOOLS = [
    get_mees_summary,
    record_regulatory_filing,
    list_regulatory_filings,
    run_compliance_scan,
    get_pack_facts,
    manage_vector_membership,
    list_building_certificates,
    list_vendor_accreditations,
    list_vendors_by_certificate_count,
    count_compliance_certificates,
    upsert_compliance_certificate,
    set_remedial_status,
    draft_certificate_renewal,
    get_compliance_saved_space_summary,
    list_country_pack,
    seed_uk_country_pack,
    list_verification_registers,
    verify_accreditation_now,
    check_vendor_registration_compliance,
    run_document_forensics,
    list_compliance_approvals,
    decide_compliance_approval,
    flag_building_change,
    get_compliance_coverage,
    generate_compliance_evidence_pack,
    get_vendor_passport,
    share_vendor_passport,
]
