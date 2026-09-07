"""Register search by company name for UKAS / BPCA / BASIS / HSE / SIA.

Flow:
  1. Search ingested dump rows (company_name / organisation_name / lookup_key)
  2. If thin/empty, scan VERIFICATION_DUMP_DIR CSV/JSON files for the same codes
  3. Always return an official deep-link with the name filled when supported
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ....config import settings
from ....core.logging import get_logger

log = get_logger(__name__)

# register key → certificate_type_codes stored in dump table / dump files
REGISTER_CODES: dict[str, list[str]] = {
    "ukas": ["UKAS_ASBESTOS", "ASBESTOS_SURVEY", "ISO_9001", "ISO_14001"],
    "bpca": ["BPCA_MEMBER"],
    "basis": ["PESTICIDE_PAx"],
    "hse": ["ASBESTOS_LICENCE"],
    "sia": ["SIA_ACS"],
}

REGISTER_LABELS: dict[str, str] = {
    "ukas": "UKAS Find an Organisation",
    "bpca": "BPCA Find a Pest Controller",
    "basis": "BASIS / PROMPT search",
    "hse": "HSE licensed asbestos contractors",
    "sia": "SIA Approved Contractor Scheme",
}

_NAME_KEYS = (
    "company_name",
    "organisation_name",
    "organization_name",
    "name",
    "trading_name",
    "contractor_name",
)


def deep_link(register: str, query: str) -> str:
    """Official register search URL with name when the site supports it."""
    q = (query or "").strip()
    enc = quote_plus(q) if q else ""
    reg = (register or "").lower().strip()
    if reg == "ukas":
        if enc:
            return f"https://www.ukas.com/find-an-organisation/?organisation_name={enc}"
        return "https://www.ukas.com/find-an-organisation/"
    if reg == "bpca":
        if enc:
            return f"https://bpca.org.uk/find-a-pest-controller/?s={enc}"
        return "https://bpca.org.uk/find-a-pest-controller/"
    if reg == "basis":
        if enc:
            return f"https://basis-reg.co.uk/search-results?q={enc}"
        return "https://basis-reg.co.uk/register/prompt"
    if reg == "hse":
        return "https://www.ukata.org.uk/library/hse-licensed-asbestos-removal-contractors-register/"
    if reg == "sia":
        return "https://www.services.sia.homeoffice.gov.uk/Pages/acs-roac.aspx?all="
    return ""


def register_for_certificate_code(certificate_type_code: str | None) -> str | None:
    code = (certificate_type_code or "").upper().strip()
    for reg, codes in REGISTER_CODES.items():
        if code in codes:
            return reg
    return None


def _company_name_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    for k in _NAME_KEYS:
        v = payload.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return None


def _row_matches_name(row: dict[str, Any], name_upper: str) -> bool:
    if not name_upper:
        return False
    for k in _NAME_KEYS:
        v = row.get(k)
        if v and name_upper in str(v).upper():
            return True
    for k in ("lookup_key", "licence_number", "member_number", "ukas_number", "acs_number"):
        v = row.get(k)
        if v and name_upper in str(v).upper():
            return True
    return False


def _hit_from_row(
    *,
    certificate_type_code: str,
    lookup_key: str,
    payload: dict[str, Any],
    source_file: str | None,
    match_source: str,
    row_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "certificate_type_code": certificate_type_code,
        "lookup_key": lookup_key,
        "company_name": _company_name_from_payload(payload) or lookup_key or None,
        "payload": payload,
        "source_file": source_file,
        "match_source": match_source,
    }


async def search_local_dumps(
    session: AsyncSession,
    *,
    register: str,
    name: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Search ingested dump rows by company name (and related keys)."""
    codes = REGISTER_CODES.get((register or "").lower().strip()) or []
    q = (name or "").strip()
    if not codes or not q:
        return []
    code_params = {f"c{i}": c for i, c in enumerate(codes)}
    code_clause = " OR ".join(f"certificate_type_code = :c{i}" for i in range(len(codes)))
    rows = (
        await session.execute(
            text(
                f"""
                SELECT id::text,
                       certificate_type_code,
                       lookup_key,
                       payload,
                       source_file,
                       ingested_at
                FROM plenum_cafm.compliance_verification_register_rows
                WHERE ({code_clause})
                  AND (
                    upper(lookup_key) LIKE :pat
                    OR upper(coalesce(payload->>'company_name', '')) LIKE :pat
                    OR upper(coalesce(payload->>'organisation_name', '')) LIKE :pat
                    OR upper(coalesce(payload->>'organization_name', '')) LIKE :pat
                    OR upper(coalesce(payload->>'name', '')) LIKE :pat
                    OR upper(coalesce(payload->>'trading_name', '')) LIKE :pat
                    OR upper(payload::text) LIKE :pat
                  )
                ORDER BY
                  CASE
                    WHEN upper(coalesce(payload->>'company_name', '')) LIKE :pat THEN 0
                    WHEN upper(coalesce(payload->>'organisation_name', '')) LIKE :pat THEN 0
                    ELSE 1
                  END,
                  ingested_at DESC
                LIMIT :lim
                """
            ),
            {
                **code_params,
                "pat": f"%{q.upper()}%",
                "lim": max(1, min(int(limit), 100)),
            },
        )
    ).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {"raw": payload}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        out.append(
            _hit_from_row(
                certificate_type_code=str(r["certificate_type_code"]),
                lookup_key=str(r["lookup_key"] or ""),
                payload=payload,
                source_file=r.get("source_file"),
                match_source="local_dump",
                row_id=r.get("id"),
            )
        )
    return out


def search_dump_files(
    *,
    register: str,
    name: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Fallback: scan dump-dir CSV/JSON by company name (no DB required)."""
    codes = set(REGISTER_CODES.get((register or "").lower().strip()) or [])
    q = (name or "").strip()
    if not codes or not q:
        return []
    dump_dir = Path(settings.verification_dump_dir or "./data/verification_dumps")
    if not dump_dir.exists():
        return []
    name_upper = q.upper()
    hits: list[dict[str, Any]] = []
    for path in sorted(dump_dir.glob("*")):
        if path.suffix.lower() not in {".csv", ".json"}:
            continue
        code = path.stem.split(".")[0].upper()
        if code not in codes:
            continue
        try:
            text_s = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            log.warning("register_search.file_read_failed", file=path.name, error=str(exc)[:160])
            continue
        if not text_s:
            continue
        rows: list[dict[str, Any]] = []
        if path.suffix.lower() == ".json" or text_s.startswith(("[", "{")):
            try:
                data = json.loads(text_s)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows = list(data.get("rows") or data.get("items") or [data])
            else:
                rows = [r for r in data if isinstance(r, dict)]
        else:
            rows = [dict(r) for r in csv.DictReader(io.StringIO(text_s))]
        for r in rows:
            if not _row_matches_name(r, name_upper):
                continue
            key = str(
                r.get("lookup_key")
                or r.get("licence_number")
                or r.get("member_number")
                or r.get("ukas_number")
                or r.get("company_name")
                or ""
            ).strip()
            hits.append(
                _hit_from_row(
                    certificate_type_code=code,
                    lookup_key=key,
                    payload=dict(r),
                    source_file=path.name,
                    match_source="dump_file",
                )
            )
            if len(hits) >= limit:
                return hits
    return hits


def _dedupe_hits(hits: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for h in hits:
        key = f"{h.get('certificate_type_code')}|{(h.get('lookup_key') or '').upper()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


async def run_register_search(
    session: AsyncSession,
    *,
    register: str,
    query: str = "",
    name: str = "",
    limit: int = 25,
    mode: str = "auto",
) -> dict[str, Any]:
    """
    Search by company name.

    mode:
      dump — local DB + dump-dir CSV only
      bot  — Playwright live portal bot (falls back to dump if bot unavailable)
      auto — dump first, then live bot if no confident match (default)
    """
    from .register_bot import (
        run_live_bot_search,
        score_hits,
        select_auto_verify_hit,
    )

    reg = (register or "").lower().strip()
    if reg not in REGISTER_CODES:
        return {
            "ok": False,
            "error": f"unknown_register:{register}",
            "registers": list(REGISTER_CODES.keys()),
        }
    q = (name or query or "").strip()
    lim = max(1, min(int(limit), 100))
    search_mode = (mode or "auto").lower().strip()
    if search_mode not in {"auto", "dump", "bot"}:
        search_mode = "auto"
    if not q:
        return {
            "ok": False,
            "error": "name_required",
            "message": "Send a company name (name or q) to search.",
            "register": reg,
            "register_label": REGISTER_LABELS.get(reg, reg),
            "deep_link": deep_link(reg, ""),
            "results": [],
            "local_hits": [],
            "result_count": 0,
            "mode": search_mode,
        }

    notes: list[str] = []
    dump_hits: list[dict[str, Any]] = []
    bot_meta: dict[str, Any] = {}

    if search_mode in {"auto", "dump", "bot"}:
        db_hits = await search_local_dumps(session, register=reg, name=q, limit=lim)
        file_hits = search_dump_files(register=reg, name=q, limit=lim) if len(db_hits) < lim else []
        dump_hits = score_hits(q, _dedupe_hits([*db_hits, *file_hits], lim))

    results = list(dump_hits)
    live_used = False
    auto_hit = select_auto_verify_hit(q, results)

    need_bot = search_mode == "bot" or (search_mode == "auto" and auto_hit is None)
    if need_bot and settings.register_bot_enabled:
        bot_meta = await run_live_bot_search(register=reg, name=q, limit=lim)
        bot_hits = score_hits(q, list(bot_meta.get("results") or []))
        if bot_hits:
            live_used = True
            results = _dedupe_hits([*bot_hits, *results], lim)
            results = score_hits(q, results)
            auto_hit = select_auto_verify_hit(q, results)
        notes.extend(list(bot_meta.get("notes") or []))
        if not bot_meta.get("ok") and bot_meta.get("error"):
            notes.append(f"Live bot: {bot_meta.get('error')}")

    link = deep_link(reg, q)
    if not results:
        notes.append(f"No rows matched name '{q}' for {reg}.")

    verified = bool(auto_hit) and bool(settings.register_bot_auto_verify)
    status = (
        "verified"
        if verified
        else ("results" if results else "no_results")
    )

    return {
        "ok": True,
        "register": reg,
        "register_label": REGISTER_LABELS.get(reg, reg),
        "name": q,
        "query": q,
        "mode": search_mode,
        "certificate_type_codes": REGISTER_CODES[reg],
        "results": results,
        "result_count": len(results),
        "local_hits": results,
        "local_hit_count": len(results),
        "deep_link": link,
        "live_scrape": live_used,
        "bot": {
            "ok": bot_meta.get("ok"),
            "engine": bot_meta.get("engine"),
            "error": bot_meta.get("error"),
            "result_count": len(bot_meta.get("results") or []) if bot_meta else 0,
        }
        if bot_meta
        else None,
        "notes": notes,
        "status": status,
        "verified": verified,
        "auto_verify_hit": auto_hit,
        "match_confidence": (auto_hit or {}).get("match_confidence") if auto_hit else None,
    }
