"""
svc-ingestion/src/agents/word_agent.py

Task 2.3 — DOCX Agent (Layer 2, Stage 2).

Extracts structured CAFM data from .docx files using python-docx + Claude Sonnet.
Handles the known 7-section site inspection report format (Sections A–G).

Behaviour:
  - python-docx: scans all paragraphs and tables for label:value pairs
  - Claude Sonnet: extracts structured JSON from assembled document text
  - EL-2.0: file type + structure validation (enforced by Stage 1 ingest.py)
  - EL-2.1: validate Claude returned parseable JSON (retry ×3 with error context)
  - EL-2.2: Pydantic validates intermediate JSON schema
  - EL-2.3: LLM-as-judge (Haiku) reviews extraction vs source → eval_score
  - Confidence routing: accept (≥0.85) / review_queue (0.60–0.84) / re-extract (<0.60)
  - Writes to `inspections` table via SQLAlchemy (one row per section finding)
  - Dual path: SOP/manual docs also queued for pgvector embedding (Tier 3)

Output: IntermediateSchema (the shared pipeline contract).

OTel spans:
  ingestion.stage2.extract       — full extraction + eval
  ingestion.eval.extraction_output — EL-2.1
  ingestion.eval.schema_conformance — EL-2.2
  ingestion.eval.llm_judge         — EL-2.3
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date, timedelta
from io import BytesIO
from typing import Any
from uuid import UUID

import anthropic
from docx import Document
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncEngine

from cafm_shared.logging import get_logger
from shared.doc_embedder import chunk_and_store
from shared.intermediate_schema import (
    AgentId,
    AuditInfo,
    ConfidenceLevel,
    ConfidenceResult,
    EntitiesBlock,
    ExtractionMethod,
    FindingEntity,
    IntermediateSchema,
    ModelUsed,
    SourceType,
    TechnicianEntity,
    VendorEntity,
)

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_HAIKU_MODEL = "claude-haiku-4-5"
_SONNET_MODEL = "claude-sonnet-4-6"

_COST_PER_INPUT_TOKEN: dict[str, float] = {
    _HAIKU_MODEL: 0.00000025,
    _SONNET_MODEL: 0.000003,
}
_COST_PER_OUTPUT_TOKEN: dict[str, float] = {
    _HAIKU_MODEL: 0.00000125,
    _SONNET_MODEL: 0.000015,
}

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0

# Sections of the known 7-section site inspection report
_SECTION_MAP: dict[str, str] = {
    "A": "Inspector / Date / Location",
    "B": "Erosion / Sediment Controls",
    "C": "Pollution Prevention",
    "D": "Stabilisation Areas",
    "E": "Discharge Observations",
    "F": "Signature / Certification",
    "G": "Additional Notes / Observations",
}

# EL-2.3 routing thresholds
_EVAL_SCORE_ACCEPT: float = 0.85   # auto-accept path
_EVAL_SCORE_REVIEW: float = 0.60   # HITL review queue (0.60–0.84)
# below 0.60 → re-extract (max 3 total attempts) → manual_only if all fail

# Haiku eval call approximate token count (for cost accounting)
_EVAL_TOKEN_ESTIMATE: int = 350


# ── Document reading helpers ───────────────────────────────────────────────────


def _extract_document_content(doc: Document) -> dict[str, Any]:
    """
    Scan all paragraphs and table cells from a python-docx Document.

    Iterates the document body in order (python-docx body elements) so that
    table rows appear immediately after the paragraph that precedes them —
    rather than all paragraphs first and all tables last. This ensures that
    short documents where all content is in tables (contracts, invoices) produce
    meaningful chunks when passed to the chunker.

    Returns:
        {
          "paragraphs": [str, ...],
          "tables": [{"rows": [[cell_text, ...], ...]}, ...],
          "full_text": str,   # concatenated text in document order
        }
    """
    paragraphs: list[str] = []
    tables: list[dict[str, Any]] = []
    lines: list[str] = []

    for child in doc.element.body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        if tag == "p":
            # Paragraph
            text = "".join(
                node.text or ""
                for node in child.iter()
                if node.tag.endswith("}t") or node.tag == "t"
            ).strip()
            if text:
                paragraphs.append(text)
                lines.append(text)

        elif tag == "tbl":
            # Table — collect rows inline (preserves document order)
            rows: list[list[str]] = []
            seen_tr: set[int] = set()
            for elem in child.iter():
                elem_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if elem_tag == "tr" and id(elem) not in seen_tr:
                    seen_tr.add(id(elem))
                    cells: list[str] = []
                    for sub in elem.iter():
                        sub_tag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                        if sub_tag == "tc":
                            cell_text = "".join(
                                node.text or ""
                                for node in sub.iter()
                                if (node.tag.split("}")[-1] if "}" in node.tag else node.tag) == "t"
                            ).strip()
                            cells.append(cell_text)
                    if any(cells):
                        rows.append(cells)
                        row_line = " | ".join(c for c in cells if c)
                        if row_line:
                            lines.append(row_line)
            if rows:
                tables.append({"rows": rows})

    return {
        "paragraphs": paragraphs,
        "tables": tables,
        "full_text": "\n".join(lines),
    }


def _detect_document_type(doc_content: dict[str, Any]) -> str:
    """
    Heuristic: classify DOCX into one of three document types.

    Returns: "inspection_report" | "vendor_contract" | "sop_or_manual"
    """
    text_upper = doc_content["full_text"].upper()

    # ── Vendor contract detection ──────────────────────────────────────────
    contract_hits = sum(
        1
        for term in (
            "CONTRACT NUMBER", "CONTRACT START DATE", "CONTRACT END DATE",
            "CONTRACT VALUE", "MAINTENANCE CONTRACT", "ANNUAL MAINTENANCE",
            "SCOPE OF WORK", "PENALTY CLAUSE", "TERMINATION CLAUSE",
        )
        if term in text_upper
    )
    if contract_hits >= 3:
        return "vendor_contract"

    # ── Inspection report detection ────────────────────────────────────────
    section_hits = sum(
        1
        for label in ("SECTION A", "SECTION B", "SECTION C", "SECTION D", "SECTION E")
        if label in text_upper
    )
    short_hits = sum(
        1
        for marker in ("EROSION", "SEDIMENT", "POLLUTION PREVENTION", "INSPECTOR")
        if marker in text_upper
    )
    if section_hits >= 2 or short_hits >= 2:
        return "inspection_report"

    return "sop_or_manual"


# ── Prompt builders ────────────────────────────────────────────────────────────


def _build_extraction_prompt(doc_content: dict[str, Any], retry_context: str = "") -> str:
    """Build the Claude Sonnet extraction prompt for a 7-section inspection report."""
    # Cap document text to avoid token overflow (12k chars ≈ ~3k tokens)
    full_text = doc_content["full_text"][:12_000]

    # Include up to 10 tables, up to 20 rows each
    tables_block = ""
    for i, tbl in enumerate(doc_content["tables"][:10]):
        row_lines = "\n".join(
            "  " + " | ".join(cell for cell in row if cell)
            for row in tbl["rows"][:20]
        )
        tables_block += f"\n[Table {i + 1}]\n{row_lines}\n"

    retry_note = (
        f"\n\nIMPORTANT — previous attempt failed: {retry_context}. "
        "Return ONLY valid JSON with no extra text.\n"
        if retry_context
        else ""
    )

    sections_desc = "\n".join(
        f"  Section {letter}: {desc}" for letter, desc in _SECTION_MAP.items()
    )

    return (
        "You are a CAFM data extraction specialist. "
        "Extract structured data from this site inspection report document.\n\n"
        "## Document text:\n"
        f"{full_text}\n\n"
        "## Tables:\n"
        f"{tables_block}\n\n"
        "## Known sections (A–G):\n"
        f"{sections_desc}\n\n"
        "## Instructions:\n"
        "1. Extract inspector name, inspection date (YYYY-MM-DD), and location.\n"
        "2. For each section (A–G), extract findings: finding_type, observations, "
        "risk_level (High|Medium|Low|None), requires_corrective_action (true|false).\n"
        "3. Include asset_code if identifiable from the document.\n"
        "4. Assign confidence for key fields: high|medium|low.\n"
        "5. Return ONLY valid JSON — no markdown fences, no extra text.\n\n"
        "## Required JSON format:\n"
        '{"inspector_name": "John Smith", '
        '"inspection_date": "2026-03-25", '
        '"inspection_location": "Building A — Floor 2", '
        '"asset_code": "MOB-AHU-001", '
        '"sections": ['
        '{"section": "B", "finding_type": "Sediment barrier failure", '
        '"observations": "Barrier displaced on east side", '
        '"risk_level": "High", "requires_corrective_action": true}'
        "], "
        '"technician_name": "Jane Doe", '
        '"confidence_per_field": {"inspector_name": "high", '
        '"inspection_date": "high", "asset_code": "medium"}}'
        + retry_note
    )


def _build_eval_prompt(source_excerpt: str, extracted_json: str) -> str:
    """Build EL-2.3 LLM-as-judge prompt for Haiku."""
    return (
        "You are a CAFM data quality evaluator (EL-2.3 LLM-as-judge).\n\n"
        "## Source document excerpt (first 4000 chars):\n"
        f"{source_excerpt[:4_000]}\n\n"
        "## Extracted JSON:\n"
        f"{extracted_json[:3_000]}\n\n"
        "Evaluate whether the extraction faithfully represents the source document.\n"
        "Check for:\n"
        "  - Missing or incomplete findings\n"
        "  - Wrong risk levels (e.g. source says Critical but extraction says Low)\n"
        "  - Incorrect or fabricated dates\n"
        "  - Fabricated asset codes not in the document\n"
        "  - Contradictions (e.g. 'Normal' observation + 'High' risk)\n\n"
        "Return ONLY valid JSON — no markdown:\n"
        '{"eval_score": 0.0, "contradictions": ["..."], '
        '"missing_fields": ["..."], "verdict": "pass|review|reject"}'
    )


def _build_contract_prompt(doc_content: dict[str, Any], retry_context: str = "") -> str:
    """Build the extraction prompt for a vendor/maintenance contract document."""
    full_text = doc_content["full_text"][:10_000]

    tables_block = ""
    for i, tbl in enumerate(doc_content["tables"][:5]):
        row_lines = "\n".join(
            "  " + " | ".join(cell for cell in row if cell)
            for row in tbl["rows"][:30]
        )
        tables_block += f"\n[Table {i + 1}]\n{row_lines}\n"

    retry_note = (
        f"\n\nIMPORTANT — previous attempt failed: {retry_context}. "
        "Return ONLY valid JSON with no extra text.\n"
        if retry_context
        else ""
    )

    return (
        "You are a CAFM contract data extraction specialist. "
        "Extract key information from this maintenance contract document.\n\n"
        "## Document text:\n"
        f"{full_text}\n\n"
        "## Tables:\n"
        f"{tables_block}\n\n"
        "## Instructions:\n"
        "1. Extract contract_number, client_name, vendor_name.\n"
        "2. Extract contract_start_date and contract_end_date as YYYY-MM-DD.\n"
        "3. Extract contract_value as a number only (no currency symbol).\n"
        "4. Extract currency (e.g. AED, USD).\n"
        "5. Extract scope_of_work (brief summary).\n"
        "6. Extract payment_terms.\n"
        "7. Extract renewal_review_days — the number of days before expiry for review "
        "(e.g. if 'reviewed 45 days before expiry' → 45).\n"
        "8. Extract sla_summary — penalty clause and response time requirements.\n"
        "9. Return ONLY valid JSON — no markdown fences, no extra text.\n\n"
        "## Required JSON format:\n"
        '{"contract_number": "AMC-2026-010", '
        '"client_name": "ABC Properties LLC", '
        '"vendor_name": "CoolTech Maintenance LLC", '
        '"contract_start_date": "2026-01-01", '
        '"contract_end_date": "2026-12-31", '
        '"contract_value": 120000, '
        '"currency": "AED", '
        '"scope_of_work": "Preventive and corrective HVAC maintenance", '
        '"payment_terms": "Quarterly upon invoice", '
        '"renewal_review_days": 45, '
        '"sla_summary": "5% penalty per incident exceeding SLA", '
        '"confidence_per_field": {"contract_end_date": "high", "contract_value": "high"}}'
        + retry_note
    )


# ── Claude API helpers ─────────────────────────────────────────────────────────


async def _call_claude_with_retry(
    client: anthropic.AsyncAnthropic,
    prompt: str,
    model: str,
    max_tokens: int = 2048,
) -> tuple[str, int, int]:
    """
    Call Claude with 3× exponential backoff on rate-limit / server errors.

    Returns:
        (response_text, tokens_in, tokens_out)
    """
    last_exc: Exception | None = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text: str = response.content[0].text  # type: ignore[union-attr]
            return text, response.usage.input_tokens, response.usage.output_tokens
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "word_agent.claude_retry",
                    attempt=attempt + 1,
                    delay_s=delay,
                    model=model,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        except Exception as exc:
            raise  # non-retryable (JSON errors, auth errors, etc.)

    raise RuntimeError(
        f"word_agent: Claude {model} failed after {_RETRY_ATTEMPTS} attempts"
    ) from last_exc


# ── EL-2.x evaluation functions ───────────────────────────────────────────────


def _el_2_1_parse_json(raw_text: str) -> dict[str, Any] | None:
    """
    EL-2.1 — Raw extraction output validation.

    Strips markdown fences, parses JSON, checks required keys.
    Returns parsed dict on success, None on failure.
    """
    text = raw_text.strip()
    # Strip opening/closing markdown fences if present
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            text = inner
    text = text.strip()

    try:
        parsed: dict[str, Any] = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    # Required top-level keys
    if "sections" not in parsed:
        return None
    if not isinstance(parsed["sections"], list):
        return None

    return parsed


def _el_2_2_build_intermediate(
    parsed: dict[str, Any],
    ingestion_id: UUID,
    source_filename: str,
    blob_url: str | None,
) -> IntermediateSchema | None:
    """
    EL-2.2 — Intermediate JSON schema conformance.

    Builds and validates an IntermediateSchema from parsed Claude output.
    Returns None if schema construction fails.
    """
    try:
        findings: list[FindingEntity] = []
        for sec in parsed.get("sections", []):
            finding = FindingEntity(
                asset_code=parsed.get("asset_code"),
                severity=sec.get("risk_level"),
                description=sec.get("observations"),
                recommendation=None,
                location=parsed.get("inspection_location"),
                extra={
                    "section": sec.get("section", ""),
                    "finding_type": sec.get("finding_type", ""),
                    "requires_corrective_action": bool(
                        sec.get("requires_corrective_action", False)
                    ),
                    "inspector_name": parsed.get("inspector_name", ""),
                    "inspection_date": parsed.get("inspection_date", ""),
                },
            )
            findings.append(finding)

        technicians: list[TechnicianEntity] = []
        tech_name = parsed.get("technician_name") or parsed.get("inspector_name")
        if tech_name:
            technicians.append(TechnicianEntity(name=tech_name))

        entities = EntitiesBlock(findings=findings, technicians=technicians)

        # Map per-field confidence strings to ConfidenceLevel enum
        per_field: dict[str, ConfidenceLevel] = {}
        for field_name, level_str in parsed.get("confidence_per_field", {}).items():
            try:
                per_field[field_name] = ConfidenceLevel(str(level_str).lower())
            except ValueError:
                per_field[field_name] = ConfidenceLevel.LOW

        return IntermediateSchema(
            ingestion_id=ingestion_id,
            source_type=SourceType.WORD,
            agent_id=AgentId.WORD,
            source_filename=source_filename,
            source_blob_url=blob_url,
            extraction_method=ExtractionMethod.PANDOC_CLAUDE,
            model_used=ModelUsed.SONNET,
            entities=entities,
            confidence=ConfidenceResult(
                overall=ConfidenceLevel.MEDIUM,  # updated after EL-2.3
                per_field=per_field,
                eval_score=0.0,
                rules_passed=False,
            ),
        )
    except (ValueError, TypeError, KeyError):
        return None


def _el_2_1_parse_contract_json(raw_text: str) -> dict[str, Any] | None:
    """
    EL-2.1 for vendor contracts — checks for contract-specific required keys.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 2:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            text = inner
    text = text.strip()
    try:
        parsed: dict[str, Any] = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    # At least one contract date must be present
    if not parsed.get("contract_end_date") and not parsed.get("contract_start_date"):
        return None
    return parsed


def _el_2_2_build_contract_intermediate(
    parsed: dict[str, Any],
    ingestion_id: UUID,
    source_filename: str,
    blob_url: str | None,
) -> IntermediateSchema | None:
    """EL-2.2 for vendor contracts — builds IntermediateSchema with VendorEntity."""
    try:
        vendor = VendorEntity(
            name=parsed.get("vendor_name") or "Unknown Vendor",
            contract_number=parsed.get("contract_number"),
            contract_start=parsed.get("contract_start_date"),
            contract_end=parsed.get("contract_end_date"),
            extra={
                "client_name": parsed.get("client_name"),
                "contract_value": parsed.get("contract_value"),
                "currency": parsed.get("currency"),
                "scope_of_work": parsed.get("scope_of_work"),
                "payment_terms": parsed.get("payment_terms"),
                "renewal_review_days": parsed.get("renewal_review_days"),
                "sla_summary": parsed.get("sla_summary"),
            },
        )
        entities = EntitiesBlock(vendors=[vendor])

        per_field: dict[str, ConfidenceLevel] = {}
        for field_name, level_str in parsed.get("confidence_per_field", {}).items():
            try:
                per_field[field_name] = ConfidenceLevel(str(level_str).lower())
            except ValueError:
                per_field[field_name] = ConfidenceLevel.LOW

        return IntermediateSchema(
            ingestion_id=ingestion_id,
            source_type=SourceType.WORD,
            agent_id=AgentId.WORD,
            source_filename=source_filename,
            source_blob_url=blob_url,
            extraction_method=ExtractionMethod.PANDOC_CLAUDE,
            model_used=ModelUsed.SONNET,
            entities=entities,
            confidence=ConfidenceResult(
                overall=ConfidenceLevel.MEDIUM,
                per_field=per_field,
                eval_score=0.0,
                rules_passed=False,
            ),
        )
    except (ValueError, TypeError, KeyError):
        return None


async def _el_2_3_llm_judge(
    client: anthropic.AsyncAnthropic,
    source_excerpt: str,
    extracted_json: str,
) -> tuple[float, list[str], bool, int, int]:
    """
    EL-2.3 — LLM-as-judge using Haiku.

    Returns:
        (eval_score, contradictions, rules_passed, tokens_in, tokens_out)
    """
    prompt = _build_eval_prompt(source_excerpt, extracted_json)
    try:
        text, tok_in, tok_out = await _call_claude_with_retry(
            client, prompt, model=_HAIKU_MODEL, max_tokens=512
        )
        text = text.strip()
        # Strip markdown fences
        if text.startswith("```"):
            parts = text.split("```", 2)
            inner = parts[1][4:] if parts[1].startswith("json") else parts[1]
            text = inner.strip()

        result: dict[str, Any] = json.loads(text)
        eval_score = round(float(result.get("eval_score", 0.0)), 3)
        contradictions: list[str] = list(result.get("contradictions", []))
        verdict = str(result.get("verdict", "reject"))
        rules_passed = verdict == "pass"
        return eval_score, contradictions, rules_passed, tok_in, tok_out
    except Exception as exc:
        logger.warning("word_agent.llm_judge_failed", error=str(exc))
        return 0.0, [f"eval error: {exc}"], False, 0, 0


# ── Inspections table writer ───────────────────────────────────────────────────


async def _write_inspections_rows(
    engine: AsyncEngine,
    parsed: dict[str, Any],
    blob_url: str | None,
    ingestion_id: UUID,
) -> int:
    """
    Write one row per section finding to plenum_cafm.inspections.

    EL-4.0: rows with no observations are skipped (logged, not silently dropped).
    Returns number of rows successfully written.
    """
    inspector = parsed.get("inspector_name") or ""
    inspection_date_raw = parsed.get("inspection_date") or None
    asset_code = parsed.get("asset_code") or None
    sections = parsed.get("sections", [])
    findings_jsonb = json.dumps(parsed, default=str)

    rows_written = 0

    async with engine.begin() as conn:
        for sec in sections:
            section_label = str(sec.get("section", ""))
            finding_type = str(sec.get("finding_type") or "")
            observations = str(sec.get("observations") or "")
            risk_level = str(sec.get("risk_level") or "Low")
            corrective_action = bool(sec.get("requires_corrective_action", False))

            # EL-4.0: skip section rows with no actual observations
            if not observations:
                logger.warning(
                    "word_agent.skipped_empty_section",
                    ingestion_id=str(ingestion_id),
                    section=section_label,
                )
                continue

            # Normalise risk_level to allowed enum values
            if risk_level not in ("High", "Medium", "Low"):
                risk_level = "Low"

            await conn.execute(
                sa_text(
                    "INSERT INTO plenum_cafm.inspections "
                    "(asset_code, inspector, inspection_date, section, finding_type, "
                    "observations, risk_level, corrective_action, source_file, findings_jsonb) "
                    "VALUES (:asset_code, :inspector, :inspection_date::date, :section, "
                    ":finding_type, :observations, :risk_level, :corrective_action, "
                    ":source_file, :findings_jsonb::jsonb)"
                ),
                {
                    "asset_code": asset_code,
                    "inspector": inspector,
                    "inspection_date": inspection_date_raw,
                    "section": section_label,
                    "finding_type": finding_type,
                    "observations": observations,
                    "risk_level": risk_level,
                    "corrective_action": corrective_action,
                    "source_file": blob_url or "",
                    "findings_jsonb": findings_jsonb,
                },
            )
            rows_written += 1

    return rows_written


# ── Contract writer ───────────────────────────────────────────────────────────


def _parse_contract_date(raw: str | None) -> str | None:
    """Parse a date string into YYYY-MM-DD, returning None if unparseable."""
    if not raw:
        return None
    raw = raw.strip()
    # Already ISO format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    # "01 January 2026" or "1 Jan 2026"
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
        "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", raw)
    if m:
        day, mon_str, year = m.group(1), m.group(2).lower()[:3], m.group(3)
        mon = months.get(mon_str)
        if mon:
            return f"{year}-{mon}-{day.zfill(2)}"
    return None


async def _write_vendor_contract(
    engine: AsyncEngine,
    parsed: dict[str, Any],
    blob_url: str | None,
    ingestion_id: UUID,
) -> tuple[str | None, date | None]:
    """
    Write a vendor contract row to plenum_cafm.vendor_contracts.

    Looks up vendor and organization by name.
    Creates stub vendor row if not found (avoids FK failure).
    Returns (contract_id, contract_end_date) for notification creation.
    """
    vendor_name = str(parsed.get("vendor_name") or "Unknown Vendor")
    client_name = str(parsed.get("client_name") or "")
    contract_name = str(parsed.get("contract_number") or parsed.get("contract_name") or "Unknown Contract")
    start_str = _parse_contract_date(parsed.get("contract_start_date"))
    end_str = _parse_contract_date(parsed.get("contract_end_date"))
    contract_value_raw = parsed.get("contract_value")
    sla_summary = parsed.get("sla_summary") or ""
    scope = parsed.get("scope_of_work") or ""
    payment_terms = parsed.get("payment_terms") or ""
    sla_terms = f"Scope: {scope}\nPayment: {payment_terms}\nSLA: {sla_summary}".strip()

    try:
        contract_value = float(str(contract_value_raw).replace(",", "")) if contract_value_raw else None
    except (ValueError, TypeError):
        contract_value = None

    contract_end_date: date | None = None
    if end_str:
        try:
            contract_end_date = date.fromisoformat(end_str)
        except ValueError:
            pass

    async with engine.begin() as conn:
        # ── Look up or create vendor ───────────────────────────────────────
        row = await conn.execute(
            sa_text(
                "SELECT id FROM plenum_cafm.vendors WHERE LOWER(name) = LOWER(:name) LIMIT 1"
            ),
            {"name": vendor_name},
        )
        vendor_row = row.fetchone()

        if vendor_row:
            vendor_id = str(vendor_row[0])
        else:
            vendor_id_result = await conn.execute(
                sa_text(
                    "INSERT INTO plenum_cafm.vendors (id, name, service_type, country) "
                    "VALUES (gen_random_uuid(), :name, 'Maintenance', 'UAE') RETURNING id"
                ),
                {"name": vendor_name},
            )
            vendor_id = str(vendor_id_result.fetchone()[0])
            logger.info("word_agent.vendor_created", vendor_name=vendor_name, vendor_id=vendor_id)

        # ── Look up organization ───────────────────────────────────────────
        if client_name:
            org_row = await conn.execute(
                sa_text(
                    "SELECT id FROM plenum_cafm.organizations WHERE LOWER(name) = LOWER(:name) LIMIT 1"
                ),
                {"name": client_name},
            )
            org_result = org_row.fetchone()
        else:
            org_result = None

        if not org_result:
            # Fall back to first organization in the DB
            org_row = await conn.execute(
                sa_text("SELECT id FROM plenum_cafm.organizations LIMIT 1")
            )
            org_result = org_row.fetchone()

        if not org_result:
            logger.warning(
                "word_agent.no_organization_found",
                ingestion_id=str(ingestion_id),
                client_name=client_name,
            )
            return None, contract_end_date

        org_id = str(org_result[0])

        # ── Upsert vendor_contracts (contract_name + vendor_id as natural key) ─
        existing = await conn.execute(
            sa_text(
                "SELECT id FROM plenum_cafm.vendor_contracts "
                "WHERE contract_name = :name AND vendor_id = :vendor_id::uuid LIMIT 1"
            ),
            {"name": contract_name, "vendor_id": vendor_id},
        )
        existing_row = existing.fetchone()

        if existing_row:
            contract_id = str(existing_row[0])
            await conn.execute(
                sa_text(
                    "UPDATE plenum_cafm.vendor_contracts SET "
                    "contract_start = :start, contract_end = :end, "
                    "contract_value = :value, sla_terms = :sla, "
                    "contract_document = :doc "
                    "WHERE id = :id::uuid"
                ),
                {
                    "start": start_str,
                    "end": end_str,
                    "value": contract_value,
                    "sla": sla_terms,
                    "doc": blob_url or "",
                    "id": contract_id,
                },
            )
        else:
            result = await conn.execute(
                sa_text(
                    "INSERT INTO plenum_cafm.vendor_contracts "
                    "(id, organization_id, vendor_id, contract_name, "
                    "contract_start, contract_end, contract_value, sla_terms, contract_document) "
                    "VALUES (gen_random_uuid(), :org_id::uuid, :vendor_id::uuid, :name, "
                    ":start::date, :end::date, :value, :sla, :doc) "
                    "RETURNING id"
                ),
                {
                    "org_id": org_id,
                    "vendor_id": vendor_id,
                    "name": contract_name,
                    "start": start_str,
                    "end": end_str,
                    "value": contract_value,
                    "sla": sla_terms,
                    "doc": blob_url or "",
                },
            )
            contract_id = str(result.fetchone()[0])

    logger.info(
        "word_agent.vendor_contract_written",
        ingestion_id=str(ingestion_id),
        contract_id=contract_id,
        contract_name=contract_name,
        vendor_name=vendor_name,
        contract_end=str(contract_end_date),
    )
    return contract_id, contract_end_date


async def _create_expiry_notification(
    engine: AsyncEngine,
    contract_id: str,
    contract_end: date,
    contract_name: str,
    vendor_name: str,
    renewal_review_days: int,
    org_id_raw: str | None,
) -> None:
    """
    Create a notification record for contract expiry review.

    Creates two notifications:
      - Now: "Contract ingested — review reminder set"
      - Alert marker at (contract_end - renewal_review_days): stored as a second
        notification with the scheduled review date in the message body so the
        frontend/scheduler can filter by it.
    """
    review_date = contract_end - timedelta(days=renewal_review_days)
    today = date.today()
    days_until_review = (review_date - today).days

    async with engine.begin() as conn:
        # Resolve org_id
        if org_id_raw:
            org_id = org_id_raw
        else:
            row = await conn.execute(
                sa_text("SELECT id FROM plenum_cafm.organizations LIMIT 1")
            )
            result = row.fetchone()
            org_id = str(result[0]) if result else None

        if not org_id:
            return

        # Ingestion receipt notification
        await conn.execute(
            sa_text(
                "INSERT INTO plenum_cafm.notifications "
                "(id, organization_id, title, message, type, entity_type, entity_id) "
                "VALUES (gen_random_uuid(), :org_id::uuid, :title, :message, :type, :entity_type, :entity_id::uuid)"
            ),
            {
                "org_id": org_id,
                "title": f"Contract ingested: {contract_name}",
                "message": (
                    f"Vendor contract '{contract_name}' with {vendor_name} has been ingested. "
                    f"Contract expires on {contract_end.isoformat()}. "
                    f"Review reminder set for {review_date.isoformat()} "
                    f"({renewal_review_days} days before expiry)."
                ),
                "type": "contract_ingested",
                "entity_type": "vendor_contract",
                "entity_id": contract_id,
            },
        )

        # Expiry alert notification (tagged with review_date for scheduler filtering)
        urgency = "URGENT" if days_until_review <= 0 else (
            "SOON" if days_until_review <= 30 else "UPCOMING"
        )
        await conn.execute(
            sa_text(
                "INSERT INTO plenum_cafm.notifications "
                "(id, organization_id, title, message, type, entity_type, entity_id) "
                "VALUES (gen_random_uuid(), :org_id::uuid, :title, :message, :type, :entity_type, :entity_id::uuid)"
            ),
            {
                "org_id": org_id,
                "title": f"[{urgency}] Contract renewal review due: {contract_name}",
                "message": (
                    f"Contract '{contract_name}' with {vendor_name} is due for renewal review "
                    f"on {review_date.isoformat()} ({days_until_review} days from today). "
                    f"Contract end date: {contract_end.isoformat()}."
                ),
                "type": "contract_expiry_alert",
                "entity_type": "vendor_contract",
                "entity_id": contract_id,
            },
        )

    logger.info(
        "word_agent.expiry_notifications_created",
        contract_id=contract_id,
        contract_end=str(contract_end),
        review_date=str(review_date),
        days_until_review=days_until_review,
    )


# ── pgvector RAG path (ALL DOCX files) ────────────────────────────────────────


async def _store_pgvector_chunks(
    doc_content: dict[str, Any],
    source_filename: str,
    ingestion_id: UUID,
    doc_type: str,
    client: anthropic.AsyncAnthropic,
    engine: AsyncEngine,
) -> int:
    """
    Primary RAG path: chunk and embed DOCX content into plenum_cafm.document_chunks.
    Called for every DOCX file — inspection reports AND SOPs/manuals.
    Returns number of chunks stored (0 if document is too short to chunk).
    """
    chunks_stored = await chunk_and_store(
        doc_content["full_text"],
        ingestion_id=ingestion_id,
        source_filename=source_filename,
        doc_type=doc_type,
        client=client,
        engine=engine,
    )
    logger.info(
        "word_agent.pgvector_chunks_stored",
        ingestion_id=str(ingestion_id),
        source_filename=source_filename,
        doc_type=doc_type,
        chunks_stored=chunks_stored,
    )
    return chunks_stored


# ── Helper: build a failed IntermediateSchema ──────────────────────────────────


def _failed_schema(
    ingestion_id: UUID,
    source_filename: str,
    blob_url: str | None,
    violation: str,
    tokens_in: int,
    tokens_out: int,
    processing_ms: int,
) -> IntermediateSchema:
    cost_usd = round(
        tokens_in * _COST_PER_INPUT_TOKEN[_SONNET_MODEL]
        + tokens_out * _COST_PER_OUTPUT_TOKEN[_SONNET_MODEL],
        6,
    )
    return IntermediateSchema(
        ingestion_id=ingestion_id,
        source_type=SourceType.WORD,
        agent_id=AgentId.WORD,
        source_filename=source_filename,
        source_blob_url=blob_url,
        extraction_method=ExtractionMethod.PANDOC_CLAUDE,
        model_used=ModelUsed.SONNET,
        entities=EntitiesBlock(),
        confidence=ConfidenceResult(
            overall=ConfidenceLevel.LOW,
            eval_score=0.0,
            rules_passed=False,
            rules_violations=[violation],
        ),
        audit=AuditInfo(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            processing_ms=processing_ms,
        ),
    )


# ── Public API ─────────────────────────────────────────────────────────────────


async def extract_docx(
    docx_bytes: bytes,
    *,
    source_filename: str,
    ingestion_id: UUID,
    blob_url: str | None,
    client: anthropic.AsyncAnthropic,
    engine: AsyncEngine,
    dry_run: bool = False,
) -> IntermediateSchema:
    """
    Extract CAFM entities from a .docx inspection report.

    Pipeline:
        1. python-docx parse → full text + table scan
        2. Detect document type (inspection_report vs sop_or_manual)
        3. RAG primary path: chunk_and_store() into pgvector for ALL DOCX files
        4. EL-2.1: Claude Sonnet extract → validate JSON (retry ×3)
        5. EL-2.2: Pydantic validate IntermediateSchema
        6. EL-2.3: Haiku LLM-as-judge → eval_score + contradiction check
        7. Confidence routing: accept / review_queue / re-extract
        8. EL-4.0: Write to inspections table (accept + inspection_report path only)

    Args:
        docx_bytes:       Raw .docx bytes
        source_filename:  Original filename (logged + written to source_file column)
        ingestion_id:     UUID from Stage 1 ingestion_documents record
        blob_url:         Azure Blob URL of original file (None in tests)
        client:           Async Anthropic client
        engine:           SQLAlchemy async engine (for inspections INSERT)

    Returns:
        IntermediateSchema — confidence.overall + eval_score reflect routing decision.
        On hard failure (EL-2.1 all retries): returns LOW confidence schema with
        violation logged — caller (confidence_router) routes to review_queue.
    """
    t0 = time.monotonic()
    tokens_in_total = 0
    tokens_out_total = 0
    tokens_eval_in = 0
    tokens_eval_out = 0

    with tracer.start_as_current_span("ingestion.stage2.extract") as span:
        span.set_attribute("cafm.ingestion_id", str(ingestion_id))
        span.set_attribute("cafm.agent_id", AgentId.WORD.value)
        span.set_attribute("cafm.source_type", SourceType.WORD.value)
        span.set_attribute("cafm.source_filename", source_filename)
        span.set_attribute("cafm.file_size_bytes", len(docx_bytes))

        try:
            # ── 1. Parse DOCX ───────────────────────────────────────────────
            doc = Document(BytesIO(docx_bytes))
            doc_content = _extract_document_content(doc)
            doc_type = _detect_document_type(doc_content)

            span.set_attribute("cafm.doc_type", doc_type)
            span.set_attribute("cafm.char_count", len(doc_content["full_text"]))
            span.set_attribute("cafm.table_count", len(doc_content["tables"]))

            logger.info(
                "word_agent.document_parsed",
                ingestion_id=str(ingestion_id),
                doc_type=doc_type,
                char_count=len(doc_content["full_text"]),
                table_count=len(doc_content["tables"]),
            )

            # ── 2. RAG primary path: chunk + embed ALL DOCX files ───────────
            # Every DOCX is stored in pgvector for Tier 3 retrieval.
            # Inspection reports additionally go through entity extraction below.
            chunks_stored = 0
            if not dry_run:
                chunks_stored = await _store_pgvector_chunks(
                    doc_content,
                    source_filename,
                    ingestion_id,
                    doc_type,
                    client,
                    engine,
                )
            span.set_attribute("cafm.chunks_stored", chunks_stored)

            # ── 3. EL-2.1: Extract JSON + validate (retry ×3) ───────────────
            # Branch on document type: contracts use a different prompt + parser
            is_contract = doc_type == "vendor_contract"
            parsed: dict[str, Any] | None = None
            last_error_context = ""

            for attempt in range(_RETRY_ATTEMPTS):
                with tracer.start_as_current_span(
                    "ingestion.eval.extraction_output"
                ) as el21_span:
                    el21_span.set_attribute("cafm.agent_id", AgentId.WORD.value)
                    el21_span.set_attribute("cafm.retry_count", attempt)

                    if is_contract:
                        prompt = _build_contract_prompt(doc_content, retry_context=last_error_context)
                    else:
                        prompt = _build_extraction_prompt(doc_content, retry_context=last_error_context)

                    raw_text, tok_in, tok_out = await _call_claude_with_retry(
                        client, prompt, model=_SONNET_MODEL
                    )
                    tokens_in_total += tok_in
                    tokens_out_total += tok_out

                    if is_contract:
                        parsed = _el_2_1_parse_contract_json(raw_text)
                        fail_msg = "Missing contract date fields or invalid JSON"
                    else:
                        parsed = _el_2_1_parse_json(raw_text)
                        fail_msg = "Missing required key 'sections' or invalid JSON structure"

                    json_valid = parsed is not None
                    el21_span.set_attribute("cafm.json_valid", json_valid)

                    if json_valid:
                        el21_span.set_status(StatusCode.OK)
                        break

                    last_error_context = fail_msg
                    el21_span.set_status(StatusCode.ERROR, last_error_context)
                    logger.warning(
                        "word_agent.el_2_1_fail",
                        ingestion_id=str(ingestion_id),
                        attempt=attempt + 1,
                        error=last_error_context,
                    )

            if parsed is None:
                logger.error(
                    "word_agent.extraction_failed_all_retries",
                    ingestion_id=str(ingestion_id),
                    source_filename=source_filename,
                )
                span.set_status(StatusCode.ERROR, "EL-2.1 failed after 3 attempts")
                return _failed_schema(
                    ingestion_id,
                    source_filename,
                    blob_url,
                    "EL-2.1: JSON parse failed after 3 attempts",
                    tokens_in_total,
                    tokens_out_total,
                    round((time.monotonic() - t0) * 1000),
                )

            # ── 4. EL-2.2: Pydantic schema conformance ──────────────────────
            with tracer.start_as_current_span(
                "ingestion.eval.schema_conformance"
            ) as el22_span:
                el22_span.set_attribute("cafm.agent_id", AgentId.WORD.value)
                entity_count = (
                    1 if is_contract
                    else len(parsed.get("sections", []))
                )
                el22_span.set_attribute("cafm.entities_count", entity_count)

                if is_contract:
                    intermediate = _el_2_2_build_contract_intermediate(
                        parsed, ingestion_id, source_filename, blob_url
                    )
                else:
                    intermediate = _el_2_2_build_intermediate(
                        parsed, ingestion_id, source_filename, blob_url
                    )
                schema_valid = intermediate is not None
                el22_span.set_attribute("cafm.schema_valid", schema_valid)

                if not schema_valid:
                    el22_span.set_status(StatusCode.ERROR, "EL-2.2 schema validation failed")
                    logger.warning(
                        "word_agent.el_2_2_fail",
                        ingestion_id=str(ingestion_id),
                        sections_count=len(parsed.get("sections", [])),
                    )
                    span.set_status(StatusCode.ERROR, "EL-2.2 failed")
                    return _failed_schema(
                        ingestion_id,
                        source_filename,
                        blob_url,
                        "EL-2.2: Pydantic schema validation failed",
                        tokens_in_total,
                        tokens_out_total,
                        round((time.monotonic() - t0) * 1000),
                    )

                el22_span.set_status(StatusCode.OK)

            # ── 5. EL-2.3: LLM-as-judge eval ────────────────────────────────
            with tracer.start_as_current_span("ingestion.eval.llm_judge") as el23_span:
                el23_span.set_attribute("cafm.agent_id", AgentId.WORD.value)

                eval_score, contradictions, rules_passed, tok_e_in, tok_e_out = (
                    await _el_2_3_llm_judge(
                        client,
                        source_excerpt=doc_content["full_text"],
                        extracted_json=json.dumps(parsed, default=str),
                    )
                )
                tokens_eval_in = tok_e_in
                tokens_eval_out = tok_e_out

                el23_span.set_attribute("cafm.eval_score", eval_score)
                el23_span.set_attribute("cafm.rules_violations_count", len(contradictions))

                # Confidence routing
                if eval_score >= _EVAL_SCORE_ACCEPT:
                    route = "accept"
                    overall_confidence = ConfidenceLevel.HIGH
                elif eval_score >= _EVAL_SCORE_REVIEW:
                    route = "review"
                    overall_confidence = ConfidenceLevel.MEDIUM
                else:
                    route = "re_extract"
                    overall_confidence = ConfidenceLevel.LOW

                el23_span.set_attribute("cafm.route", route)
                el23_span.set_status(StatusCode.OK)

                logger.info(
                    "word_agent.llm_judge_complete",
                    ingestion_id=str(ingestion_id),
                    eval_score=eval_score,
                    rules_passed=rules_passed,
                    contradictions=contradictions,
                    route=route,
                )

            # ── 6. Update intermediate schema with EL-2.3 results ───────────
            assert intermediate is not None  # guaranteed by EL-2.2 check above
            intermediate.confidence = ConfidenceResult(
                overall=overall_confidence,
                per_field=intermediate.confidence.per_field,
                eval_score=eval_score,
                rules_passed=rules_passed,
                rules_violations=contradictions,
            )

            # ── 7. Write to DB (accept path only, skipped in dry_run) ──────
            rows_written = 0
            if route == "accept" and not dry_run:
                if is_contract:
                    # Contract flow: vendor_contracts + expiry notifications
                    contract_id, contract_end = await _write_vendor_contract(
                        engine, parsed, blob_url, ingestion_id
                    )
                    if contract_id and contract_end:
                        renewal_days = int(parsed.get("renewal_review_days") or 45)
                        vendor_name = str(parsed.get("vendor_name") or "")
                        contract_name = str(parsed.get("contract_number") or "")
                        await _create_expiry_notification(
                            engine, contract_id, contract_end,
                            contract_name, vendor_name, renewal_days,
                            org_id_raw=None,
                        )
                    rows_written = 1 if contract_id else 0
                    logger.info(
                        "word_agent.contract_written",
                        ingestion_id=str(ingestion_id),
                        contract_id=contract_id,
                    )
                else:
                    rows_written = await _write_inspections_rows(
                        engine, parsed, blob_url, ingestion_id
                    )
                    logger.info(
                        "word_agent.inspections_written",
                        ingestion_id=str(ingestion_id),
                        rows=rows_written,
                    )
            else:
                logger.info(
                    "word_agent.routed_to_queue",
                    ingestion_id=str(ingestion_id),
                    route=route,
                    eval_score=eval_score,
                )

            # ── 8. Final audit accounting ─────────────────────────────────────
            processing_ms = round((time.monotonic() - t0) * 1000)
            cost_usd = round(
                tokens_in_total * _COST_PER_INPUT_TOKEN[_SONNET_MODEL]
                + tokens_out_total * _COST_PER_OUTPUT_TOKEN[_SONNET_MODEL]
                + tokens_eval_in * _COST_PER_INPUT_TOKEN[_HAIKU_MODEL]
                + tokens_eval_out * _COST_PER_OUTPUT_TOKEN[_HAIKU_MODEL],
                6,
            )

            intermediate.audit = AuditInfo(
                passes=1,
                tokens_in=tokens_in_total + tokens_eval_in,
                tokens_out=tokens_out_total + tokens_eval_out,
                cost_usd=cost_usd,
                processing_ms=processing_ms,
            )

            span.set_attribute("cafm.confidence_overall", overall_confidence.value)
            span.set_attribute("cafm.eval_score", eval_score)
            span.set_attribute("cafm.findings_count", len(parsed.get("sections", [])))
            span.set_attribute("cafm.rows_written", rows_written)
            span.set_attribute("cafm.chunks_stored", chunks_stored)
            span.set_status(StatusCode.OK)

            logger.info(
                "word_agent.extraction_complete",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                doc_type=doc_type,
                findings=len(parsed.get("sections", [])),
                eval_score=eval_score,
                route=route,
                rows_written=rows_written,
                chunks_stored=chunks_stored,
                cost_usd=cost_usd,
                processing_ms=processing_ms,
            )

            return intermediate

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            raise
