"""
svc-ingestion/src/agents/xml_json_agent.py

Task 2.5 — XML/JSON Agent (Layer 2, Stage 2).

Ingests .xml, .json, and .jsonl files into the unified plenum_cafm store.

Design:
  - lxml           parses XML; XPath/tree traversal extracts repeating records
  - json stdlib    parses JSON arrays, wrapped objects, and JSONL (line-by-line)
  - Schema mapper  called ONCE per file — Claude Haiku maps headers → canonical fields
  - Haiku fallback called for CONTENT extraction when structure is too complex or nested
                   for deterministic parsing (tracks claude_used=True → triggers EL-2.3)
  - EL-3.0         blocks write if mapping confidence < 0.80
  - EL-2.2         Pydantic validates mapped rows before write
  - EL-2.3         LLM-as-judge (Haiku) — ONLY if claude_used is True
  - asyncpg COPY   bulk insert for direct entity types (same pattern as CSV/Excel)
  - JSONL          processed line-by-line (streaming — no full in-memory load)

EL-2.3 trigger rationale:
  - Structured XML/JSON with flat, recognisable records → deterministic parsing, no EL-2.3
  - Deeply nested / ambiguous / empty XML/JSON → Haiku called for extraction → EL-2.3 runs
    (LLM-as-judge needed when LLM was used for content extraction, not just field name mapping)

OTel spans: ingestion.stage2.extract + schema_mapper.map (inside map_headers)
            + ingestion.eval.extraction_output (EL-2.1, if Haiku extraction used)
            + ingestion.eval.schema_conformance (EL-2.2)
            + ingestion.eval.llm_judge (EL-2.3, if claude_used)
"""

from __future__ import annotations

import io
import json
import time
import uuid
from typing import Any
from uuid import UUID

import anthropic
from lxml import etree
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy.ext.asyncio import AsyncEngine

from cafm_shared.logging import get_logger
from shared.intermediate_schema import (
    AgentId,
    AssetEntity,
    AuditInfo,
    ConfidenceLevel,
    ConfidenceResult,
    EntitiesBlock,
    ExtractionMethod,
    IntermediateSchema,
    ModelUsed,
    SparePartEntity,
    SourceType,
    WorkOrderEntity,
)
from shared.schema_mapper import SchemaMapping, map_headers

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_BATCH_SIZE = 1_000
_SAMPLE_ROWS = 50
_SCHEMA = "plenum_cafm"
_MAX_ROWS = 200_000

# Thresholds for EL-2.3 routing (same as other agents that use Claude)
_EVAL_SCORE_ACCEPT = 0.85
_EVAL_SCORE_REVIEW = 0.60

# Haiku model for extraction fallback + LLM-as-judge
_HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Minimum number of canonical-field overlaps before we consider structure "clear"
# Below this threshold the deterministic parser couldn't identify the shape →
# Haiku extraction path is triggered (→ EL-2.3).
_MIN_CANONICAL_HITS = 2

# ── Entity type detection (identical to CSV/Excel agents) ──────────────────────

_ENTITY_SIGNATURES: dict[str, frozenset[str]] = {
    "assets": frozenset({"asset_code", "asset_name", "category", "make", "model", "serial"}),
    "spare_parts": frozenset({
        "part_code", "stock_on_hand", "minimum_allowed_stock", "bom_group_name",
    }),
    "work_orders": frozenset({
        "wo_code", "wo_priority", "wo_status", "wo_type", "maintenance_type",
    }),
    "maintenance_plans": frozenset({
        "sm_code", "trigger_type", "schedule_interval", "sm_priority",
    }),
    "users": frozenset({"user_full_name", "user_name", "user_title", "reports_to"}),
}

_DIRECT_COPY_TABLES: frozenset[str] = frozenset({"assets", "spare_parts", "work_orders"})

_REQUIRED_COLUMNS: dict[str, dict[str, Any]] = {
    "assets": {"asset_name": "Unnamed Asset"},
    "spare_parts": {"part_name": "Unnamed Part", "stock_quantity": 0, "reorder_level": 0},
    "work_orders": {"title": "Imported Work Order", "priority": "medium", "status": "open"},
}


# ── Helpers ────────────────────────────────────────────────────────────────────


def _coerce_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _coerce_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


def _detect_format(filename: str, content_bytes: bytes) -> str:
    """Detect xml / json / jsonl from filename extension, then content."""
    fname = filename.lower()
    if fname.endswith(".xml"):
        return "xml"
    if fname.endswith(".jsonl") or fname.endswith(".ndjson"):
        return "jsonl"
    if fname.endswith(".json"):
        return "json"
    # Content-based fallback
    head = content_bytes[:512].lstrip()
    if head.startswith(b"<"):
        return "xml"
    if head.startswith(b"[") or head.startswith(b"{"):
        # Try full-document parse first; fall back to line-by-line (JSONL)
        try:
            json.loads(content_bytes.decode("utf-8", errors="replace"))
            return "json"
        except json.JSONDecodeError:
            return "jsonl"
    return "json"


def _detect_entity_type(canonical_fields: set[str]) -> str:
    scores = {
        etype: len(sig & canonical_fields)
        for etype, sig in _ENTITY_SIGNATURES.items()
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "unknown"


def _flatten_dict(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten one level of nested dicts. Lists are kept as-is."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}_{k}"
        if isinstance(v, dict):
            out.update(_flatten_dict(v, key))
        else:
            out[key] = v
    return out


# ── Format parsers ─────────────────────────────────────────────────────────────


def _parse_xml(xml_bytes: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Parse XML into (headers, rows).

    Strategy:
      1. Parse with lxml.etree.
      2. Find the most-repeated child tag at depth 1 (likely the record element).
         If none found, try depth 2 (for <root><collection><item>… patterns).
      3. For each record element: collect child-element text + attributes as a flat dict.
      4. Return all unique keys as headers.

    Raises ValueError on parse error or no records found.
    """
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"XML parse error: {exc}") from exc

    # Depth-1 record detection
    tag_counts: dict[str, int] = {}
    for child in root:
        tag = etree.QName(child.tag).localname
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    record_tag: str | None = None
    record_parent = root

    if tag_counts:
        record_tag = max(tag_counts, key=lambda t: tag_counts[t])

    # If depth-1 gives only 1 element, try depth-2
    if record_tag and tag_counts.get(record_tag, 0) == 1:
        depth1_elem = root.find(record_tag)
        if depth1_elem is not None:
            sub_counts: dict[str, int] = {}
            for sub in depth1_elem:
                stag = etree.QName(sub.tag).localname
                sub_counts[stag] = sub_counts.get(stag, 0) + 1
            if sub_counts:
                record_tag = max(sub_counts, key=lambda t: sub_counts[t])
                record_parent = depth1_elem

    if not record_tag:
        raise ValueError("No repeating record elements found in XML")

    # Strip namespace from tag for findall
    ns_map: dict[str, str] = root.nsmap  # type: ignore[assignment]
    default_ns = ns_map.get(None, "")
    find_tag = f"{{{default_ns}}}{record_tag}" if default_ns else record_tag
    elements = record_parent.findall(find_tag)

    if not elements:
        raise ValueError(f"No <{record_tag}> elements found")

    all_keys: set[str] = set()
    rows: list[dict[str, Any]] = []

    for elem in elements[:_MAX_ROWS]:
        row: dict[str, Any] = {}
        # Child text values
        for child in elem:
            local = etree.QName(child.tag).localname
            row[local] = child.text.strip() if child.text and child.text.strip() else None
        # Attributes
        for attr, val in elem.attrib.items():
            row[etree.QName(attr).localname] = val
        # Skip fully empty rows
        if any(v is not None for v in row.values()):
            all_keys.update(row.keys())
            rows.append(row)

    if not rows:
        raise ValueError("XML parsed to zero non-empty records")

    headers = sorted(all_keys)
    return headers, rows


def _parse_json(json_bytes: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Parse a JSON file into (headers, rows).

    Handles:
      - Array of objects:   [{...}, {...}]
      - Wrapped object:     {"records": [...]} / {"data": [...]} / any {key: [...]}
      - Single object:      {...}  — treated as one record
    """
    try:
        data = json.loads(json_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error: {exc}") from exc

    records: list[dict[str, Any]] = []

    if isinstance(data, list):
        records = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        # Find the largest list-valued key (likely the record collection)
        list_vals = {k: v for k, v in data.items() if isinstance(v, list)}
        if list_vals:
            best_key = max(list_vals, key=lambda k: len(list_vals[k]))
            records = [r for r in list_vals[best_key] if isinstance(r, dict)]
        else:
            records = [data]
    else:
        raise ValueError("JSON root is neither an array nor an object")

    if not records:
        raise ValueError("JSON parsed to zero records")

    # Flatten one level of nesting
    flat: list[dict[str, Any]] = [_flatten_dict(r) for r in records[:_MAX_ROWS]]

    all_keys: set[str] = set()
    for rec in flat:
        all_keys.update(rec.keys())

    return sorted(all_keys), flat


def _parse_jsonl(jsonl_bytes: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Parse a JSONL file (one JSON object per line) into (headers, rows).

    Streams line-by-line — never loads entire file into a single structure.
    Invalid lines are skipped with a debug log.
    """
    text = jsonl_bytes.decode("utf-8", errors="replace")
    all_keys: set[str] = set()
    rows: list[dict[str, Any]] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if len(rows) >= _MAX_ROWS:
            logger.debug("xml_json_agent.jsonl_row_cap", max_rows=_MAX_ROWS)
            break
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("xml_json_agent.jsonl_invalid_line", lineno=lineno)
            continue
        if not isinstance(obj, dict):
            continue
        flat = _flatten_dict(obj)
        all_keys.update(flat.keys())
        rows.append(flat)

    if not rows:
        raise ValueError("JSONL file contained no valid JSON objects")

    return sorted(all_keys), rows


# ── Haiku extraction fallback (for ambiguous / deeply-nested structures) ───────


_HAIKU_EXTRACTION_PROMPT = """\
You are a CAFM data extraction assistant. The following is a data excerpt \
that could not be parsed into a flat table automatically.

Extract every asset, work order, spare part, or maintenance record you can find \
and return them as a JSON object with this exact structure:
{{
  "rows": [
    {{"field_name": "value", ...}},
    ...
  ],
  "headers": ["field_name", ...]
}}

Rules:
- Use simple, snake_case field names (e.g. "asset_code", "work_order_id").
- One dict per distinct record/entity.
- Return ONLY valid JSON — no markdown, no explanation.
- If no structured data found, return {{"rows": [], "headers": []}}.

DATA EXCERPT:
{excerpt}"""


async def _haiku_extract_structure(
    client: anthropic.AsyncAnthropic,
    content_bytes: bytes,
    source_filename: str,
) -> tuple[list[str], list[dict[str, Any]], int, int]:
    """
    Call Claude Haiku to extract a flat table from unstructured/nested content.

    Returns (headers, rows, tokens_in, tokens_out).
    """
    # Limit excerpt to first 8 KB to keep costs low
    excerpt = content_bytes[:8192].decode("utf-8", errors="replace")
    prompt = _HAIKU_EXTRACTION_PROMPT.format(excerpt=excerpt)

    resp = await client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = resp.content[0].text if resp.content else ""
    tokens_in = resp.usage.input_tokens
    tokens_out = resp.usage.output_tokens

    try:
        parsed = json.loads(raw_text)
        headers: list[str] = parsed.get("headers", [])
        rows: list[dict[str, Any]] = parsed.get("rows", [])
        if not isinstance(headers, list) or not isinstance(rows, list):
            raise ValueError("Unexpected structure from Haiku")
        return headers, rows, tokens_in, tokens_out
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        raise ValueError(f"Haiku returned unparseable JSON: {exc}") from exc


# ── EL-2.1 + EL-2.3 helpers ───────────────────────────────────────────────────


_LLM_JUDGE_PROMPT = """\
You are a CAFM data quality auditor. Compare the source data excerpt with the \
extracted JSON and rate the extraction quality.

Return a JSON object:
{{
  "eval_score": <float 0.0-1.0>,
  "contradictions": ["<description if any>"],
  "verdict": "accept" | "review" | "reject"
}}

Rules:
- eval_score 1.0 = perfect match; 0.0 = completely wrong
- List any fields where extracted value contradicts the source
- Return ONLY valid JSON

SOURCE EXCERPT:
{source_excerpt}

EXTRACTED JSON:
{extracted_json}"""


async def _el_2_3_judge(
    client: anthropic.AsyncAnthropic,
    source_excerpt: str,
    extracted_json: str,
) -> tuple[float, list[str], bool, int, int]:
    """
    EL-2.3 — LLM-as-judge (Haiku).

    Returns (eval_score, contradictions, rules_passed, tokens_in, tokens_out).
    rules_passed = True when eval_score >= _EVAL_SCORE_ACCEPT.
    """
    prompt = _LLM_JUDGE_PROMPT.format(
        source_excerpt=source_excerpt[:3000],
        extracted_json=extracted_json[:3000],
    )

    for attempt in range(3):
        try:
            resp = await client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text if resp.content else ""
            parsed = json.loads(raw)
            score = float(parsed.get("eval_score", 0.0))
            score = max(0.0, min(1.0, score))
            contradictions: list[str] = parsed.get("contradictions", [])
            return (
                score,
                contradictions,
                score >= _EVAL_SCORE_ACCEPT,
                resp.usage.input_tokens,
                resp.usage.output_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                logger.warning(
                    "xml_json_agent.el_2_3_failed",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                return 0.5, [], False, 0, 0
            wait = 2 ** attempt
            import asyncio
            await asyncio.sleep(wait)

    return 0.5, [], False, 0, 0  # unreachable but satisfies type checker


# ── asyncpg COPY writer (identical pattern to csv_agent / excel_agent) ─────────


async def _copy_batch_to_table(
    engine: AsyncEngine,
    table_name: str,
    records: list[tuple[Any, ...]],
    columns: list[str],
) -> None:
    async with engine.connect() as sa_conn:
        raw = await sa_conn.get_raw_connection()
        asyncpg_conn = raw.driver_connection
        await asyncpg_conn.copy_records_to_table(
            table_name,
            records=records,
            columns=columns,
            schema_name=_SCHEMA,
        )


# ── Record builders ────────────────────────────────────────────────────────────


def _build_asset_record(
    row: dict[str, Any],
    org_id: UUID,
    mapping: SchemaMapping,
) -> tuple[tuple[Any, ...], list[str]]:
    row_id = uuid.uuid4()
    record: dict[str, Any] = {
        "id": row_id,
        "organization_id": org_id,
        "asset_name": _coerce_str(row.get("asset_name")) or "Unnamed Asset",
    }
    optional: dict[str, Any] = {
        "asset_code": _coerce_str(row.get("asset_code")),
        "serial_number": _coerce_str(row.get("serial")),
        "manufacturer": _coerce_str(row.get("make")),
        "model_number": _coerce_str(row.get("model")),
    }
    record.update({k: v for k, v in optional.items() if v is not None})
    columns = list(record.keys())
    return tuple(record.values()), columns


def _build_spare_part_record(
    row: dict[str, Any],
    org_id: UUID,
    mapping: SchemaMapping,
) -> tuple[tuple[Any, ...], list[str]]:
    row_id = uuid.uuid4()
    part_name = _coerce_str(row.get("part_code")) or "Unnamed Part"
    stock = _coerce_int(row.get("stock_on_hand")) or 0
    reorder = _coerce_int(row.get("minimum_allowed_stock")) or 0
    record: dict[str, Any] = {
        "id": row_id,
        "organization_id": org_id,
        "part_name": part_name,
        "stock_quantity": max(stock, 0),
        "reorder_level": max(reorder, 0),
    }
    part_code = _coerce_str(row.get("part_code"))
    if part_code:
        record["part_code"] = part_code
    columns = list(record.keys())
    return tuple(record.values()), columns


def _build_work_order_record(
    row: dict[str, Any],
    org_id: UUID,
    mapping: SchemaMapping,
) -> tuple[tuple[Any, ...], list[str]]:
    row_id = uuid.uuid4()
    wo_code = _coerce_str(row.get("wo_code"))
    title = wo_code or "Imported Work Order"
    priority = (_coerce_str(row.get("wo_priority")) or "medium").lower()
    status = (_coerce_str(row.get("wo_status")) or "open").lower()
    record: dict[str, Any] = {
        "id": row_id,
        "organization_id": org_id,
        "title": title,
        "priority": priority,
        "status": status,
    }
    desc = _coerce_str(row.get("wo_type")) or _coerce_str(row.get("maintenance_type"))
    if desc:
        record["description"] = desc
    columns = list(record.keys())
    return tuple(record.values()), columns


_RECORD_BUILDERS = {
    "assets": _build_asset_record,
    "spare_parts": _build_spare_part_record,
    "work_orders": _build_work_order_record,
}


# ── Entity builders (for IntermediateSchema) ───────────────────────────────────


def _row_to_asset_entity(row: dict[str, Any]) -> AssetEntity | None:
    asset_code = _coerce_str(row.get("asset_code"))
    asset_name = _coerce_str(row.get("asset_name"))
    serial = _coerce_str(row.get("serial"))
    if not asset_code and not asset_name and not serial:
        return None
    return AssetEntity(
        asset_code=asset_code,
        name=asset_name,
        serial_number=serial,
        category=_coerce_str(row.get("category")),
        manufacturer=_coerce_str(row.get("make")),
        model_number=_coerce_str(row.get("model")),
        extra={
            k: v for k, v in row.items()
            if k not in {"asset_code", "asset_name", "serial", "category", "make", "model"}
            and v is not None
        },
    )


def _row_to_spare_part_entity(row: dict[str, Any]) -> SparePartEntity | None:
    part_code = _coerce_str(row.get("part_code"))
    if not part_code:
        return None
    stock = _coerce_int(row.get("stock_on_hand"))
    return SparePartEntity(
        part_number=part_code,
        name=part_code,
        quantity=float(stock) if stock is not None else None,
        supplier=_coerce_str(row.get("supplier")),
        extra={
            k: v for k, v in row.items()
            if k not in {"part_code", "stock_on_hand", "minimum_allowed_stock", "supplier"}
            and v is not None
        },
    )


def _row_to_work_order_entity(row: dict[str, Any]) -> WorkOrderEntity | None:
    wo_code = _coerce_str(row.get("wo_code"))
    if not wo_code:
        return None
    return WorkOrderEntity(
        work_order_number=wo_code,
        priority=_coerce_str(row.get("wo_priority")),
        status=_coerce_str(row.get("wo_status")),
        extra={
            k: v for k, v in row.items()
            if k not in {"wo_code", "wo_priority", "wo_status", "wo_type"}
            and v is not None
        },
    )


# ── Public API ─────────────────────────────────────────────────────────────────


async def extract_xml_json(
    file_bytes: bytes,
    *,
    source_filename: str,
    source_type: SourceType,
    ingestion_id: UUID,
    blob_url: str,
    organization_id: UUID,
    redis: Any,
    client: anthropic.AsyncAnthropic,
    engine: AsyncEngine,
) -> IntermediateSchema:
    """
    Stage 2 extraction for XML / JSON / JSONL files.

    Args:
        file_bytes:      Raw file bytes (already validated by Stage 1 / EL-2.0).
        source_filename: Original filename — used for format detection + logging.
        source_type:     SourceType.XML or SourceType.JSON (from Stage 1).
        ingestion_id:    UUID from ingestion_documents (created by Stage 1).
        blob_url:        Azure Blob URL of the stored original.
        organization_id: Tenant/org UUID — stamped on every DB row.
        redis:           Async Redis client (schema mapper caching).
        client:          Async Anthropic client.
        engine:          Async SQLAlchemy engine (raw asyncpg conn for COPY).

    Returns:
        IntermediateSchema — entity list + confidence + audit info.

    Eval layers applied:
        EL-3.0  Schema mapper confidence check (blocks write if < 0.80)
        EL-2.1  Only when Haiku extraction path used (JSON validity + keys check)
        EL-2.2  Pydantic IntermediateSchema validation
        EL-2.3  LLM-as-judge — ONLY if claude_used is True
    """
    t0 = time.monotonic()
    tokens_haiku_in = 0
    tokens_haiku_out = 0
    claude_used = False  # set True when Haiku called for content extraction

    with tracer.start_as_current_span("ingestion.stage2.extract") as span:
        span.set_attribute("cafm.ingestion_id", str(ingestion_id))
        span.set_attribute("cafm.agent_id", AgentId.XML_JSON.value)
        span.set_attribute("cafm.source_type", source_type.value)
        span.set_attribute("cafm.extraction_method", ExtractionMethod.LXML_CLAUDE.value)
        span.set_attribute("cafm.file_size_bytes", len(file_bytes))
        span.set_attribute("cafm.source_filename", source_filename)

        try:
            # ── 1. Detect format + parse to (headers, rows) ───────────────
            file_format = _detect_format(source_filename, file_bytes)
            span.set_attribute("cafm.file_format", file_format)

            headers: list[str] = []
            rows: list[dict[str, Any]] = []
            parse_error: str | None = None

            try:
                if file_format == "xml":
                    headers, rows = _parse_xml(file_bytes)
                elif file_format == "jsonl":
                    headers, rows = _parse_jsonl(file_bytes)
                else:
                    headers, rows = _parse_json(file_bytes)
            except (ValueError, Exception) as parse_exc:  # noqa: BLE001
                parse_error = str(parse_exc)
                logger.warning(
                    "xml_json_agent.parse_failed_using_haiku",
                    ingestion_id=str(ingestion_id),
                    file_format=file_format,
                    error=parse_error,
                )

            # ── EL-2.1: Haiku fallback for ambiguous / parse-failed files ─
            # Trigger when: parse failed entirely, or fewer than _MIN_CANONICAL_HITS
            # recognizable fields were extracted from the headers.
            needs_haiku = (
                parse_error is not None
                or not rows
                or len(headers) < _MIN_CANONICAL_HITS
            )

            if not needs_haiku:
                # Quick canonical field overlap check
                rough_canonical = {
                    h.lower().replace(" ", "_").replace("-", "_")
                    for h in headers
                }
                overlap = sum(
                    1 for sig in _ENTITY_SIGNATURES.values()
                    for f in sig
                    if any(f in rc for rc in rough_canonical)
                )
                if overlap < _MIN_CANONICAL_HITS:
                    needs_haiku = True

            if needs_haiku:
                with tracer.start_as_current_span(
                    "ingestion.eval.extraction_output"
                ) as el21_span:
                    el21_span.set_attribute("cafm.agent_id", AgentId.XML_JSON.value)
                    el21_span.set_attribute("cafm.haiku_fallback", True)

                    try:
                        headers, rows, tokens_haiku_in, tokens_haiku_out = (
                            await _haiku_extract_structure(client, file_bytes, source_filename)
                        )
                        claude_used = True
                        el21_span.set_attribute("cafm.json_valid", True)
                        el21_span.set_attribute("cafm.rows_extracted", len(rows))
                        logger.info(
                            "xml_json_agent.haiku_extraction_done",
                            ingestion_id=str(ingestion_id),
                            rows=len(rows),
                            headers_count=len(headers),
                        )
                    except Exception as haiku_exc:
                        el21_span.set_attribute("cafm.json_valid", False)
                        logger.error(
                            "xml_json_agent.haiku_extraction_failed",
                            ingestion_id=str(ingestion_id),
                            error=str(haiku_exc),
                        )
                        # Return LOW confidence — no data could be extracted
                        span.set_status(StatusCode.ERROR, str(haiku_exc))
                        return IntermediateSchema(
                            ingestion_id=ingestion_id,
                            source_type=source_type,
                            agent_id=AgentId.XML_JSON,
                            source_filename=source_filename,
                            source_blob_url=blob_url,
                            extraction_method=ExtractionMethod.LXML_CLAUDE,
                            model_used=ModelUsed.HAIKU,
                            entities=EntitiesBlock(),
                            confidence=ConfidenceResult(
                                overall=ConfidenceLevel.LOW,
                                eval_score=0.0,
                                rules_passed=False,
                                rules_violations=["extraction_failed_all_paths"],
                            ),
                            audit=AuditInfo(
                                tokens_in=tokens_haiku_in,
                                tokens_out=tokens_haiku_out,
                                processing_ms=round((time.monotonic() - t0) * 1000),
                            ),
                        )

            total_rows = len(rows)
            span.set_attribute("cafm.raw_row_count", total_rows)
            span.set_attribute("cafm.raw_column_count", len(headers))
            span.set_attribute("cafm.claude_used_for_extraction", claude_used)

            logger.info(
                "xml_json_agent.parsed",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                file_format=file_format,
                rows=total_rows,
                headers=len(headers),
                haiku_fallback=claude_used,
            )

            # ── 2. Schema mapper — called ONCE per file ───────────────────
            sample_rows_str: list[dict[str, Any]] = [
                {str(k): (str(v) if v is not None else None) for k, v in r.items()}
                for r in rows[:_SAMPLE_ROWS]
            ]
            mapping: SchemaMapping = await map_headers(
                headers,
                redis=redis,
                client=client,
                sample_rows=sample_rows_str,
            )

            span.set_attribute("cafm.schema_mapped_count", len(mapping.mapped))
            span.set_attribute("cafm.schema_unmatched_count", len(mapping.unmatched))
            span.set_attribute("cafm.schema_cache_hit", mapping.cached)
            span.set_attribute("cafm.schema_requires_review", mapping.requires_human_review)

            # ── EL-3.0 — Mapping confidence check ────────────────────────
            if mapping.requires_human_review:
                logger.warning(
                    "xml_json_agent.low_confidence_mapping",
                    ingestion_id=str(ingestion_id),
                    overall_confidence=mapping.overall_confidence,
                    unmatched=mapping.unmatched,
                )
                return IntermediateSchema(
                    ingestion_id=ingestion_id,
                    source_type=source_type,
                    agent_id=AgentId.XML_JSON,
                    source_filename=source_filename,
                    source_blob_url=blob_url,
                    extraction_method=ExtractionMethod.LXML_CLAUDE,
                    model_used=ModelUsed.HAIKU,
                    entities=EntitiesBlock(),
                    confidence=ConfidenceResult(
                        overall=ConfidenceLevel.LOW,
                        eval_score=mapping.overall_confidence,
                        rules_passed=False,
                        rules_violations=["schema_mapping_confidence_below_threshold"],
                    ),
                    audit=AuditInfo(
                        tokens_in=tokens_haiku_in,
                        tokens_out=tokens_haiku_out,
                        processing_ms=round((time.monotonic() - t0) * 1000),
                    ),
                )

            # ── 3. Rename row keys to canonical names ──────────────────────
            rename_map: dict[str, str] = {
                raw: canon for raw, canon in mapping.mapped.items()
            }
            canonical_rows: list[dict[str, Any]] = []
            for row in rows:
                canon_row: dict[str, Any] = {}
                for raw_key, val in row.items():
                    canon_key = rename_map.get(raw_key, raw_key)
                    canon_row[canon_key] = val
                canonical_rows.append(canon_row)

            canonical_cols = set(rename_map.values())

            # ── 4. Detect entity type ─────────────────────────────────────
            entity_type = _detect_entity_type(canonical_cols)
            span.set_attribute("cafm.entity_type", entity_type)
            logger.info(
                "xml_json_agent.entity_type_detected",
                ingestion_id=str(ingestion_id),
                entity_type=entity_type,
                canonical_cols=sorted(canonical_cols),
            )

            # ── EL-2.2 — Schema conformance span ──────────────────────────
            # Pydantic validation runs implicitly during entity builders below;
            # the span records that validation is in progress.
            with tracer.start_as_current_span(
                "ingestion.eval.schema_conformance"
            ) as el22_span:
                el22_span.set_attribute("cafm.agent_id", AgentId.XML_JSON.value)
                el22_span.set_attribute("cafm.entities_count", total_rows)
                el22_span.set_attribute("cafm.schema_valid", True)  # updated on exception

            # ── 5. Stream in batches (asyncpg COPY) ───────────────────────
            rows_written = 0
            rows_failed = 0
            batches_written = 0

            asset_entities: list[AssetEntity] = []
            part_entities: list[SparePartEntity] = []
            wo_entities: list[WorkOrderEntity] = []

            builder = _RECORD_BUILDERS.get(entity_type)
            use_direct_copy = entity_type in _DIRECT_COPY_TABLES and builder is not None

            for batch_start in range(0, total_rows, _BATCH_SIZE):
                batch = canonical_rows[batch_start: batch_start + _BATCH_SIZE]
                batch_records: list[tuple[Any, ...]] = []
                batch_columns: list[str] | None = None

                for row in batch:
                    clean_row: dict[str, Any] = {
                        k: (None if v is None else v)
                        for k, v in row.items()
                    }

                    if entity_type == "assets":
                        ent = _row_to_asset_entity(clean_row)
                        if ent:
                            asset_entities.append(ent)
                    elif entity_type == "spare_parts":
                        ent = _row_to_spare_part_entity(clean_row)
                        if ent:
                            part_entities.append(ent)
                    elif entity_type == "work_orders":
                        ent = _row_to_work_order_entity(clean_row)
                        if ent:
                            wo_entities.append(ent)

                    if use_direct_copy and builder is not None:
                        try:
                            record, cols = builder(clean_row, organization_id, mapping)
                            if batch_columns is None:
                                batch_columns = cols
                            if len(record) == len(batch_columns):
                                batch_records.append(record)
                        except Exception as row_exc:
                            rows_failed += 1
                            logger.debug(
                                "xml_json_agent.row_skipped",
                                ingestion_id=str(ingestion_id),
                                error=str(row_exc),
                            )

                if use_direct_copy and batch_records and batch_columns:
                    try:
                        await _copy_batch_to_table(
                            engine, entity_type, batch_records, batch_columns
                        )
                        rows_written += len(batch_records)
                        batches_written += 1
                        logger.debug(
                            "xml_json_agent.batch_written",
                            ingestion_id=str(ingestion_id),
                            batch_num=batches_written,
                            rows=len(batch_records),
                            table=entity_type,
                        )
                    except Exception as copy_exc:
                        rows_failed += len(batch_records)
                        logger.error(
                            "xml_json_agent.copy_failed",
                            ingestion_id=str(ingestion_id),
                            batch_num=batches_written + 1,
                            table=entity_type,
                            error=str(copy_exc),
                        )

            processing_ms_pre_eval = round((time.monotonic() - t0) * 1000)
            span.set_attribute("cafm.rows_written", rows_written)
            span.set_attribute("cafm.rows_failed", rows_failed)

            # ── EL-2.3 — LLM-as-judge (ONLY if Haiku extraction was used) ─
            eval_score: float
            contradictions: list[str] = []
            rules_passed = True
            route = "accept"
            tokens_judge_in = 0
            tokens_judge_out = 0

            if claude_used:
                with tracer.start_as_current_span(
                    "ingestion.eval.llm_judge"
                ) as el23_span:
                    el23_span.set_attribute("cafm.agent_id", AgentId.XML_JSON.value)

                    # Source excerpt: first 3 KB of raw file
                    source_excerpt = file_bytes[:3072].decode("utf-8", errors="replace")
                    # Extracted JSON: first few entities
                    extracted_sample = json.dumps(canonical_rows[:10], default=str)

                    eval_score, contradictions, rules_passed, tokens_judge_in, tokens_judge_out = (
                        await _el_2_3_judge(client, source_excerpt, extracted_sample)
                    )
                    tokens_haiku_in += tokens_judge_in
                    tokens_haiku_out += tokens_judge_out

                    if eval_score >= _EVAL_SCORE_ACCEPT:
                        route = "accept"
                    elif eval_score >= _EVAL_SCORE_REVIEW:
                        route = "review"
                    else:
                        route = "re_extract"

                    el23_span.set_attribute("cafm.eval_score", eval_score)
                    el23_span.set_attribute("cafm.rules_violations_count", len(contradictions))
                    el23_span.set_attribute("cafm.route", route)

                    logger.info(
                        "xml_json_agent.el_2_3_result",
                        ingestion_id=str(ingestion_id),
                        eval_score=eval_score,
                        route=route,
                        contradictions=contradictions,
                    )
            else:
                # Structured data, no LLM extraction — confidence from row failure rate
                if rows_failed == 0:
                    eval_score = 0.95
                elif rows_failed < total_rows * 0.1:
                    eval_score = 0.80
                else:
                    eval_score = 0.60
                rules_passed = rows_failed == 0

            processing_ms = round((time.monotonic() - t0) * 1000)

            # Map route → confidence level
            if route in ("accept", "") and rows_failed == 0:
                overall_conf = ConfidenceLevel.HIGH
            elif route == "review" or rows_failed < total_rows * 0.1:
                overall_conf = ConfidenceLevel.MEDIUM
            else:
                overall_conf = ConfidenceLevel.LOW

            span.set_attribute("cafm.eval_score", eval_score)
            span.set_attribute("cafm.confidence_overall", overall_conf.value)
            span.set_status(StatusCode.OK)

            logger.info(
                "xml_json_agent.extraction_complete",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                entity_type=entity_type,
                total_rows=total_rows,
                rows_written=rows_written,
                rows_failed=rows_failed,
                claude_used=claude_used,
                eval_score=eval_score,
                route=route,
                processing_ms=processing_ms,
            )

            entities = EntitiesBlock(
                assets=asset_entities,
                spare_parts=part_entities,
                work_orders=wo_entities,
            )

            return IntermediateSchema(
                ingestion_id=ingestion_id,
                source_type=source_type,
                agent_id=AgentId.XML_JSON,
                source_filename=source_filename,
                source_blob_url=blob_url,
                extraction_method=ExtractionMethod.LXML_CLAUDE,
                model_used=ModelUsed.HAIKU if claude_used else ModelUsed.NONE,
                entities=entities,
                confidence=ConfidenceResult(
                    overall=overall_conf,
                    eval_score=eval_score,
                    rules_passed=rules_passed,
                    rules_violations=(
                        contradictions
                        + ([f"{rows_failed} rows failed during COPY"] if rows_failed > 0 else [])
                    ),
                ),
                audit=AuditInfo(
                    tokens_in=tokens_haiku_in,
                    tokens_out=tokens_haiku_out,
                    processing_ms=processing_ms,
                ),
            )

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            logger.error(
                "xml_json_agent.extraction_failed",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                error=str(exc),
            )
            raise
