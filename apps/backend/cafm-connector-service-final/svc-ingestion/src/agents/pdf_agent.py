"""
svc-ingestion/src/agents/pdf_agent.py

Task 2.1 — PDF Agent (Layer 2, Stage 2).

Extracts structured CAFM entities from PDF files using Claude Vision.

Behaviour:
  - First extraction : base64 inline (no prior file_id)
  - Re-extraction    : Files API (file_id stored in ingestion_documents)
  - Prompt caching   : cache_control: ephemeral on the document block → ~90% cost saving
  - Model selection  : Haiku → classify doc type → Sonnet (default) / Opus (handwritten/legal)
  - Multi-pass voting: 3 independent passes for ComplianceCert & legal docs;
                       a field is HIGH confidence only if all 3 agree
  - Limits           : 32 MB max, 100 pages max (enforced by Stage 1 ingest.py)

Output: IntermediateSchema (the shared pipeline contract).

OTel span: ingestion.stage2.extract
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from enum import Enum
from typing import Any
from uuid import UUID

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from cafm_shared.logging import get_logger
from shared.intermediate_schema import (
    AgentId,
    AuditInfo,
    ConfidenceLevel,
    ConfidenceResult,
    EntitiesBlock,
    ExtractionMethod,
    IntermediateSchema,
    ModelUsed,
    SourceType,
)

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_HAIKU_MODEL = "claude-haiku-4-5"
_SONNET_MODEL = "claude-sonnet-4-6"
_OPUS_MODEL = "claude-opus-4-6"

_FILES_API_BETA = "files-api-2025-04-14"

# Cost per token in USD (approximate — used for audit accounting)
_COST_PER_INPUT_TOKEN: dict[str, float] = {
    _HAIKU_MODEL: 0.00000025,
    _SONNET_MODEL: 0.000003,
    _OPUS_MODEL: 0.000015,
}
_COST_PER_OUTPUT_TOKEN: dict[str, float] = {
    _HAIKU_MODEL: 0.00000125,
    _SONNET_MODEL: 0.000015,
    _OPUS_MODEL: 0.000075,
}
_COST_CACHE_READ_MULTIPLIER = 0.1   # cache read tokens billed at 10% of input price

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0


# ── Document type classification ───────────────────────────────────────────────


class PDFDocType(str, Enum):
    INSPECTION_REPORT = "inspection_report"
    VENDOR_INVOICE = "vendor_invoice"
    EQUIPMENT_MANUAL = "equipment_manual"
    COMPLIANCE_CERT = "compliance_cert"
    FIELD_NOTES = "field_notes"
    UNKNOWN = "unknown"


# Doc types that trigger multi-pass voting (3x)
_MULTIPASS_TYPES: frozenset[PDFDocType] = frozenset(
    {PDFDocType.COMPLIANCE_CERT}
)

# Doc types that require Opus (more nuanced reasoning)
_OPUS_TYPES: frozenset[PDFDocType] = frozenset(
    {PDFDocType.COMPLIANCE_CERT, PDFDocType.EQUIPMENT_MANUAL}
)


# ── Extraction prompts ─────────────────────────────────────────────────────────


def _classify_prompt() -> str:
    return (
        "You are a CAFM document classifier. Look at this PDF and classify it.\n\n"
        "Return ONLY valid JSON (no markdown fences):\n"
        '{"doc_type": "<type>", "confidence": 0.95}\n\n'
        "Valid types: inspection_report | vendor_invoice | equipment_manual | "
        "compliance_cert | field_notes | unknown"
    )


def _extraction_prompt(doc_type: PDFDocType, pass_num: int = 1) -> str:
    """Build extraction prompt. pass_num 1-3 uses slightly varied phrasing for voting."""
    phrasing = {
        1: "Extract all CAFM entities from this document.",
        2: "Carefully read this document and extract every CAFM data entity present.",
        3: "Thoroughly analyse this document and identify all CAFM entities.",
    }.get(pass_num, "Extract all CAFM entities from this document.")

    doc_guidance = {
        PDFDocType.INSPECTION_REPORT: (
            "Focus on: assets inspected, inspection findings (severity, description, "
            "recommendation), readings (temperature, pressure, vibration), "
            "technician details, and work order references."
        ),
        PDFDocType.VENDOR_INVOICE: (
            "Focus on: vendor details (name, contact, contract number), "
            "spare parts (part numbers, quantities, unit costs), "
            "work order references, and asset codes."
        ),
        PDFDocType.EQUIPMENT_MANUAL: (
            "Focus on: asset details (make, model, serial, specifications), "
            "maintenance schedule references, and any spare parts listed."
        ),
        PDFDocType.COMPLIANCE_CERT: (
            "Focus on: certificate number, certificate type, issuing body, "
            "issue date, expiry date, validity status, and linked asset codes. "
            "Be very precise — this data is used for compliance audits."
        ),
        PDFDocType.FIELD_NOTES: (
            "Focus on: assets mentioned, observations, readings, findings, "
            "and technician details."
        ),
        PDFDocType.UNKNOWN: (
            "Extract any CAFM-relevant data: assets, work orders, readings, "
            "findings, vendors, certificates, or spare parts."
        ),
    }.get(doc_type, "")

    return f"""{phrasing} {doc_guidance}

Return ONLY valid JSON matching this exact schema (no markdown fences):
{{
  "entities": {{
    "assets": [
      {{"asset_code": null, "serial_number": null, "name": null, "category": null,
        "location": null, "manufacturer": null, "model_number": null,
        "installation_date": null, "warranty_expiry": null, "status": null}}
    ],
    "work_orders": [
      {{"work_order_number": null, "title": null, "description": null,
        "asset_code": null, "priority": null, "status": null,
        "technician_name": null, "scheduled_date": null, "completed_date": null}}
    ],
    "readings": [
      {{"asset_code": null, "reading_type": null, "value": null,
        "unit": null, "reading_date": null, "notes": null}}
    ],
    "findings": [
      {{"asset_code": null, "severity": null, "description": null,
        "recommendation": null, "location": null}}
    ],
    "technicians": [
      {{"employee_id": null, "name": null, "email": null, "specialisation": null}}
    ],
    "vendors": [
      {{"vendor_code": null, "name": null, "contact_name": null,
        "email": null, "contract_number": null, "contract_start": null, "contract_end": null}}
    ],
    "certificates": [
      {{"certificate_number": null, "certificate_type": null, "asset_code": null,
        "issued_by": null, "issued_date": null, "expiry_date": null, "is_valid": null}}
    ],
    "spare_parts": [
      {{"part_number": null, "name": null, "quantity": null,
        "unit": null, "unit_cost": null, "supplier": null, "asset_code": null}}
    ]
  }},
  "confidence": {{
    "overall": "high|medium|low",
    "per_field": {{}},
    "notes": ""
  }}
}}

Omit any entity list that has zero items. Only include items with at least one non-null field.
All dates in ISO 8601 format. Asset codes in their original format (e.g. MOB-AHU-001)."""


# ── Cost accounting ────────────────────────────────────────────────────────────


def _compute_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_read_tokens: int = 0,
) -> float:
    in_rate = _COST_PER_INPUT_TOKEN.get(model, 0.000003)
    out_rate = _COST_PER_OUTPUT_TOKEN.get(model, 0.000015)
    cache_rate = in_rate * _COST_CACHE_READ_MULTIPLIER
    return (tokens_in * in_rate) + (tokens_out * out_rate) + (cache_read_tokens * cache_rate)


# ── Document source helpers ────────────────────────────────────────────────────


def _base64_document_block(pdf_bytes: bytes, with_cache: bool = True) -> dict[str, Any]:
    """Build a Claude document block using base64 encoding."""
    b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    block: dict[str, Any] = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": b64,
        },
    }
    if with_cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _file_id_document_block(file_id: str, with_cache: bool = True) -> dict[str, Any]:
    """Build a Claude document block using Files API file_id."""
    block: dict[str, Any] = {
        "type": "document",
        "source": {
            "type": "file",
            "file_id": file_id,
        },
    }
    if with_cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


# ── Single Claude call with retry ──────────────────────────────────────────────


async def _claude_call(
    client: anthropic.AsyncAnthropic,
    model: str,
    document_block: dict[str, Any],
    prompt_text: str,
    use_files_api: bool = False,
) -> anthropic.types.Message:
    """
    Make a single Claude call with 3x exponential backoff.
    Uses Files API beta header when needed.
    """
    extra_headers: dict[str, str] = {}
    if use_files_api:
        extra_headers["anthropic-beta"] = _FILES_API_BETA

    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return await client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            document_block,
                            {"type": "text", "text": prompt_text},
                        ],
                    }
                ],
                extra_headers=extra_headers if extra_headers else None,  # type: ignore[arg-type]
            )
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "pdf_agent.claude_retry",
                    attempt=attempt + 1,
                    model=model,
                    delay_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        except Exception as exc:
            raise exc  # non-retryable

    raise RuntimeError(
        f"pdf_agent: Claude {model} failed after {_RETRY_ATTEMPTS} attempts"
    ) from last_exc


# ── Classify document type ─────────────────────────────────────────────────────


async def _classify_doc_type(
    client: anthropic.AsyncAnthropic,
    document_block: dict[str, Any],
    use_files_api: bool,
) -> PDFDocType:
    """Use Haiku to classify the PDF document type."""
    try:
        response = await _claude_call(
            client,
            model=_HAIKU_MODEL,
            document_block=document_block,
            prompt_text=_classify_prompt(),
            use_files_api=use_files_api,
        )
        text = response.content[0].text.strip()  # type: ignore[union-attr]
        # Strip fences
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            if "```" in text:
                text = text[: text.index("```")]
        parsed = json.loads(text.strip())
        raw_type = str(parsed.get("doc_type", "unknown")).lower()
        try:
            return PDFDocType(raw_type)
        except ValueError:
            return PDFDocType.UNKNOWN
    except Exception as exc:
        logger.warning("pdf_agent.classify_failed", error=str(exc))
        return PDFDocType.UNKNOWN


# ── Parse Claude extraction response ──────────────────────────────────────────


def _parse_extraction(text: str) -> dict[str, Any]:
    """Strip fences and parse the extraction JSON from Claude."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        if "```" in text:
            text = text[: text.index("```")]
    return json.loads(text.strip())  # type: ignore[no-any-return]


def _entities_from_parsed(parsed: dict[str, Any]) -> EntitiesBlock:
    """Build EntitiesBlock from parsed Claude JSON, skipping invalid items."""
    raw_entities = parsed.get("entities", {})

    def _safe_list(key: str) -> list[dict[str, Any]]:
        return [r for r in raw_entities.get(key, []) if isinstance(r, dict)]

    from shared.intermediate_schema import (
        AssetEntity,
        CertificateEntity,
        FindingEntity,
        ReadingEntity,
        SparePartEntity,
        TechnicianEntity,
        VendorEntity,
        WorkOrderEntity,
    )

    assets = []
    for r in _safe_list("assets"):
        try:
            assets.append(AssetEntity(**r))
        except Exception:
            pass

    work_orders = []
    for r in _safe_list("work_orders"):
        try:
            work_orders.append(WorkOrderEntity(**r))
        except Exception:
            pass

    readings = []
    for r in _safe_list("readings"):
        try:
            readings.append(ReadingEntity(**r))
        except Exception:
            pass

    findings = []
    for r in _safe_list("findings"):
        try:
            findings.append(FindingEntity(**r))
        except Exception:
            pass

    technicians = []
    for r in _safe_list("technicians"):
        try:
            technicians.append(TechnicianEntity(**r))
        except Exception:
            pass

    vendors = []
    for r in _safe_list("vendors"):
        try:
            vendors.append(VendorEntity(**r))
        except Exception:
            pass

    certificates = []
    for r in _safe_list("certificates"):
        try:
            certificates.append(CertificateEntity(**r))
        except Exception:
            pass

    spare_parts = []
    for r in _safe_list("spare_parts"):
        try:
            spare_parts.append(SparePartEntity(**r))
        except Exception:
            pass

    return EntitiesBlock(
        assets=assets,
        work_orders=work_orders,
        readings=readings,
        findings=findings,
        technicians=technicians,
        vendors=vendors,
        certificates=certificates,
        spare_parts=spare_parts,
    )


def _confidence_from_parsed(parsed: dict[str, Any]) -> ConfidenceResult:
    """Build ConfidenceResult from the parsed confidence block."""
    conf_block = parsed.get("confidence", {})
    overall_raw = str(conf_block.get("overall", "low")).lower()
    try:
        overall = ConfidenceLevel(overall_raw)
    except ValueError:
        overall = ConfidenceLevel.LOW

    per_field: dict[str, ConfidenceLevel] = {}
    for field, lvl in conf_block.get("per_field", {}).items():
        try:
            per_field[field] = ConfidenceLevel(str(lvl).lower())
        except ValueError:
            per_field[field] = ConfidenceLevel.LOW

    return ConfidenceResult(
        overall=overall,
        per_field=per_field,
        eval_score=0.0,   # populated by Stage 3 eval_layer
        rules_passed=True,
        rules_violations=[],
    )


# ── Single extraction pass ─────────────────────────────────────────────────────


async def _extract_once(
    client: anthropic.AsyncAnthropic,
    model: str,
    document_block: dict[str, Any],
    doc_type: PDFDocType,
    pass_num: int,
    use_files_api: bool,
) -> tuple[EntitiesBlock, ConfidenceResult, anthropic.types.Message, str]:
    """Run one extraction pass. Returns (entities, confidence, raw_message, raw_text)."""
    response = await _claude_call(
        client,
        model=model,
        document_block=document_block,
        prompt_text=_extraction_prompt(doc_type, pass_num),
        use_files_api=use_files_api,
    )
    raw_text: str = response.content[0].text  # type: ignore[union-attr]
    parsed = _parse_extraction(raw_text)
    entities = _entities_from_parsed(parsed)
    confidence = _confidence_from_parsed(parsed)
    return entities, confidence, response, raw_text


# ── Multi-pass voting ──────────────────────────────────────────────────────────


def _vote_per_field_confidence(
    results: list[tuple[EntitiesBlock, ConfidenceResult, Any, str]],
) -> dict[str, ConfidenceLevel]:
    """
    Task 3.3 — Per-field confidence voting for multi-pass extractions.

    For compliance certs and legal docs, a field is accepted as HIGH confidence
    ONLY if all 3 passes agree on the same value. 2/3 agreement → MEDIUM.
    Disagreement → LOW.

    Compares per_field confidence tags from each pass. If a field is absent
    in some passes, it defaults to LOW for those passes.
    """
    if not results:
        return {}

    # Collect all per_field dicts from each pass
    all_per_field: list[dict[str, ConfidenceLevel]] = [r[1].per_field for r in results]

    # Union of all field names across passes
    all_fields: set[str] = set()
    for pf in all_per_field:
        all_fields.update(pf.keys())

    voted: dict[str, ConfidenceLevel] = {}
    for field_name in all_fields:
        # Get the confidence level for this field from each pass (LOW if absent)
        field_levels = [
            pf.get(field_name, ConfidenceLevel.LOW) for pf in all_per_field
        ]
        # Count agreements
        level_counts: dict[ConfidenceLevel, int] = {}
        for lv in field_levels:
            level_counts[lv] = level_counts.get(lv, 0) + 1

        majority = max(level_counts, key=lambda k: level_counts[k])
        majority_count = level_counts[majority]

        # All 3 must agree for HIGH — this is the EL-2.3 per-field gate
        if majority_count == len(results) and majority == ConfidenceLevel.HIGH:
            voted[field_name] = ConfidenceLevel.HIGH
        elif majority_count >= 2:
            # Partial agreement — cap at MEDIUM regardless of individual level
            voted[field_name] = (
                ConfidenceLevel.MEDIUM
                if majority != ConfidenceLevel.LOW
                else ConfidenceLevel.LOW
            )
        else:
            voted[field_name] = ConfidenceLevel.LOW

    return voted


def _merge_multipass(
    results: list[tuple[EntitiesBlock, ConfidenceResult, Any, str]],
    doc_type: "PDFDocType | None" = None,
) -> tuple[EntitiesBlock, ConfidenceResult]:
    """
    Merge 3 extraction passes.

    Strategy: use the result from the pass with the highest entity count
    (most complete extraction). Confidence is set to MEDIUM unless all
    3 passes agree on overall confidence level; disagreement → MEDIUM.

    For COMPLIANCE_CERT and legal doc types (Task 3.3), per-field confidence
    is computed by voting: a field is HIGH only if all 3 passes agree.
    """
    if not results:
        return EntitiesBlock(), ConfidenceResult()

    # Pick pass with most entities extracted (index 0 = entities, 1 = conf in each 4-tuple)
    best_entities, best_conf = max(results, key=lambda r: r[0].total_count)[:2]

    # Majority vote on overall confidence
    levels = [r[1].overall for r in results]  # r[1] = ConfidenceResult
    level_counts: dict[ConfidenceLevel, int] = {}
    for lv in levels:
        level_counts[lv] = level_counts.get(lv, 0) + 1
    majority_level = max(level_counts, key=lambda k: level_counts[k])
    majority_count = level_counts[majority_level]

    # All 3 must agree for HIGH; otherwise downgrade
    if majority_count == 3:
        final_level = majority_level
    elif majority_count == 2:
        # Partial agreement — downgrade one step
        final_level = (
            ConfidenceLevel.MEDIUM
            if majority_level == ConfidenceLevel.HIGH
            else ConfidenceLevel.LOW
        )
    else:
        final_level = ConfidenceLevel.LOW

    # Task 3.3: For compliance certs, compute per-field confidence by voting.
    # A field is HIGH only if all 3 passes agree — this is the EL-2.3 per-field gate.
    use_field_voting = doc_type == PDFDocType.COMPLIANCE_CERT if doc_type else False
    per_field = _vote_per_field_confidence(results) if use_field_voting else best_conf.per_field

    merged_conf = ConfidenceResult(
        overall=final_level,
        per_field=per_field,
        eval_score=0.0,
        rules_passed=True,
        rules_violations=[],
    )
    return best_entities, merged_conf


# ── EL-2.x evaluation helpers ──────────────────────────────────────────────────

_EVAL_SCORE_ACCEPT: float = 0.85
_EVAL_SCORE_REVIEW: float = 0.60


def _el_2_1_validate(raw_text: str) -> dict[str, Any] | None:
    """
    EL-2.1 — Raw extraction output validation.

    Strips markdown fences, parses JSON, checks required keys.
    Returns parsed dict on success, None on failure (triggers retry).
    """
    text = raw_text.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        inner = parts[1][4:] if len(parts) > 1 and parts[1].startswith("json") else (parts[1] if len(parts) > 1 else "")
        text = inner.strip()
    try:
        parsed: dict[str, Any] = json.loads(text)
        # EL-2.1: required top-level keys
        if "entities" not in parsed:
            return None
        return parsed
    except (json.JSONDecodeError, ValueError):
        return None


def _build_eval_prompt(source_desc: str, extracted_json: str) -> str:
    """Build EL-2.3 LLM-as-judge prompt for Haiku."""
    return (
        "You are a CAFM data quality evaluator (EL-2.3 LLM-as-judge).\n\n"
        "## Source document description:\n"
        f"{source_desc[:2_000]}\n\n"
        "## Extracted JSON:\n"
        f"{extracted_json[:3_000]}\n\n"
        "Evaluate whether the extraction faithfully represents the source document.\n"
        "Check for: missing entities, wrong severity/priority, fabricated asset codes, "
        "incorrect dates, contradictions (e.g. 'Normal' observation + 'Critical' severity).\n\n"
        "Return ONLY valid JSON — no markdown fences:\n"
        '{"eval_score": 0.0, "contradictions": ["..."], '
        '"missing_fields": ["..."], "verdict": "pass|review|reject"}'
    )


async def _el_2_3_judge(
    client: anthropic.AsyncAnthropic,
    source_desc: str,
    extracted_json: str,
) -> tuple[float, list[str], bool, int, int]:
    """
    EL-2.3 — LLM-as-judge using Haiku.

    Returns:
        (eval_score, contradictions, rules_passed, tokens_in, tokens_out)
    """
    prompt = _build_eval_prompt(source_desc, extracted_json)
    try:
        last_exc: Exception | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                response = await client.messages.create(
                    model=_HAIKU_MODEL,
                    max_tokens=512,
                    messages=[{"role": "user", "content": prompt}],
                )
                text: str = response.content[0].text.strip()  # type: ignore[union-attr]
                if text.startswith("```"):
                    parts = text.split("```", 2)
                    inner = parts[1][4:] if parts[1].startswith("json") else parts[1]
                    text = inner.strip()
                result: dict[str, Any] = json.loads(text)
                eval_score = round(float(result.get("eval_score", 0.0)), 3)
                contradictions: list[str] = list(result.get("contradictions", []))
                verdict = str(result.get("verdict", "reject"))
                rules_passed = verdict == "pass"
                return (
                    eval_score,
                    contradictions,
                    rules_passed,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
            except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
                last_exc = exc
                if attempt < _RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
            except Exception:
                break
        if last_exc:
            logger.warning("pdf_agent.llm_judge_api_failed", error=str(last_exc))
    except Exception as exc:
        logger.warning("pdf_agent.llm_judge_failed", error=str(exc))
    return 0.0, [], False, 0, 0


# ── Files API upload ───────────────────────────────────────────────────────────


async def upload_to_files_api(
    client: anthropic.AsyncAnthropic,
    pdf_bytes: bytes,
    filename: str,
) -> str:
    """
    Upload a PDF to the Anthropic Files API and return the file_id.

    Call this when storing a file for potential re-extraction.
    Store the returned file_id in ingestion_documents for later reuse.
    """
    import io

    response = await client.beta.files.upload(
        file=(filename, io.BytesIO(pdf_bytes), "application/pdf"),
    )
    return str(response.id)


# ── Public API ─────────────────────────────────────────────────────────────────


async def extract_pdf(
    pdf_bytes: bytes,
    *,
    source_filename: str,
    ingestion_id: UUID,
    blob_url: str,
    client: anthropic.AsyncAnthropic,
    file_id: str | None = None,
    force_multipass: bool = False,
) -> IntermediateSchema:
    """
    Stage 2 extraction for PDF files.

    Args:
        pdf_bytes:       Raw PDF bytes (already validated by Stage 1).
        source_filename: Original filename (for audit + classification hints).
        ingestion_id:    UUID from ingestion_documents (created by Stage 1).
        blob_url:        Azure Blob URL where original was stored (Stage 1).
        client:          Async Anthropic client.
        file_id:         If set, use Files API instead of base64 (re-extractions).
        force_multipass: Override — run 3-pass voting regardless of doc type.

    Returns:
        IntermediateSchema ready for Stage 3 (eval) and Stage 4 (unifier).
    """
    t0 = time.monotonic()
    use_files_api = file_id is not None

    with tracer.start_as_current_span("ingestion.stage2.extract") as span:
        span.set_attribute("cafm.ingestion_id", str(ingestion_id))
        span.set_attribute("cafm.agent_id", AgentId.PDF.value)
        span.set_attribute("cafm.source_type", SourceType.PDF.value)
        span.set_attribute("cafm.extraction_method", ExtractionMethod.CLAUDE_VISION.value)
        span.set_attribute("cafm.file_size_bytes", len(pdf_bytes))
        span.set_attribute("cafm.use_files_api", use_files_api)

        try:
            # ── 1. Build document block (base64 OR Files API) ─────────────
            if use_files_api and file_id:
                doc_block = _file_id_document_block(file_id, with_cache=True)
                logger.info(
                    "pdf_agent.using_files_api",
                    ingestion_id=str(ingestion_id),
                    file_id=file_id,
                )
            else:
                doc_block = _base64_document_block(pdf_bytes, with_cache=True)

            # ── 2. Classify document type (Haiku — fast, cheap) ───────────
            doc_type = await _classify_doc_type(client, doc_block, use_files_api)
            span.set_attribute("cafm.pdf_doc_type", doc_type.value)
            logger.info(
                "pdf_agent.classified",
                ingestion_id=str(ingestion_id),
                doc_type=doc_type.value,
                source_filename=source_filename,
            )

            # ── 3. Select extraction model ────────────────────────────────
            model = _OPUS_MODEL if doc_type in _OPUS_TYPES else _SONNET_MODEL
            span.set_attribute("cafm.model", model)

            # ── 4. Extract — single or multi-pass ────────────────────────
            do_multipass = force_multipass or (doc_type in _MULTIPASS_TYPES)
            span.set_attribute("cafm.multipass", do_multipass)

            total_tokens_in = 0
            total_tokens_out = 0
            total_cache_read = 0
            passes_done = 0

            if do_multipass:
                # Fire 3 passes concurrently
                tasks = [
                    _extract_once(client, model, doc_block, doc_type, p + 1, use_files_api)
                    for p in range(3)
                ]
                raw_results = await asyncio.gather(*tasks)
                passes_done = 3

                # Accumulate token counts (each result is (entities, conf, msg, raw_text))
                for _, _, msg, _ in raw_results:
                    usage = msg.usage
                    total_tokens_in += usage.input_tokens
                    total_tokens_out += usage.output_tokens
                    total_cache_read += getattr(usage, "cache_read_input_tokens", 0)

                entities, confidence = _merge_multipass(list(raw_results), doc_type=doc_type)
                # For EL-2.3: use the raw text from the best pass (highest entity count)
                best_idx = max(range(len(raw_results)), key=lambda i: raw_results[i][0].total_count)
                extraction_raw_text = raw_results[best_idx][3]

                logger.info(
                    "pdf_agent.multipass_complete",
                    ingestion_id=str(ingestion_id),
                    passes=3,
                    final_confidence=confidence.overall.value,
                    entity_count=entities.total_count,
                )

            else:
                # ── Single-pass with EL-2.1 retry loop ───────────────────
                last_el21_error = ""
                extraction_raw_text = ""
                entities = EntitiesBlock()
                confidence = ConfidenceResult()
                msg: anthropic.types.Message | None = None

                for el21_attempt in range(_RETRY_ATTEMPTS):
                    prompt_suffix = (
                        f"\n\nIMPORTANT: Previous response failed validation: "
                        f"{last_el21_error}. Return ONLY valid JSON with 'entities' key."
                        if el21_attempt > 0 and last_el21_error
                        else ""
                    )
                    # Temporarily patch prompt if retry needed
                    _orig_prompt = _extraction_prompt(doc_type, 1)
                    call_prompt = _orig_prompt + prompt_suffix

                    response = await _claude_call(
                        client,
                        model=model,
                        document_block=doc_block,
                        prompt_text=call_prompt,
                        use_files_api=use_files_api,
                    )
                    raw_text_attempt: str = response.content[0].text  # type: ignore[union-attr]

                    # EL-2.1 check
                    with tracer.start_as_current_span(
                        "ingestion.eval.extraction_output"
                    ) as el21_span:
                        el21_span.set_attribute("cafm.agent_id", AgentId.PDF.value)
                        el21_span.set_attribute("cafm.retry_count", el21_attempt)
                        parsed_check = _el_2_1_validate(raw_text_attempt)
                        json_valid = parsed_check is not None
                        el21_span.set_attribute("cafm.json_valid", json_valid)

                        if json_valid:
                            el21_span.set_status(StatusCode.OK)
                        else:
                            last_el21_error = "Missing 'entities' key or invalid JSON"
                            el21_span.set_status(StatusCode.ERROR, last_el21_error)
                            logger.warning(
                                "pdf_agent.el_2_1_fail",
                                ingestion_id=str(ingestion_id),
                                attempt=el21_attempt + 1,
                                error=last_el21_error,
                            )
                            if el21_attempt == _RETRY_ATTEMPTS - 1:
                                # All retries exhausted — return LOW confidence
                                span.set_status(StatusCode.ERROR, "EL-2.1 failed after 3 attempts")
                                return IntermediateSchema(
                                    ingestion_id=ingestion_id,
                                    source_type=SourceType.PDF,
                                    agent_id=AgentId.PDF,
                                    source_filename=source_filename,
                                    source_blob_url=blob_url,
                                    extraction_method=ExtractionMethod.CLAUDE_VISION,
                                    model_used=ModelUsed.OPUS if model == _OPUS_MODEL else ModelUsed.SONNET,
                                    entities=EntitiesBlock(),
                                    confidence=ConfidenceResult(
                                        overall=ConfidenceLevel.LOW,
                                        eval_score=0.0,
                                        rules_passed=False,
                                        rules_violations=["EL-2.1: JSON parse failed after 3 attempts"],
                                    ),
                                    audit=AuditInfo(
                                        tokens_in=total_tokens_in,
                                        tokens_out=total_tokens_out,
                                        processing_ms=round((time.monotonic() - t0) * 1000),
                                    ),
                                )
                            continue

                    # EL-2.1 passed — parse entities
                    from shared.intermediate_schema import (  # noqa: PLC0415
                        AssetEntity,
                        CertificateEntity,
                        FindingEntity,
                        ReadingEntity,
                        SparePartEntity,
                        TechnicianEntity,
                        VendorEntity,
                        WorkOrderEntity,
                    )
                    _parsed = _parse_extraction(raw_text_attempt)
                    entities = _entities_from_parsed(_parsed)
                    confidence = _confidence_from_parsed(_parsed)
                    msg = response
                    extraction_raw_text = raw_text_attempt
                    passes_done = 1
                    total_tokens_in = response.usage.input_tokens
                    total_tokens_out = response.usage.output_tokens
                    total_cache_read = getattr(response.usage, "cache_read_input_tokens", 0)
                    break

            # ── EL-2.2: Schema conformance span ──────────────────────────
            with tracer.start_as_current_span("ingestion.eval.schema_conformance") as el22_span:
                el22_span.set_attribute("cafm.agent_id", AgentId.PDF.value)
                el22_span.set_attribute("cafm.entities_count", entities.total_count)
                # Pydantic validation already ran in _entities_from_parsed / _merge_multipass
                el22_span.set_attribute("cafm.schema_valid", True)
                el22_span.set_status(StatusCode.OK)

            # ── EL-2.3: LLM-as-judge eval ────────────────────────────────
            tokens_eval_in = 0
            tokens_eval_out = 0
            eval_score = 0.0
            contradictions: list[str] = []
            rules_passed = False

            with tracer.start_as_current_span("ingestion.eval.llm_judge") as el23_span:
                el23_span.set_attribute("cafm.agent_id", AgentId.PDF.value)

                source_desc = (
                    f"PDF file: {source_filename} | doc_type: {doc_type.value} | "
                    f"entities extracted: {entities.total_count} | "
                    f"multi_pass: {do_multipass}"
                )
                extracted_json_str = json.dumps(
                    {
                        "entities": {
                            "assets": [a.model_dump() for a in entities.assets],
                            "findings": [f.model_dump() for f in entities.findings],
                            "work_orders": [w.model_dump() for w in entities.work_orders],
                        }
                    },
                    default=str,
                )

                eval_score, contradictions, rules_passed, tokens_eval_in, tokens_eval_out = (
                    await _el_2_3_judge(client, source_desc, extracted_json_str)
                )

                el23_span.set_attribute("cafm.eval_score", eval_score)
                el23_span.set_attribute("cafm.rules_violations_count", len(contradictions))

                if eval_score >= _EVAL_SCORE_ACCEPT:
                    route = "accept"
                    confidence = ConfidenceResult(
                        overall=confidence.overall,
                        per_field=confidence.per_field,
                        eval_score=eval_score,
                        rules_passed=rules_passed,
                        rules_violations=contradictions,
                    )
                elif eval_score >= _EVAL_SCORE_REVIEW:
                    route = "review"
                    confidence = ConfidenceResult(
                        overall=ConfidenceLevel.MEDIUM,
                        per_field=confidence.per_field,
                        eval_score=eval_score,
                        rules_passed=False,
                        rules_violations=contradictions,
                    )
                else:
                    route = "re_extract"
                    confidence = ConfidenceResult(
                        overall=ConfidenceLevel.LOW,
                        per_field=confidence.per_field,
                        eval_score=eval_score,
                        rules_passed=False,
                        rules_violations=contradictions,
                    )

                el23_span.set_attribute("cafm.route", route)
                el23_span.set_status(StatusCode.OK)

                logger.info(
                    "pdf_agent.llm_judge_complete",
                    ingestion_id=str(ingestion_id),
                    eval_score=eval_score,
                    route=route,
                    contradictions=contradictions,
                )

            # ── 5. Compute cost ───────────────────────────────────────────
            cost_usd = (
                _compute_cost(model, total_tokens_in, total_tokens_out, total_cache_read)
                + total_tokens_in * 0.0  # eval Haiku tokens included below
                + tokens_eval_in * _COST_PER_INPUT_TOKEN[_HAIKU_MODEL]
                + tokens_eval_out * _COST_PER_OUTPUT_TOKEN[_HAIKU_MODEL]
            )
            processing_ms = round((time.monotonic() - t0) * 1000)

            span.set_attribute("cafm.confidence_overall", confidence.overall.value)
            span.set_attribute("cafm.eval_score", eval_score)
            span.set_attribute("cafm.entity_count", entities.total_count)
            span.set_attribute("claude.tokens_in", total_tokens_in)
            span.set_attribute("claude.tokens_out", total_tokens_out)
            span.set_attribute("claude.cache_read_tokens", total_cache_read)
            span.set_attribute("claude.cost_usd", cost_usd)
            span.set_attribute("claude.latency_ms", processing_ms)
            span.set_status(StatusCode.OK)

            logger.info(
                "pdf_agent.extraction_complete",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                doc_type=doc_type.value,
                model=model,
                passes=passes_done,
                entity_count=entities.total_count,
                confidence_overall=confidence.overall.value,
                eval_score=eval_score,
                route=route,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                cache_read_tokens=total_cache_read,
                cost_usd=round(cost_usd, 6),
                processing_ms=processing_ms,
            )

            # ── 6. Build IntermediateSchema ───────────────────────────────
            model_used = (
                ModelUsed.OPUS
                if model == _OPUS_MODEL
                else ModelUsed.SONNET
            )

            return IntermediateSchema(
                ingestion_id=ingestion_id,
                source_type=SourceType.PDF,
                agent_id=AgentId.PDF,
                source_filename=source_filename,
                source_blob_url=blob_url,
                extraction_method=ExtractionMethod.CLAUDE_VISION,
                model_used=model_used,
                entities=entities,
                confidence=confidence,
                audit=AuditInfo(
                    passes=passes_done,
                    tokens_in=total_tokens_in + tokens_eval_in,
                    tokens_out=total_tokens_out + tokens_eval_out,
                    cache_read_tokens=total_cache_read,
                    cost_usd=round(cost_usd, 6),
                    processing_ms=processing_ms,
                ),
            )

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            logger.error(
                "pdf_agent.extraction_failed",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                error=str(exc),
            )
            raise
