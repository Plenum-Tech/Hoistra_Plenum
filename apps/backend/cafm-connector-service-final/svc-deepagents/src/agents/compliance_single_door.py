"""
Single Door → Feature A (Compliance) auto-route.

When PM uploads building certificates or vendor accreditation PDFs via the
Orchestrator, classify and call svc-operations-intelligence extract + draft upsert
so structured certificate fields are drafted for HITL confirmation.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog

from ..config import settings
from ..http_client import request as _request
from .compliance_offers import offers_for_row, row_from_ingest
from .contract_performance_single_door import extract_text_from_upload
from .document_forensics import analyze_certificate_document, forensics_summary_line
from .pdf_vision_extract import extract_document_text

log = structlog.get_logger(__name__)


# CCC §1 canonical aliases for Type code: lines and residual legacy tokens
_CODE_ALIASES_INLINE = {
    "FIRE_ALARM_SERVICE": "FIRE_ALARM_SVC",
    "SPRINKLER": "SPRINKLER_TEST",
    "GAS_SAFE": "GAS_CP17",
    "L8_RISK": "LEGIONELLA_RA",
    "BPCA": "BPCA_MEMBER",
    "NICEIC": "NICEIC_CONTRACTOR",
    "NAPIT": "NAPIT_REG",
    "ACS_CARD": "ACS_GAS_CARD",
    "SIA_INDIVIDUAL": "SIA_LICENCE",
    "NSI_GOLD_SECURITY": "NSI_GOLD_SEC",
    "BAFE_SP203_1": "BAFE_SP203",
    "HSE_ASBESTOS_LICENCE": "ASBESTOS_LICENCE",
    "LCA": "LCA_REG",
    "REFCOM": "REFCOM_COMPANY",
    "LEIA": "LEIA_MEMBER",
    "CONTRACTOR_PL_INSURANCE": "CONTRACTOR_PL",
    "CONTRACTOR_EL_INSURANCE": "CONTRACTOR_EL",
    "PA1_PA2_PA6": "PESTICIDE_PAx",
    "ASBESTOS_P402_P403_P404": "BOHS_P40x",
    "BTEC_LEGIONELLA": "LEGIONELLA_COMP",
}

# Filename / message / body hints → (certificate_type_code, cert_scope)
# Building Fire types MUST come before NICEIC/Gas Safe vendor tokens — contractor
# accreditation names often appear on building certificates (e.g. NICEIC on EL tests).
# Vendor-specific tokens MUST come before any generic wording.
_TYPE_HINTS: list[tuple[re.Pattern[str], str, str]] = [
# CODE_ALIASES_INLINE marker

    # ── Building (statutory) ──────────────────────────────────────────
    (
        re.compile(
            r"\b(emergency[\s_-]?lighting|el[\s_-]?test|bs[\s_-]?5266)\b",
            re.I,
        ),
        "EMERGENCY_LIGHTING",
        "Building",
    ),
    (re.compile(r"\b(sprinkler)\b", re.I), "SPRINKLER_TEST", "Building"),
    (re.compile(r"\b(fire[\s_-]?door)\b", re.I), "FIRE_DOOR", "Building"),
    (
        re.compile(r"\b(eicr|electrical[\s_-]?installation[\s_-]?condition)\b", re.I),
        "EICR",
        "Building",
    ),
    # Building landlord gas BEFORE vendor Gas Safe company register
    (
        re.compile(
            r"\b(cp12|cp17|landlord[\s_-]?gas|gas[\s_-]?safety[\s_-]?record|"
            r"gas[\s_-]?safety[\s_-]?certificate)\b",
            re.I,
        ),
        "GAS_CP17",
        "Building",
    ),
    (
        # NB: do NOT match the bare "Regulatory Reform (Fire Safety) Order" here — every
        # fire certificate (alarm, sprinkler, door) cites the FSO, so it captured Fire Alarm
        # service certs as FRA. FRA must key on "fire risk assessment" / FRA-ref only.
        re.compile(
            r"\b(fire[\s_-]?risk(?:\s+assessment)?|fra[\s_-]?\d+)\b",
            re.I,
        ),
        "FRA",
        "Building",
    ),
    (re.compile(r"\b(asbestos[\s_-]?survey|asb[\s_-]?survey)\b", re.I), "ASBESTOS_SURVEY", "Building"),
    (re.compile(r"\b(legionella|l8[\s_-]?risk)\b", re.I), "LEGIONELLA_RA", "Building"),
    (re.compile(r"\b(pat[\s_-]?test|portable[\s_-]?appliance)\b", re.I), "PAT", "Building"),
    (re.compile(r"\b(epc|energy[\s_-]?performance)\b", re.I), "EPC", "Building"),
    (re.compile(r"\b(dec|display[\s_-]?energy[\s_-]?certificate)\b", re.I), "DEC", "Building"),
    (
        re.compile(
            # \btm44(?!\d) so "TM44_AirCon" (underscore breaks \b) still matches, plus the
            # air-conditioning inspection wording that appears in the certificate body.
            r"(\btm[\s_-]?44(?!\d)"
            r"|air[\s_-]?condition\w*\s+(?:inspection|report|energy\s+assessment)"
            r"|air[\s_-]?con\s+inspection|ac[\s_-]?inspection\s+(?:certificate|report))",
            re.I,
        ),
        "TM44",
        "Building",
    ),
    # ── Vendor company accreditations (A3) ────────────────────────────
    # These realistic certificates carry the ISSUER's accreditation in the letterhead
    # (e.g. a building Fire-Alarm service cert or an NSI Gold cert both printed on a
    # "BAFE SP203-1" letterhead). Match the certificate's OWN title first so the
    # letterhead's "BAFE" token can't capture the document.
    (
        re.compile(
            r"\b(fire[\s_-]?alarm[\s_-]?(?:system[\s_-]?)?(?:service|test|inspection|maintenance))\b",
            re.I,
        ),
        "FIRE_ALARM_SVC",
        "Building",
    ),
    (
        re.compile(
            r"\b(public[\s_-]?liability[\s_-]?insurance|contractors?[\s_-]?public[\s_-]?liability)\b",
            re.I,
        ),
        "CONTRACTOR_PL",
        "Vendor",
    ),
    (
        re.compile(
            r"\b(employers?[\s_-]?liability[\s_-]?insurance|contractors?[\s_-]?employers?[\s_-]?liability)\b",
            re.I,
        ),
        "CONTRACTOR_EL",
        "Vendor",
    ),
    (
        re.compile(r"\b(nacoss|nsi[\s_-]?gold[\s_-]?security|nsi[\s_-]?nacoss)\b", re.I),
        "NSI_GOLD_SEC",
        "Vendor",
    ),
    # BAFE SP105 (dry/wet riser service) BEFORE SP101 so a combined/riser cert whose text also
    # mentions "sp101" is not mis-captured as the extinguisher scheme.
    (
        re.compile(
            r"\b(bafe[\s_-]?sp105|sp105|dry[\s_-]?(?:&|and|/)?[\s_-]?wet[\s_-]?riser|"
            r"wet[\s_-]?riser|dry[\s_-]?riser|falling[\s_-]?installation)\b",
            re.I,
        ),
        "BAFE_SP105",
        "Vendor",
    ),
    (re.compile(r"\b(bafe[\s_-]?sp101|sp101|lpc[\s_-]?rules)\b", re.I), "BAFE_SP101", "Vendor"),
    # BAFE SP203-1 requires the scheme token (not a bare "BAFE" letterhead mention).
    (re.compile(r"\b(bafe[\s_-]?sp203|sp203[\s_-]?1)\b", re.I), "BAFE_SP203", "Vendor"),
    (re.compile(r"\b(nsi[\s_-]?gold[\s_-]?fire|nsi[\s_-]?fire)\b", re.I), "NSI_GOLD_FIRE", "Vendor"),
    (
        re.compile(
            r"\b(bpca|pest[\s_-]?guard|pest[\s_-]?control|british[\s_-]?pest)\b",
            re.I,
        ),
        "BPCA_MEMBER",
        "Vendor",
    ),
    (re.compile(r"\b(pa1|pa2|pa6|pesticide)\b", re.I), "PESTICIDE_PAx", "Vendor"),
    (
        re.compile(r"\b(p402|p403|p404|asbestos\s+competenc)\b", re.I),
        "BOHS_P40x",
        "Vendor",
    ),
    (re.compile(r"\b(acs[\s_-]?card)\b", re.I), "ACS_GAS_CARD", "Vendor"),
    (
        re.compile(
            r"\b(gas[\s_-]?safe[\s_-]?(?:register|company|business|membership|id)|"
            r"gas[\s_-]?safe[\s_-]?engineer)\b",
            re.I,
        ),
        "GASSAFE_COMPANY",
        "Vendor",
    ),
    (
        re.compile(
            r"\b(nia|niceic[\s_-]?approved[\s_-]?contractor|"
            r"niceic(?![\s_-]?eicr)|electrical[\s_-]?contractor)\b",
            re.I,
        ),
        "NICEIC_CONTRACTOR",
        "Vendor",
    ),
    (re.compile(r"\b(napit)\b", re.I), "NAPIT_REG", "Vendor"),
    (re.compile(r"\b(c(?:ity)?[\s_-]?&?[\s_-]?g(?:uilds)?[\s_-]?2382|cg[\s_-]?2382)\b", re.I), "CG_2382", "Vendor"),
    (re.compile(r"\b(leia)\b", re.I), "LEIA_MEMBER", "Vendor"),
    (re.compile(r"\b(loler[\s_-]?competent|loler[\s_-]?cp)\b", re.I), "LOLER_CP", "Vendor"),
    (
        re.compile(
            # Matches "HSE asbestos", "asbestos HSE licence", "asbestos licence", the HSE
            # licence wording, and the HSE form code (FOD ASB2A) — so an HSE asbestos LICENCE
            # is not swallowed by the generic "asbestos" -> ASBESTOS_SURVEY fallback below.
            r"\b(hse[\s_-]?asbestos|asbestos[\s_-]?(?:hse[\s_-]?)?licen[cs]e"
            r"|licen[cs]e\s+to\s+undertake\s+work\s+with\s+asbestos|asb2a)\b",
            re.I,
        ),
        "ASBESTOS_LICENCE",
        "Vendor",
    ),
    (re.compile(r"\b(ukas[\s_-]?asbestos|ukas)\b", re.I), "UKAS_ASBESTOS", "Vendor"),
    (re.compile(r"\b(lca|legionella[\s_-]?control[\s_-]?associat)\b", re.I), "LCA_REG", "Vendor"),
    (re.compile(r"\b(btec[\s_-]?legionella)\b", re.I), "LEGIONELLA_COMP", "Vendor"),
    (re.compile(r"\b(refcom|f[\s_-]?gas[\s_-]?company)\b", re.I), "REFCOM_COMPANY", "Vendor"),
    (re.compile(r"\b(c(?:ity)?[\s_-]?&?[\s_-]?g(?:uilds)?[\s_-]?2079|cg[\s_-]?2079)\b", re.I), "CG_2079", "Vendor"),
    (re.compile(r"\b(chas|ssip)\b", re.I), "CHAS_SSIP", "Vendor"),
    (re.compile(r"\b(iso[\s_-]?9001)\b", re.I), "ISO_9001", "Vendor"),
    (re.compile(r"\b(iso[\s_-]?14001)\b", re.I), "ISO_14001", "Vendor"),
    (re.compile(r"\b(sia[\s_-]?acs|sia[\s_-]?approved[\s_-]?contractor)\b", re.I), "SIA_ACS", "Vendor"),
    (re.compile(r"\b(sia[\s_-]?(?:individual|licence|license|badge)?)\b", re.I), "SIA_LICENCE", "Vendor"),
    (re.compile(r"\b(nsi[\s_-]?gold[\s_-]?security|nsi[\s_-]?security)\b", re.I), "NSI_GOLD_SEC", "Vendor"),
    # Building fire alarm AFTER vendor BAFE/NSI so contractor packs don't misroute
    (
        re.compile(
            r"\b(fire[\s_-]?alarm[\s_-]?service|bs[\s_-]?5839|"
            r"fire[\s_-]?alarm[\s_-]?(?:test|inspection|maintenance))\b",
            re.I,
        ),
        "FIRE_ALARM_SVC",
        "Building",
    ),
    (re.compile(r"\b(fire[\s_-]?alarm)\b", re.I), "FIRE_ALARM_SVC", "Building"),
    # Building gas leftover (after Vendor Gas Safe company patterns)
    (re.compile(r"\b(gas[\s_-]?safe)\b", re.I), "GAS_CP17", "Building"),
    (re.compile(r"\b(asbestos|asb)\b", re.I), "ASBESTOS_SURVEY", "Building"),
]

# Known compliance tokens for route eligibility — never invent EICR from bare "certificate"
_CERT_MSG = re.compile(
    r"\b(gas\s+safe|eicr|asbestos|fire\s+risk|legionella|bpca|pest[\s_-]?control|"
    r"niceic|napit|bafe|refcom|chas|sia|leia|loler|emergency\s+lighting|"
    r"fire\s+door|sprinkler|iso\s*9001|iso\s*14001)\b",
    re.I,
)


def classify_compliance_certificate_doc(
    file_path: str,
    user_query: str | None = None,
    *,
    source_text: str | None = None,
    peek_file: bool = True,
) -> dict[str, str] | None:
    """
    Detect Feature A certificate uploads from filename + user message + body text.
    Returns {certificate_type_code, cert_scope} or None.
    """
    name = re.sub(r"[_\-.]+", " ", Path(file_path).name)
    msg = (user_query or "").strip()
    body = (source_text or "").strip()
    if not body and peek_file:
        body = extract_text_from_upload(file_path, max_chars=4_000)
    hay = f"{name} {msg} {body[:4000]}".strip()

    # Highest priority: an explicit "Type code: <CODE>" line stated in the document body. The
    # conformed certificate template declares it verbatim, and it is definitive — it must beat the
    # fuzzy keyword hints below, which otherwise misfire on LETTERHEAD tokens. e.g. an NSI Gold
    # certificate on Aegis Security letterhead carries "SIA ACS 228104", so the earlier-listed
    # SIA_ACS hint matches before NSI_GOLD_SECURITY and the cert is mislabelled. Reading the stated
    # Type code (validated against the known hint codes) removes that ambiguity.
    _known_codes = {c for _p, c, _s in _TYPE_HINTS}
    _scope_by_code = {c: s for _p, c, s in _TYPE_HINTS}
    _tc = re.search(r"type\s*code\s*[:\-|]?\s*([A-Za-z][A-Za-z0-9_]{2,60})", body, re.I)
    if _tc:
        code = _tc.group(1).strip().upper()
        code = _CODE_ALIASES_INLINE.get(code, code)
        if code in _known_codes or code in _CODE_ALIASES_INLINE.values():
            _sc = re.search(r"scope\s*[:\-|]?\s*(building|vendor)", body, re.I)
            scope = _sc.group(1).title() if _sc else _scope_by_code.get(code, "Vendor")
            return {
                "certificate_type_code": code,
                "cert_scope": scope,
                "method": "keyword",
                "strength": "typecode",
            }

    # Route by the document's OWN identity (filename + user message) before its body.
    # These certificates are printed on the issuer's accreditation letterhead (BAFE / CHAS /
    # UKAS / NICEIC …); reading the whole body lets a letterhead token capture the document
    # (e.g. an ISO 9001 cert issued by a UKAS-accredited body → UKAS, or an SIA ACS cert on a
    # CHAS-registered contractor's paper → CHAS). The filename/title states what the cert IS.
    primary = f"{name} {msg}".strip()
    for pattern, code, scope in _TYPE_HINTS:
        if pattern.search(primary):
            code = _CODE_ALIASES_INLINE.get(code, code)
            return {
                "certificate_type_code": code,
                "cert_scope": scope,
                "method": "keyword",
                "strength": "primary",
            }

    # Fall back to the full body only when the filename/message carry no recognisable type
    # (e.g. an upload literally named "scan001.pdf").
    for pattern, code, scope in _TYPE_HINTS:
        if pattern.search(hay):
            code = _CODE_ALIASES_INLINE.get(code, code)
            return {
                "certificate_type_code": code,
                "cert_scope": scope,
                "method": "keyword",
                "strength": "body",
            }

    if _CERT_MSG.search(hay):
        return None
    return None


# ── Hybrid classifier: LLM-primary + keyword, reconciled by re-ranking ─────────
# Codes the LLM may return, derived from the keyword hints so both signals agree.
_KNOWN_CODES: set[str] = {_CODE_ALIASES_INLINE.get(c, c) for _p, c, _s in _TYPE_HINTS}
_SCOPE_BY_CODE: dict[str, str] = {
    _CODE_ALIASES_INLINE.get(c, c): s for _p, c, s in _TYPE_HINTS
}

# US CountryPack types — so a US certificate is classified onto its OWN pack type (not forced
# onto the closest UK type). Kept alongside the UK catalogue; country routing (below) then
# stamps the certificate with the right country_code.
_US_TYPES: dict[str, str] = {
    "US_FIRE_ALARM_NFPA72": "Building",
    "US_SPRINKLER_NFPA25": "Building",
    "US_FIRE_EXT_NFPA10": "Building",
    "US_FIRE_DOOR_NFPA80": "Building",
    "US_EMERGENCY_LIGHT_NFPA101": "Building",
    "US_ELEVATOR_A17": "Building",
    "US_BOILER_INSPECTION": "Building",
    "US_BACKFLOW": "Building",
    "US_GENERATOR_NFPA110": "Building",
    "US_LEGIONELLA_ASHRAE188": "Building",
    "US_ASBESTOS_OM": "Building",
    "US_GL_INSURANCE": "Vendor",
    "US_WORKERS_COMP": "Vendor",
    "US_EPA_608": "Vendor",
    "US_STATE_CONTRACTOR_LICENSE": "Vendor",
    "US_OSHA_30": "Vendor",
    "US_NICET_FIRE": "Vendor",
    "US_LEAD_RRP": "Vendor",
}
_KNOWN_CODES |= set(_US_TYPES)
_SCOPE_BY_CODE.update(_US_TYPES)


def _detect_country_single_door(
    code: str | None,
    source_text: str | None,
    file_name: str | None,
    hint_country: str | None = None,
) -> str:
    """Route an ingested certificate to its country's pack.

    Order: the classifier's own country (the semantic catalogue spans every country pack, so
    the winning type already knows which country it came from - essential for UAE, whose codes
    carry no prefix), then a US_/UAE_ code prefix, then document standards / authorities.
    """
    hc = (hint_country or "").strip().upper()
    if hc in {"UK", "US", "UAE"}:
        return hc
    c = (code or "").upper()
    if c.startswith("US_"):
        return "US"
    if c.startswith("UAE_"):
        return "UAE"
    hay = f"{file_name or ''} {(source_text or '')[:6000]}".lower()
    us = sum(
        1
        for s in (
            "nfpa", "asme", "epa ", "osha", "ashrae", "ansi", "acord",
            "certificate of liability", "section 608", "nicet", "national board",
            "40 cfr", "backflow", "workers compensation", "united states",
        )
        if s in hay
    )
    uae = sum(
        1
        for s in (
            "dcd", "dewa", "esma", "civil defence", "civil defense", "dubai",
            "abu dhabi", "oshad", "united arab emirates", " uae",
        )
        if s in hay
    )
    if us and us >= uae:
        return "US"
    if uae:
        return "UAE"
    return "UK"

# Re-rank scoring — LLM is the first preference; a strong keyword still wins on a
# clear signal (definitive "Type code:" line, or a filename/title match vs a low-
# confidence LLM guess). Tuned so agreement always dominates.
_LLM_CONF_SCORE = {"high": 0.90, "medium": 0.72, "low": 0.55}
_LLM_RANK_PENALTY = [0.0, 0.08, 0.16]  # 1st, 2nd, 3rd LLM candidate
_LLM_PREFERENCE_BONUS = 0.06  # LLM wins close ties (first preference)
_KW_STRENGTH_SCORE = {"typecode": 1.0, "primary": 0.85, "body": 0.65}
_AGREEMENT_BONUS = 0.12  # both signals name the same code


def _llm_code_catalogue() -> str:
    return "\n".join(
        f"- {code} ({_SCOPE_BY_CODE[code]})" for code in sorted(_KNOWN_CODES)
    )


def _score_to_confidence(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


async def classify_compliance_certificate_llm(
    file_path: str,
    user_query: str | None = None,
    *,
    source_text: str | None = None,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """LLM classifier (primary signal). Returns a RANKED list of candidate
    ``{certificate_type_code, cert_scope, confidence}`` (best first), or ``[]`` when the
    document is not a compliance/accreditation certificate. Best-effort; never raises.
    """
    key = (settings.anthropic_api_key or "").strip()
    if not key:
        return []
    name = re.sub(r"[_\-.]+", " ", Path(file_path).name)
    msg = (user_query or "").strip()
    body = (source_text or "").strip()[:6000]
    prompt = (
        "Decide whether this uploaded document is a UK facilities-management "
        "COMPLIANCE or ACCREDITATION certificate (building statutory cert or vendor "
        "contractor accreditation), and if so which type(s) best fit.\n\n"
        "Choose certificate_type_code values ONLY from this catalogue "
        "(building = property/PM statutory; vendor = contractor accreditation):\n"
        f"{_llm_code_catalogue()}\n\n"
        "If the document is NOT such a certificate (invoice, manual, floor plan, "
        "email, generic report), return compliance=false with an empty candidates list.\n\n"
        f"Return ONLY JSON: {{\"compliance\": true|false, \"candidates\": "
        f"[{{\"certificate_type_code\": \"<CODE>\", \"cert_scope\": \"Building|Vendor\", "
        f"\"confidence\": \"high|medium|low\"}}]}} — up to {top_k} candidates, best first.\n\n"
        f"File name: {name}\n"
        f"User message: {msg or '(none)'}\n"
        f"Document text (may be partial/OCR):\n{body or '(no text layer extracted)'}"
    )
    try:
        import json as _json

        import anthropic

        client = anthropic.AsyncAnthropic(api_key=key)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=350,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        )
        raw = resp.content[0].text if resp.content else "{}"
        raw = re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        data = _json.loads(raw)
        if not data.get("compliance"):
            return []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for cand in (data.get("candidates") or [])[:top_k]:
            code = str(cand.get("certificate_type_code") or "").strip().upper()
            code = _CODE_ALIASES_INLINE.get(code, code)
            if code not in _KNOWN_CODES or code in seen:
                continue
            seen.add(code)
            conf = str(cand.get("confidence") or "medium").strip().lower()
            if conf not in _LLM_CONF_SCORE:
                conf = "medium"
            out.append(
                {
                    "certificate_type_code": code,
                    "cert_scope": _SCOPE_BY_CODE.get(code, "Vendor"),
                    "confidence": conf,
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001 — classification is best-effort
        log.warning("single_door.compliance.llm_classify_failed", error=str(exc)[:200])
        return []


def _rerank_candidates(
    llm_candidates: list[dict[str, Any]],
    keyword_hint: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Fuse the LLM's ranked candidates with the keyword hint into one winner.

    - A definitive keyword ``strength == "typecode"`` (the doc literally states its code)
      overrides everything.
    - Otherwise every candidate is scored; agreement between the two signals is boosted;
      the LLM carries a small preference bonus so it wins close ties. Highest score wins.
    """
    scored: dict[str, dict[str, Any]] = {}

    def _bump(code: str, scope: str, score: float, source: str) -> None:
        entry = scored.get(code)
        if entry is None or score > entry["score"]:
            scored[code] = {
                "certificate_type_code": code,
                "cert_scope": _SCOPE_BY_CODE.get(code, scope),
                "score": score,
                "source": source,
            }

    kw_code = keyword_hint.get("certificate_type_code") if keyword_hint else None
    kw_strength = keyword_hint.get("strength") if keyword_hint else None

    # Definitive: the certificate declares its own "Type code:" — trust it outright.
    if keyword_hint and kw_strength == "typecode" and kw_code:
        agree = any(c["certificate_type_code"] == kw_code for c in llm_candidates)
        return {
            "certificate_type_code": kw_code,
            "cert_scope": _SCOPE_BY_CODE.get(kw_code, keyword_hint.get("cert_scope") or "Vendor"),
            "method": "rerank:typecode",
            "confidence": "high",
            "score": 1.0,
            "agreement": agree,
            "rerank_reason": "declared_type_code",
        }

    for idx, cand in enumerate(llm_candidates):
        base = _LLM_CONF_SCORE.get(cand.get("confidence", "medium"), 0.72)
        penalty = _LLM_RANK_PENALTY[idx] if idx < len(_LLM_RANK_PENALTY) else 0.2
        _bump(
            cand["certificate_type_code"],
            cand.get("cert_scope", "Vendor"),
            base - penalty + _LLM_PREFERENCE_BONUS,
            "llm",
        )

    if keyword_hint and kw_code:
        _bump(
            kw_code,
            keyword_hint.get("cert_scope", "Vendor"),
            _KW_STRENGTH_SCORE.get(kw_strength, 0.65),
            "keyword",
        )

    if not scored:
        return None

    # Agreement bonus — both signals naming the same code is the strongest evidence.
    llm_codes = {c["certificate_type_code"] for c in llm_candidates}
    if kw_code and kw_code in llm_codes and kw_code in scored:
        scored[kw_code]["score"] = min(1.0, scored[kw_code]["score"] + _AGREEMENT_BONUS)
        scored[kw_code]["source"] = "llm+keyword"

    winner = max(scored.values(), key=lambda e: e["score"])
    return {
        "certificate_type_code": winner["certificate_type_code"],
        "cert_scope": winner["cert_scope"],
        "method": f"rerank:{winner['source']}",
        "confidence": _score_to_confidence(winner["score"]),
        "score": round(winner["score"], 3),
        "agreement": winner["source"] == "llm+keyword",
        "rerank_reason": "top_score",
    }


async def classify_compliance_certificate_hybrid(
    file_path: str,
    user_query: str | None = None,
    *,
    source_text: str | None = None,
    peek_file: bool = True,
    use_llm: bool = True,
) -> dict[str, Any] | None:
    """Hybrid detection — **LLM-primary + keyword, reconciled by re-ranking**.

    Runs the LLM classifier (ranked candidates) and the keyword pass concurrently, then
    fuses them via :func:`_rerank_candidates`. ``use_llm=False`` degrades to the keyword
    pass only (used by fast bulk pre-checks / when no API key). Returns the winning hint
    ``{certificate_type_code, cert_scope, method, confidence, score, agreement}`` or None.
    """
    # Peek text once for both the semantic and keyword paths.
    peek = source_text
    if peek is None and peek_file:
        try:
            peek = extract_text_from_upload(file_path, max_chars=6000)
        except Exception:  # noqa: BLE001
            peek = None

    # PRIMARY: semantic classification — embed the document + the full country-pack
    # catalogue (all 54 types) and match by cosine similarity, LLM-confirmed. No per-type
    # keyword is required, so any pack type (TM44, DEC, …) is classifiable automatically.
    if use_llm and (settings.openai_api_key or "").strip():
        from .semantic_classify import classify_semantic

        semantic = await classify_semantic(source_text=peek, file_name=Path(file_path).name)
        if semantic and semantic.get("certificate_type_code"):
            log.info(
                "single_door.compliance.semantic",
                file=Path(file_path).name,
                winner=semantic["certificate_type_code"],
                scope=semantic["cert_scope"],
                method=semantic["method"],
                confidence=semantic["confidence"],
            )
            return semantic

    # FALLBACK: keyword + Claude re-rank (embeddings unavailable / no OpenAI key / no match).
    keyword_hint = classify_compliance_certificate_doc(
        file_path, user_query, source_text=peek, peek_file=False
    )

    if not use_llm or not (settings.anthropic_api_key or "").strip():
        if keyword_hint:
            keyword_hint.setdefault("method", "keyword")
        return keyword_hint

    llm_candidates = await classify_compliance_certificate_llm(
        file_path, user_query, source_text=source_text
    )
    result = _rerank_candidates(llm_candidates, keyword_hint)
    if result:
        log.info(
            "single_door.compliance.rerank",
            file=Path(file_path).name,
            winner=result["certificate_type_code"],
            scope=result["cert_scope"],
            method=result["method"],
            score=result.get("score"),
            agreement=result.get("agreement"),
            llm_candidates=[c["certificate_type_code"] for c in llm_candidates],
            keyword=(keyword_hint or {}).get("certificate_type_code"),
        )
    return result


def _extract_company_name(extracted: dict[str, Any], text: str, filename: str) -> str | None:
    for key in ("Company name", "company_name", "vendor_name", "Contractor", "Business name"):
        val = extracted.get(key)
        if val and str(val).strip():
            return str(val).strip()[:255]
    m = re.search(
        r"(?:company|contractor|business|trading)\s*name\s*[:|]?\s*([A-Z][^\n|]{2,80})",
        text or "",
        re.I,
    )
    if m:
        return m.group(1).strip()[:255]
    # BPCA membership certificate layout:
    #   Membership number: M15/035
    #   Rentokil Pest Control
    #   is a Full Member of the British Pest Control Association
    m_bpca = re.search(
        r"(?:membership\s*(?:number|no\.?)|bpca\s*member\s*(?:no\.?|number))"
        r"\s*:?\s*[A-Z0-9/\s]{0,20}\s*"
        r"\n+\s*([A-Z][A-Za-z0-9 &.'\-]{2,80}?)\s*\n+\s*is a\s+(?:Full\s+)?Member",
        text or "",
        re.I,
    )
    if m_bpca:
        return m_bpca.group(1).strip()[:255]
    # Filename token after pack number e.g. 53_BPCA_PestGuard
    stem = re.sub(r"[_\-.]+", " ", Path(filename).stem)
    m2 = re.search(
        r"\b(?:bpca|niceic|napit|bafe|refcom|chas|sia)\s+([A-Za-z][A-Za-z0-9 &\-]{2,40})\b",
        stem,
        re.I,
    )
    if m2:
        cand = m2.group(1).strip()
        # Reject certificate boilerplate — a filename like
        # "sia_acs_certificate_of_approval_september" must NOT become the company name.
        if not re.search(
            r"\b(certificate|approval|scheme|licen[cs]e|acs|report|inspection|"
            r"of|the|and|for)\b",
            cand,
            re.I,
        ):
            return cand
    if re.search(r"pest[\s_-]?guard", filename, re.I):
        return "PestGuard"
    if re.search(r"rentokil", f"{filename} {text or ''}", re.I):
        return "Rentokil Pest Control"
    return None


async def route_compliance_certificate_upload(
    *,
    file_path: str,
    organization_id: str | None = None,
    user_query: str | None = None,
    document_id: str | None = None,
    source_text: str | None = None,
    hint: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """
    If the upload is a compliance certificate, call Feature A extract (+ draft upsert).
    Returns None when not a Feature A document.

    ``hint`` — a precomputed {certificate_type_code, cert_scope} from the caller's hybrid
    classify, reused so the (possibly LLM) classification is not run twice per file.
    """
    # Peek with fast text-layer first for classification; upgrade to Claude/OpenAI
    # full-PDF vision extract when the layer is thin (scanned certificates).
    peek = source_text if source_text is not None else extract_text_from_upload(
        file_path, max_chars=4_000
    )
    # Reuse the caller's hybrid result; only classify here when none was supplied
    # (keyword pass + LLM fallback) so a scanned/oddly-named cert still routes.
    if hint is None:
        hint = await classify_compliance_certificate_hybrid(
            file_path, user_query, source_text=peek, peek_file=False
        )
    if hint is None:
        return None

    doc = await extract_document_text(file_path, max_chars=50_000)
    text = doc.get("text") or peek or ""
    extract_method = doc.get("method") or "text_layer"

    base = settings.operations_intelligence_base_url.rstrip("/")
    name = Path(file_path).name
    code = hint["certificate_type_code"]
    scope = hint["cert_scope"]

    # Insurance disambiguation: the semantic classifier confuses Public Liability with
    # Employers' Liability (near-identical titles → high cosine similarity). The certificate
    # wording is explicit, so correct it deterministically from the document text.
    _low = (text or "").lower()
    _is_insurance = "liability insurance" in _low or "certificate of insurance" in _low or any(
        t in code.upper() for t in ("INSUR", "_EL", "_PL", "LIABIL")
    )
    if _is_insurance:
        _has_pl = ("public liability" in _low) or ("third party liability" in _low) or (
            "third-party liability" in _low
        )
        _has_el = ("employers' liability" in _low) or ("employers liability" in _low) or (
            "employer's liability" in _low
        )
        if _has_pl and not _has_el:
            code, scope = "CONTRACTOR_PL_INSURANCE", "Vendor"
        elif _has_el and not _has_pl and "EL" not in code.upper():
            code, scope = "EL_INSURANCE", scope

    log.info(
        "single_door.compliance.route_start",
        file=name,
        certificate_type_code=code,
        cert_scope=scope,
        ops_base=base,
        text_chars=len(text or ""),
        extract_method=extract_method,
    )

    # ── Document forensics / authenticity gate (before extract) ───────
    forensics = await analyze_certificate_document(
        file_path=file_path,
        source_text=text,
        certificate_type_code=code,
    )
    log.info(
        "single_door.compliance.forensics",
        file=name,
        verdict=forensics.get("verdict"),
        risk_score=forensics.get("risk_score"),
        allow_extract=forensics.get("allow_extract", True),
    )

    if forensics.get("allow_extract") is False:
        f_line = forensics_summary_line(forensics) or (
            "**Document forensics:** FAIL (risk n/a)"
        )
        auth = forensics.get("authenticity_warning") or (
            "Document forensics: HIGH RISK — PDF failed structural authenticity checks. "
            "PM retains override authority (soft authenticity gate)."
        )
        return {
            "kind": "compliance_certificate",
            "ok": False,
            "summary": (
                f"### Compliance authenticity check — `{name}`\n"
                f"- **Status:** Not ingested (extract blocked)\n"
                f"- {f_line}\n"
                f"- **Authenticity:** {auth}\n"
                f"- Upload a readable certificate PDF and try again."
            ),
            "result": None,
            "forensics": forensics,
            "certificate_type_code": code,
            "cert_scope": scope,
            "document_id": document_id,
            "source_filename": name,
        }

    # Route this certificate to its country's CountryPack (US/UAE/UK) so it is classified,
    # stored and verified against the correct country.
    cert_country = _detect_country_single_door(
        code, text, name, (hint or {}).get("country_code")
    )
    log.info("single_door.compliance.country_routed", file=name, code=code, country=cert_country)

    try:
        extract_payload: dict[str, Any] = {
            "certificate_type_code": code,
            "cert_scope": scope,
            "source_text": text,
            "country_code": cert_country,
            "extract_method": extract_method,
        }
        # ALWAYS send the raw PDF so ops Claude extract can vision-read the complete
        # document. source_text is often a partial/garbled doc-rag chunk (or empty for
        # scanned image certs), which otherwise leaves every field empty and forces the
        # company to fall back to the filename (e.g. SIA -> "acs certificate of approval
        # september"). The full PDF lets ops extract the real Company name / number / dates.
        try:
            import base64

            pdf_bytes = Path(file_path).read_bytes()
            if 0 < len(pdf_bytes) <= 20 * 1024 * 1024:
                extract_payload["pdf_base64"] = base64.standard_b64encode(
                    pdf_bytes
                ).decode("ascii")
        except Exception as bexc:  # noqa: BLE001
            log.warning(
                "single_door.compliance.pdf_attach_failed",
                error=str(bexc)[:160],
            )
        resp = await _request(
            "POST",
            base,
            "/api/compliance/extract",
            service="operations_intelligence",
            timeout=120.0,
            json=extract_payload,
        )
        body = resp.json()
        extracted = body.get("extracted") or {}
        upsert_body = None
        cert_id = None

        extracted_num = (
            extracted.get("certificate_number")
            # Insurance certs — the policy number is the certificate number.
            or extracted.get("Policy no.")
            or extracted.get("Policy number")
            or extracted.get("Policy Number")
            or extracted.get("policy_number")
            or extracted.get("BPCA member no.")
            or extracted.get("Membership number")
            or extracted.get("Member number")
        )
        extracted_exp = (
            extracted.get("expiry_date")
            or extracted.get("Date of expiry of insurance policy")
            or extracted.get("Period of insurance (to)")
            or extracted.get("Review date")
            or extracted.get("Expiry date")
            or extracted.get("Certificate valid until")
        )
        extracted_issue = (
            extracted.get("issue_date")
            or extracted.get("Date of commencement of insurance policy")
            or extracted.get("Period of insurance (from)")
            or extracted.get("Date")
            or extracted.get("Membership year")
        )
        company = _extract_company_name(extracted, text or "", name)
        accreditation_number = (
            extracted.get("inspector_accreditation_number")
            or extracted.get("accreditation_number")
            or extracted.get("BPCA member no.")
            or extracted_num
        )

        # ── Vendor registration + compliance search (on-platform + Verify-now) ──
        vendor_registration: dict[str, Any] | None = None
        try:
            vr = await _request(
                "POST",
                base,
                "/api/compliance/vendor-registration-check",
                service="operations_intelligence",
                timeout=45.0,
                json={
                    "vendor_name": company,
                    "certificate_type_code": body.get("certificate_type_code") or code,
                    "accreditation_number": accreditation_number,
                    "cert_scope": body.get("cert_scope") or scope,
                    "country_code": cert_country,
                    "organization_id": organization_id,
                },
            )
            vendor_registration = vr.json()
            log.info(
                "single_door.compliance.vendor_registration",
                file=name,
                vendor=company,
                platform_compliance=(vendor_registration or {}).get(
                    "platform_compliance"
                ),
                is_compliant=(vendor_registration or {}).get("is_compliant"),
            )
        except Exception as vexc:  # noqa: BLE001
            log.warning(
                "single_door.compliance.vendor_registration_failed",
                error=str(vexc)[:200],
            )
            vendor_registration = {
                "ok": False,
                "platform_compliance": "check_unavailable",
                "is_compliant": None,
                "status_message": (
                    f"Vendor registration check unavailable: {str(vexc)[:120]}"
                ),
                "authenticity_warning": (
                    "Vendor registration/compliance search could not be completed — "
                    "please Verify now against the public register. "
                    "PM retains override authority (soft authenticity gate)."
                ),
                "requires_pm_review": True,
            }

        if scope != "Vendor":
            # Building certificates (EPC/DEC/TM44) have no contractor/company — discard any
            # vendor registration result so it can never attach a "vendor/company name
            # missing" authenticity warning to a building cert.
            vendor_registration = None

        upsert_skipped_reason: str | None = None
        if body.get("ok") and (extracted_exp or extracted_num):
            upsert_payload: dict[str, Any] = {
                "cert_scope": body.get("cert_scope") or scope,
                "certificate_type_code": body.get("certificate_type_code") or code,
                "certificate_number": extracted_num,
                "issue_date": extracted_issue,
                "expiry_date": extracted_exp,
                "inspector_name": extracted.get("inspector_name")
                or extracted.get("Assessor name and qualification"),
                "inspector_accreditation_number": extracted.get(
                    "inspector_accreditation_number"
                )
                or accreditation_number,
                "result": extracted.get("result")
                or extracted.get("Pass/Refer/Fail")
                or extracted.get("Pass")
                or extracted.get("Fail"),
                "defects_found": extracted.get("defects_found")
                or extracted.get("Failures")
                or extracted.get("Remedial actions"),
                "remedial_actions": extracted.get("remedial_actions")
                or extracted.get("Remedial actions"),
                "organization_id": organization_id,
                "document_id": document_id,
                "country_code": cert_country,
                "field_confidence": body.get("field_confidence") or {},
                "raw_metadata": {
                    "source_filename": name,
                    "source_file_path": file_path,
                    "single_door": True,
                    # How this type was decided (LLM-primary + keyword re-rank) — audit trail.
                    "classification_method": hint.get("method"),
                    "classification_confidence": hint.get("confidence"),
                    "classification_score": hint.get("score"),
                    "classification_agreement": hint.get("agreement"),
                    "company_name": company,
                    "vendor_name": company,
                    # Insurer (FCA-authorised underwriter) for insurance certs — distinct
                    # from the policyholder above; §8 FCA verify searches the register by
                    # this, not the policyholder.
                    "insurer_name": extracted.get("insurer_name"),
                    "forensics": forensics,
                    "forensics_verdict": forensics.get("verdict"),
                    "forensics_risk_score": forensics.get("risk_score"),
                    "requires_forensics_review": bool(
                        forensics.get("requires_pm_review")
                    ),
                    "extract_method": extract_method,
                    "vendor_registration": vendor_registration,
                    "vendor_platform_compliance": (vendor_registration or {}).get(
                        "platform_compliance"
                    ),
                    "vendor_is_compliant": (vendor_registration or {}).get(
                        "is_compliant"
                    ),
                    "verify_now_url": (vendor_registration or {}).get(
                        "verification_url"
                    ),
                },
                "source_file_path": file_path,
                "confirmed_by_pm": False,
            }
            if company:
                upsert_payload["vendor_name"] = company
            try:
                up = await _request(
                    "POST",
                    base,
                    "/api/compliance/certificates",
                    service="operations_intelligence",
                    timeout=60.0,
                    json=upsert_payload,
                )
                upsert_body = up.json()
                cert_id = (upsert_body or {}).get("id") or (
                    (upsert_body or {}).get("certificate") or {}
                ).get("id")
                if not cert_id:
                    upsert_skipped_reason = (
                        f"ops-intelligence returned no certificate id "
                        f"(ok={upsert_body.get('ok') if isinstance(upsert_body, dict) else None}, "
                        f"error={upsert_body.get('error') if isinstance(upsert_body, dict) else None})"
                    )
            except Exception as uexc:  # noqa: BLE001
                upsert_skipped_reason = f"upsert HTTP/error: {uexc}"
                log.warning(
                    "single_door.compliance.upsert_failed",
                    error=str(uexc)[:200],
                )
        elif body.get("ok"):
            upsert_skipped_reason = (
                "extract returned no certificate_number or expiry_date — draft upsert skipped"
            )

        draft_ok = bool(cert_id) and bool((upsert_body or {}).get("ok", True))
        # Announce the matched compliance pack explicitly (Building vs Vendor).
        scope_label = "Vendor compliance" if scope == "Vendor" else "Building compliance"
        type_name = ((upsert_body or {}).get("pack") or {}).get("certificate_type_name") or code
        space_link = "[Open Compliance](/compliance)"
        missing = body.get("missing_required") or []
        field_conf = body.get("field_confidence") or {}
        conf_vals = [
            float(v)
            for v in (field_conf.values() if isinstance(field_conf, dict) else [])
            if isinstance(v, (int, float))
        ]
        avg_conf = (sum(conf_vals) / len(conf_vals)) if conf_vals else None
        source_md = (
            f"[{name}](doc:{document_id}?c={avg_conf:.2f})"
            if document_id and avg_conf is not None
            else (f"[{name}](doc:{document_id})" if document_id else f"`{name}`")
        )
        # Record state: a saved draft awaits PM confirmation. If the certificate is already
        # expired, that confirmation is really for a RENEWAL — say so explicitly.
        from datetime import date as _date_cls

        cert_status_lc = str(
            ((upsert_body or {}).get("certificate") or {}).get("status") or ""
        ).lower()
        try:
            _expired_by_date = bool(extracted_exp) and (
                _date_cls.fromisoformat(str(extracted_exp)[:10]) < _date_cls.today()
            )
        except (ValueError, TypeError):
            _expired_by_date = False
        expired = cert_status_lc == "lapsed" or _expired_by_date
        if draft_ok:
            record_line = (
                "- **Record:** Draft (awaiting confirmation for renewal)"
                if expired
                else "- **Record:** Draft (awaiting confirmation)"
            )
        else:
            record_line = "- **Status:** Extracted (draft not persisted)"
        lines = [
            f"### {scope_label} — {type_name}",
            f"- **Source:** {source_md}",
            f"- **Type:** {scope}",
            record_line,
        ]
        if company:
            lines.append(f"- **Vendor / company:** {company}")
        if extracted_num:
            lines.append(f"- **Certificate / membership #:** `{extracted_num}`")
        if extracted_issue:
            lines.append(f"- **Issue / membership start:** {extracted_issue}")
        if extracted_exp:
            lines.append(f"- **Expiry / valid until:** {extracted_exp}")
        f_line = forensics_summary_line(forensics)
        if f_line:
            lines.append(f"- {f_line}")
        if forensics.get("authenticity_warning"):
            lines.append(f"- **Authenticity:** {forensics['authenticity_warning']}")
        # §8 accreditation verification result — computed here so the vendor-registration
        # line below can be reconciled against it.
        ver = None
        if isinstance(upsert_body, dict):
            ver = (
                (upsert_body.get("certificate") or {}).get("raw_metadata", {}) or {}
            ).get("verification") or upsert_body.get("verification")
        accreditation_verified = isinstance(ver, dict) and ver.get("verified") is True

        # Vendor registration / compliance search result — reconciled with §8 verification so
        # a genuinely-verified certificate never reads as "not compliant / unknown vendor".
        if vendor_registration:
            pc = vendor_registration.get("platform_compliance") or "unknown"
            ic = vendor_registration.get("is_compliant")
            reg_name = vendor_registration.get("issuing_body") or "the issuing register"
            if accreditation_verified and ic is not True and pc in (
                "unknown_vendor",
                "no_accreditation_on_record",
                "unknown",
                None,
            ):
                # Accreditation IS verified against the official register — the vendor just
                # isn't onboarded in the platform yet. Say that clearly instead of the
                # alarming "unknown_vendor / not compliant".
                lines.append(
                    f"- **Vendor registration:** new vendor — accreditation **verified** "
                    f"against {reg_name}; the vendor is registered on confirm."
                )
            else:
                ic_label = "YES" if ic is True else ("NO" if ic is False else "UNKNOWN")
                lines.append(
                    f"- **Vendor registration:** {pc} — on-platform compliant: **{ic_label}**"
                )
                if vendor_registration.get("status_message"):
                    lines.append(
                        f"- **Vendor compliance:** {vendor_registration['status_message']}"
                    )
                vurl = vendor_registration.get("verification_url")
                if vurl:
                    body_name = vendor_registration.get("issuing_body") or "register"
                    lines.append(f"- **Verify now ({body_name}):** {vurl}")
                elif vendor_registration.get("authenticity_warning"):
                    lines.append(
                        f"- **Authenticity:** {vendor_registration['authenticity_warning']}"
                    )
        if avg_conf is not None:
            lines.append(f"- **Extraction confidence:** **{avg_conf:.0%}**")
        if missing:
            lines.append(f"- **Missing required fields:** {', '.join(str(m) for m in missing)}")
        if draft_ok:
            lines.append(
                f"- Next: confirm fields in {space_link} to finalise "
                "(draft until PM confirmation)."
            )
        else:
            reason = upsert_skipped_reason or "Draft upsert did not return an id"
            lines.append(f"- **Why draft was not saved:** {reason}")
            lines.append(
                f"- Fix: re-upload after extraction is fixed, or enter fields manually in {space_link}."
            )
        # CCC §2 / §8 — surface verification status (ver was computed above).
        if isinstance(ver, dict) and (ver.get("status") or ver.get("verified") is not None):
            vstatus = ver.get("status") or (
                "verified" if ver.get("verified") is True else "not_checked"
            )
            lines.append(f"- **Accreditation verification status:** `{vstatus}`")
            if ver.get("channel"):
                lines.append(f"- **Accreditation verification channel:** `{ver.get('channel')}`")
            if ver.get("source_url"):
                lines.append(f"- **Register:** {ver.get('source_url')}")
        # Next steps — for a lapsed / expiring certificate the §5 expiry ladder has queued a
        # notification (renewal email draft in Approvals / the alert list); surface that.
        cert_status = (
            ((upsert_body or {}).get("certificate") or {}).get("status")
            if isinstance(upsert_body, dict)
            else None
        )
        if cert_status and cert_status not in ("Current", "Not on record"):
            lines.append(
                f"- **Next steps — notification of ladder:** {cert_status} — an alert-ladder "
                f"notification is queued; review and send the renewal email from Approvals / "
                f"the alert list."
            )
        mem_qid = (
            (upsert_body or {}).get("membership_queue_item_id")
            if isinstance(upsert_body, dict)
            else None
        )
        if document_id:
            # Auto-keep: the PDF is already indexed in the vector DB on ingest. No hold,
            # no "Keep / Remove" prompt — the PM only manages membership on demand. (Do
            # NOT emit VECTOR_MEMBERSHIP_PENDING, which is what renders the inline prompt.)
            lines.append(
                f"- **Vector DB:** indexed (kept) — manage membership from the "
                f"Compliance Saved Space if you need to remove it."
            )
        summary = "\n".join(lines)
        # What the PM can do about the certificate they just uploaded. Same rule set the
        # query path uses, so "Mark reviewed" means the same thing whether the certificate
        # was found by asking or by uploading.
        ingest_offers = offers_for_row(
            row_from_ingest(
                cert_id=str(cert_id or ""),
                certificate_type_code=code,
                type_name=type_name,
                owner=company or "",
                status=((upsert_body or {}).get("certificate") or {}).get("status"),
                expiry_date=extracted_exp,
                forensics=forensics,
                persisted=draft_ok,
            )
        )
        if ingest_offers:
            lines.append("")
            lines.append("**What you can do now**")
            for _o in ingest_offers:
                lines.append(f"- **{_o['label']}** — {_o['reason']}")
            summary = "\n".join(lines)
        return {
            "kind": "compliance_certificate",
            "ok": bool(body.get("ok", True)),
            "summary": summary,
            "offers": ingest_offers,
            "result": body,
            "upsert": upsert_body,
            "forensics": forensics,
            "vendor_registration": vendor_registration,
            "certificate_type_code": code,
            "cert_scope": scope,
            "certificate_id": cert_id,
            "document_id": document_id,
            "confidence": avg_conf,
            "source_filename": name,
            "verification": ver if isinstance(ver, dict) else None,
            "membership_pending": False,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("single_door.compliance.route_failed", error=str(exc)[:300])
        return {
            "kind": "compliance_certificate",
            "ok": False,
            "summary": f"Compliance A route failed for {name}: {exc}",
            "result": None,
            "forensics": forensics,
        }
