"""Document → plenum_cafm table matcher.

Given an ingested document (compliance certificate or any other), returns:

* ``is_compliance`` + ``compliance_match`` — for a compliance certificate, the resolved
  certificate type and its canonical target table (``compliance_certificates``). This is
  the "(a)" path: the compliance target is known deterministically from classification and
  can be applied straight away.
* ``table_matches`` — a ranked list of the best-matching ``plenum_cafm`` tables with a
  confidence score and one-line reason (Claude over a compact table catalog). This is the
  "(b)" path and is produced for EVERY document, compliance or not.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from . import classify as classify_svc

log = get_logger(__name__)

_SCHEMA = "plenum_cafm"
# Tables that never receive an ingested document's payload — drop them from the catalogue
# so the ranker spends its budget on real entity tables.
_EXCLUDE = re.compile(
    r"(^alembic_version$|_audit(_log)?$|^audit_logs$|_cache$|tokens?$|^claude_|"
    r"corrections_log$|activity_log|ingestion_audit|_snapshots?$|budget_config)",
    re.I,
)
_MAX_COLS = 8
_MODEL = "claude-haiku-4-5-20251001"


async def _table_catalog(session: AsyncSession, *, max_cols: int = _MAX_COLS) -> dict[str, list[str]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = :schema
                ORDER BY table_name, ordinal_position
                """
            ),
            {"schema": _SCHEMA},
        )
    ).all()
    catalog: dict[str, list[str]] = {}
    for table_name, column_name in rows:
        if _EXCLUDE.search(table_name):
            continue
        cols = catalog.setdefault(table_name, [])
        if len(cols) < max_cols:
            cols.append(column_name)
    return catalog


async def _rank_tables_llm(
    *,
    source_text: str | None,
    pdf_base64: str | None,
    catalog: dict[str, list[str]],
    top_k: int,
) -> dict[str, Any]:
    """Ask Claude to pick the best-matching tables and judge whether the document is a
    compliance certificate. Returns ``{"matches": [...], "is_compliance": bool}``;
    on any failure returns empty matches + is_compliance=None (undecided)."""
    if not settings.anthropic_api_key:
        return {"matches": [], "is_compliance": None}
    catalog_lines = "\n".join(f"- {t}: {', '.join(cols)}" for t, cols in sorted(catalog.items()))
    prompt = (
        "You map an ingested document to the database table its data belongs in.\n"
        f"Return the {top_k} best-matching tables from the catalogue as JSON only:\n"
        '{"is_compliance_certificate": true|false,'
        '"matches":[{"table":"<name>","confidence":0.0-1.0,"reason":"<=12 words"}]}\n'
        "Set is_compliance_certificate true ONLY if the document is itself a compliance/"
        "accreditation/inspection certificate or licence (e.g. EPC, SIA ACS, BAFE, asbestos "
        "licence, fire/gas certificate) — NOT for work orders, invoices, assets, readings.\n"
        "Rank by how well the document's entity/content fits the table's columns. "
        "Only use table names from the catalogue. Highest confidence first.\n\n"
        f"TABLE CATALOGUE ({_SCHEMA}):\n{catalog_lines}\n\n"
    )
    if source_text and source_text.strip():
        prompt += f"DOCUMENT TEXT (excerpt):\n{source_text[:6000]}\n"
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        content: list[dict[str, Any]] = []
        if pdf_base64 and not (source_text and source_text.strip()) and len(pdf_base64) < 28_000_000:
            content.append(
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_base64},
                }
            )
        content.append({"type": "text", "text": prompt})
        resp = await client.messages.create(
            model=_MODEL, max_tokens=800, messages=[{"role": "user", "content": content}]
        )
        raw = resp.content[0].text if resp.content else "{}"
        raw = re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        data = json.loads(raw)
        out: list[dict[str, Any]] = []
        for m in (data.get("matches") or [])[:top_k]:
            tbl = m.get("table")
            if tbl in catalog:  # never trust a hallucinated table name
                out.append(
                    {
                        "table": tbl,
                        "confidence": round(float(m.get("confidence") or 0.0), 3),
                        "reason": str(m.get("reason") or "")[:120],
                        "columns": catalog[tbl],
                    }
                )
        return {"matches": out, "is_compliance": bool(data.get("is_compliance_certificate"))}
    except Exception as exc:  # noqa: BLE001
        log.warning("table_match.llm_failed", error=str(exc)[:200])
        return {"matches": [], "is_compliance": None}


async def match_document(
    session: AsyncSession,
    *,
    source_text: str | None = None,
    pdf_base64: str | None = None,
    file_name: str | None = None,
    country_code: str = "UK",
    certificate_type_code: str | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Return {is_compliance, compliance_match, table_matches} for an ingested document."""
    # (b) ranked plenum_cafm table matches — always produced; also judges compliance so a
    # work order / invoice is not force-classified as a certificate.
    catalog = await _table_catalog(session)
    ranked = await _rank_tables_llm(
        source_text=source_text, pdf_base64=pdf_base64, catalog=catalog, top_k=top_k
    )
    table_matches = ranked["matches"]
    is_compliance = bool(ranked.get("is_compliance"))

    # (a) compliance target — only for documents the ranker judged to be certificates.
    compliance_match: dict[str, Any] | None = None
    if is_compliance:
        cls = await classify_svc.classify_certificate_type(
            session,
            source_text=source_text,
            file_name=file_name,
            pdf_base64=pdf_base64,
            certificate_type_code=certificate_type_code,
            country_code=country_code,
        )
        if cls.get("ok") and cls.get("certificate_type_code"):
            compliance_match = {
                "certificate_type_code": cls.get("certificate_type_code"),
                "certificate_type_name": cls.get("certificate_type_name"),
                "cert_scope": cls.get("cert_scope"),
                # Country the classifier routed this document to (auto-detected US/UAE/UK) so the
                # certificate is stored + verified against its own country's pack.
                "country_code": cls.get("country_code"),
                "target_table": "compliance_certificates",
                "classification_method": cls.get("method"),
                "classification_confidence": cls.get("confidence"),
                "auto_apply": True,
            }
            # Compliance target leads the ranked list.
            if not any(m["table"] == "compliance_certificates" for m in table_matches):
                table_matches.insert(
                    0,
                    {
                        "table": "compliance_certificates",
                        "confidence": 0.99,
                        "reason": f"Compliance certificate ({cls.get('certificate_type_code')})",
                        "columns": catalog.get("compliance_certificates", []),
                    },
                )
        else:
            is_compliance = False

    return {
        "ok": True,
        "is_compliance": is_compliance,
        "compliance_match": compliance_match,
        "table_matches": table_matches,
        "best_table": table_matches[0]["table"] if table_matches else None,
        "tables_considered": len(catalog),
    }
