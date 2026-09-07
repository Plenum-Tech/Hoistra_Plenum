"""
svc-ingestion/src/agents/csv_agent.py

Task 2.2 — CSV Agent (Layer 2, Stage 2).

Ingests structured CSV/TSV files into the unified plenum_cafm store.

Design:
  - encoding=latin1   for all known client files (assets, parts, WOs, PM, users)
  - Schema mapper     called ONCE per file — Claude Haiku maps columns → canonical fields
  - asyncpg COPY      for tables where all required columns can be satisfied directly
  - Batch size        1000 rows per COPY call
  - IntermediateSchema returned for all entity types; Stage 4 (unifier) handles
                      rows that need FK resolution (maintenance_plans, users)

Known client files and their target tables:
  assets.csv        → plenum_cafm.assets
  parts.csv         → plenum_cafm.spare_parts
  work_orders.csv   → plenum_cafm.work_orders
  scheduled_pm.csv  → plenum_cafm.maintenance_plans  (via unifier — needs asset_id FK)
  task_groups.csv   → plenum_cafm.work_order_tasks   (via unifier — needs work_order_id FK)
  users.csv         → plenum_cafm.users               (via unifier — needs password_hash)

OTel spans: ingestion.stage2.extract + schema_mapper.map (inside map_headers call)
"""

from __future__ import annotations

import io
import time
import uuid
from typing import Any
from uuid import UUID

import anthropic
import pandas as pd
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import text
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
    FindingEntity,
    IntermediateSchema,
    ModelUsed,
    ReadingEntity,
    SparePartEntity,
    SourceType,
    TechnicianEntity,
    VendorEntity,
    WorkOrderEntity,
)
from shared.schema_mapper import SchemaMapping, map_headers

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_BATCH_SIZE = 1_000
_SAMPLE_ROWS = 50       # rows passed to schema mapper for context
_ENCODING = "latin-1"   # all known client CSV files use latin-1
_SCHEMA = "plenum_cafm"

# ── Entity type detection ──────────────────────────────────────────────────────
# Score each known entity type by how many of its signature fields appear
# in the mapped canonical fields. Highest score wins.
# Signatures use BOTH the legacy client field names AND the standard DB field names
# so both export formats are detected correctly.

# ── DB-direct bypass ──────────────────────────────────────────────────────────
# Files that are already DB exports (have UUID `id` column + `created_at` or
# `organization_id`) skip Claude schema mapping entirely.
# Canonical mappings are defined per entity type using the known DB column names.

_DB_DIRECT_FILENAME_MAP: dict[str, str] = {
    "organizations":          "organizations",
    "sla_policies":           "sla_policies",
    "technician_skills":      "technician_skills",
    "technician_utilization": "technician_utilization",
    "asset_readings":         "asset_readings",
    "asset_inspections":      "inspections",
    "locations":              "locations",
    "technicians":            "technicians",
    "work_orders":            "work_orders",
    "assets":                 "assets",
    "spare_parts":            "spare_parts",
    "maintenance_plans":      "maintenance_plans",
    "users":                  "users",
    "vendors":                "vendors",
    "asset_categories":       "asset_categories",
}

# DB column name → canonical field name, per entity type.
# Only columns that map to a recognised canonical field are listed;
# the rest go to raw_metadata (unmatched).
_DB_COLUMN_TO_CANONICAL: dict[str, dict[str, str]] = {
    "organizations": {
        "name":         "org_name",
        "industry":     "org_industry",
        "country":      "org_country",
        "timezone":     "org_timezone",
        "status":       "org_status",
    },
    "sla_policies": {
        "name":                     "sla_name",
        "priority":                 "sla_priority",
        "response_time_minutes":    "response_time_minutes",
        "response_time_hours":      "response_time_minutes",   # convert on read
        "resolution_time_minutes":  "resolution_time_minutes",
        "resolution_time_hours":    "resolution_time_minutes",
    },
    "technician_skills": {
        "skill_name":   "skill_name",
        "skill_level":  "skill_level",
    },
    "technician_utilization": {
        "month":          "utilization_month",
        "planned_hours":  "planned_hours",
        "actual_hours":   "actual_hours",
        "overtime_hours": "overtime_hours",
        "travel_hours":   "travel_hours",
        "training_hours": "training_hours",
        "utilization_pct": "utilization_pct",
    },
    "asset_readings": {
        "reading_type":  "reading_type",
        "value":         "reading_value",
        "unit":          "reading_unit",
        "recorded_at":   "recorded_at",
        "anomaly_flag":  "anomaly_flag",
    },
    "inspections": {
        "inspection_type":  "inspection_type",
        "inspection_date":  "inspection_date",
        "inspector_name":   "inspector_name",
        "inspector":        "inspector_name",
        "findings_count":   "findings_count",
        "critical_flag":    "critical_flag",
        "notes":            "inspection_notes",
        "risk_level":       "risk_level",
    },
    "locations": {
        "name":         "location_name",
        "type":         "location_type",
        "code":         "location_code",
        "level":        "location_level",
        "parent_id":    "parent_location",
        "parent_code":  "parent_location",
    },
    "technicians": {
        "employee_id":       "technician_code",
        "full_name":         "technician_name",
        "name":              "technician_name",
        "base_location":     "base_location",
        "status":            "availability_status",
        "performance_score": "performance_score",
    },
    "work_orders": {
        "title":        "wo_title",
        "priority":     "wo_priority",
        "status":       "wo_status",
        "type":         "wo_type",
        "description":  "wo_description",
        "fault_code":   "fault_code",
        "cause_code":   "cause_code",
    },
    "assets": {
        "asset_code":        "asset_code",
        "asset_name":        "asset_name",
        "name":              "asset_name",
        "serial_number":     "serial",
        "manufacturer":      "make",
        "model_number":      "model",
        "status":            "asset_status",
        "health_score":      "health_score",
        "criticality":       "criticality",
        "installation_date": "installation_date",
        "warranty_expiry":   "warranty_expiry",
    },
    "spare_parts": {
        "part_code":      "part_code",
        "part_name":      "part_name",
        "stock_quantity": "stock_on_hand",
        "reorder_level":  "minimum_allowed_stock",
        "unit_cost":      "unit_cost",
    },
    "maintenance_plans": {
        "plan_code":       "sm_code",
        "trigger_type":    "trigger_type",
        "frequency_value": "schedule_interval",
        "frequency_type":  "frequency_type",
        "priority":        "sm_priority",
        "next_due_date":   "next_due_date",
    },
    "users": {
        "full_name":   "user_full_name",
        "username":    "user_name",
        "email":       "user_email",
        "title":       "user_title",
        "status":      "user_status",
        "phone":       "user_phone",
    },
    "vendors": {
        "name":         "vendor_name",
        "code":         "vendor_code",
        "address":      "vendor_address",
        "contact_name": "vendor_contact",
        "email":        "vendor_email",
        "phone":        "vendor_phone",
    },
    "asset_categories": {
        "name":        "category_name",
        "description": "category_description",
    },
}


def _is_db_format(headers: list[str]) -> bool:
    """
    Return True when the CSV looks like a DB export.

    Heuristic: has an `id` column AND at least one of the DB-export
    marker columns (`organization_id`, `created_at`, `updated_at`,
    `technician_id`, `asset_id`).
    """
    h = {c.lower().strip() for c in headers}
    has_id = "id" in h
    has_marker = bool(
        h & {"organization_id", "created_at", "updated_at",
             "technician_id", "asset_id", "plan_id", "vendor_id"}
    )
    return has_id and has_marker


def _entity_type_from_filename(filename: str) -> str | None:
    """
    Derive entity type from a DB-export filename.

    e.g. 'organizations_db.csv' → 'organizations'
         'asset_readings_db.csv' → 'asset_readings'
         'asset_inspections_db.csv' → 'inspections'
    """
    stem = filename.lower().rsplit(".", 1)[0]
    stem = stem.replace("-", "_")
    # Strip common suffixes
    for suffix in ("_db", "_export", "_data", "_dump"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return _DB_DIRECT_FILENAME_MAP.get(stem)


def _build_db_direct_mapping(entity_type: str, headers: list[str]) -> "SchemaMapping":
    """
    Build a SchemaMapping for a DB-format file without calling Claude.

    All columns that appear in _DB_COLUMN_TO_CANONICAL[entity_type] are
    mapped; the rest are considered unmatched (→ raw_metadata).
    Confidence is 1.0 — no AI needed.
    """
    import hashlib

    col_map = _DB_COLUMN_TO_CANONICAL.get(entity_type, {})
    mapped: dict[str, str] = {}
    unmatched: list[str] = []
    _db_meta_cols = frozenset({
        "id", "organization_id", "created_at", "updated_at",
        "technician_id", "asset_id", "plan_id", "vendor_id",
        "location_id", "user_id", "work_order_id",
        "category_id", "parent_id", "deleted_at",
    })
    for h in headers:
        canonical = col_map.get(h.lower().strip()) or col_map.get(h.strip())
        if canonical:
            mapped[h] = canonical
        elif h.lower().strip() not in _db_meta_cols:
            unmatched.append(h)

    source_hash = hashlib.sha256(
        (entity_type + "|" + ",".join(sorted(headers))).encode()
    ).hexdigest()

    return SchemaMapping(
        source_hash=source_hash,
        mapped=mapped,
        unmatched=unmatched,
        overall_confidence=1.0,
        cached=False,
        requires_human_review=False,
    )


# ── End DB-direct bypass ───────────────────────────────────────────────────────

_ENTITY_SIGNATURES: dict[str, frozenset[str]] = {
    # ── Direct-write tables (asyncpg COPY) ────────────────────────────────
    "assets": frozenset({
        "asset_code", "asset_name", "make", "model", "serial",
        "installation_date", "warranty_expiry", "asset_status", "health_score",
        "criticality", "qr_code",
    }),
    "spare_parts": frozenset({
        "part_code", "part_name", "stock_on_hand", "minimum_allowed_stock",
        "bom_group_name", "unit_cost",
    }),
    "work_orders": frozenset({
        "wo_code", "wo_priority", "wo_status", "wo_type", "maintenance_type",
        "wo_title", "fault_code", "cause_code", "resolution_code",
        "labor_minutes", "sla_due_at", "cost_parts", "sla_breached",
    }),
    # ── Via unifier (FK resolution needed) ───────────────────────────────
    "maintenance_plans": frozenset({
        "sm_code", "trigger_type", "schedule_interval", "sm_priority",
        "frequency_type", "frequency_value", "next_due_date",
    }),
    "users": frozenset({
        "user_full_name", "user_name", "user_email", "user_title",
        "reports_to", "user_status", "user_phone", "user_role",
    }),
    "locations": frozenset({
        "location_name", "location_type", "location_code", "parent_location",
    }),
    "technicians": frozenset({
        "technician_code", "technician_name", "base_location",
        "availability_status", "performance_score",
    }),
    "technician_skills": frozenset({
        "skill_name", "skill_level",
    }),
    "vendors": frozenset({
        "vendor_name", "vendor_code", "vendor_address",
        "vendor_contact", "vendor_email",
    }),
    "sla_policies": frozenset({
        "sla_name", "sla_priority", "response_time_minutes",
        "resolution_time_minutes",
    }),
    "inspections": frozenset({
        "inspection_type", "inspection_date", "inspector_name",
        "findings_count", "critical_flag", "inspection_notes",
    }),
    "asset_readings": frozenset({
        "reading_type", "reading_value", "reading_unit",
        "recorded_at", "anomaly_flag",
    }),
    "technician_utilization": frozenset({
        "utilization_month", "planned_hours", "actual_hours",
        "utilization_pct", "overtime_hours",
    }),
    "asset_categories": frozenset({
        "category_name",          # category files have ONLY name + description
        "category_description",
    }),
    "organizations": frozenset({
        "org_name", "org_industry", "org_country", "org_timezone", "org_status",
    }),
}

# Minimum matches required to claim an entity type (avoids spurious hits on small files)
_MIN_SIGNATURE_MATCHES: dict[str, int] = {
    "asset_categories": 1,
    "technician_skills": 1,
    "default": 2,
}

# Canonical field → actual DB column name (per entity type)
_FIELD_TO_COLUMN: dict[str, dict[str, str]] = {
    "assets": {
        "asset_code":        "asset_code",
        "asset_name":        "asset_name",
        "make":              "manufacturer",
        "model":             "model_number",
        "serial":            "serial_number",
        "installation_date": "installation_date",
        "warranty_expiry":   "warranty_expiry",
        "asset_status":      "status",
        "health_score":      "health_score",
        "criticality":       "criticality",
        "qr_code":           "qr_code",
    },
    "spare_parts": {
        "part_code":              "part_code",
        "part_name":              "part_name",
        "stock_on_hand":          "stock_quantity",
        "minimum_allowed_stock":  "reorder_level",
        "unit_cost":              "unit_cost",
    },
    "work_orders": {
        "wo_code":          "external_wo_id",
        "wo_title":         "title",
        "wo_priority":      "priority",
        "wo_status":        "status",
        "wo_type":          "work_order_type",
        "wo_description":   "description",
        "fault_code":       "fault_code",
        "cause_code":       "cause_code",
        "resolution_code":  "resolution_code",
        "labor_minutes":    "labor_minutes",
        "travel_minutes":   "travel_minutes",
        "cost_parts":       "cost_parts_aed",
        "cost_vendor":      "cost_vendor_aed",
        "sla_breached":     "sla_breached",
        "sla_due_at":       "sla_due_at",
        "responded_at":     "responded_at",
        "completed_at":     "completed_at",
    },
}

# Tables where asyncpg COPY is used directly (all required non-null columns satisfiable)
_DIRECT_COPY_TABLES: frozenset[str] = frozenset({"assets", "spare_parts", "work_orders"})

# Required columns per table (non-null, no server default) that we must always supply
_REQUIRED_COLUMNS: dict[str, dict[str, Any]] = {
    "assets": {
        "asset_name": "Unnamed Asset",
    },
    "spare_parts": {
        "part_name": "Unnamed Part",
        "stock_quantity": 0,
        "reorder_level": 0,
    },
    "work_orders": {
        "title": "Imported Work Order",
        "priority": "medium",
        "status": "open",
    },
}


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _resolve_organization_id(engine: AsyncEngine, requested_id: UUID) -> UUID:
    """
    Return a valid organization_id that actually exists in the DB.

    Priority:
      1. Use `requested_id` if it exists in organizations table
      2. Fall back to the first org found in the table
      3. If table is empty, insert a minimal placeholder and return its id
    """
    async with engine.connect() as conn:
        # 1. Check if the requested id already exists
        row = (await conn.execute(
            text("SELECT id FROM plenum_cafm.organizations WHERE id = :id"),
            {"id": str(requested_id)},
        )).fetchone()
        if row:
            return requested_id

        # 2. Use any existing org
        row = (await conn.execute(
            text("SELECT id FROM plenum_cafm.organizations LIMIT 1"),
        )).fetchone()
        if row:
            found_id = row[0]
            return UUID(str(found_id))

        # 3. Insert a minimal default org so FK resolves
        placeholder_id = UUID("00000000-0000-0000-0000-000000000001")
        await conn.execute(
            text(
                "INSERT INTO plenum_cafm.organizations (id, name) "
                "VALUES (:id, 'Default Organization') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(placeholder_id)},
        )
        await conn.commit()
        logger.info("csv_agent.created_default_org", org_id=str(placeholder_id))
        return placeholder_id


def _detect_delimiter(sample: str) -> str:
    """Infer delimiter from first 2048 characters of file."""
    counts = {d: sample.count(d) for d in [",", ";", "\t", "|"]}
    return max(counts, key=lambda k: counts[k])


def _detect_entity_type(canonical_fields: set[str]) -> str:
    """Return entity type with highest signature overlap above minimum match threshold."""
    scores = {
        etype: len(sig & canonical_fields)
        for etype, sig in _ENTITY_SIGNATURES.items()
    }
    # Filter by minimum required matches to avoid spurious detection
    qualified = {
        etype: score for etype, score in scores.items()
        if score >= _MIN_SIGNATURE_MATCHES.get(etype, _MIN_SIGNATURE_MATCHES["default"])
    }
    if not qualified:
        return "unknown"
    return max(qualified, key=lambda k: qualified[k])


def _coerce_int(val: Any) -> int | None:
    """Safe int coercion — returns None on failure."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


def _coerce_str(val: Any) -> str | None:
    """Safe string coercion — returns None for NaN/empty."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s else None


def _build_asset_record(
    row: dict[str, Any],
    org_id: UUID,
    mapping: SchemaMapping,
) -> tuple[tuple[Any, ...], list[str]]:
    """
    Build an asyncpg COPY record tuple for the assets table.
    Returns (record_tuple, columns_list).
    """
    row_id = uuid.uuid4()
    # Start with required columns
    record: dict[str, Any] = {
        "id": row_id,
        "organization_id": org_id,
        "asset_name": _coerce_str(row.get("asset_name")) or "Unnamed Asset",
    }
    # Optional mapped columns
    optional = {
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
    """Build asyncpg COPY record for spare_parts table."""
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
    """Build asyncpg COPY record for work_orders table."""
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


# ── asyncpg COPY writer ────────────────────────────────────────────────────────


async def _copy_batch_to_table(
    engine: AsyncEngine,
    table_name: str,
    records: list[tuple[Any, ...]],
    columns: list[str],
) -> None:
    """
    Write one batch of records to a plenum_cafm table using asyncpg COPY.

    Uses SQLAlchemy 2.0 async engine to retrieve the raw asyncpg connection.
    All records in the batch must have the same columns (same order).
    """
    async with engine.connect() as sa_conn:
        raw = await sa_conn.get_raw_connection()
        asyncpg_conn = raw.driver_connection  # the underlying asyncpg Connection

        await asyncpg_conn.copy_records_to_table(
            table_name,
            records=records,
            columns=columns,
            schema_name=_SCHEMA,
        )


# ── Entity builders (for IntermediateSchema) ───────────────────────────────────


def _row_to_asset_entity(row: dict[str, Any]) -> AssetEntity | None:
    """Convert a canonical-keyed row to an AssetEntity. Returns None if unusable."""
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
        extra={k: v for k, v in row.items()
               if k not in {"asset_code", "asset_name", "serial", "category", "make", "model"}
               and v is not None},
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
        extra={k: v for k, v in row.items()
               if k not in {"part_code", "stock_on_hand", "minimum_allowed_stock", "supplier"}
               and v is not None},
    )


def _row_to_work_order_entity(row: dict[str, Any]) -> WorkOrderEntity | None:
    wo_code = _coerce_str(row.get("wo_code"))
    title   = _coerce_str(row.get("wo_title")) or _coerce_str(row.get("wo_code"))
    if not wo_code and not title:
        return None
    return WorkOrderEntity(
        work_order_number=wo_code,
        title=title,
        description=_coerce_str(row.get("wo_description")),
        priority=_coerce_str(row.get("wo_priority")),
        status=_coerce_str(row.get("wo_status")),
        extra={k: v for k, v in row.items()
               if k not in {"wo_code", "wo_title", "wo_priority", "wo_status",
                            "wo_type", "wo_description"}
               and v is not None},
    )


def _row_to_technician_entity(row: dict[str, Any]) -> TechnicianEntity | None:
    """Convert a canonical-keyed row to a TechnicianEntity."""
    tech_code = _coerce_str(row.get("technician_code"))
    tech_name = _coerce_str(row.get("technician_name")) or _coerce_str(row.get("user_full_name"))
    tech_email = _coerce_str(row.get("user_email"))
    if not tech_code and not tech_name and not tech_email:
        return None
    return TechnicianEntity(
        employee_id=tech_code,
        name=tech_name,
        email=tech_email,
        specialisation=_coerce_str(row.get("skill_name")),
        extra={k: v for k, v in row.items()
               if k not in {"technician_code", "technician_name", "user_full_name",
                            "user_email", "skill_name"}
               and v is not None},
    )


def _row_to_vendor_entity(row: dict[str, Any]) -> VendorEntity | None:
    """Convert a canonical-keyed row to a VendorEntity."""
    name = _coerce_str(row.get("vendor_name"))
    code = _coerce_str(row.get("vendor_code"))
    if not name and not code:
        return None
    return VendorEntity(
        vendor_code=code,
        name=name,
        address=_coerce_str(row.get("vendor_address")),
        contact_name=_coerce_str(row.get("vendor_contact")),
        email=_coerce_str(row.get("vendor_email")),
        phone=_coerce_str(row.get("vendor_phone")),
        extra={k: v for k, v in row.items()
               if k not in {"vendor_name", "vendor_code", "vendor_address",
                            "vendor_contact", "vendor_email", "vendor_phone"}
               and v is not None},
    )


def _row_to_reading_entity(row: dict[str, Any]) -> ReadingEntity | None:
    """Convert a canonical-keyed row to a ReadingEntity."""
    reading_type = _coerce_str(row.get("reading_type"))
    raw_val = row.get("reading_value") or row.get("value")
    if not reading_type and raw_val is None:
        return None
    value: float | None = None
    if raw_val is not None:
        try:
            value = float(str(raw_val).strip())
        except (ValueError, TypeError):
            pass
    return ReadingEntity(
        reading_type=reading_type,
        value=value,
        unit=_coerce_str(row.get("reading_unit") or row.get("unit")),
        reading_date=_coerce_str(row.get("recorded_at")),
        extra={k: v for k, v in row.items()
               if k not in {"reading_type", "reading_value", "value",
                            "reading_unit", "unit", "recorded_at"}
               and v is not None},
    )


def _row_to_finding_entity(row: dict[str, Any]) -> FindingEntity | None:
    """Convert a canonical-keyed row for inspection findings."""
    finding_type = _coerce_str(row.get("finding_type")) or _coerce_str(row.get("inspection_type"))
    notes = _coerce_str(row.get("inspection_notes")) or _coerce_str(row.get("risk_level"))
    if not finding_type and not notes:
        return None
    return FindingEntity(
        finding_id=_coerce_str(row.get("inspection_id")),
        severity=_coerce_str(row.get("risk_level")),
        description=notes,
        extra={k: v for k, v in row.items()
               if k not in {"finding_type", "inspection_type", "inspection_notes",
                            "risk_level", "inspection_id"}
               and v is not None},
    )


# ── Public API ─────────────────────────────────────────────────────────────────


async def extract_csv(
    csv_bytes: bytes,
    *,
    source_filename: str,
    ingestion_id: UUID,
    blob_url: str,
    organization_id: UUID,
    redis: Any,
    client: anthropic.AsyncAnthropic,
    engine: AsyncEngine,
    dry_run: bool = False,
    _meta_out: dict[str, Any] | None = None,
) -> IntermediateSchema:
    """
    _meta_out: if provided, will be populated with:
      - unmatched_columns: list[str]  — columns that didn't map to any canonical field
      - entity_type: str              — detected entity type
      - target_table: str            — the plenum_cafm table name for this file
    """
    """
    Stage 2 extraction for CSV/TSV files.

    Args:
        csv_bytes:       Raw file bytes (already validated by Stage 1).
        source_filename: Original filename — used for logging and audit.
        ingestion_id:    UUID from ingestion_documents (created by Stage 1).
        blob_url:        Azure Blob URL of the stored original.
        organization_id: Tenant/org UUID — stamped on every DB row.
        redis:           Async Redis client (passed to schema mapper for caching).
        client:          Async Anthropic client (Haiku — schema mapping only).
        engine:          Async SQLAlchemy engine (used to get raw asyncpg conn).

    Returns:
        IntermediateSchema — summary of what was ingested + full entity list
        for any rows that need FK resolution (handled by Stage 4 / unifier).
    """
    t0 = time.monotonic()

    with tracer.start_as_current_span("ingestion.stage2.extract") as span:
        span.set_attribute("cafm.ingestion_id", str(ingestion_id))
        span.set_attribute("cafm.agent_id", AgentId.CSV.value)
        span.set_attribute("cafm.source_type", SourceType.CSV.value)
        span.set_attribute("cafm.extraction_method", ExtractionMethod.PANDAS_CLAUDE.value)
        span.set_attribute("cafm.file_size_bytes", len(csv_bytes))
        span.set_attribute("cafm.source_filename", source_filename)

        try:
            # ── 1. Detect delimiter + read with pandas ────────────────────
            sample = csv_bytes[:2048].decode(_ENCODING, errors="replace")
            delimiter = _detect_delimiter(sample)

            df = pd.read_csv(
                io.BytesIO(csv_bytes),
                encoding=_ENCODING,
                sep=delimiter,
                dtype=str,          # keep everything as string; coerce later
                keep_default_na=False,
                na_values=["", "NULL", "null", "N/A", "n/a", "NA"],
            )
            df = df.dropna(how="all")  # drop fully empty rows

            # Deduplicate column names (some exports repeat column headers)
            if df.columns.duplicated().any():
                seen: dict[str, int] = {}
                new_cols = []
                for col in df.columns:
                    if col in seen:
                        seen[col] += 1
                        new_cols.append(f"{col}_{seen[col]}")
                    else:
                        seen[col] = 0
                        new_cols.append(col)
                df.columns = pd.Index(new_cols)

            total_rows = len(df)
            raw_headers = list(df.columns)

            span.set_attribute("cafm.raw_row_count", total_rows)
            span.set_attribute("cafm.raw_column_count", len(raw_headers))

            logger.info(
                "csv_agent.file_read",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                rows=total_rows,
                columns=len(raw_headers),
                delimiter=repr(delimiter),
            )

            # ── 2. Schema mapper — called ONCE per file ───────────────────
            # DB-direct bypass: if the file is already a DB export, skip
            # Claude schema mapping entirely and build the mapping from
            # known DB column names (confidence = 1.0, no EL-3.0 gate).
            db_entity_type = (
                _entity_type_from_filename(source_filename)
                if _is_db_format(raw_headers)
                else None
            )

            if db_entity_type is not None:
                mapping: SchemaMapping = _build_db_direct_mapping(db_entity_type, raw_headers)
                span.set_attribute("cafm.db_direct_bypass", True)
                span.set_attribute("cafm.db_entity_type", db_entity_type)
                logger.info(
                    "csv_agent.db_direct_bypass",
                    ingestion_id=str(ingestion_id),
                    source_filename=source_filename,
                    db_entity_type=db_entity_type,
                    mapped=len(mapping.mapped),
                    unmatched=len(mapping.unmatched),
                )
            else:
                span.set_attribute("cafm.db_direct_bypass", False)
                sample_rows = df.head(_SAMPLE_ROWS).to_dict(orient="records")
                mapping = await map_headers(
                    raw_headers,
                    redis=redis,
                    client=client,
                    sample_rows=sample_rows,
                )

            span.set_attribute("cafm.schema_mapped_count", len(mapping.mapped))
            span.set_attribute("cafm.schema_unmatched_count", len(mapping.unmatched))
            span.set_attribute("cafm.schema_cache_hit", mapping.cached)
            span.set_attribute("cafm.schema_requires_review", mapping.requires_human_review)

            # If mapping confidence is too low, return immediately with LOW confidence
            if mapping.requires_human_review:
                logger.warning(
                    "csv_agent.low_confidence_mapping",
                    ingestion_id=str(ingestion_id),
                    overall_confidence=mapping.overall_confidence,
                    unmatched=mapping.unmatched,
                )
                return IntermediateSchema(
                    ingestion_id=ingestion_id,
                    source_type=SourceType.CSV,
                    agent_id=AgentId.CSV,
                    source_filename=source_filename,
                    source_blob_url=blob_url,
                    extraction_method=ExtractionMethod.PANDAS_CLAUDE,
                    model_used=ModelUsed.HAIKU,
                    entities=EntitiesBlock(),
                    confidence=ConfidenceResult(
                        overall=ConfidenceLevel.LOW,
                        eval_score=mapping.overall_confidence,
                        rules_passed=False,
                        rules_violations=["schema_mapping_confidence_below_threshold"],
                    ),
                    audit=AuditInfo(
                        processing_ms=round((time.monotonic() - t0) * 1000),
                    ),
                )

            # ── 3. Rename columns to canonical names ──────────────────────
            rename_map = {raw: canon for raw, canon in mapping.mapped.items()}
            df = df.rename(columns=rename_map)
            canonical_cols = set(df.columns) & set(mapping.mapped.values())

            # ── 4. Detect entity type ─────────────────────────────────────
            # DB-direct files already know their entity type; others use signature.
            entity_type = db_entity_type or _detect_entity_type(canonical_cols)
            span.set_attribute("cafm.entity_type", entity_type)

            # Populate meta-out for callers that need unmatched columns + table name
            if _meta_out is not None:
                _meta_out["unmatched_columns"] = list(mapping.unmatched)
                _meta_out["entity_type"] = entity_type
                _meta_out["target_table"] = entity_type  # same name as plenum_cafm table
            logger.info(
                "csv_agent.entity_type_detected",
                ingestion_id=str(ingestion_id),
                entity_type=entity_type,
                canonical_cols=sorted(canonical_cols),
            )

            # ── 5. Resolve organization_id → verified FK ─────────────────
            resolved_org_id = await _resolve_organization_id(engine, organization_id)
            if resolved_org_id != organization_id:
                logger.info(
                    "csv_agent.org_id_resolved",
                    ingestion_id=str(ingestion_id),
                    requested=str(organization_id),
                    resolved=str(resolved_org_id),
                )

            # ── 6. Stream in batches ──────────────────────────────────────
            rows_written = 0
            rows_failed = 0
            batches_written = 0

            asset_entities: list[AssetEntity] = []
            part_entities: list[SparePartEntity] = []
            wo_entities: list[WorkOrderEntity] = []
            technician_entities: list[TechnicianEntity] = []
            vendor_entities: list[VendorEntity] = []
            reading_entities: list[ReadingEntity] = []
            finding_entities: list[FindingEntity] = []

            # Cap in-memory entity preview to avoid OOM on large files.
            # The full data is still written to DB via asyncpg COPY.
            _ENTITY_PREVIEW_LIMIT = 500

            builder = _RECORD_BUILDERS.get(entity_type)
            use_direct_copy = entity_type in _DIRECT_COPY_TABLES and builder is not None

            rows_as_dicts = df.to_dict(orient="records")

            # Debug: log first row structure for non-direct-copy tables
            if not use_direct_copy and total_rows > 0 and rows_as_dicts:
                first_row = rows_as_dicts[0]
                logger.info(
                    "csv_agent.debug_first_row_keys",
                    ingestion_id=str(ingestion_id),
                    entity_type=entity_type,
                    first_row_keys=list(first_row.keys()),
                    first_row_sample={k: str(v)[:50] for k, v in list(first_row.items())[:5]},
                )

            for batch_start in range(0, total_rows, _BATCH_SIZE):
                batch = rows_as_dicts[batch_start: batch_start + _BATCH_SIZE]
                batch_records: list[tuple[Any, ...]] = []
                batch_columns: list[str] | None = None

                for row_idx, row in enumerate(batch):
                    # Convert NaN floats to None
                    clean_row: dict[str, Any] = {
                        k: (None if isinstance(v, float) and pd.isna(v) else v)
                        for k, v in row.items()
                    }

                    # Build entity for IntermediateSchema (preview capped at _ENTITY_PREVIEW_LIMIT).
                    # Each branch checks entity_type first so rows beyond the cap are
                    # skipped cleanly — never misrouted to another entity type.
                    entity_built = False
                    if entity_type == "assets":
                        if len(asset_entities) < _ENTITY_PREVIEW_LIMIT:
                            ent = _row_to_asset_entity(clean_row)
                            if ent:
                                asset_entities.append(ent)
                                entity_built = True
                    elif entity_type == "spare_parts":
                        if len(part_entities) < _ENTITY_PREVIEW_LIMIT:
                            ent = _row_to_spare_part_entity(clean_row)
                            if ent:
                                part_entities.append(ent)
                                entity_built = True
                    elif entity_type == "work_orders":
                        if len(wo_entities) < _ENTITY_PREVIEW_LIMIT:
                            ent = _row_to_work_order_entity(clean_row)
                            if ent:
                                wo_entities.append(ent)
                                entity_built = True
                    elif entity_type in {"technicians", "technician_skills"}:
                        if len(technician_entities) < _ENTITY_PREVIEW_LIMIT:
                            ent = _row_to_technician_entity(clean_row)
                            if ent:
                                technician_entities.append(ent)
                                entity_built = True
                            elif batch_start == 0 and row_idx < 3:
                                # Debug: log first few rows that failed to build
                                logger.debug(
                                    "csv_agent.technician_entity_build_failed",
                                    ingestion_id=str(ingestion_id),
                                    row_idx=row_idx,
                                    row_keys=list(clean_row.keys()),
                                    technician_code=clean_row.get("technician_code"),
                                    technician_name=clean_row.get("technician_name"),
                                    user_email=clean_row.get("user_email"),
                                )
                    elif entity_type == "vendors":
                        if len(vendor_entities) < _ENTITY_PREVIEW_LIMIT:
                            ent = _row_to_vendor_entity(clean_row)
                            if ent:
                                vendor_entities.append(ent)
                                entity_built = True
                    elif entity_type == "asset_readings":
                        if len(reading_entities) < _ENTITY_PREVIEW_LIMIT:
                            ent = _row_to_reading_entity(clean_row)
                            if ent:
                                reading_entities.append(ent)
                                entity_built = True
                    elif entity_type == "inspections":
                        if len(finding_entities) < _ENTITY_PREVIEW_LIMIT:
                            ent = _row_to_finding_entity(clean_row)
                            if ent:
                                finding_entities.append(ent)
                                entity_built = True
                    # else: entity types with no dedicated builder (locations,
                    # maintenance_plans, users, sla_policies, asset_categories,
                    # technician_utilization, organizations) produce no in-memory
                    # entities — they are not written via asyncpg COPY either.
                    # A dedicated direct-copy path per table is the correct approach.

                    # Build COPY record if direct-write table
                    if use_direct_copy and builder is not None:
                        try:
                            record, cols = builder(clean_row, resolved_org_id, mapping)
                            if batch_columns is None:
                                batch_columns = cols
                            if list(record.__class__.__mro__) or True:  # always true
                                # Ensure all records in batch have same columns
                                if len(record) == len(batch_columns):
                                    batch_records.append(record)
                        except Exception as row_exc:
                            rows_failed += 1
                            logger.debug(
                                "csv_agent.row_skipped",
                                ingestion_id=str(ingestion_id),
                                error=str(row_exc),
                            )

                # Write batch via asyncpg COPY (skipped in dry_run mode)
                if use_direct_copy and batch_records and batch_columns and not dry_run:
                    try:
                        await _copy_batch_to_table(
                            engine, entity_type, batch_records, batch_columns
                        )
                        rows_written += len(batch_records)
                        batches_written += 1
                        logger.debug(
                            "csv_agent.batch_written",
                            ingestion_id=str(ingestion_id),
                            batch_num=batches_written,
                            rows=len(batch_records),
                            table=entity_type,
                        )
                    except Exception as copy_exc:
                        rows_failed += len(batch_records)
                        logger.error(
                            "csv_agent.copy_failed",
                            ingestion_id=str(ingestion_id),
                            batch_num=batches_written + 1,
                            table=entity_type,
                            error=str(copy_exc),
                        )

            processing_ms = round((time.monotonic() - t0) * 1000)
            total_entities = (
                len(asset_entities) + len(part_entities) + len(wo_entities)
                + len(technician_entities) + len(vendor_entities)
                + len(reading_entities) + len(finding_entities)
            )

            span.set_attribute("cafm.rows_written", rows_written)
            span.set_attribute("cafm.rows_failed", rows_failed)
            span.set_attribute("cafm.entity_count", total_entities)
            span.set_attribute("cafm.confidence_overall", ConfidenceLevel.HIGH.value)
            span.set_status(StatusCode.OK)

            logger.info(
                "csv_agent.extraction_complete",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                entity_type=entity_type,
                total_rows=total_rows,
                total_entities=total_entities,
                rows_written=rows_written,
                rows_failed=rows_failed,
                direct_copy=use_direct_copy,
                processing_ms=processing_ms,
            )

            # ── 7. Build IntermediateSchema ───────────────────────────────
            # Confidence: HIGH if no failures; MEDIUM if some rows failed
            if rows_failed == 0:
                overall_conf = ConfidenceLevel.HIGH
                eval_score = 0.95
            elif rows_failed < total_rows * 0.1:
                overall_conf = ConfidenceLevel.MEDIUM
                eval_score = 0.80
            else:
                overall_conf = ConfidenceLevel.LOW
                eval_score = 0.60

            entities = EntitiesBlock(
                assets=asset_entities,
                spare_parts=part_entities,
                work_orders=wo_entities,
                technicians=technician_entities,
                vendors=vendor_entities,
                readings=reading_entities,
                findings=finding_entities,
            )

            return IntermediateSchema(
                ingestion_id=ingestion_id,
                source_type=SourceType.CSV,
                agent_id=AgentId.CSV,
                source_filename=source_filename,
                source_blob_url=blob_url,
                extraction_method=ExtractionMethod.PANDAS_CLAUDE,
                model_used=ModelUsed.HAIKU,  # Haiku used for schema mapping
                entities=entities,
                confidence=ConfidenceResult(
                    overall=overall_conf,
                    eval_score=eval_score,
                    rules_passed=rows_failed == 0,
                    rules_violations=(
                        [f"{rows_failed} rows failed during COPY"]
                        if rows_failed > 0 else []
                    ),
                ),
                audit=AuditInfo(
                    processing_ms=processing_ms,
                ),
            )

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            logger.error(
                "csv_agent.extraction_failed",
                ingestion_id=str(ingestion_id),
                source_filename=source_filename,
                error=str(exc),
            )
            raise
