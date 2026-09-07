"""
svc-ingestion/src/shared/schema_mapper.py

Task 2.6 — Layer 3: AI Schema Mapper.

Maps raw customer column headers → canonical CAFM field registry using
Claude Haiku. Called once per new source file; result cached in Redis
for 24 hours.

Usage (by any ingestion agent):
    mapping = await map_headers(headers, redis=redis, client=client, sample_rows=rows)
    renamed, raw_metadata = apply_mapping(raw_row_dict, mapping)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import BaseModel

from cafm_shared.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Canonical field registry ───────────────────────────────────────────────────

CANONICAL_FIELDS: frozenset[str] = frozenset(
    {
        # ── Assets ────────────────────────────────────────────────────────
        "asset_code",           # A0000001 / external_asset_id
        "asset_name",           # descriptive name
        "category",             # category name (string, not UUID)
        "category_name",        # alternate: asset_categories.name
        "location_code",        # S0001 / site code
        "location_name",        # full location name
        "location_type",        # site | building | floor | room
        "parent_location",      # parent location code
        "make",                 # manufacturer / make
        "model",                # model number
        "serial",               # serial number
        "installation_date",    # date asset was installed
        "warranty_expiry",      # warranty expiry date
        "asset_status",         # active | inactive | decommissioned
        "health_score",         # 0–100
        "criticality",          # High | Med | Low
        "qr_code",              # QR code path/value

        # ── Work Orders ───────────────────────────────────────────────────
        "wo_code",              # WO00000001 / external_wo_id
        "wo_priority",          # P1 / P2 / Highest / High / Medium / Low
        "wo_status",            # open | in_progress | completed | cancelled
        "wo_type",              # Corrective | Preventive | Emergency
        "wo_title",             # short title / description
        "wo_description",       # full description
        "maintenance_type",     # PM / CM / EM
        "fault_code",           # fault code (e.g. Noise)
        "cause_code",           # cause code (e.g. Unknown)
        "resolution_code",      # resolution code (e.g. CleanAdjust)
        "labor_minutes",        # actual labour time
        "travel_minutes",       # travel time
        "sla_due_at",           # SLA due timestamp
        "responded_at",         # first response timestamp
        "completed_at",         # completion timestamp
        "cost_parts",           # parts cost (AED or other currency)
        "cost_vendor",          # vendor cost
        "sla_breached",         # True / False

        # ── Scheduled PM / Maintenance Plans ─────────────────────────────
        "sm_code",              # PM plan code
        "trigger_type",         # t (time) | m (meter)
        "schedule_interval",    # numeric interval
        "sm_priority",          # PM priority
        "frequency_type",       # month | week | day | hour
        "frequency_value",      # numeric frequency
        "next_due_date",        # next scheduled date
        "last_service_date",    # last completed service date

        # ── Parts / Inventory ─────────────────────────────────────────────
        "part_code",            # part number / SKU
        "part_name",            # part description
        "stock_on_hand",        # current stock quantity
        "minimum_allowed_stock",# reorder threshold
        "supplier",             # supplier name
        "bom_group_name",       # BOM group
        "unit_cost",            # cost per unit

        # ── Users ─────────────────────────────────────────────────────────
        "user_full_name",       # full name
        "user_email",           # email address
        "user_title",           # job title
        "user_name",            # username / login
        "reports_to",           # manager / supervisor
        "user_status",          # active | inactive
        "user_phone",           # phone number
        "user_role",            # role name

        # ── Technicians ───────────────────────────────────────────────────
        "technician_code",      # tech identifier
        "technician_name",      # tech full name
        "base_location",        # home base location
        "availability_status",  # available | busy | on_leave
        "performance_score",    # numeric performance score
        "skill_name",           # skill label (HVAC, Electrical, etc.)
        "skill_level",          # Junior | Senior | SrTech etc.

        # ── Vendors ───────────────────────────────────────────────────────
        "vendor_name",          # vendor / company name
        "vendor_code",          # vendor identifier
        "vendor_address",       # address
        "vendor_contact",       # contact name
        "vendor_email",         # contact email
        "vendor_phone",         # contact phone

        # ── SLA Policies ──────────────────────────────────────────────────
        "sla_name",             # policy name
        "sla_priority",         # P1 / P2 / P3
        "response_time_minutes",# target response time
        "resolution_time_minutes", # target resolution time

        # ── Inspections ───────────────────────────────────────────────────
        "inspector_name",       # inspector full name
        "inspection_date",      # date/time of inspection
        "inspection_type",      # MEPChecklist | Visual | etc.
        "inspection_location",  # location of inspection
        "finding_type",         # finding category
        "risk_level",           # High | Medium | Low
        "findings_count",       # number of findings
        "critical_flag",        # True / False
        "inspection_notes",     # free-text notes

        # ── Asset Readings ────────────────────────────────────────────────
        "reading_type",         # Temp | Pressure | Vibration | Current | etc.
        "reading_value",        # numeric reading
        "reading_unit",         # C | kPa | mm/s | A | Pa
        "recorded_at",          # timestamp of reading
        "anomaly_flag",         # True / False

        # ── Technician Utilisation ────────────────────────────────────────
        "utilization_month",    # YYYY-MM
        "planned_hours",        # planned work hours
        "actual_hours",         # actual hours worked
        "overtime_hours",       # overtime hours
        "travel_hours",         # travel hours
        "training_hours",       # training hours
        "utilization_pct",      # utilisation percentage

        # ── Organizations ─────────────────────────────────────────────────
        "org_name",             # organization / tenant name
        "org_industry",         # industry sector
        "org_country",          # country
        "org_timezone",         # timezone (e.g. Asia/Dubai)
        "org_status",           # active | inactive

        # ── Locations ─────────────────────────────────────────────────────
        "location_level",       # hierarchy level (0=site, 1=building, 2=floor …)

        # ── Asset Categories ──────────────────────────────────────────────
        "category_description", # human-readable description of the category
    }
)

_CANONICAL_SORTED: list[str] = sorted(CANONICAL_FIELDS)  # stable ordering for prompts

_CACHE_TTL: int = 86_400          # 24 hours in seconds
_REVIEW_THRESHOLD: float = 0.80   # flag for human review below this
_HAIKU_MODEL: str = "claude-haiku-4-5"
_MAX_SAMPLE_ROWS: int = 5
_RETRY_ATTEMPTS: int = 3
_RETRY_BASE_DELAY: float = 1.0    # seconds


# ── Output model ───────────────────────────────────────────────────────────────


class SchemaMapping(BaseModel):
    """
    Result of mapping raw column headers to the canonical CAFM field registry.

    Agents use ``mapped`` to rename their columns.
    Columns in ``unmatched`` go to raw_metadata JSONB — never dropped.
    If ``requires_human_review`` is True, the ingestion should be paused
    and routed to the review queue before proceeding.
    """

    source_hash: str
    mapped: dict[str, str]   # raw_header → canonical_field
    unmatched: list[str]     # headers that could not be mapped
    overall_confidence: float  # avg of per-column confidence scores (0.0–1.0)
    requires_human_review: bool  # True when overall_confidence < _REVIEW_THRESHOLD
    cached: bool = False         # True when result was served from Redis cache


# ── Internal helpers ───────────────────────────────────────────────────────────


def _hash_headers(headers: list[str]) -> str:
    """Stable SHA-256 of sorted, lowercased headers — used as the cache key."""
    normalised = sorted(h.strip().lower() for h in headers)
    return hashlib.sha256(json.dumps(normalised).encode()).hexdigest()


def _cache_key(source_hash: str) -> str:
    return f"schema_map:{source_hash}"


def _build_prompt(
    headers: list[str],
    sample_rows: list[dict[str, Any]] | None,
) -> str:
    canonical_block = "\n".join(f"  - {f}" for f in _CANONICAL_SORTED)
    headers_json = json.dumps(headers, indent=2)
    sample_block = ""
    if sample_rows:
        trimmed = sample_rows[: _MAX_SAMPLE_ROWS]
        sample_block = (
            f"\n\nSample data ({len(trimmed)} rows shown):\n"
            + json.dumps(trimmed, indent=2, default=str)
        )

    return (
        "You are a CAFM data integration expert. "
        "Map the following column headers to the canonical CAFM field registry.\n\n"
        f"## Canonical fields (map TO these):\n{canonical_block}\n\n"
        f"## Raw column headers:\n{headers_json}"
        f"{sample_block}\n\n"
        "## Rules:\n"
        "1. For each raw header find the best matching canonical field.\n"
        "2. If no canonical field matches set canonical to null.\n"
        "3. Assign confidence 0.0–1.0 for each mapping.\n"
        "4. Be liberal: 'Asset Code', 'asset_code', 'AssetCode', 'ASSET_CODE' "
        "all map to 'asset_code'.\n"
        "5. Never invent canonical fields that are not in the list above.\n\n"
        "Return ONLY valid JSON — no markdown fences — in this exact format:\n"
        '{"mappings": ['
        '{"raw": "Asset Code", "canonical": "asset_code", "confidence": 0.99},'
        '{"raw": "UnknownCol", "canonical": null, "confidence": 0.0}'
        "]}"
    )


def _parse_response_text(text: str) -> list[dict[str, Any]]:
    """Strip markdown fences if present and parse JSON mappings list."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        # Remove closing fence if present
        if "```" in text:
            text = text[: text.index("```")]
    parsed: dict[str, Any] = json.loads(text.strip())
    return list(parsed["mappings"])


def _build_schema_mapping(
    source_hash: str,
    raw_mappings: list[dict[str, Any]],
    cached: bool = False,
) -> SchemaMapping:
    """Convert raw Haiku output into a validated SchemaMapping model."""
    mapped: dict[str, str] = {}
    unmatched: list[str] = []
    confidences: list[float] = []

    for m in raw_mappings:
        raw: str = str(m["raw"])
        canonical: str | None = m.get("canonical")
        confidence: float = float(m.get("confidence", 0.0))
        confidences.append(confidence)

        if canonical and canonical in CANONICAL_FIELDS:
            mapped[raw] = canonical
        else:
            unmatched.append(raw)

    # Only average confidence over columns that were actually mapped to a canonical field.
    # Unmatched columns (confidence=0.0) correctly go to raw_metadata — they should not
    # penalise the mapping quality score (EL-3.0 measures how well mappable columns mapped).
    mapped_confidences = [
        float(m.get("confidence", 0.0))
        for m in raw_mappings
        if m.get("canonical") and m["canonical"] in CANONICAL_FIELDS
    ]
    overall = sum(mapped_confidences) / len(mapped_confidences) if mapped_confidences else 0.0

    return SchemaMapping(
        source_hash=source_hash,
        mapped=mapped,
        unmatched=unmatched,
        overall_confidence=round(overall, 4),
        requires_human_review=overall < _REVIEW_THRESHOLD,
        cached=cached,
    )


async def _call_haiku_with_retry(
    client: anthropic.AsyncAnthropic,
    headers: list[str],
    sample_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Call Claude Haiku with 3x exponential backoff."""
    prompt = _build_prompt(headers, sample_rows)
    last_exc: Exception | None = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            response = await client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return _parse_response_text(response.content[0].text)  # type: ignore[union-attr]
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "schema_mapper.haiku_retry",
                    attempt=attempt + 1,
                    delay_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        except Exception as exc:
            raise exc  # non-retryable

    raise RuntimeError(
        f"schema_mapper: Claude Haiku failed after {_RETRY_ATTEMPTS} attempts"
    ) from last_exc


# ── Public API ─────────────────────────────────────────────────────────────────


async def map_headers(
    headers: list[str],
    *,
    redis: Any,  # redis.asyncio.Redis — typed as Any to avoid hard import
    client: anthropic.AsyncAnthropic,
    sample_rows: list[dict[str, Any]] | None = None,
) -> SchemaMapping:
    """
    Map raw column headers to the canonical CAFM field registry.

    Cache check:  Redis key ``schema_map:{source_hash}`` (TTL 24h)
    Cache miss:   Claude Haiku called once, result cached
    Side effect:  ``requires_human_review=True`` when confidence < 0.80

    Args:
        headers:     List of raw column names from the source file.
        redis:       Async Redis client.
        client:      Async Anthropic client.
        sample_rows: Optional first-N rows as list[dict] to help Haiku.

    Returns:
        SchemaMapping with .mapped, .unmatched, .overall_confidence,
        .requires_human_review, .cached.
    """
    source_hash = _hash_headers(headers)
    ck = _cache_key(source_hash)

    with tracer.start_as_current_span("schema_mapper.map") as span:
        span.set_attribute("cafm.source_hash", source_hash)
        span.set_attribute("cafm.headers_count", len(headers))

        try:
            # ── 1. Cache check ────────────────────────────────────────────
            t0 = time.monotonic()
            cached_bytes: bytes | None = await redis.get(ck)

            if cached_bytes is not None:
                raw_mappings = json.loads(cached_bytes)
                mapping = _build_schema_mapping(source_hash, raw_mappings, cached=True)
                span.set_attribute("cafm.cache_hit", True)
                span.set_attribute("cafm.mapped_count", len(mapping.mapped))
                span.set_attribute("cafm.unmatched_count", len(mapping.unmatched))
                span.set_status(StatusCode.OK)
                logger.info(
                    "schema_mapper.cache_hit",
                    source_hash=source_hash,
                    mapped=len(mapping.mapped),
                    unmatched=len(mapping.unmatched),
                    latency_ms=round((time.monotonic() - t0) * 1000),
                )
                return mapping

            # ── 2. Call Haiku ─────────────────────────────────────────────
            span.set_attribute("cafm.cache_hit", False)
            raw_mappings = await _call_haiku_with_retry(client, headers, sample_rows)

            # ── 3. Store in Redis ─────────────────────────────────────────
            await redis.setex(ck, _CACHE_TTL, json.dumps(raw_mappings))

            mapping = _build_schema_mapping(source_hash, raw_mappings, cached=False)
            latency_ms = round((time.monotonic() - t0) * 1000)

            span.set_attribute("cafm.mapped_count", len(mapping.mapped))
            span.set_attribute("cafm.unmatched_count", len(mapping.unmatched))
            span.set_status(StatusCode.OK)

            logger.info(
                "schema_mapper.mapped",
                source_hash=source_hash,
                mapped=len(mapping.mapped),
                unmatched=len(mapping.unmatched),
                overall_confidence=mapping.overall_confidence,
                requires_human_review=mapping.requires_human_review,
                latency_ms=latency_ms,
            )

            if mapping.requires_human_review:
                logger.warning(
                    "schema_mapper.low_confidence",
                    source_hash=source_hash,
                    overall_confidence=mapping.overall_confidence,
                    threshold=_REVIEW_THRESHOLD,
                    unmatched=mapping.unmatched,
                )

            return mapping

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            raise


def apply_mapping(
    row: dict[str, Any],
    mapping: SchemaMapping,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Apply a SchemaMapping to a single raw row dict.

    Returns:
        (canonical_row, raw_metadata)
        - canonical_row: keys renamed to canonical CAFM field names
        - raw_metadata:  unmatched columns — always preserved, never dropped
    """
    canonical_row: dict[str, Any] = {}
    raw_metadata: dict[str, Any] = {}

    for raw_key, value in row.items():
        canonical = mapping.mapped.get(raw_key)
        if canonical:
            canonical_row[canonical] = value
        else:
            raw_metadata[raw_key] = value

    return canonical_row, raw_metadata
