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

import asyncio
import difflib
import itertools
import re
import uuid
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request
from . import name_match
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


# ── names ranked by the shared ladder (29 Oct 2026) ───────────────────────────────────────────
# "HOW MANY FLOORS IN JASMEET BUILDING" found nothing because every comparison of a typed name
# with a stored one worked its own way. These tools kept EVERY row a name touched, by substring or
# one-letter closeness, so "JASMEET" also returned "Jasmeet Heights", "Acme" returned "Pacme
# Systems Ltd" and "the building" returned "The Building Centre". Register values are now ranked
# by name_match's ladder: exact, same letters and digits, phrase, every word in any order, close
# spelling. Exact beats partial; every value at the best rung is kept, never one picked; a phrase
# of kind words and fillers alone names nothing. All of it serves reads: nothing here creates,
# links or upserts a record.

#: Words that name the KIND of record beside a name, never the name itself.
BUILDING_KIND_WORDS = frozenset({"building", "buildings", "bldg", "site", "sites", "property",
                                 "properties", "premises"})
VENDOR_KIND_WORDS = frozenset({"vendor", "vendors", "contractor", "contractors", "supplier",
                               "suppliers"})
_KIND_WORDS = {
    "building_name": BUILDING_KIND_WORDS,
    "vendor_name": VENDOR_KIND_WORDS,
    "inspector_name": frozenset({"inspector", "inspectors", "engineer", "engineers"}),
    "issuer": frozenset({"issuer", "issuers"}),
}
#: Fields holding a company's name, ranked a second time without the legal form (Ltd = Limited).
_COMPANY_FIELDS = frozenset({"vendor_name", "issuer"})
#: How a value was matched, by rung.
MATCHED_BY = ("exact", "same letters and digits", "phrase", "every word", "close spelling")


def _legal_free(text) -> str:
    """A company name without its legal form, initials joined: "R.D.M. Electrical Services
    Limited" and "RDM Electrical Services Ltd" are both "rdm electrical services"."""
    return " ".join(_tokens(name_match.clean(text), _LEGAL_FORM_WORDS))


def _code_inside(query, stored, kind) -> bool:
    """A code typed without its separators inside a longer text: "B090765" in "B-090765_FRA.pdf".
    Word-aligned, so "Building 7" is never inside "Building 70"."""
    key = name_match.compact(" ".join(name_match.meaningful(query, kind)))
    if not key or not any(ch.isdigit() for ch in key):
        return False
    sw = name_match.words(stored)
    for i in range(len(sw)):
        joined = ""
        for w in sw[i:]:
            joined += w
            if joined == key:
                return True
            if not key.startswith(joined):
                break
    return False


def _close_words(query, stored, kind) -> float:
    """Every word of the name somewhere in the stored text, each by its start or one letter out
    (codes, numbers and one-/two-letter words exactly), scored 0-1; 0 when any word is missing.
    name_match.similarity compares whole names, so "R.D.M. Electrcal" (a word short AND a letter
    out) is not close to "RDM Electrical Services"; word by word it is."""
    core = name_match.meaningful(query, kind)
    sw = name_match.words(stored)
    if not core or not sw:
        return 0.0
    scores = []
    for w in core:
        if name_match.exact_token(w):
            best = 1.0 if w in sw else 0.0
        else:
            best = max(1.0 if s.startswith(w) else difflib.SequenceMatcher(None, w, s).ratio()
                       for s in sw)
        if best < name_match.FUZZY_MIN:
            return 0.0
        scores.append(best)
    return sum(scores) / len(scores)


def _rank(query, rows, kind) -> tuple[int | None, list[tuple]]:
    """``rows`` of (key, text) at the best rung any reaches, and that rung (None: nothing).
    name_match.best_matches, plus a code typed without its separators inside a longer text,
    which counts as a phrase."""
    if not name_match.meaningful(query, kind):
        return None, []                           # "the building" names nothing
    hits = name_match.best_matches(query, rows, kind)
    top = name_match.rung(query, hits[0][1], kind) if hits else None
    if top is None or top >= 2:
        inside = [r for r in rows if r not in hits and _code_inside(query, r[1], kind)]
        if inside:
            return 2, (hits if top == 2 else []) + inside
    return top, hits


def _closest_words(query, rows, kind) -> list[tuple]:
    """The rows clearly closest word by word (_close_words): the best, and every one tied
    with it."""
    scored = [(_close_words(query, text, kind), key, text) for key, text in rows]
    scored = [x for x in scored if x[0] >= name_match.FUZZY_MIN]
    if not scored:
        return []
    best = max(x[0] for x in scored)
    return [(key, text) for sc, key, text in scored if sc >= best - name_match.FUZZY_TIE]


def match_names(field: str, query, values) -> tuple[int | None, list[str]]:
    """The register ``values`` that ``query`` names at the best rung, and that rung
    (MATCHED_BY[rung]; None when nothing matched). A company field is ranked twice, as written
    and without its legal form, and the better rung wins. Close spelling (rung 4) is for reads:
    never call this to choose the record a write creates or links."""
    kind = _KIND_WORDS.get(field, frozenset())
    vals = list(dict.fromkeys(str(v) for v in values if str(v or "").strip()))
    passes = [(str(query or ""), [(v, v) for v in vals])]
    if field in _COMPANY_FIELDS:
        passes.append((_legal_free(query), [(v, _legal_free(v)) for v in vals]))
    best_rung: int | None = None
    best: list[str] = []
    for q, rows in passes:
        r, hits = _rank(q, rows, kind)
        if r is None:
            continue
        keys = [k for k, _ in hits]
        if best_rung is None or r < best_rung:
            best_rung, best = r, keys
        elif r == best_rung:
            best += [k for k in keys if k not in best]
    if best_rung is None:
        for q, rows in passes:
            best += [k for k, _ in _closest_words(q, rows, kind) if k not in best]
        best_rung = 4 if best else None
    return best_rung, best


def _name_matches(query: str, candidate: str) -> bool:
    """Company name: whether ``query`` names ``candidate`` at any rung of the ladder. One row at
    a time; _filter_by_field ranks every candidate instead, so an exact name beats a partial one."""
    return bool(match_names("vendor_name", query, [candidate])[1])


def _building_matches(query: str, candidate: str) -> bool:
    """Building name: whether ``query`` names ``candidate`` at any rung ("JASMEET BUILDING" ->
    "JASMEET", "B090765" -> "B-090765", "tower hussain" -> "Hussain Tower")."""
    return bool(match_names("building_name", query, [candidate])[1])


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


#: Fields compared word by word rather than as a name: a trade is a category, not a record.
_FIELD_MATCHERS = {
    "trade_category": _trade_matches,
}


def _filter_by_field(payload: dict[str, Any], field: str, query: str) -> dict[str, Any]:
    """Keep the rows whose `field` the query names, and say what matched.

    A name (building_name, vendor_name) is ranked by the shared ladder (match_names): the
    register values at the best rung are kept, every one of them when several share it.
    `<field>_match` tells the model which register values were matched and how, so it can
    report them, or notice that several did. When NOTHING matches, every row is returned
    together with the register's distinct values for that field and `no_match: true`, so the
    model can decide whether one of them is what the user meant, or answer that it is not on record.
    """
    if not isinstance(payload, dict):
        return payload
    rows = payload.get("certificates")
    if not isinstance(rows, list):
        return payload
    all_values = sorted({str(r.get(field)) for r in rows if isinstance(r, dict) and r.get(field)})
    rung: int | None = None
    if field in _FIELD_MATCHERS:
        matcher = _FIELD_MATCHERS[field]
        kept = [r for r in rows if isinstance(r, dict) and matcher(query, str(r.get(field) or ""))]
    else:
        rung, chosen = match_names(field, query, all_values)
        named = set(chosen)
        kept = [r for r in rows
                if isinstance(r, dict) and r.get(field) and str(r.get(field)) in named]
    out = dict(payload)
    matched = sorted({str(r.get(field)) for r in kept})
    match: dict[str, Any] = {
        "query": query,
        "matched_values": matched,
        "rows": len(kept),
        "how": ("names ranked by one ladder, best rung wins: exact (case, accents, quotes "
                "ignored), the same letters and digits (B090765 = B-090765), the phrase from the "
                "start of a word, every word in any order, one wrong or missing letter - codes "
                "and numbers always exact; 'building', 'site', 'vendor', 'the' are not part "
                "of a name; Ltd/Limited and initials ignored for companies; trade words mapped "
                "onto the register's categories (fire safety->Fire, lifts->LOLER, air "
                "conditioning->HVAC)"),
    }
    if rung is not None:
        match["matched_by"] = MATCHED_BY[rung]
    notes = []
    if rung == 4:
        notes.append(f"'{query}' is not on the register as written; the closest {field} is "
                     + ", ".join(f"'{v}'" for v in matched[:5]) + " - say so.")
    if len(matched) > 1 and field not in _FIELD_MATCHERS:
        notes.append(f"'{query}' names {len(matched)} {field} values - answer for each and "
                     "name them all; never pick one.")
    if notes:
        match["note"] = " ".join(notes)
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


#: count_compliance_certificates' name filters: the field each is ranked as, and the register
#: columns its values are read from (the columns the service's own substring test reads).
_COUNT_NAME_FILTERS = {
    "vendor_name_contains": ("vendor_name", ("vendor_name",)),
    "inspector_name": ("inspector_name", ("inspector_name",)),
    "issuer_contains": ("issuer", ("issuer", "issuing_body", "inspector_accreditation_number")),
}
#: More combinations of matched names than this is a name too loose to count by: say so instead.
_MAX_COUNT_READS = 10


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _exactly(row: dict[str, Any], combo: dict[str, str]) -> bool:
    """Whether a counted row carries exactly the matched names. The service counts every name that
    CONTAINS one ("Acme Facilities Ltd" also counts "Acme Facilities Ltd North"). An issuer is read
    across three columns the count's preview does not all carry, so its containment stands."""
    for param, value in combo.items():
        col = _COUNT_NAME_FILTERS[param][0]
        got = str(row.get(col) or "").strip().casefold()
        if col != "issuer" and got != value.strip().casefold():
            return False
    return True


async def _count_by_register_names(params: dict[str, Any], names: dict[str, str]) -> dict:
    """count_compliance_certificates for a question naming a vendor, inspector or issuer
    (29 Oct 2026).

    The service counts rows whose column CONTAINS the typed text, so "ACME Facilities Limited"
    counted 0 certificates of "Acme Facilities Ltd", "Okafor James" 0 of "James Okafor", and
    "Acme" counted "Pacme Systems Ltd" too. Each name is first resolved against the register's own
    values, read through the same scoped service with the same scope filters (match_names: the best
    rung, every value sharing it); each matched value is then counted by the service, every other
    filter staying the service's, and rows of a longer namesake are left out. Several matched
    values are all counted and all named, never one picked.
    """
    base = {k: v for k, v in params.items() if k not in _COUNT_NAME_FILTERS}
    listing = {k: base[k] for k in ("cert_scope", "status", "draft", "risk_filter") if k in base}
    listing["limit"] = 1000
    try:
        resp = await _request("GET", _base(), "/api/compliance/certificates", service=_SERVICE,
                              timeout=_TIMEOUT, params=listing)
        body = resp.json() or {}
        register = [r for r in (body.get("certificates") or []) if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001
        # The register could not be read to resolve the names: the service's own count, said so.
        log.warning("compliance_engine.count_names_unresolved", error=str(exc)[:300])
        resp = await _request("GET", _base(), "/api/compliance/certificates/count",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        out = resp.json()
        if isinstance(out, dict):
            out["name_match"] = {"resolved": False, "note": (
                "The register could not be read to resolve the name; this count compares the text "
                "exactly as typed, as a substring - say so.")}
        return out

    def values_of(cols) -> list[str]:
        return sorted({str(r.get(c)).strip()
                       for r in register for c in cols if str(r.get(c) or "").strip()})

    resolved: dict[str, list[str]] = {}
    info: dict[str, Any] = {}
    for param, typed in names.items():
        field, cols = _COUNT_NAME_FILTERS[param]
        values = values_of(cols)
        rung, hits = match_names(field, typed, values)
        entry: dict[str, Any] = {"query": typed, "matched_values": hits}
        if rung is None:
            entry.update(no_match=True, all_values=values, note=(
                f"No {field} on the register matched '{typed}', so nothing was counted for it; "
                "decide whether one of all_values is what the user meant, or answer that it is "
                "not on record."))
        else:
            entry["matched_by"] = MATCHED_BY[rung]
            notes = []
            if rung == 4:
                notes.append(f"'{typed}' is not on the register as written; the closest is "
                             + ", ".join(f"'{v}'" for v in hits[:5]) + " - say so.")
            if len(hits) > 1:
                notes.append(f"'{typed}' names {len(hits)} {field} values; all were counted - "
                             "name them all.")
            if notes:
                entry["note"] = " ".join(notes)
        resolved[param] = hits
        info[field + "_match"] = entry
    if any(not hits for hits in resolved.values()):
        return {"ok": True, "count": 0, "matches": [], **info}
    combos = [dict(zip(resolved, vals)) for vals in itertools.product(*resolved.values())]
    if len(combos) > _MAX_COUNT_READS:
        # No "count": a number here would be read as the answer.
        return {"ok": True, "ambiguous": True, "matches": [], **info, "note": (
            f"The name matches {len(combos)} register values - too many to count as one; ask which "
            "is meant, naming matched_values.")}
    outs = await asyncio.gather(*(
        _request("GET", _base(), "/api/compliance/certificates/count", service=_SERVICE,
                 timeout=_TIMEOUT, params={**base, **combo})
        for combo in combos))
    counted: dict[str, dict[str, Any]] = {}
    beyond: list[dict[str, Any]] = []
    beyond_count = 0
    by_value: list[dict[str, Any]] = []
    notes: list[str] = []
    for combo, resp in zip(combos, outs):
        body = resp.json() or {}
        n = _int(body.get("count"))
        preview = [m for m in (body.get("matches") or []) if isinstance(m, dict)]
        if n <= len(preview):
            # Every counted row is listed: keep exactly the rows of the matched names.
            own = [m for m in preview if _exactly(m, combo)]
            for m in own:
                counted.setdefault(str(m.get("id") or id(m)), m)
            by_value.append({**combo, "count": len(own)})
            continue
        # More rows than the service lists: its count stands, and says what else it includes.
        beyond_count += n
        beyond += preview
        by_value.append({**combo, "count": n})
        for param, value in combo.items():
            field, cols = _COUNT_NAME_FILTERS[param]
            longer = [v for v in values_of(cols)
                      if value.casefold() in v.casefold() and v.casefold() != value.casefold()]
            if longer:
                notes.append(f"the count for '{value}' also includes {field} values containing it: "
                             + ", ".join(f"'{v}'" for v in longer[:5]))
    result: dict[str, Any] = {"ok": True, "count": len(counted) + beyond_count,
                              "matches": (list(counted.values()) + beyond)[:50], **info}
    if len(combos) > 1:
        result["by_value"] = by_value
    if notes:
        result["approximate"] = True
        result["note"] = "; ".join(notes) + " - say the count may include them."
    return result


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
    vendor_name_contains / inspector_name / issuer_contains: pass the name as the user wrote it;
    it is resolved against the register first (Ltd/Limited, word order, a missing hyphen or
    letter do not matter) and `<field>_match` says which register values were counted.
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
        names = {p: str(params[p]).strip()
                 for p in _COUNT_NAME_FILTERS if str(params.get(p) or "").strip()}
        if names:
            return await _count_by_register_names(params, names)
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


def _split_vendor_ref(
    vendor_id: str | None, vendor_name: str | None
) -> tuple[str | None, str | None]:
    """Sort a vendor reference into the field that can hold it.

    A certificate prints its contractor's NAME. It does not print a UUID. So a model filling
    this tool in knows "Meridian Mechanical Ltd" and nothing else — and until vendor_name
    existed here, the only vendor-shaped parameter on offer was `vendor_id`. It put the name
    there, CertificateUpsertRequest types that column as a UUID, and Pydantic returned 422.
    The write was lost and the agent reported the certificate as unstored when the
    single-door pass had already stored it.

    A name arriving in the id field is therefore read as what it is rather than refused. An
    explicit vendor_name always wins; ops-intelligence resolves it to the vendor either way.
    """
    vid = (vendor_id or "").strip()
    name = (vendor_name or "").strip() or None
    if not vid:
        return None, name
    try:
        uuid.UUID(vid)
    except (ValueError, AttributeError, TypeError):
        return None, name or vid
    return vid, name


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
    vendor_name: str | None = None,
    inspector_name: str | None = None,
    inspector_accreditation_number: str | None = None,
    result: str | None = None,
    defects_found: str | None = None,
    remedial_actions: str | None = None,
    organization_id: str | None = None,
    country_code: str = "UK",
) -> dict:
    """Upsert a building or vendor certificate (A2/A3 ingest).

    vendor_id must be a UUID. When you know the contractor only by the name printed on the
    certificate — which is the usual case — pass vendor_name instead and leave vendor_id
    unset; it is resolved to the vendor on the server.

    confirmed_by_pm=False still persists a draft (appears in lists / Saved Space).
    Set confirmed_by_pm=True after the PM reviews extracted fields to finalise.
    Fail/Advisory opens remedial_status only after confirm (no WO auto-created).
    """
    vendor_id, vendor_name = _split_vendor_ref(vendor_id, vendor_name)
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
                "vendor_name": vendor_name,
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


def _membership_texts(cert: dict[str, Any]) -> list[str]:
    """What a certificate can be named by: its type, type code, vendor, filename, company."""
    meta = cert.get("raw_metadata")
    meta = meta if isinstance(meta, dict) else {}
    return [
        str(x)
        for x in (
            cert.get("certificate_type_name"),
            cert.get("certificate_type_code"),
            cert.get("vendor_name"),
            meta.get("source_filename"),
            meta.get("company_name"),
        )
        if str(x or "").strip()
    ]


def _membership_near(q: str, cert: dict[str, Any]) -> bool:
    """Whether the query names this certificate nearer than close spelling: a known alias
    ('air conditioning' → TM44), or any rung but the last over its texts one by one, or over
    all of them together (every word in any order across type, vendor and filename)."""
    texts = _membership_texts(cert)
    hay = " ".join(texts).lower()
    ql = q.strip().lower()
    for phrase, code in _MEMBERSHIP_ALIASES.items():
        if phrase in ql and code in hay:
            return True
    rows = [(i, t) for i, t in enumerate(texts)] + [("all", " ".join(texts))]
    rung, _ = _rank(q, rows, _MEMBERSHIP_STOPWORDS)
    return rung is not None and rung < 4


def _membership_matches(q: str, certs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The certificates the query names (29 Oct 2026).

    Every word of one or two letters was dropped and the rest compared by substring, so
    "Building 7" matched every "Building ..." document (Building 70 too), "Acme" matched "Pacme",
    "report" matched every report and "B090765" never met "B-090765_FRA.pdf". Now: empty matches
    everything (manage all); otherwise every certificate the shared ladder names nearer than
    close spelling, and only when none is, the clearly closest spellings. A query of command and
    kind words alone ("remove the report") names nothing.
    """
    if not (q or "").strip():
        return list(certs)
    near = [c for c in certs if _membership_near(q, c)]
    if near:
        return near
    core = " ".join(name_match.meaningful(q, _MEMBERSHIP_STOPWORDS))
    if not core:
        return []
    rows = [(i, t) for i, c in enumerate(certs) for t in _membership_texts(c)]
    picked = name_match.pick_closest(core, rows) or _closest_words(q, rows, _MEMBERSHIP_STOPWORDS)
    return [certs[i] for i in dict.fromkeys(k for k, _ in picked)]


def _match_membership_query(q: str, cert: dict[str, Any]) -> bool:
    """True when the free-text query names this certificate by its type / vendor / filename."""
    return bool(_membership_matches(q, [cert]))


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

        # Ranked over every indexed certificate at once: the closest spelling is only taken
        # when nothing names a document more nearly.
        indexed = [c for c in certs if c.get("document_id")]
        named = {id(c) for c in _membership_matches(query, indexed)}
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for c in certs:
            did = c.get("document_id")
            if not did:
                continue  # not indexed → no vector membership to manage
            did = str(did)
            if did in seen or id(c) not in named:
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


from .document_search_tool import search_documents  # noqa: E402 - what the linked documents say
from .thread_scope import work_order_blockers  # noqa: E402 - blocked by the vendor, one read

COMPLIANCE_ENGINE_TOOLS = [
    search_documents,
    work_order_blockers,
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
