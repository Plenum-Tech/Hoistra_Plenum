"""Semantic compliance-certificate classifier.

Instead of hardcoded keyword regexes, this embeds the uploaded document and every
certificate type in the country pack (all 54, fetched live from
svc-operations-intelligence), ranks types by cosine similarity, and lets the LLM confirm
the best match from the top candidates. Any pack type is therefore classifiable without a
per-type keyword — a new type is picked up automatically as soon as it is in the pack.
"""
from __future__ import annotations

import json
import math
import re
import time
from typing import Any

import httpx
import structlog

from ..config import settings
from ..llm_factory import _supports_temperature

log = structlog.get_logger(__name__)

_EMBED_MODEL = "text-embedding-3-small"
_CATALOGUE_TTL = 3600  # re-fetch + re-embed the catalogue at most hourly
# Cache: (fetched_at, [{"code","scope","descriptor","embedding"}])
_catalogue_cache: tuple[float, list[dict[str, Any]]] | None = None


def _openai_client():
    import openai

    base = (settings.openai_api_base or "").strip() or None
    return openai.AsyncOpenAI(api_key=settings.openai_api_key, base_url=base)


async def _embed(texts: list[str]) -> list[list[float]]:
    client = _openai_client()
    resp = await client.embeddings.create(model=_EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def _descriptor(t: dict[str, Any]) -> str:
    """A rich natural-language descriptor of a certificate type for embedding."""
    parts = [
        t.get("certificate_type_name"),
        f"{t.get('certificate_scope')} certificate",
        f"trade: {t.get('trade_category')}" if t.get("trade_category") else None,
        f"regulation: {t.get('regulation_reference')}" if t.get("regulation_reference") else None,
        f"issued by: {t.get('issuing_body')}" if t.get("issuing_body") else None,
        f"accreditation: {t.get('required_contractor_accreditation')}"
        if t.get("required_contractor_accreditation")
        else None,
    ]
    kf = (t.get("key_fields_schema") or {}).get("source_key_fields")
    if kf:
        parts.append(f"key fields: {kf}")
    return " — ".join(str(p) for p in parts if p)


# Every seeded country pack. Classifying against ALL of them lets a US or UAE document
# match its OWN pack type (e.g. US_EPA_608) instead of being forced onto the closest UK
# type (REFCOM_COMPANY), which then cascaded into UK-only forensics checklists and a UK
# register lookup. Country routing downstream uses the winning code's own country.
CATALOGUE_COUNTRIES: tuple[str, ...] = ("UK", "US", "UAE")


def _unwrap(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    for v in (data or {}).values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


async def _fetch_catalogue() -> list[dict[str, Any]]:
    base = settings.operations_intelligence_base_url.rstrip("/")
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0) as c:
        for country in CATALOGUE_COUNTRIES:
            try:
                r = await c.get(
                    f"{base}/api/compliance/country-pack",
                    params={"country_code": country},
                )
                for t in _unwrap(r.json()):
                    # Stamp the country so the winner carries it (packs may omit it).
                    t.setdefault("country_code", country)
                    out.append(t)
            except Exception as exc:  # noqa: BLE001 — one country must not break the rest
                log.warning(
                    "semantic_classify.catalogue_fetch_failed",
                    country=country,
                    error=str(exc)[:200],
                )
    return out


async def _catalogue_embeddings() -> list[dict[str, Any]]:
    global _catalogue_cache
    now = time.time()
    if _catalogue_cache and (now - _catalogue_cache[0]) < _CATALOGUE_TTL:
        return _catalogue_cache[1]
    items = await _fetch_catalogue()
    items = [t for t in items if t.get("certificate_type_code") and t.get("is_active", True)]
    descriptors = [_descriptor(t) for t in items]
    embeddings = await _embed(descriptors) if descriptors else []
    catalogue = [
        {
            "code": t["certificate_type_code"],
            "scope": t.get("certificate_scope"),
            "country": t.get("country_code") or "UK",
            "descriptor": descriptors[i],
            "embedding": embeddings[i],
        }
        for i, t in enumerate(items)
    ]
    _catalogue_cache = (now, catalogue)
    return catalogue


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def rank_by_similarity(
    *, source_text: str | None, file_name: str | None, top_k: int = 6
) -> list[dict[str, Any]]:
    """Return the top_k certificate types by cosine similarity to the document.

    Each item: {certificate_type_code, cert_scope, similarity}. Empty on failure so the
    caller can fall back to keyword matching."""
    if not settings.openai_api_key:
        return []
    try:
        catalogue = await _catalogue_embeddings()
        if not catalogue:
            return []
        query = f"{file_name or ''}\n{(source_text or '')[:6000]}".strip()
        query_emb = (await _embed([query]))[0]
        scored = sorted(
            (
                {
                    "certificate_type_code": c["code"],
                    "cert_scope": c["scope"],
                    "country_code": c.get("country") or "UK",
                    "similarity": round(_cosine(query_emb, c["embedding"]), 4),
                }
                for c in catalogue
            ),
            key=lambda x: x["similarity"],
            reverse=True,
        )
        return scored[:top_k]
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic_classify.rank_failed", error=str(exc)[:200])
        return []


async def classify_semantic(
    *, source_text: str | None, file_name: str | None, min_similarity: float = 0.30
) -> dict[str, str] | None:
    """Classify by cosine similarity over the full catalogue, then LLM-confirm the winner
    among the top candidates. Returns {certificate_type_code, cert_scope, confidence,
    method} or None when nothing is a plausible compliance certificate."""
    ranked = await rank_by_similarity(source_text=source_text, file_name=file_name, top_k=6)
    if not ranked:
        return None
    top = ranked[0]
    if top["similarity"] < min_similarity:
        # Nothing in the catalogue is semantically close — likely not a compliance cert.
        return None

    # LLM confirms/picks among the top cosine candidates (LLM + cosine).
    picked = await _llm_pick(ranked, source_text=source_text, file_name=file_name)
    winner = picked or top
    conf = (
        "high"
        if winner["similarity"] >= 0.55
        else ("medium" if winner["similarity"] >= 0.40 else "low")
    )
    return {
        "certificate_type_code": winner["certificate_type_code"],
        "cert_scope": winner["cert_scope"],
        "country_code": winner.get("country_code") or "UK",
        "confidence": conf,
        "method": "semantic:cosine+llm" if picked else "semantic:cosine",
    }


async def _llm_pick(
    candidates: list[dict[str, Any]], *, source_text: str | None, file_name: str | None
) -> dict[str, Any] | None:
    """Ask the LLM to choose the best code from the cosine top-k (or reject all)."""
    if not settings.openai_api_key:
        return None
    codes = [c["certificate_type_code"] for c in candidates]
    menu = "\n".join(
        f"- {c['certificate_type_code']} ({c.get('country_code') or 'UK'}, "
        f"{c['cert_scope']}, similarity {c['similarity']})"
        for c in candidates
    )
    prompt = (
        "You classify a compliance/accreditation certificate, which may be from the UK, "
        "the US or the UAE. Pick the candidate from that document's OWN country - a US "
        "document must match a US_ type, never the nearest UK equivalent. From the "
        "candidate list "
        "below (already ranked by semantic similarity), return the single best-matching "
        'certificate_type_code as JSON: {"certificate_type_code": "<code or null>"}. '
        "Return null only if NONE fit.\n\n"
        f"CANDIDATES:\n{menu}\n\n"
        f"DOCUMENT file: {file_name or ''}\n"
        f"DOCUMENT text (excerpt):\n{(source_text or '')[:3000]}\n"
    )
    try:
        client = _openai_client()
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            # Reasoning-tier models reject any temperature but the default.
            **({"temperature": 0} if _supports_temperature(settings.openai_model) else {}),
            max_tokens=60,
        )
        raw = resp.choices[0].message.content or "{}"
        raw = re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        code = (json.loads(raw) or {}).get("certificate_type_code")
        if code and code in codes:
            return next(c for c in candidates if c["certificate_type_code"] == code)
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic_classify.llm_pick_failed", error=str(exc)[:200])
    return None
