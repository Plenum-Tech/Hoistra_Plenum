"""The contract terms and invoices a single end-to-end workbook carries, through the engines.

A building's workbook migrates its tables on the Migration page. Two kinds of record in it are
not tables to copy: a contract's TERMS become a parameter set only through the contract ingest
(which records where each term came from and what was left to a platform default), and an
invoice's LINES mean something only once the invoice matcher has checked each against the work
order it bills and held what does not match. The migration therefore sets those sheets aside
(svc-ai-schema-mapper ingest_node.POST_WRITE_SHEETS) and this reads them from the same upload
after the write:

  Contract_Terms   one row per term: contract_ref, contract_name, vendor_code, vendor_name,
                   signed_date, term, value, source (contract|default), clause, page
                   -> parameters.ingest_contract_parameters, one DRAFT per contract
  Invoice_Lines    one row per line: invoice_no, invoice_date, vendor_code/vendor_name,
                   contract_ref, line_id, wo_code, description, labour_hours, labour_rate,
                   part_code, parts_cost, amount
                   -> invoice.verify_invoice per invoice, against the vendor's completed work
                      orders and the rate its contract states

  Chiller_Design_Specs, Chiller_Readings, Weather_Degree_Days, BMS_Trends
                   -> energy.workbook_telemetry, into the stores the energy scan and the
                      investigation read

Nothing is confirmed: a confirmed parameter set makes a vendor's numbers binding and needs a
named person. An invoice already verified for the company is not verified again.
"""
from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import invoice as invoice_svc
from . import parameters as params_svc
from . import scoring as score_svc
from ..energy import workbook_telemetry

log = get_logger(__name__)

#: Terms that are KPI clauses rather than a column of their own.
_KPI_TERMS = ("Completion target", "First-time fix target", "Recall window", "Service credit formula",
              "Recall chargeability", "Uplift review")


def _norm(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def read_sheets(content: bytes) -> dict[str, list[dict[str, Any]]]:
    """{normalised sheet name: rows as dicts} for the sheets this reads."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        for ws in wb.worksheets:
            key = _norm(ws.title)
            if key not in ("contractterms", "invoicelines") + workbook_telemetry.SHEETS:
                continue
            it = ws.iter_rows(values_only=True)
            hdr = [str(h).strip() if h is not None else "" for h in next(it, [])]
            out[key] = [dict(zip(hdr, r)) for r in it if any(v not in (None, "") for v in r)]
    finally:
        wb.close()
    return out


def term_hours(value: Any) -> float | None:
    """'4 hours' -> 4, '1 business day' -> 24 (a business day counted as 24 elapsed hours)."""
    m = re.match(r"\s*([\d.]+)\s*(hours?|business days?)", str(value or ""))
    if not m:
        return None
    n = float(m.group(1))
    return n if m.group(2).startswith("hour") else n * 24


def extraction_from_terms(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The extractor's shape, from a contract's term rows. Only terms stated by the contract
    go in; the engine fills the rest with its defaults and marks each one so."""
    ext: dict[str, Any] = {}
    kpi: dict[str, Any] = {}
    for t in rows:
        if str(t.get("source") or "").lower() != "contract":
            continue
        lbl, val = str(t.get("term") or ""), t.get("value")
        cite = {k: t[k] for k in ("clause", "page") if t.get(k) not in (None, "", 0)}
        m = re.match(r"P([1-4]) response", lbl)
        if m:
            h = term_hours(val)
            if h is not None:
                ext[f"sla_response_p{m.group(1)}_hours"] = h
        elif lbl.startswith("Labour rate") and "standard" in lbl:
            ext["labour_hour_rate"] = float(re.sub(r"[^\d.]", "", str(val)) or 0) or None
        elif lbl.startswith("Labour rate") and "out of hours" in lbl:
            ext["overtime_rate"] = float(re.sub(r"[^\d.]", "", str(val)) or 0) or None
        elif lbl == "Parts mark-up cap":
            ext["parts_pricing_json"] = {"markup_cap_pct": float(re.sub(r"[^\d.]", "", str(val)) or 0), **cite}
        elif lbl in _KPI_TERMS:
            kpi[lbl] = {"value": val, **cite}
    if kpi:
        ext["kpi_clauses_json"] = kpi
    return {k: v for k, v in ext.items() if v is not None}


def _date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _f(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _vendor(session: AsyncSession, org: UUID, code: Any, name: Any) -> tuple[str, str] | None:
    row = (await session.execute(text(
        """SELECT id::text, vendor_name FROM plenum_cafm.vendors
            WHERE organization_id = :o AND (vendor_code = :c OR lower(vendor_name) = lower(:n))
            ORDER BY (vendor_code = :c) DESC LIMIT 1"""),
        {"o": org, "c": str(code or ""), "n": str(name or "")})).first()
    return (row[0], row[1]) if row else None


async def run(session: AsyncSession, *, content: bytes, organization_id: UUID,
              building_name: str | None = None, building_reference: str | None = None) -> dict[str, Any]:
    sheets = read_sheets(content)
    terms, lines = sheets.get("contractterms") or [], sheets.get("invoicelines") or []
    has_telemetry = any(sheets.get(k) for k in workbook_telemetry.SHEETS)
    out: dict[str, Any] = {"ok": True, "found": bool(terms or lines or has_telemetry), "contracts": [],
                           "invoices": [], "skipped": []}
    if not out["found"]:
        return out

    by_contract: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in terms:
        if t.get("contract_ref"):
            by_contract[str(t["contract_ref"]).strip()].append(t)
    rate_of_vendor: dict[str, tuple[float | None, float | None, dict]] = {}
    for ref, rows in by_contract.items():
        head = rows[0]
        v = await _vendor(session, organization_id, head.get("vendor_code"), head.get("vendor_name"))
        if not v:
            out["skipped"].append({"contract_ref": ref, "reason": "vendor not on record"})
            continue
        ext = extraction_from_terms(rows)
        ext["contract_ref"] = ref
        merged, _, _ = params_svc.merge_extraction_with_defaults(ext)
        rate_of_vendor[v[0]] = (merged.get("labour_day_rate"), ext.get("labour_hour_rate"), ext.get("parts_pricing_json") or {})
        contract_id = (await session.execute(text(
            """SELECT id FROM plenum_cafm.vendor_contracts WHERE organization_id = :o AND vendor_id::text = :v
                 AND contract_name = :n LIMIT 1"""),
            {"o": organization_id, "v": v[0], "n": str(head.get("contract_name") or "")})).scalar()
        r = await params_svc.ingest_contract_parameters(
            session, extracted=ext, organization_id=organization_id, vendor_id=UUID(v[0]), vendor_name=v[1],
            contract_id=contract_id, contract_ref=ref, signed_date=_date(head.get("signed_date")),
            building_name=building_name, building_reference=building_reference, file_name=f"{ref} (workbook)")
        out["contracts"].append({"contract_ref": ref, "vendor": v[1], "terms_stated": len(ext) - 1,
                                 "status": r.get("status") or ("draft" if r.get("ok", True) else r.get("reason"))})

    by_invoice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        if ln.get("invoice_no"):
            by_invoice[str(ln["invoice_no"]).strip()].append(ln)
    done = {r[0] for r in (await session.execute(text(
        "SELECT invoice_ref FROM plenum_cafm.invoice_verifications WHERE organization_id = :o"),
        {"o": organization_id})).all()}
    wo_cache: dict[str, list[dict[str, Any]]] = {}
    for no, rows in by_invoice.items():
        if no in done:
            out["skipped"].append({"invoice_no": no, "reason": "already verified"})
            continue
        v = await _vendor(session, organization_id, rows[0].get("vendor_code"), rows[0].get("vendor_name"))
        if not v:
            out["skipped"].append({"invoice_no": no, "reason": "vendor not on record"})
            continue
        if v[0] not in wo_cache:
            wo_cache[v[0]] = await score_svc.fetch_completed_work_orders_from_udr(
                session, vendor_id=UUID(v[0]), organization_id=organization_id, limit=5000)
        day, hour, parts = rate_of_vendor.get(v[0], (None, None, {}))
        payload = [{"line_id": f"{no}-{ln.get('line_id') or i + 1}", "wo_code": ln.get("wo_code"),
                    "description": ln.get("description"), "labour_hours": _f(ln.get("labour_hours")),
                    "labour_rate": _f(ln.get("labour_rate")), "part_code": ln.get("part_code") or None,
                    "parts_cost": _f(ln.get("parts_cost")), "amount": _f(ln.get("amount"))}
                   for i, ln in enumerate(rows)]
        r = await invoice_svc.verify_invoice(
            session, invoice_ref=no, lines=payload, work_orders=wo_cache[v[0]], invoice_ref_identifies=True,
            vendor_id=UUID(v[0]), organization_id=organization_id, building_name=building_name,
            building_reference=building_reference, file_name=f"{no} (workbook)",
            labour_day_rate=day, labour_hour_rate=hour, parts_pricing_json=parts)
        out["invoices"].append({"invoice_no": no, "vendor": v[1], "lines": len(payload),
                                "matched": r.get("matched_count"), "held": r.get("flagged_count")})
    await session.commit()
    if has_telemetry:
        out["telemetry"] = await workbook_telemetry.run(session, sheets, organization_id)
    log.info("workbook_extras.done", contracts=len(out["contracts"]), invoices=len(out["invoices"]),
             skipped=len(out["skipped"]))
    return out
