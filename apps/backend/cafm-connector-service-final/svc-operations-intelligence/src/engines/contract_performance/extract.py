"""B1/B3 — Relationships Agent: extract contract / invoice fields from source text."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from .parameters import ingest_contract_parameters, merge_extraction_with_defaults
from .invoice import verify_invoice

log = get_logger(__name__)

CONTRACT_FIELDS = [
    "sla_response_p1_hours",
    "sla_response_p2_hours",
    "sla_response_p3_hours",
    "sla_response_p4_hours",
    "sla_completion_p1_hours",
    "sla_completion_p2_hours",
    "sla_completion_p3_hours",
    "sla_completion_p4_hours",
    "labour_day_rate",
    "overtime_rate",
    "call_out_rate",
    "payment_terms",
    "parts_pricing_json",
    "kpi_clauses_json",
    "ppm_obligations_json",
    "task_criticality_json",
    "contract_ref",
    "signed_date",
    # Who the contract is WITH. Without this the parameters are ingested against no vendor at
    # all, and scoring — which loads a vendor's confirmed contract — has nothing to find.
    "vendor_name",
]

_SIGNED_DATE_RE = re.compile(
    r"(?:signed|executed|dated|effective)[^\d]{0,30}"
    r"(\d{1,2})[^\d](\w+)[^\d](\d{4})"
    r"|(\d{4})-(\d{2})-(\d{2})"
    r"|(\d{1,2})/(\d{1,2})/(\d{4})",
    re.I,
)
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_signed_date(text: str) -> str | None:
    """Heuristic: extract signing date as YYYY-MM-DD string or return None."""
    for m in _SIGNED_DATE_RE.finditer(text):
        try:
            if m.group(1) and m.group(2) and m.group(3):
                # "15 January 2026"
                mon_str = m.group(2)[:3].lower()
                mon = _MONTH_MAP.get(mon_str)
                if mon:
                    return f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
            elif m.group(4) and m.group(5) and m.group(6):
                # "2026-01-15"
                return f"{m.group(4)}-{m.group(5)}-{m.group(6)}"
            elif m.group(7) and m.group(8) and m.group(9):
                # "15/01/2026" — assume DD/MM/YYYY
                return f"{m.group(9)}-{int(m.group(8)):02d}-{int(m.group(7)):02d}"
        except Exception:  # noqa: BLE001
            continue
    return None


def _heuristic_contract_extract(text: str) -> dict[str, Any]:
    """Regex/heuristic fallback when Claude unavailable."""
    out: dict[str, Any] = {}
    conf: dict[str, str] = {}
    lower = text.lower()

    # P1 response e.g. "P1 response: 1 hour" / "Priority 1 attend within 2 hours"
    for pri, key in [
        ("p1", "sla_response_p1_hours"),
        ("p2", "sla_response_p2_hours"),
        ("p3", "sla_response_p3_hours"),
        ("p4", "sla_response_p4_hours"),
    ]:
        m = re.search(
            rf"(?:{pri}|priority\s*{pri[-1]})\D{{0,40}}(?:response|attend|attend(?:ance)?)\D{{0,20}}(\d+(?:\.\d+)?)\s*(h|hr|hour)",
            lower,
        )
        if m:
            out[key] = float(m.group(1))
            conf[key] = "medium"

    for pri, key in [
        ("p1", "sla_completion_p1_hours"),
        ("p2", "sla_completion_p2_hours"),
        ("p3", "sla_completion_p3_hours"),
        ("p4", "sla_completion_p4_hours"),
    ]:
        m = re.search(
            rf"(?:{pri}|priority\s*{pri[-1]})\D{{0,40}}(?:complet|fix|resolve)\D{{0,20}}(\d+(?:\.\d+)?)\s*(h|hr|hour)",
            lower,
        )
        if m:
            out[key] = float(m.group(1))
            conf[key] = "medium"

    # An hourly rate is read first and kept as one. Folding it into labour_day_rate would
    # make the invoice check divide it by eight and compare a £62/h contract against £7.75/h.
    m = re.search(
        r"(?:labour|labor)[^0-9]{0,30}?[£$]?(\d+(?:\.\d+)?)\s*(?:per\s*hour|/\s*h(?:r|our)?\b|p\.?h\b)",
        lower,
    )
    if not m:
        m = re.search(
            r"(?:hourly\s*rate|rate\s*per\s*hour)[^0-9]{0,20}[£$]?(\d+(?:\.\d+)?)", lower
        )
    if m:
        out["labour_hour_rate"] = float(m.group(1))
        conf["labour_hour_rate"] = "medium"
    m = re.search(
        r"(?:labour|labor)\s*(?:day|daily)\s*rate[^0-9]{0,20}[£$]?(\d+(?:\.\d+)?)", lower
    )
    if not m and "labour_hour_rate" not in out:
        m = re.search(r"(?:labour|labor)\s*rate[^0-9]{0,20}[£$]?(\d+(?:\.\d+)?)", lower)
    if m:
        out["labour_day_rate"] = float(m.group(1))
        conf["labour_day_rate"] = "medium"
    m = re.search(r"overtime[^0-9]{0,20}[£$]?(\d+(?:\.\d+)?)", lower)
    if m:
        out["overtime_rate"] = float(m.group(1))
        conf["overtime_rate"] = "medium"
    m = re.search(r"call[\s-]?out[^0-9]{0,20}[£$]?(\d+(?:\.\d+)?)", lower)
    if m:
        out["call_out_rate"] = float(m.group(1))
        conf["call_out_rate"] = "medium"
    m = re.search(r"(net\s*\d+|payment\s*terms[:\s]+[^\n.]{3,40})", lower)
    if m:
        out["payment_terms"] = m.group(1).strip()
        conf["payment_terms"] = "low"
    m = re.search(r"(?:contract|agreement|po)[\s#:.-]*([A-Z0-9][A-Z0-9\-_/]{3,})", text, re.I)
    if m:
        out["contract_ref"] = m.group(1)
        conf["contract_ref"] = "medium"

    sd = _parse_signed_date(text)
    if sd:
        out["signed_date"] = sd
        conf["signed_date"] = "medium"

    # KPI / penalty keywords → placeholder clauses
    if any(k in lower for k in ("penalty", "bonus", "kpi", "service credit")):
        out["kpi_clauses_json"] = {"detected": True, "raw_snippet": "KPI/penalty language present — PM to confirm"}
        conf["kpi_clauses_json"] = "low"
    if "ppm" in lower or "planned preventive" in lower:
        out["ppm_obligations_json"] = {"detected": True, "note": "PPM obligations mentioned — PM to confirm schedule"}
        conf["ppm_obligations_json"] = "low"

    return {"extracted": out, "field_confidence": conf}


# Long contracts put the commercially interesting parts at the back. UKRI-2938 is 94k
# characters and its KPI schedule begins at character 25,502 — a blind head-truncation to 12k
# sends the model the award letter and the boilerplate, and never the schedule that carries
# the SLA targets, the service credits or the rate card. Select by relevance instead.
_RELEVANT_MARKERS = (
    "schedule", "sla", "service level", "key performance", "kpi", "priority",
    "response time", "charges", "rate", "payment terms", "ppm", "planned preventative",
    "planned preventive", "service credit", "criticality", "parts",
)


# How much document text can actually be sent. The old 40,000-character budget was set as if
# context were scarce: claude-haiku-4-5 has a 200k-token window, and text costs roughly one
# token per four characters, so 40k characters used about 5% of it. Practically every FM
# contract fits whole — UKRI-2938 is 93k characters, around 23k tokens.
#
# The one real constraint is that a PDF sent as a document block competes for the same window
# (roughly 1.5–3k tokens per page, so a 52-page contract can be 150k on its own). When the PDF
# is attached the model is already reading every page of it, and the text is a supplement, so
# it gets the smaller budget. With no PDF the text IS the document and is sent in full.
_TEXT_BUDGET_WITH_PDF = 100_000
_TEXT_BUDGET_TEXT_ONLY = 500_000


def _relevant_excerpt(text: str, budget: int = _TEXT_BUDGET_TEXT_ONLY) -> tuple[str, int]:
    """Return (excerpt, characters_dropped) for a document that exceeds the budget.

    Always keeps the opening (parties, references, term), then adds the paragraphs that
    mention commercial or service-level terms, in document order, until the budget is spent.
    A document within budget is returned whole and drops nothing, which is now the normal
    case rather than the exception.

    The dropped count is returned rather than discarded so a caller can say that it happened.
    Silently shortening a contract and then reporting the extraction as complete is how a
    parameter the contract really does state gets recorded as "not stated".
    """
    if len(text) <= budget:
        return text, 0
    head = text[:6000]
    remaining = budget - len(head)
    picked: list[str] = []
    for para in re.split(r"\n\s*\n", text[6000:]):
        low = para.lower()
        if not any(m in low for m in _RELEVANT_MARKERS):
            continue
        if len(para) > remaining:
            break
        picked.append(para)
        remaining -= len(para)
    out = head + "\n\n" + "\n\n".join(picked)
    return out, len(text) - len(out)


async def _claude_contract_extract(
    source_text: str, pdf_base64: str | None = None
) -> dict[str, Any]:
    """Extract contract parameters, reading the PDF itself when one is available.

    A contract's commercial terms live in tables — the KPI schedule, the rate card. Flattening
    those to text interleaves the columns and the values stop being readable as a row, so the
    KPI clauses come back null however capable the model is. Measured on UKRI-2938: from
    flattened text, kpi_clauses_json is null on both Haiku and Opus; from the same pages sent
    as a PDF document, Haiku returns every KPI with its target, action levels and weighting.

    The model is not the bottleneck here. Passing the document is.
    """
    try:
        import anthropic
    except ImportError:
        return _heuristic_contract_extract(source_text)

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    prompt = (
        "Extract FM contract SLA parameters as JSON only. Keys: "
        + ", ".join(CONTRACT_FIELDS)
        + ". Use null for missing. Hours as numbers. "
        "signed_date as YYYY-MM-DD string (the date the contract was signed/executed). "
        "parts_pricing_json as {part_code: price}. "
        "kpi_clauses_json and ppm_obligations_json as objects. "
        "task_criticality_json as {L1,L2,L3: description} if defined.\n"
        "Use null where the contract does not STATE a value — never guess. A contract often "
        "references 'specified priority timescales' without stating the hours; that is still "
        "null. Look hardest at schedules titled Charges and Key Performance Indicators.\n"
        "vendor_name is the SERVICE PROVIDER / SUPPLIER / CONTRACTOR — the party being "
        "engaged to perform the work. It is NOT the client, customer, authority or buyer who "
        "is awarding the contract. In an award letter reading 'we are pleased to award this "
        "contract to you', the vendor is the addressee, not the sender. Give the full legal "
        "entity name as written.\n\n"
    )
    has_pdf = bool(pdf_base64) and len(pdf_base64 or "") < 28_000_000
    excerpt, dropped = _relevant_excerpt(
        source_text or "",
        budget=_TEXT_BUDGET_WITH_PDF if has_pdf else _TEXT_BUDGET_TEXT_ONLY,
    )
    if dropped:
        # Never silent. A shortened document that still produces a confident-looking result
        # is worse than a slow one, because nothing downstream can tell the difference.
        log.warning(
            "contract_performance.source_text_trimmed",
            chars_total=len(source_text or ""),
            chars_sent=len(excerpt),
            chars_dropped=dropped,
            pdf_attached=has_pdf,
        )
    prompt += f"DOCUMENT:\n{excerpt}"
    content: list[dict[str, Any]] = []
    if has_pdf:
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
    content.append({"type": "text", "text": prompt})

    resp = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": content}],
    )
    # Take the first TEXT block: a thinking-enabled model puts a ThinkingBlock at content[0],
    # and indexing blindly raises AttributeError the moment the model is changed.
    raw = next(
        (b.text for b in (resp.content or []) if getattr(b, "type", "") == "text"), "{}"
    )
    m = re.search(r"\{[\s\S]*\}", raw)
    data = json.loads(m.group(0) if m else "{}")
    conf = {k: "high" for k in data if data[k] is not None}
    out: dict[str, Any] = {"extracted": data, "field_confidence": conf}
    if dropped:
        out["source_truncated"] = {"chars_dropped": dropped, "chars_sent": len(excerpt)}
    return out


async def _default_org_for_vendor_create(session: AsyncSession) -> Any:
    """The platform organization, in whatever type the column actually uses.

    Not compliance's resolve_default_org, deliberately. That one casts the row to UUID
    because its caller assigns the result to a UUID column — but plenum_cafm.organizations.id
    is an INTEGER, so the cast always throws and the function always returns None. Vendor
    creation needs a truthy organization_id and writes it to vendors.organization_id, itself
    a legacy INTEGER, so the value is used here exactly as the database stores it.
    """
    try:
        from sqlalchemy import text as _sql

        row = (
            await session.execute(
                _sql("SELECT id FROM plenum_cafm.organizations ORDER BY id LIMIT 1")
            )
        ).mappings().first()
        return row["id"] if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("contract_performance.default_org_lookup_failed", error=str(exc)[:200])
        return None


async def _resolve_contract_vendor(
    session: AsyncSession,
    *,
    vendor_id: Any,
    vendor_name: Any,
    organization_id: Any,
) -> dict[str, Any]:
    """Link the contract to a vendor, registering the service provider if they are new.

    Returns what happened as well as the id, because "matched an existing vendor" and
    "created one from this document" are different facts to a PM: the second means a new
    party just entered the register on the strength of a single contract, and is worth
    seeing rather than discovering later.

    Never raises. A contract whose vendor cannot be resolved is still worth ingesting —
    the parameters are real and a PM can attach them — so failure here degrades to an
    unlinked draft rather than losing the extraction.
    """
    if vendor_id:
        return {"vendor_id": vendor_id, "vendor_name": vendor_name, "status": "supplied"}

    name = str(vendor_name or "").strip()
    if not name:
        return {"vendor_id": None, "vendor_name": None, "status": "not_named_in_contract"}

    try:
        from ..compliance.contractors import resolve_or_create_vendor

        org = organization_id or await _default_org_for_vendor_create(session)
        existing = await resolve_or_create_vendor(
            session, company_name=name, organization_id=org, create_if_missing=False
        )
        if existing:
            return {"vendor_id": existing, "vendor_name": name, "status": "matched"}

        created = await resolve_or_create_vendor(
            session, company_name=name, organization_id=org, create_if_missing=True
        )
        if created:
            log.info(
                "contract_performance.vendor_registered",
                vendor_id=str(created),
                vendor_name=name,
            )
            return {"vendor_id": created, "vendor_name": name, "status": "created"}
        return {"vendor_id": None, "vendor_name": name, "status": "could_not_create"}
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "contract_performance.vendor_resolve_failed", vendor_name=name, error=str(exc)[:300]
        )
        return {"vendor_id": None, "vendor_name": name, "status": "error"}


async def extract_contract_parameters(
    session: AsyncSession,
    *,
    source_text: str | None = None,
    pdf_base64: str | None = None,
    extracted_fields: dict[str, Any] | None = None,
    organization_id=None,
    vendor_id=None,
    document_id=None,
    contract_ref: str | None = None,
    signed_date=None,
    auto_ingest: bool = True,
) -> dict[str, Any]:
    """
    Relationships Agent mid-layer: map contract document text → structured fields,
    then optionally ingest as draft for PM inline edit/confirm.
    """
    field_confidence: dict[str, str] = {}
    extracted = dict(extracted_fields or {})

    if not extracted and source_text:
        if settings.anthropic_api_key:
            result = await _claude_contract_extract(source_text, pdf_base64)
        else:
            result = _heuristic_contract_extract(source_text)
        extracted = {k: v for k, v in (result.get("extracted") or {}).items() if v is not None}
        field_confidence = result.get("field_confidence") or {}

    # A contract is with somebody. Ingesting the parameters against no vendor leaves them
    # unreachable: scoring loads a vendor's confirmed contract, so an unlinked contract can
    # never govern anything, however carefully its terms were extracted. Match the named
    # service provider against the vendor register, and register them if they are new.
    vendor_link = await _resolve_contract_vendor(
        session,
        vendor_id=vendor_id,
        vendor_name=extracted.get("vendor_name"),
        organization_id=organization_id,
    )
    if vendor_link.get("vendor_id"):
        vendor_id = vendor_link["vendor_id"]

    preview, defaults_used, field_sources = merge_extraction_with_defaults(extracted)
    response: dict[str, Any] = {
        "vendor": vendor_link,
        "ok": True,
        "requires_pm_confirmation": True,
        "extracted": extracted,
        "field_confidence": field_confidence,
        "preview_with_defaults": preview,
        "defaults_used": defaults_used,
        "field_sources": field_sources,
        "message": (
            "PM must review/override extracted SLA parameters in the inline-editable "
            "table, then confirm (POST /contracts/{id}/confirm)."
        ),
    }

    if auto_ingest:
        # Prefer caller-supplied signed_date; fall back to extracted value.
        effective_signed_date = signed_date or extracted.get("signed_date")
        ingested = await ingest_contract_parameters(
            session,
            extracted=extracted,
            organization_id=organization_id,
            vendor_id=vendor_id,
            document_id=document_id,
            contract_ref=contract_ref or extracted.get("contract_ref"),
            signed_date=effective_signed_date,
        )
        response["ingest"] = ingested
        response["parameters_id"] = (ingested.get("parameters") or {}).get("id")

    return response


def _heuristic_invoice_lines(text: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    # WO-xxx ... amount
    for i, m in enumerate(
        re.finditer(
            r"(WO[-\s]?\d[\w-]*)[^\n]{0,80}?(?:£|GBP|amount[:\s]*)?(\d+(?:\.\d{2})?)",
            text,
            re.I,
        )
    ):
        lines.append(
            {
                "line_id": str(i + 1),
                "wo_code": re.sub(r"\s+", "", m.group(1).upper().replace("WO ", "WO-")),
                "amount": float(m.group(2)),
            }
        )
    # Hours patterns near WO
    for line in lines:
        m = re.search(
            rf"{re.escape(line['wo_code'])}[^\n]{{0,60}}?(\d+(?:\.\d+)?)\s*h(?:ours?)?",
            text,
            re.I,
        )
        if m:
            line["labour_hours"] = float(m.group(1))
    return lines


#: "Invoice no.", "Invoice Number:", "Tax Invoice #" and the rest, followed by the value.
#: The label word is REQUIRED — a bare "INVOICE" heading sits above the supplier's name on
#: most layouts, and matching that would key the register on "Halden". \s* spans the
#: newline because a PDF header block routinely prints the label and its value on separate
#: lines, which is exactly how the invoice that prompted this is laid out.
_INVOICE_NUMBER = re.compile(
    r"\b(?:tax\s+|vat\s+)?invoice\s*"
    r"(?:number|no\.?|num\.?|ref(?:erence)?|#)\s*[:#.\-]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9/\-]{2,31})\b",
    re.I,
)


def invoice_number_in(source_text: str | None) -> str | None:
    """The invoice number printed on the document, or None.

    None is an answer, not a failure: a document that does not print its number has none to
    record, and the caller falls back to a label rather than inventing one.
    """
    for m in _INVOICE_NUMBER.finditer(source_text or ""):
        found = (m.group(1) or "").strip(" .,;:-/")
        # A real invoice number carries at least one digit. Without this the pattern reads
        # "Invoice number is not shown" as the number "is".
        if len(found) >= 3 and any(ch.isdigit() for ch in found):
            return found
    return None


async def extract_and_verify_invoice(
    session: AsyncSession,
    *,
    source_text: str | None = None,
    lines: list[dict[str, Any]] | None = None,
    work_orders: list[dict[str, Any]] | None = None,
    invoice_ref: str | None = None,
    invoice_ref_fallback: str | None = None,
    vendor_id=None,
    organization_id=None,
    document_id=None,
    labour_day_rate: float | None = None,
    labour_hour_rate: float | None = None,
    parts_pricing_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Orchestrator invoice upload path: parse lines then verify against ingested WOs."""
    # What this invoice is called. An explicit ref from the caller is a person's answer and
    # wins; otherwise the document's own number, which is what a supplier, a PM and an
    # accounts system all quote; and only failing both, a label the caller supplied — the
    # uploaded filename — which identifies nothing and is never used as a key.
    ref_from_document = invoice_number_in(source_text)
    invoice_ref = invoice_ref or ref_from_document or invoice_ref_fallback
    if ref_from_document:
        log.info("invoice.ref_read_from_document", invoice_ref=ref_from_document)
    elif invoice_ref_fallback:
        log.warning("invoice.ref_not_printed_on_document", fallback=invoice_ref_fallback)
    parsed = list(lines or [])
    if not parsed and source_text:
        if settings.anthropic_api_key:
            try:
                import anthropic

                client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
                prompt = (
                    "Extract invoice line items as JSON array. Each item keys: "
                    "line_id, wo_code, labour_hours, labour_rate, parts_cost, part_code, amount. "
                    # Was a blind 12k head-truncation. An invoice's disputed lines are as
                    # likely to sit at the end as the start.
                    f"DOCUMENT:\n"
                    f"{_relevant_excerpt(source_text or '', _TEXT_BUDGET_TEXT_ONLY)[0]}"
                )
                resp = await client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=3000,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = resp.content[0].text if resp.content else "[]"
                m = re.search(r"\[[\s\S]*\]", raw)
                parsed = json.loads(m.group(0) if m else "[]")
            except Exception as exc:  # noqa: BLE001
                log.warning("invoice.claude_extract_failed", error=str(exc)[:200])
                parsed = _heuristic_invoice_lines(source_text)
        else:
            parsed = _heuristic_invoice_lines(source_text)

    # Is this actually an invoice? A filename containing "invoice" is enough to route a
    # document here, and a CSV that is not one — a report, an export, a mark sheet — parses
    # into rows with no billable content. Verifying those produces line-by-line findings
    # about nothing and pushes them to a PM as if a supplier had billed them.
    # A work order reference alone is not an invoice line — plenty of files list wo_codes.
    # What makes a line billable is something being charged on it.
    _MONEY_KEYS = ("labour_hours", "labour_rate", "parts_cost", "line_total", "amount", "total")
    billable = [
        x
        for x in (parsed or [])
        if any(x.get(k) is not None and x.get(k) != "" for k in _MONEY_KEYS)
    ]

    # Shape is not enough on its own. Asked to read a file as an invoice, the model
    # obligingly reads it as one — a findings report with a GBP column comes back as
    # billable lines. So also require the document to identify itself as an invoice, the
    # way every real one does: an invoice number or date, or a billing structure of hours
    # and rates. A report about invoices has neither.
    # Checked against the head of the document — a CSV header row, or a PDF letterhead —
    # and deliberately NOT against the parsed lines. Asked to read a file as an invoice the
    # model reads it as one: given a findings report whose text mentions "labour rate 78.5
    # exceeds contracted 62.0", it returns labour_rate 78.5 and the file vouches for itself.
    # The document's own header cannot be talked into anything.
    head = (source_text or "")[:400]
    identifies_as_invoice = bool(
        re.search(
            r"invoice[\s_-]*(no|num|number|ref|date|#)|tax\s+invoice|bill[\s_-]*(no|to)"
            r"|labour[\s_-]*(rate|hours)|unit[\s_-]*price|line[\s_-]*total|net[\s_-]*amount",
            head,
            re.I,
        )
    )
    if not identifies_as_invoice:
        log.warning(
            "contract_performance.not_an_invoice",
            invoice_ref=invoice_ref,
            reason="no invoice number, date or billing structure",
            parsed_rows=len(parsed or []),
        )
        return {
            "ok": False,
            "error": "not_an_invoice",
            "message": (
                "This file does not identify itself as an invoice — no invoice number or "
                "date, and no hours or rates being billed — so there is nothing to verify. "
                "It was routed here because its filename looks like an invoice."
            ),
            "parsed_lines": parsed or [],
            "verification": None,
        }

    if not billable:
        log.warning(
            "contract_performance.not_an_invoice",
            invoice_ref=invoice_ref,
            parsed_rows=len(parsed or []),
        )
        return {
            "ok": False,
            "error": "not_an_invoice",
            "message": (
                "This file has no billable lines — no work order references, hours or "
                "amounts — so there is nothing to verify. It was routed here because its "
                "name looks like an invoice."
            ),
            "parsed_lines": parsed or [],
            "verification": None,
        }

    # Auto-load work orders from DB if not provided
    wos = list(work_orders or [])
    if not wos and parsed:
        codes = [str(x.get("wo_code")) for x in parsed if x.get("wo_code")]
        wos = await _fetch_work_orders_by_codes(session, codes)

    # Not one line matched a work order. Read literally that means the supplier invented
    # every job on the invoice, which is not what it usually means — it means the work
    # orders have not been ingested yet. Verifying anyway produces a page of "unmatched"
    # findings and a dispute against a vendor who did nothing wrong, so refuse and say
    # which reading applies. A partial miss is different and still verifies: those lines
    # genuinely are unmatched and belong in front of a PM.
    cited = {str(x.get("wo_code")).strip() for x in parsed if x.get("wo_code")}
    if cited and not wos:
        log.warning(
            "contract_performance.no_work_orders_for_invoice",
            invoice_ref=invoice_ref,
            cited_codes=len(cited),
        )
        return {
            "ok": False,
            "error": "no_work_orders_ingested",
            "message": (
                f"None of the {len(cited)} work orders this invoice cites are in the "
                "system, so there is nothing to verify against. Ingest the work orders "
                "for this period first, then re-run the invoice."
            ),
            "cited_work_orders": sorted(cited)[:10],
            "parsed_lines": parsed,
            "work_orders_loaded": 0,
            "verification": None,
        }

    # The invoice names its vendor; resolve it the same way a contract does, so the
    # verification is attributed and the contract below can be found at all.
    if vendor_id is None:
        vendor_link = await _resolve_contract_vendor(
            session,
            vendor_id=None,
            vendor_name=_invoice_vendor_name(parsed, source_text),
            organization_id=organization_id,
        )
        vendor_id = vendor_link.get("vendor_id")
    if vendor_id is None and source_text:
        # _invoice_vendor_name reads a CSV-shaped pattern and finds nothing in a PDF, so an
        # uploaded invoice was attributed to no vendor at all — which also means no contract
        # is found, and the rate and parts checks are skipped on an invoice that reads as
        # fully verified. Ask the register instead: which vendor we already have is named
        # here. It cannot invent one.
        from ...shared.vendor_identity import vendor_named_in

        vendor_id = await vendor_named_in(session, source_text)
        if vendor_id:
            log.info("invoice.vendor_matched_from_document", vendor_id=str(vendor_id))

    # Contract-derived thresholds. Work orders already auto-load when the caller does not
    # supply them; these did not, and the chat upload path supplies neither — so the
    # "labour rate exceeds contracted rate" and "parts outside framework" checks were
    # skipped entirely, on an invoice that looked fully verified. Load them from the
    # vendor's CONFIRMED contract for the same reason B2 refuses to score without one:
    # disputing a vendor's invoice against a rate nobody agreed to is worse than not
    # checking, because it puts a number on it.
    contract_confirmed = True
    if labour_day_rate is None or labour_hour_rate is None or not parts_pricing_json:
        from .scoring import _load_confirmed_params

        params = await _load_confirmed_params(session, vendor_id=vendor_id)
        contract_confirmed = bool(params.get("_contract_confirmed"))
        if contract_confirmed:
            if labour_day_rate is None and params.get("labour_day_rate") is not None:
                labour_day_rate = float(params["labour_day_rate"])
            if labour_hour_rate is None and params.get("labour_hour_rate") is not None:
                labour_hour_rate = float(params["labour_hour_rate"])
            if not parts_pricing_json:
                parts_pricing_json = params.get("parts_pricing_json") or {}
        # Report on what the checks ended up with, not on where it came from. A caller that
        # passed the rate itself still gets the rate check; saying "skipped" because no
        # confirmed contract was found would be the same kind of misleading all-clear this
        # guard exists to prevent, pointed the other way.
        contract_confirmed = labour_day_rate is not None or labour_hour_rate is not None
        if not contract_confirmed:
            log.info(
                "contract_performance.invoice_rate_checks_skipped",
                invoice_ref=invoice_ref,
                reason="contract_parameters_unconfirmed",
            )

    verification = await verify_invoice(
        session,
        invoice_ref=invoice_ref,
        # Only a number the document printed identifies the invoice well enough to say
        # "this one again". A filename does not.
        invoice_ref_identifies=bool(ref_from_document),
        lines=parsed,
        work_orders=wos,
        vendor_id=vendor_id,
        organization_id=organization_id,
        document_id=document_id,
        labour_day_rate=labour_day_rate,
        labour_hour_rate=labour_hour_rate,
        parts_pricing_json=parts_pricing_json,
    )
    return {
        "ok": True,
        "parsed_lines": parsed,
        "work_orders_loaded": len(wos),
        "vendor_id": str(vendor_id) if vendor_id else None,
        # Said out loud rather than left to be inferred from an invoice that came back
        # clean: without a confirmed contract there is no rate or parts framework to
        # check against, so those two checks did not run.
        "rate_checks_ran": contract_confirmed,
        "contract_parameters_unconfirmed": not contract_confirmed,
        "verification": verification,
    }


def _invoice_vendor_name(
    parsed: list[dict[str, Any]] | None, source_text: str | None
) -> str | None:
    """The supplier named on the invoice, from a parsed line or the document text."""
    for line in parsed or []:
        name = (line.get("vendor_name") or line.get("supplier") or "").strip()
        if name:
            return name
    m = re.search(r"vendor_name[^\n]*\n[^,\n]*,[^,\n]*,\s*([^,\n]+)", source_text or "")
    return m.group(1).strip() if m else None


async def _fetch_work_orders_by_codes(
    session: AsyncSession,
    codes: list[str],
) -> list[dict[str, Any]]:
    if not codes:
        return []
    from sqlalchemy import text

    try:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, wo_code, status AS wo_status,
                           completed_at, actual_cost AS actual_cost,
                           estimated_cost AS estimated_cost,
                           -- What the invoice is actually checked against: billed hours
                           -- versus attended hours, and billed parts versus the parts on
                           -- the job. Both columns exist and neither was selected, so an
                           -- invoice verified through the upload path could not run the
                           -- hours or parts checks at all — it only ever compared costs.
                           labour_hours,
                           parts_cost,
                           asset_id::text AS asset_id
                    FROM plenum_cafm.work_orders
                    WHERE wo_code = ANY(:codes)
                    LIMIT 200
                    """
                ),
                {"codes": codes},
            )
        ).mappings().all()
        return [
            {
                "id": r["id"],
                "wo_code": r["wo_code"],
                "status": r["wo_status"],
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
                "cost_actual": float(r["actual_cost"]) if r["actual_cost"] is not None else None,
                "cost_estimated": float(r["estimated_cost"]) if r["estimated_cost"] is not None else None,
                # Selected above and then dropped here, which is why adding them to the
                # query alone changed nothing: the hours and parts checks read this dict,
                # not the row.
                "labour_hours": float(r["labour_hours"]) if r["labour_hours"] is not None else None,
                "parts_cost": float(r["parts_cost"]) if r["parts_cost"] is not None else None,
                "asset_id": r["asset_id"],
            }
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("invoice.wo_lookup_failed", error=str(exc)[:200])
        return []
