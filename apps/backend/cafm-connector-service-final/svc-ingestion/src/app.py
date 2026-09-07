"""
svc-ingestion/src/app.py

CAFM Ingestion Service — FastAPI application.

Routes:
  GET  /health          — liveness probe
  GET  /metrics         — Prometheus scrape
  POST /ingest/pdf      — Upload PDF → Claude Vision extraction → extracted fields
  POST /ingest/word     — Upload DOCX → Claude Sonnet extraction → extracted fields
  POST /ingest/csv      — Upload CSV → schema mapper → row extraction
  POST /ingest/excel    — Upload XLSX → schema mapper → row extraction
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import anthropic
import redis.asyncio as aioredis
import re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from cafm_shared.logging import configure_logging, get_logger
from cafm_shared.telemetry import configure_telemetry
from config import get_settings
from shared.db import _engine

logger = get_logger(__name__)


class _SuppressMetricsHealth(logging.Filter):
    """Drop uvicorn access-log lines for /metrics and /health (Prometheus noise)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/metrics" not in msg and "/health" not in msg


class ColumnDef(BaseModel):
    column_name: str
    column_type: str  # one of: text, integer, decimal, boolean, date, timestamp


class SchemaExtendRequest(BaseModel):
    table_name: str
    columns: list[ColumnDef]


def _coerce_assets_for_validation(entities: dict[str, Any]) -> None:
    """
    Ensure asset rows satisfy AssetEntity minimum identifier requirement.
    """
    records = entities.get("assets")
    if not isinstance(records, list):
        return

    fallback_keys = (
        "asset_code",
        "serial_number",
        "name",
        "asset_name",
        "asset_id",
        "code",
        "title",
        "id",
    )

    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("asset_code") or rec.get("serial_number") or rec.get("name"):
            continue
        for key in fallback_keys:
            val = rec.get(key)
            if val is not None and str(val).strip():
                rec["name"] = str(val).strip()
                break


def _normalize_intermediate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize external IntermediateSchema payloads to svc-ingestion enums/shape.
    """
    normalized = dict(payload)

    source_type = str(normalized.get("source_type") or "csv").strip().lower()
    if source_type in {"xlsx", "xls", "xlsm"}:
        source_type = "excel"
    normalized["source_type"] = source_type

    agent_id = str(normalized.get("agent_id") or "").strip().lower()
    if agent_id in {"schema-mapper", "schema_mapper", "schema mapper", "csv"}:
        # schema-mapper handoffs are tabular; align agent with source_type enum pairing
        if source_type == "excel":
            agent_id = "excel-agent"
        elif source_type == "csv":
            agent_id = "csv-agent"
        elif source_type == "word":
            agent_id = "word-agent"
        elif source_type == "pdf":
            agent_id = "pdf-agent"
        else:
            agent_id = "csv-agent"
    elif agent_id and not agent_id.endswith("-agent"):
        agent_id = f"{agent_id}-agent"
    normalized["agent_id"] = agent_id or ("excel-agent" if source_type == "excel" else "csv-agent")

    extraction_method = str(normalized.get("extraction_method") or "").strip().lower()
    if extraction_method in {"ai-schema-mapper", "schema-mapper", "schema_mapper"}:
        extraction_method = "none"
    normalized["extraction_method"] = extraction_method or "none"

    model_used = str(normalized.get("model_used") or "").strip().lower()
    if model_used in {"claude-haiku", "haiku"}:
        model_used = "claude-haiku-4-5"
    elif model_used in {"schema-mapper", "schema_mapper"}:
        model_used = "none"
    normalized["model_used"] = model_used or "none"

    entities = normalized.get("entities")
    if isinstance(entities, dict):
        _coerce_assets_for_validation(entities)

    return normalized


def _resolve_org_uuid(
    normalized_payload: dict[str, Any],
    organization_id: str | None,
) -> uuid.UUID:
    """
    Prefer explicit organization_id, then payload/row hints, then default tenant.
    """
    candidates: list[str] = []
    if organization_id:
        candidates.append(organization_id)

    top_level_org = normalized_payload.get("organization_id")
    if top_level_org:
        candidates.append(str(top_level_org))

    entities = normalized_payload.get("entities")
    if isinstance(entities, dict):
        for records in entities.values():
            if not isinstance(records, list):
                continue
            for rec in records:
                if isinstance(rec, dict) and rec.get("organization_id"):
                    candidates.append(str(rec["organization_id"]))
                    break
            if candidates:
                break

    for candidate in candidates:
        try:
            return uuid.UUID(candidate)
        except Exception:
            continue

    return uuid.UUID("00000000-0000-0000-0000-000000000001")


# Apply filter to uvicorn access logger so Prometheus scrapes don't flood stdout
logging.getLogger("uvicorn.access").addFilter(_SuppressMetricsHealth())

# ── App-level singletons (created once at startup) ────────────────────────────

_anthropic_client: anthropic.AsyncAnthropic | None = None
_redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _anthropic_client, _redis_client
    settings = get_settings()

    configure_telemetry(service_name="cafm-ingestion-service", app=app)
    configure_logging(debug=settings.debug)

    # Initialise Claude client
    _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Initialise Redis client
    _redis_client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
        socket_connect_timeout=5,
    )

    logger.info(
        "cafm_ingestion_service_started",
        env=settings.environment,
        port=settings.port,
        claude_key_set=bool(settings.anthropic_api_key),
    )

    yield

    await _redis_client.aclose()
    logger.info("cafm_ingestion_service_stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CAFM Ingestion Service",
        description=(
            "Sprint 2 — Multi-agent document ingestion. "
            "Upload PDF, DOCX, CSV, or Excel and get back structured CAFM entities."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health + Metrics ──────────────────────────────────────────────────────

    @app.get("/health", tags=["Health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "cafm-ingestion-service"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/api/ingest", tags=["Ingestion"], summary="Accept IntermediateSchema handoff")
    async def ingest_intermediate_schema(
        payload: dict[str, Any],
        organization_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Backward-compatible endpoint used by svc-ai-schema-mapper Node 9.
        """
        from shared.db import get_session_factory
        from shared.intermediate_schema import IntermediateSchema
        from shared.unifier import unify

        normalized_payload = _normalize_intermediate_payload(payload)

        try:
            schema = IntermediateSchema.model_validate(normalized_payload)
        except Exception as exc:
            raise HTTPException(422, f"Invalid IntermediateSchema payload: {exc}") from exc

        org_uuid = _resolve_org_uuid(normalized_payload, organization_id)

        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                result = await unify(
                    schema=schema,
                    organization_id=org_uuid,
                    db=session,
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                raise HTTPException(500, f"Failed to persist handoff payload: {exc}") from exc

        return {
            "status": "accepted",
            "ingestion_id": str(schema.ingestion_id),
            "organization_id": str(org_uuid),
            "entities_written": result.entities_written,
            "assets_written": result.assets_written,
            "work_orders_written": result.work_orders_written,
            "readings_written": result.readings_written,
            "vendors_written": result.vendors_written,
            "spare_parts_written": result.spare_parts_written,
            "documents_written": result.documents_written,
            "unresolved_count": result.unresolved_count,
        }

    # ── PDF Ingestion ─────────────────────────────────────────────────────────

    @app.post("/ingest/pdf", tags=["Ingestion"], summary="Extract fields from a PDF document")
    async def ingest_pdf(
        file: UploadFile = File(..., description="PDF file (max 32MB, max 100 pages)"),
        force_multipass: bool = Form(False, description="Force 3-pass voting (compliance certs)"),
    ) -> dict:
        """
        Upload a PDF and get back every CAFM entity Claude extracted from it.

        Runs the full EL-2.1 → EL-2.2 → EL-2.3 eval pipeline.
        Response includes: entities, confidence scores, eval_score, route decision, cost.
        """
        if _anthropic_client is None:
            raise HTTPException(503, "Anthropic client not initialised")

        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "File must be a .pdf")

        pdf_bytes = await file.read()
        if len(pdf_bytes) > 32 * 1024 * 1024:
            raise HTTPException(413, "PDF exceeds 32MB limit")

        ingestion_id = uuid.uuid4()

        try:
            from agents.pdf_agent import extract_pdf
            schema = await extract_pdf(
                pdf_bytes=pdf_bytes,
                source_filename=file.filename,
                ingestion_id=ingestion_id,
                blob_url=f"local://{file.filename}",
                client=_anthropic_client,
                force_multipass=force_multipass,
            )
        except Exception as exc:
            logger.error("pdf_ingestion_failed", filename=file.filename, error=str(exc))
            raise HTTPException(500, f"Extraction failed: {exc}") from exc

        return _schema_to_response(schema, file.filename)

    # ── DOCX Ingestion ────────────────────────────────────────────────────────

    @app.post("/ingest/word", tags=["Ingestion"], summary="Extract fields from a Word document")
    async def ingest_word(
        file: UploadFile = File(..., description="DOCX file"),
        dry_run: bool = Form(False, description="Preview only — skip DB write"),
    ) -> dict:
        """
        Upload a .docx file (inspection report, SOP, or any CAFM document)
        and get back every structured field Claude extracted.

        Runs EL-2.1 → EL-2.2 → EL-2.3. Inspection reports are also written
        to the inspections table in plenum_cafm (unless dry_run=true).
        """
        if _anthropic_client is None:
            raise HTTPException(503, "Anthropic client not initialised")

        filename = file.filename or "document.docx"
        if not filename.lower().endswith((".docx", ".doc")):
            raise HTTPException(400, "File must be a .docx")

        docx_bytes = await file.read()
        ingestion_id = uuid.uuid4()

        try:
            from agents.word_agent import extract_docx
            schema = await extract_docx(
                docx_bytes=docx_bytes,
                source_filename=filename,
                ingestion_id=ingestion_id,
                blob_url=f"local://{filename}",
                client=_anthropic_client,
                engine=_engine(),
                dry_run=dry_run,
            )
        except Exception as exc:
            logger.error("docx_ingestion_failed", filename=filename, error=str(exc))
            raise HTTPException(500, f"Extraction failed: {exc}") from exc

        resp = _schema_to_response(schema, filename)
        resp["dry_run"] = dry_run
        return resp

    # ── CSV Ingestion ─────────────────────────────────────────────────────────

    @app.post("/ingest/csv", tags=["Ingestion"], summary="Ingest a CSV file into the unified store")
    async def ingest_csv(
        file: UploadFile = File(..., description="CSV or TSV file (latin-1 encoding)"),
        organization_id: str = Form(
            default="00000000-0000-0000-0000-000000000001",
            description="Organisation UUID to stamp on ingested rows",
        ),
        dry_run: bool = Form(False, description="Preview only — skip DB write"),
    ) -> dict:
        """
        Upload a CSV/TSV file. Claude Haiku maps column names → canonical CAFM fields
        (once per file via schema mapper), then rows are bulk-loaded into plenum_cafm
        (unless dry_run=true, which returns a preview without writing).

        Returns: mapping used, row counts, entity summary, cost.
        """
        if _anthropic_client is None:
            raise HTTPException(503, "Anthropic client not initialised")
        if _redis_client is None:
            raise HTTPException(503, "Redis client not initialised")

        filename = file.filename or "data.csv"
        csv_bytes = await file.read()
        ingestion_id = uuid.uuid4()

        try:
            org_uuid = uuid.UUID(organization_id)
        except ValueError:
            raise HTTPException(400, f"Invalid organization_id UUID: {organization_id}")

        meta: dict = {}
        try:
            from agents.csv_agent import extract_csv
            schema = await extract_csv(
                csv_bytes=csv_bytes,
                source_filename=filename,
                ingestion_id=ingestion_id,
                blob_url=f"local://{filename}",
                organization_id=org_uuid,
                redis=_redis_client,
                client=_anthropic_client,
                engine=_engine(),
                dry_run=dry_run,
                _meta_out=meta,
            )
        except Exception as exc:
            logger.error("csv_ingestion_failed", filename=filename, error=str(exc))
            raise HTTPException(500, f"Extraction failed: {exc}") from exc

        resp = _schema_to_response(schema, filename)
        resp["dry_run"] = dry_run
        resp["unmatched_columns"] = meta.get("unmatched_columns", [])
        resp["entity_type"] = meta.get("entity_type", "unknown")
        resp["target_table"] = meta.get("target_table", "unknown")
        return resp

    # ── Excel Ingestion ───────────────────────────────────────────────────────

    @app.post("/ingest/excel", tags=["Ingestion"], summary="Ingest an Excel file into the unified store")
    async def ingest_excel(
        file: UploadFile = File(..., description="XLSX, XLS, or XLSM file"),
        organization_id: str = Form(
            default="00000000-0000-0000-0000-000000000001",
            description="Organisation UUID",
        ),
        dry_run: bool = Form(False, description="Preview only — skip DB write"),
    ) -> dict:
        """
        Upload an Excel file. Schema mapper runs once, then rows bulk-loaded
        into plenum_cafm (unless dry_run=true). Returns extracted entities + mapping + cost.
        """
        if _anthropic_client is None:
            raise HTTPException(503, "Anthropic client not initialised")
        if _redis_client is None:
            raise HTTPException(503, "Redis client not initialised")

        filename = file.filename or "data.xlsx"
        if not filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
            raise HTTPException(400, "File must be .xlsx, .xls, or .xlsm")

        excel_bytes = await file.read()
        ingestion_id = uuid.uuid4()

        try:
            org_uuid = uuid.UUID(organization_id)
        except ValueError:
            raise HTTPException(400, f"Invalid organization_id UUID: {organization_id}")

        try:
            from agents.excel_agent import extract_excel
            schema = await extract_excel(
                excel_bytes=excel_bytes,
                source_filename=filename,
                ingestion_id=ingestion_id,
                blob_url=f"local://{filename}",
                organization_id=org_uuid,
                redis=_redis_client,
                client=_anthropic_client,
                engine=_engine(),
                dry_run=dry_run,
            )
        except Exception as exc:
            logger.error("excel_ingestion_failed", filename=filename, error=str(exc))
            raise HTTPException(500, f"Extraction failed: {exc}") from exc

        resp = _schema_to_response(schema, filename)
        resp["dry_run"] = dry_run
        return resp

    # ── Schema Extension ──────────────────────────────────────────────────────

    # Tables the user is allowed to extend (whitelist — prevents arbitrary ALTER)
    _EXTENDABLE_TABLES: frozenset[str] = frozenset({
        "assets", "spare_parts", "work_orders", "maintenance_plans",
        "technicians", "vendors", "locations", "users",
        "inspections", "asset_readings", "organizations", "sla_policies",
        "technician_skills", "technician_utilization", "asset_categories",
    })

    # Allowed column types (SQL-safe subset)
    _ALLOWED_TYPES: dict[str, str] = {
        "text":    "TEXT",
        "integer": "INTEGER",
        "decimal": "NUMERIC",
        "boolean": "BOOLEAN",
        "date":    "DATE",
        "timestamp": "TIMESTAMPTZ",
    }

    _SAFE_COL = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

    @app.post("/schema/extend", tags=["Schema"], summary="Add new columns to a plenum_cafm table")
    async def schema_extend(req: SchemaExtendRequest) -> dict:
        """
        Add one or more columns to an existing plenum_cafm table.

        - table_name must be in the allowed list
        - column_name must be lowercase alphanumeric + underscores
        - column_type must be one of: text, integer, decimal, boolean, date, timestamp
        - Uses ADD COLUMN IF NOT EXISTS — safe to call repeatedly
        """
        from sqlalchemy import text as sa_text

        if req.table_name not in _EXTENDABLE_TABLES:
            raise HTTPException(400, f"Table '{req.table_name}' is not extendable. "
                                f"Allowed: {sorted(_EXTENDABLE_TABLES)}")

        added: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        engine = _engine()
        async with engine.begin() as conn:
            for col in req.columns:
                col_name = col.column_name.lower().strip()
                col_type = _ALLOWED_TYPES.get(col.column_type.lower())

                if not _SAFE_COL.match(col_name):
                    errors.append(f"{col.column_name}: invalid name (use lowercase a-z, 0-9, _)")
                    continue
                if col_type is None:
                    errors.append(f"{col.column_name}: unknown type '{col.column_type}'. "
                                  f"Use one of: {list(_ALLOWED_TYPES.keys())}")
                    continue

                sql = (
                    f"ALTER TABLE plenum_cafm.{req.table_name} "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                )
                try:
                    await conn.execute(sa_text(sql))
                    added.append(col_name)
                    logger.info(
                        "schema.column_added",
                        table=req.table_name,
                        column=col_name,
                        col_type=col_type,
                    )
                except Exception as exc:
                    errors.append(f"{col_name}: {exc}")

        return {
            "table": req.table_name,
            "added": added,
            "skipped": skipped,
            "errors": errors,
        }

    return app


# ── Response helper ───────────────────────────────────────────────────────────

def _schema_to_response(schema, filename: str) -> dict:
    """
    Convert IntermediateSchema → clean JSON response showing extracted fields.
    This is what you read to validate extraction quality.
    """
    entities = schema.entities

    # Count non-empty entity lists
    entity_counts = {
        k: len(v)
        for k, v in {
            "assets": entities.assets,
            "work_orders": entities.work_orders,
            "findings": entities.findings,
            "readings": entities.readings,
            "technicians": entities.technicians,
            "vendors": entities.vendors,
            "certificates": entities.certificates,
            "spare_parts": entities.spare_parts,
        }.items()
        if v
    }

    confidence = schema.confidence

    return {
        "ingestion_id": str(schema.ingestion_id),
        "source_filename": filename,
        "agent_id": schema.agent_id,
        "extraction_method": schema.extraction_method,
        "model_used": schema.model_used,

        # ── What was extracted ──────────────────────────────────────────────
        "entity_counts": entity_counts,
        "entities": {
            "assets": [e.model_dump(exclude_none=True) for e in entities.assets],
            "work_orders": [e.model_dump(exclude_none=True) for e in entities.work_orders],
            "findings": [e.model_dump(exclude_none=True) for e in entities.findings],
            "readings": [e.model_dump(exclude_none=True) for e in entities.readings],
            "technicians": [e.model_dump(exclude_none=True) for e in entities.technicians],
            "vendors": [e.model_dump(exclude_none=True) for e in entities.vendors],
            "certificates": [e.model_dump(exclude_none=True) for e in entities.certificates],
            "spare_parts": [e.model_dump(exclude_none=True) for e in entities.spare_parts],
        },

        # ── Eval / confidence ───────────────────────────────────────────────
        "confidence": {
            "overall": confidence.overall,
            "eval_score": confidence.eval_score,
            "rules_passed": confidence.rules_passed,
            "rules_violations": confidence.rules_violations,
        },
        "route": _eval_score_to_route(confidence.eval_score),

        # ── Cost ────────────────────────────────────────────────────────────
        "audit": {
            "tokens_in": schema.audit.tokens_in,
            "tokens_out": schema.audit.tokens_out,
            "cost_usd": round(schema.audit.cost_usd, 6),
            "cost_aed": round(schema.audit.cost_aed, 6),
            "processing_ms": schema.audit.processing_ms,
        },
    }


def _eval_score_to_route(eval_score: float | None) -> str:
    if eval_score is None:
        return "not_evaluated"
    if eval_score >= 0.85:
        return "accept"
    if eval_score >= 0.60:
        return "review_queue"
    return "re_extract"


app = create_app()
