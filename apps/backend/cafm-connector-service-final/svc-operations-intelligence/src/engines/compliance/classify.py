"""CCC §3 — classify an uploaded compliance PDF onto a CountryPack certificate type.

`/extract` needs a `certificate_type_code` up front. When the UDR chat uploads a raw
PDF without one (the common case for the 5-file batch — §3.1), this resolves it against
the seeded UK pack: caller-supplied code first, then Claude over the document text/PDF,
then a keyword heuristic. Returns the pack's own `certificate_type_code` (the code
`get_pack_type` / `extract` / `upsert_certificate` accept) plus its `cert_scope`.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from .country_pack import get_pack_type, list_pack_types
from .verification_sources import canonicalize_type_code

log = get_logger(__name__)

# Brand / regulation keywords that rarely appear in the pack type NAME but strongly
# imply a type. Maps a lowercase keyword → the canonical CCC code (matched against the
# pack via canonicalize on both sides, so pack-code or CCC-code seeding both resolve).
_KEYWORD_HINTS: dict[str, str] = {
    "cp17": "GAS_CP17",
    "gas safe": "GASSAFE_COMPANY",
    "gassafe": "GASSAFE_COMPANY",
    "acs": "ACS_GAS_CARD",
    "niceic": "NICEIC_CONTRACTOR",
    "napit": "NAPIT_REG",
    "eicr": "EICR",
    "18th edition": "CG_2382",
    "2382": "CG_2382",
    "emergency lighting": "EMERGENCY_LIGHTING",
    "bs 5266": "EMERGENCY_LIGHTING",
    "fire alarm": "FIRE_ALARM_SVC",
    "bs 5839": "FIRE_ALARM_SVC",
    "sprinkler": "SPRINKLER_TEST",
    "bs en 12845": "SPRINKLER_TEST",
    "fire door": "FIRE_DOOR",
    "fire risk assessment": "FRA",
    "bafe sp203": "BAFE_SP203",
    "bafe sp101": "BAFE_SP101",
    "nsi gold": "NSI_GOLD_FIRE",
    "legionella": "LEGIONELLA_RA",
    "acop l8": "LEGIONELLA_RA",
    "hsg274": "LEGIONELLA_RA",
    "cold water": "CWST_INSPECTION",
    "loler": "LOLER_LIFT",
    "leia": "LEIA_MEMBER",
    "pressure system": "WSE_PRESSURE",
    "written scheme": "WSE_PRESSURE",
    "asbestos survey": "ASBESTOS_SURVEY",
    "asbestos register": "ASBESTOS_REGISTER",
    "asbestos licence": "ASBESTOS_LICENCE",
    "hse licen": "ASBESTOS_LICENCE",
    "ukas": "UKAS_ASBESTOS",
    "p402": "BOHS_P40x",
    "p403": "BOHS_P40x",
    "p404": "BOHS_P40x",
    "lca": "LCA_REG",
    "epc": "EPC",
    "energy performance": "EPC",
    "display energy": "DEC",
    "tm44": "TM44",
    "air conditioning inspection": "TM44",
    "esos": "ESOS",
    "f-gas": "FGAS_LEAK",
    "f gas": "FGAS_LEAK",
    "refcom": "REFCOM_COMPANY",
    "2079": "CG_2079",
    "pat": "PAT",
    "portable appliance": "PAT",
    "boiler service": "BOILER_SVC",
    "health & safety policy": "HS_POLICY",
    "health and safety policy": "HS_POLICY",
    "risk assessment register": "RISK_REGISTER",
    "employers' liability": "EL_INSURANCE",
    "employers liability": "EL_INSURANCE",
    "public liability": "CONTRACTOR_PL",
    "higher-risk building": "HRB_REGISTRATION",
    "building safety case": "SAFETY_CASE",
    "chas": "CHAS_SSIP",
    "ssip": "CHAS_SSIP",
    "iso 9001": "ISO_9001",
    "iso 14001": "ISO_14001",
    "sia acs": "SIA_ACS",
    "approved contractor scheme": "SIA_ACS",
    "sia licen": "SIA_LICENCE",
    "bpca": "BPCA_MEMBER",
    "pest control": "BPCA_MEMBER",
    "basis prompt": "PESTICIDE_PAx",
    "pa1": "PESTICIDE_PAx",
    "pa6": "PESTICIDE_PAx",
}

# Name tokens too generic to carry signal on their own.
_STOP_TOKENS = {
    "certificate",
    "report",
    "record",
    "inspection",
    "registration",
    "individual",
    "company",
    "test",
    "scheme",
    "annual",
    "membership",
    "competency",
    "card",
    "and",
    "the",
    "of",
    "for",
    "&",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


# Country signals for auto-routing an ingested document to the right CountryPack. Matched on a
# lowercased haystack (filename + extracted text). Order is priority only for ties.
_COUNTRY_SIGNALS: dict[str, tuple[str, ...]] = {
    "US": (
        "nfpa", "asme", "epa ", "osha", "ansi", "ashrae", "ul listed", "section 608",
        "niosh", "united states", "u.s.", "acord", "certificate of liability", "backflow",
        "nicet", "national board", "40 cfr", "state of ", "workers compensation",
    ),
    "UAE": (
        "dcd", "dewa", "esma", "civil defence", "civil defense", "dubai", "abu dhabi",
        "oshad", "trakhees", "united arab emirates", " uae", "municipality", "sharjah",
    ),
    "UK": (
        "gas safe", "niceic", "bafe", "napit", "bs 5839", "bs7671", "bs 7671",
        "united kingdom", "hse", "ukas", "fire risk assessment", "landlord", "sia acs",
        "electrical installation condition report",
    ),
}


async def _seeded_countries(session: AsyncSession) -> set[str]:
    try:
        rows = (
            await session.execute(
                text(
                    "SELECT DISTINCT country_code FROM plenum_cafm.country_certificate_packs"
                )
            )
        ).scalars().all()
        return {str(r).upper() for r in rows if r}
    except Exception:  # noqa: BLE001
        return {"UK"}


async def _detect_country(session: AsyncSession, haystack: str, *, fallback: str) -> str:
    """Pick the country whose pack the document most likely belongs to, among seeded packs."""
    seeded = await _seeded_countries(session)
    text_l = (haystack or "").lower()
    best, best_score = fallback, 0
    for country, sigs in _COUNTRY_SIGNALS.items():
        if country not in seeded:
            continue
        score = sum(1 for s in sigs if s in text_l)
        if score > best_score:
            best, best_score = country, score
    if best_score > 0:
        return best
    return fallback if fallback.upper() in seeded else (next(iter(seeded)) if seeded else "UK")


async def classify_certificate_type(
    session: AsyncSession,
    *,
    source_text: str | None = None,
    file_name: str | None = None,
    pdf_base64: str | None = None,
    country_code: str = "UK",
    certificate_type_code: str | None = None,
    cert_scope: str | None = None,
) -> dict[str, Any]:
    """Resolve a pack certificate type for one document.

    Returns ``{ok, certificate_type_code, certificate_type_name, cert_scope,
    confidence, method}``. ``certificate_type_code`` is the pack's own code so it feeds
    straight into ``extract_certificate_fields`` / ``upsert_certificate``.
    """
    haystack = f"{file_name or ''}\n{(source_text or '')[:8000]}"
    # Country routing: an explicit non-UK hint wins; otherwise auto-detect the country from the
    # document so a US/UAE certificate is classified + verified against ITS country's pack.
    hint = (country_code or "UK").strip().upper()
    effective_country = (
        hint if hint and hint != "UK"
        else await _detect_country(session, haystack, fallback=hint or "UK")
    )

    # 1) Caller already knows the type — just resolve the pack row (raw then canonical).
    if certificate_type_code and str(certificate_type_code).strip():
        pack = await _resolve_pack(session, certificate_type_code, effective_country)
        if pack:
            return _hit(pack, cert_scope, "high", "provided", effective_country)

    packs = await list_pack_types(session, country_code=effective_country)
    if not packs:
        return {
            "ok": False,
            "error": f"No {effective_country} country pack seeded.",
            "certificate_type_code": None,
            "cert_scope": cert_scope,
            "country_code": effective_country,
        }

    # 2) Claude classifier when a key is set and we have text or a PDF to read.
    if settings.anthropic_api_key and (
        (source_text and source_text.strip()) or pdf_base64
    ):
        picked = await _claude_classify(
            packs=packs,
            source_text=source_text or "",
            file_name=file_name,
            pdf_base64=pdf_base64 if not (source_text and source_text.strip()) else None,
        )
        if picked:
            pack = await _resolve_pack(session, picked, effective_country)
            if pack:
                return _hit(pack, cert_scope, "high", "claude", effective_country)

    # 3) Keyword heuristic fallback (works for scanned certs with an OCR text layer).
    pack, confidence = _heuristic_pick(packs, haystack)
    if pack:
        return _hit(pack, cert_scope, confidence, "heuristic", effective_country)

    return {
        "ok": False,
        "error": "Could not classify certificate type — confirm the type manually",
        "certificate_type_code": None,
        "cert_scope": cert_scope,
        "method": "unresolved",
        "country_code": effective_country,
    }


async def _resolve_pack(
    session: AsyncSession, code: str, country_code: str
) -> Any | None:
    """Match a pack row by the raw code, then by the canonicalised code."""
    pack = await get_pack_type(session, code, country_code=country_code)
    if pack:
        return pack
    canon = canonicalize_type_code(code)
    if canon and canon != code:
        pack = await get_pack_type(session, canon, country_code=country_code)
    return pack


def _hit(
    pack: Any,
    cert_scope: str | None,
    confidence: str,
    method: str,
    country_code: str | None = None,
) -> dict[str, Any]:
    scope = _norm_scope(cert_scope) or pack.certificate_scope
    return {
        "ok": True,
        "certificate_type_code": pack.certificate_type_code,
        "certificate_type_name": pack.certificate_type_name,
        "cert_scope": scope,
        "confidence": confidence,
        "method": method,
        "country_code": country_code or getattr(pack, "country_code", None),
    }


def _norm_scope(scope: str | None) -> str | None:
    if not scope:
        return None
    s = str(scope).strip().lower()
    if s.startswith("build"):
        return "Building"
    if s.startswith("vend") or s.startswith("contract"):
        return "Vendor"
    return None


def _heuristic_pick(packs: list[Any], haystack: str) -> tuple[Any | None, str]:
    hay = haystack.lower()
    by_canon: dict[str, Any] = {}
    for p in packs:
        canon = canonicalize_type_code(p.certificate_type_code) or p.certificate_type_code
        by_canon.setdefault(canon.upper(), p)

    scores: dict[int, float] = {}
    # Keyword hints — strong signal.
    for kw, canon in _KEYWORD_HINTS.items():
        if kw in hay:
            target = by_canon.get(canon.upper())
            if target is not None:
                scores[id(target)] = scores.get(id(target), 0.0) + 3.0

    # Name-token overlap — softer signal.
    for p in packs:
        name_tokens = [t for t in _tokens(p.certificate_type_name) if t not in _STOP_TOKENS]
        hits = sum(1 for t in set(name_tokens) if len(t) > 2 and t in hay)
        if hits:
            scores[id(p)] = scores.get(id(p), 0.0) + hits

    if not scores:
        return None, "low"

    best_id = max(scores, key=lambda k: scores[k])
    best = next((p for p in packs if id(p) == best_id), None)
    top = scores[best_id]
    ordered = sorted(scores.values(), reverse=True)
    gap = top - (ordered[1] if len(ordered) > 1 else 0.0)
    confidence = "medium" if top >= 3.0 and gap >= 1.0 else "low"
    return best, confidence


async def _claude_classify(
    *,
    packs: list[Any],
    source_text: str,
    file_name: str | None,
    pdf_base64: str | None,
) -> str | None:
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        catalogue = "\n".join(
            f"- {p.certificate_type_code} | {p.certificate_type_name} "
            f"| {p.certificate_scope}"
            for p in packs
        )
        prompt = (
            "You are classifying a UK facilities-management compliance document.\n"
            "Pick the single best matching certificate_type_code from this catalogue "
            "(building = property/PM; vendor = contractor accreditation):\n"
            f"{catalogue}\n\n"
            'Return ONLY JSON: {"certificate_type_code": "<code>", '
            '"confidence": "high|medium|low"}. Use a code exactly as listed, or '
            '"UNKNOWN" if none fit.'
        )
        if file_name:
            prompt += f"\n\nFile name: {file_name}"

        content: list[dict[str, Any]] = []
        if pdf_base64 and len(pdf_base64) < 28_000_000:
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_base64,
                    },
                }
            )
            model = "claude-haiku-4-5-20251001"
        else:
            if source_text.strip():
                prompt += f"\n\nDocument text (may be partial/OCR):\n{source_text[:9000]}"
            model = "claude-haiku-4-5-20251001"
        content.append({"type": "text", "text": prompt})

        resp = await client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text if resp.content else "{}"
        raw = re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        data = json.loads(raw)
        code = str(data.get("certificate_type_code") or "").strip()
        if not code or code.upper() == "UNKNOWN":
            return None
        return code
    except Exception as exc:  # noqa: BLE001 — classification is best-effort
        log.warning("classify.claude_failed", error=str(exc)[:200])
        return None
