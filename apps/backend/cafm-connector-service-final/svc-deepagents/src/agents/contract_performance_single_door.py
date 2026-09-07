"""
Single Door → Feature B (Contract Performance) auto-route.

When PM uploads FM contracts, framework agreements, POs, or invoices via the
Orchestrator, classify and call svc-operations-intelligence extract APIs
(Relationships Agent mid-layer) so structured Contract/Invoice fields are
drafted for HITL confirmation.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, Literal

import structlog

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

ContractPerfKind = Literal["contract", "invoice"]

_CONTRACT_NAME = re.compile(
    r"\b(contract|framework|agreement|purchase[\s_-]?order|\bpo[\s_-]|"
    r"sla|framework[\s_-]?agreement|msa|sow)\b",
    re.I,
)
_INVOICE_NAME = re.compile(
    r"\b(invoice|inv[\s_-]|bill|credit[\s_-]?note|remittance)\b",
    re.I,
)
_CONTRACT_MSG = re.compile(
    r"\b(contract|framework agreement|purchase order|\bpo\b|sla param|"
    r"vendor agreement|fm contract)\b",
    re.I,
)
_INVOICE_MSG = re.compile(
    r"\b(invoice|bill|verify invoice|invoice check|invoice match)\b",
    re.I,
)


def classify_contract_performance_doc(
    file_path: str,
    user_query: str | None = None,
) -> ContractPerfKind | None:
    """
    Detect Feature B uploads from filename + user message.
    Invoice wins over contract if both match (invoice often cites PO/contract).
    """
    # Normalize separators so word-boundary regex works on Framework_Agreement etc.
    name = re.sub(r"[_\-.]+", " ", Path(file_path).name)
    # ...and split CamelCase, because the keywords below are word-bounded and procurement
    # portals export names like "ProcurementContractData" with the word glued inside a
    # compound. "Contract" is right there in the filename, but contract cannot see it,
    # so the document silently misses Feature B entirely unless the user happens to say
    # "contract" in their message.
    # Digits close the boundary just as tightly as case does — "Agreement2024" hides the
    # word from agreement — so break letter/digit runs apart as well.
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", " ", name)
    msg = (user_query or "").strip()

    inv_hit = bool(_INVOICE_NAME.search(name) or (msg and _INVOICE_MSG.search(msg)))
    con_hit = bool(_CONTRACT_NAME.search(name) or (msg and _CONTRACT_MSG.search(msg)))

    if inv_hit and not con_hit:
        return "invoice"
    if con_hit and not inv_hit:
        return "contract"
    if inv_hit and con_hit:
        if _INVOICE_NAME.search(name) or (msg and _INVOICE_MSG.search(msg)):
            return "invoice"
        return "contract"
    return None


# A 40-page cap silently truncated the UKRI FM contract at page 40 of 52 — dropping the
# KPI Schedule entirely. "Service Credit" appears 14 times in that document and 0 times in
# its first 40 pages. FM contracts put their SLA annexes at the back, so a cap that reads
# only the front discards precisely the part the performance engine exists to read.
_MAX_PDF_PAGES = 250


def _pdf_base64(file_path: str, *, limit: int = 28_000_000) -> str | None:
    """Base64 of the PDF, so the extractor can read tables and scanned pages visually.

    Returns None for non-PDFs and for files past the API's document limit — in both cases
    extraction falls back to the text it was already given rather than failing.
    """
    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:  # noqa: BLE001
        log.warning("single_door.cp.pdf_bytes_failed", error=str(exc)[:200])
        return None
    encoded = base64.b64encode(raw).decode()
    if len(encoded) >= limit:
        log.warning(
            "single_door.cp.pdf_too_large_for_vision",
            name=path.name,
            encoded_len=len(encoded),
        )
        return None
    return encoded


def extract_text_from_upload(file_path: str, *, max_chars: int = 50_000) -> str:
    """Best-effort text for Relationships Agent extraction."""
    path = Path(file_path)
    ext = path.suffix.lower()
    try:
        if ext in {".txt", ".md", ".csv"}:
            return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        if ext in {".docx"}:
            try:
                from docx import Document  # type: ignore

                doc = Document(str(path))
                parts = [p.text for p in doc.paragraphs if p.text]
                for table in doc.tables:
                    for row in table.rows:
                        parts.append(" | ".join(c.text for c in row.cells))
                return "\n".join(parts)[:max_chars]
            except Exception as exc:  # noqa: BLE001
                log.warning("single_door.cp.docx_read_failed", error=str(exc)[:200])
        if ext == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore

                reader = PdfReader(str(path))
                parts = []
                for page in reader.pages[:_MAX_PDF_PAGES]:
                    parts.append(page.extract_text() or "")
                text = "\n".join(parts).strip()
                if text:
                    return text[:max_chars]
            except Exception as exc:  # noqa: BLE001
                log.warning("single_door.cp.pdf_read_failed", error=str(exc)[:200])
            # Second rung, for PDFs pypdf reads as empty (many scanned or
            # producer-quirky certificates). This used to import pdfplumber, which is not a
            # dependency of this service — so the rung always raised ModuleNotFoundError and
            # the document fell through to the filename stub with no body text at all. That
            # is what left a generically-named certificate unclassifiable. PyMuPDF is
            # installed and reads these reliably.
            try:
                import pymupdf  # type: ignore

                with pymupdf.open(str(path)) as pdf:
                    parts = [page.get_text() or "" for page in pdf.pages(0, _MAX_PDF_PAGES)]
                text = "\n".join(parts).strip()
                if text:
                    return text[:max_chars]
                log.warning("single_door.cp.pdf_text_empty", path=path.name)
            except Exception as exc:  # noqa: BLE001
                log.warning("single_door.cp.pymupdf_failed", error=str(exc)[:200])
    except OSError as exc:
        log.warning("single_door.cp.read_failed", error=str(exc)[:200])

    # Fallback: filename + stub so extract still runs heuristics / Claude on thin context
    return f"Document filename: {path.name}\n(User uploaded via Single Door Orchestrator.)"


async def route_contract_performance_upload(
    *,
    file_path: str,
    organization_id: str | None = None,
    user_query: str | None = None,
    document_id: str | None = None,
) -> dict[str, Any] | None:
    """
    If the upload is a contract/PO/invoice, call Feature B extract APIs.
    Returns None when not a Feature B document.
    """
    kind = classify_contract_performance_doc(file_path, user_query)
    if kind is None:
        return None

    # Whole document, not the first 50k: extraction downstream selects the relevant
    # passages itself, and can only select from what it is given.
    source_text = extract_text_from_upload(file_path, max_chars=400_000)
    base = settings.operations_intelligence_base_url.rstrip("/")
    name = Path(file_path).name

    try:
        if kind == "contract":
            resp = await _request(
                "POST",
                base,
                "/api/contract-performance/contracts/extract",
                service="operations_intelligence",
                timeout=90.0,
                json={
                    "source_text": source_text,
                    "pdf_base64": _pdf_base64(file_path),
                    "organization_id": organization_id,
                    "document_id": document_id,
                    "contract_ref": Path(file_path).stem,
                    "auto_ingest": True,
                },
            )
            body = resp.json()
            params_id = body.get("parameters_id") or (body.get("ingest") or {}).get(
                "parameters", {}
            ).get("id")
            defaults = list(body.get("defaults_used") or [])
            found = {k: v for k, v in (body.get("extracted") or {}).items() if v is not None}
            vendor = body.get("vendor") or {}
            vendor_note = ""
            if vendor.get("status") == "created":
                vendor_note = f" Vendor '{vendor.get('vendor_name')}' was new and is now registered."
            elif vendor.get("status") == "matched":
                vendor_note = f" Linked to existing vendor '{vendor.get('vendor_name')}'."
            elif vendor.get("status") == "not_named_in_contract":
                vendor_note = " No service provider named in the document — contract is unlinked."
            summary = (
                f"Contract Performance B1: extracted {len(found)} parameters from {name} "
                f"— {len(defaults)} not stated by the contract and defaulted."
                f"{vendor_note} Scoring stays blocked until these are confirmed."
            )
            return {
                "kind": "contract",
                "ok": bool(body.get("ok", True)),
                "summary": summary,
                "result": body,
                "parameters_id": params_id,
                # Everything the chat card needs to show the extraction inline, so the PM
                # can see what was read without navigating away to find out.
                "extraction": {
                    "document_name": name,
                    "parameters_id": params_id,
                    "vendor": vendor,
                    "extracted": body.get("extracted") or {},
                    "field_confidence": body.get("field_confidence") or {},
                    "field_sources": body.get("field_sources") or {},
                    "defaults_used": defaults,
                    "preview_with_defaults": body.get("preview_with_defaults") or {},
                    "source_truncated": body.get("source_truncated"),
                },
            }

        # invoice
        resp = await _request(
            "POST",
            base,
            "/api/contract-performance/invoices/extract-verify",
            service="operations_intelligence",
            timeout=90.0,
            json={
                "source_text": source_text,
                "organization_id": organization_id,
                "document_id": document_id,
                "invoice_ref": Path(file_path).stem,
            },
        )
        body = resp.json()
        verification = body.get("verification") or body
        matched = verification.get("matched_count")
        flagged = verification.get("flagged_count")
        summary = (
            f"Contract Performance B3: verified invoice {name} — "
            f"matched={matched}, flagged={flagged}. "
            f"Review flagged lines in Approvals (Feature B); £500+ went through Adversary."
        )
        return {
            "kind": "invoice",
            "ok": bool(body.get("ok", True)),
            "summary": summary,
            "result": body,
            "verification_id": verification.get("verification_id"),
        }
    except Exception as exc:  # noqa: BLE001
        log.error(
            "single_door.contract_performance.route_failed",
            kind=kind,
            file=name,
            error=str(exc)[:300],
        )
        return {
            "kind": kind,
            "ok": False,
            "summary": f"Feature B {kind} route failed for {name}: {exc}",
            "error": str(exc)[:500],
            "result": None,
        }
