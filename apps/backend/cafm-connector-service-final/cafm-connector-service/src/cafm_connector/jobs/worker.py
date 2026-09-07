"""
Import job worker.

Supports importing CSV/Excel/JSON/XML/Parquet files into any of the 29
plenum_cafm tables. The target table is specified via connector options
target_table field set by POST /imports/file/run.
"""

from __future__ import annotations

import hashlib
import os
import importlib
import uuid
from datetime import date, datetime
from typing import Any

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import RedisSettings

from cafm_connector.core.config import get_settings
from cafm_connector.core.logging import get_logger
from cafm_connector.core.types import ConflictMode, ImportJobStatus, RawRow

logger = get_logger(__name__)

PROGRESS_CHANNEL = "cafm:import:progress"

# ── Table → Model class mapping ───────────────────────────────────────

TABLE_MODEL_MAP = {
    "organizations":          "Organization",
    "users":                  "User",
    "roles":                  "Role",
    "permissions":            "Permission",
    "user_roles":             "UserRole",
    "role_permissions":       "RolePermission",
    "locations":              "Location",
    "asset_categories":       "AssetCategory",
    "assets":                 "Asset",
    "asset_documents":        "AssetDocument",
    "asset_readings":         "AssetReading",
    "maintenance_plans":      "MaintenancePlan",
    "technicians":            "Technician",
    "technician_skills":      "TechnicianSkill",
    "vendors":                "Vendor",
    "vendor_contacts":        "VendorContact",
    "vendor_contracts":       "VendorContract",
    "sla_policies":           "SLAPolicy",
    "work_orders":            "WorkOrder",
    "work_order_tasks":       "WorkOrderTask",
    "work_order_comments":    "WorkOrderComment",
    "work_order_attachments": "WorkOrderAttachment",
    "work_order_history":     "WorkOrderHistory",
    "maintenance_history":    "MaintenanceHistory",
    "spare_parts":            "SparePart",
    "inventory_transactions": "InventoryTransaction",
    "work_order_parts":       "WorkOrderPart",
    "notifications":          "Notification",
    "audit_logs":             "AuditLog",
}


def get_model_class(target_table: str):
    class_name = TABLE_MODEL_MAP.get(target_table)
    if not class_name:
        raise ValueError(
            f"Unknown target_table '{target_table}'. "
            f"Valid options: {', '.join(sorted(TABLE_MODEL_MAP.keys()))}"
        )
    module = importlib.import_module("cafm_connector.models.plenum_cafm")
    return getattr(module, class_name)


# ── Progress broadcasting ─────────────────────────────────────────────

async def _broadcast_progress(redis: aioredis.Redis, job_id: str, payload: dict) -> None:
    import json
    await redis.publish(PROGRESS_CHANNEL, json.dumps({"job_id": job_id, **payload}))


# ── Duplicate detection ───────────────────────────────────────────────

def _dedup_hash(row: RawRow, fields: list[str]) -> str:
    parts = "|".join(str(row.get(f, "")) for f in fields)
    return hashlib.sha256(parts.encode()).hexdigest()


# ── Type coercion helpers ─────────────────────────────────────────────

def _parse_uuid(val):
    if val is None or str(val).strip() == "":
        return None
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, AttributeError):
        return None


def _parse_int(val):
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_bool(val):
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


def _parse_date(val):
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ── QR code generation ────────────────────────────────────────────────

async def _generate_qr_url(asset_id: str, settings) -> str:
    import io
    import os
    import qrcode

    qr_data = f"cafm://asset/{asset_id}"
    png_img = qrcode.make(qr_data)

    try:
        from cafm_connector.storage.blob import AzureBlobStorage
        buf = io.BytesIO()
        png_img.save(buf, format="PNG")
        buf.seek(0)
        blob_storage = AzureBlobStorage(settings)
        result = await blob_storage.upload(
            file_bytes=buf.getvalue(),
            original_filename=f"{asset_id}.png",
            content_type="image/png",
        )
        return result.blob_url
    except Exception:
        os.makedirs(settings.qr_local_dir, exist_ok=True)
        png_path = os.path.join(settings.qr_local_dir, f"{asset_id}.png")
        png_img.save(png_path)
        return png_path


# ── Field map transform ───────────────────────────────────────────────

def _apply_field_map(row: RawRow, field_maps: list[dict]) -> RawRow:
    result = dict(row)
    for fm in field_maps:
        src = fm["source_field"]
        tgt = fm["target_field"]
        fn_name = fm.get("transform_fn")
        if src in result:
            val = result.pop(src)
            if fn_name == "to_uppercase" and isinstance(val, str):
                val = val.upper()
            elif fn_name == "to_lowercase" and isinstance(val, str):
                val = val.lower()
            elif fn_name == "strip":
                val = str(val).strip()
            result[tgt] = val
    return result


# ── Generic row → model kwargs converter ─────────────────────────────

def _row_to_kwargs(row, model_class, organization_id, row_num, qr_url=None) -> dict:
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import String, Text, Integer, Boolean, Date, DateTime, Numeric
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    mapper = sa_inspect(model_class)
    columns = {col.key: col for col in mapper.columns}

    def _find(col_name):
        if col_name in row:
            return row[col_name]
        for k in row:
            if k.lower() == col_name.lower():
                return row[k]
        return None

    kwargs: dict = {"id": uuid.uuid4()}

    if "organization_id" in columns and organization_id:
        kwargs["organization_id"] = uuid.UUID(organization_id)

    if "qr_code" in columns and qr_url:
        kwargs["qr_code"] = str(qr_url)[:255]

    for col_name, col in columns.items():
        if col_name in kwargs:
            continue
        if col.primary_key and col_name == "id":
            continue

        val = _find(col_name)
        if val is None or str(val).strip() == "":
            continue

        col_type = type(col.type)

        try:
            if col_type in (String, Text):
                max_len = getattr(col.type, "length", None)
                coerced = str(val)
                kwargs[col_name] = coerced[:max_len] if max_len else coerced
            elif col_type == Integer:
                kwargs[col_name] = _parse_int(val)
            elif col_type == Boolean:
                kwargs[col_name] = _parse_bool(val)
            elif col_type == Date:
                kwargs[col_name] = _parse_date(val)
            elif col_type == DateTime:
                if isinstance(val, datetime):
                    kwargs[col_name] = val
                else:
                    kwargs[col_name] = datetime.fromisoformat(str(val).strip())
            elif col_type == Numeric:
                from decimal import Decimal
                kwargs[col_name] = Decimal(str(val))
            elif col_type == PG_UUID or "UUID" in str(col_type):
                parsed = _parse_uuid(val)
                if parsed:
                    kwargs[col_name] = parsed
            else:
                kwargs[col_name] = val
        except Exception:
            pass

    return kwargs


# ── Core job function ─────────────────────────────────────────────────

async def run_import_job(ctx: dict, job_id: str) -> dict:
    """
    Imports rows from any file-based source into any plenum_cafm table.
    The target table is read from connector_model.options['target_table'].
    """
    settings   = ctx["settings"]
    db_factory = ctx["db_factory"]
    redis      = ctx["redis"]

    logger.info("import_job_started", job_id=job_id)

    async with db_factory() as session:
        from sqlalchemy import select, delete as sa_delete
        from cafm_connector.models.db import (
            ConnectorModel, FieldMapModel, ImportErrorModel, ImportJobModel,
        )
        from cafm_connector.connectors.registry import ConnectorRegistry
        from cafm_connector.connectors.base import ConnectorConfig
        from cafm_connector.core.types import DataSourceType
        from cafm_connector.secrets.backend import get_secrets_backend

        # ── Load job ──────────────────────────────────────────────
        job_q = await session.execute(
            select(ImportJobModel).where(ImportJobModel.id == job_id)
        )
        job: ImportJobModel | None = job_q.scalar_one_or_none()
        if job is None:
            logger.error("import_job_not_found", job_id=job_id)
            return {"status": "failed", "error": "Job not found"}

        job.status     = ImportJobStatus.RUNNING
        job.started_at = datetime.utcnow()
        await session.commit()

        # ── Load connector ────────────────────────────────────────
        conn_q = await session.execute(
            select(ConnectorModel).where(ConnectorModel.id == job.connector_id)
        )
        connector_model: ConnectorModel = conn_q.scalar_one()

        organization_id: str | None = connector_model.options.get("organization_id")
        target_table: str = connector_model.options.get("target_table", "assets")

        # Resolve model class
        try:
            ModelClass = get_model_class(target_table)
        except ValueError as e:
            job.status = ImportJobStatus.FAILED
            job.finished_at = datetime.utcnow()
            await session.commit()
            return {"status": "failed", "error": str(e)}

        # organization_id required for org-scoped tables
        org_scoped_tables = {
            "assets", "organizations", "locations", "asset_categories",
            "asset_readings", "maintenance_plans", "technicians", "vendors",
            "vendor_contracts", "sla_policies", "work_orders", "spare_parts",
            "notifications", "audit_logs",
        }
        if target_table in org_scoped_tables and not organization_id:
            job.status = ImportJobStatus.FAILED
            job.finished_at = datetime.utcnow()
            await session.commit()
            return {"status": "failed", "error": f"organization_id required for table '{target_table}'"}

        # Decrypt credentials
        secrets = get_secrets_backend(settings)
        credentials: dict[str, Any] = {}
        if connector_model.config_encrypted:
            credentials = await secrets.decrypt(connector_model.config_encrypted)

        config = ConnectorConfig(
            name=connector_model.name,
            source_type=DataSourceType(connector_model.source_type),
            connection_params=connector_model.connection_params,
            credentials=credentials,
            options=connector_model.options,
        )

        # ── Load field maps ───────────────────────────────────────
        fm_q = await session.execute(
            select(FieldMapModel).where(FieldMapModel.connector_id == job.connector_id)
        )
        field_maps = [
            {"source_field": fm.source_field, "target_field": fm.target_field, "transform_fn": fm.transform_fn}
            for fm in fm_q.scalars().all()
        ]

        inserted_ids: list[uuid.UUID] = []
        registry = ConnectorRegistry()
        connector = registry.create(config)

        imported = 0
        skipped  = 0
        errors   = 0
        row_num  = 0
        total    = 0

        try:
            async with connector.session():
                total = await connector.count_rows(job.table_name or "")
                job.total_rows = total
                await session.commit()

                await _broadcast_progress(redis, job_id, {
                    "status": "running", "total": total, "imported": 0, "progress": 0
                })

                conflict_mode = ConflictMode(job.conflict_mode)

                async for batch in connector.stream_rows(
                    job.table_name or "",
                    batch_size=settings.import_streaming_batch_size,
                ):
                    batch_objects = []

                    for raw_row in batch:
                        row_num += 1
                        try:
                            row = _apply_field_map(raw_row, field_maps)

                            qr_url = None
                            if target_table == "assets":
                                asset_id_str = str(
                                    row.get("asset_code") or row.get("code") or uuid.uuid4()
                                )
                                qr_url = await _generate_qr_url(asset_id_str, settings)

                            kwargs = _row_to_kwargs(
                                row=row,
                                model_class=ModelClass,
                                organization_id=organization_id,
                                row_num=row_num,
                                qr_url=qr_url,
                            )
                            obj = ModelClass(**kwargs)
                            batch_objects.append(obj)
                            inserted_ids.append(kwargs["id"])
                            imported += 1

                        except Exception as row_exc:
                            errors += 1
                            session.add(ImportErrorModel(
                                job_id=job_id,
                                row_num=row_num,
                                raw_data=raw_row,
                                error_msg=str(row_exc),
                            ))

                    session.add_all(batch_objects)
                    await session.flush()

                    job.imported_rows = imported
                    job.skipped_rows  = skipped
                    job.error_count   = errors
                    await session.commit()

                    progress = round((row_num / total * 100), 1) if total else 0
                    await _broadcast_progress(redis, job_id, {
                        "status": "running",
                        "total": total,
                        "imported": imported,
                        "skipped": skipped,
                        "errors": errors,
                        "progress": progress,
                    })
                    logger.info("import_batch", job_id=job_id, row_num=row_num, progress=progress)

            job.status      = ImportJobStatus.COMPLETED
            job.finished_at = datetime.utcnow()
            await session.commit()

            await _broadcast_progress(redis, job_id, {
                "status": "completed", "total": total,
                "imported": imported, "skipped": skipped, "errors": errors, "progress": 100,
            })
            logger.info("import_job_completed", job_id=job_id, imported=imported, errors=errors)
            return {"status": "completed", "imported": imported, "skipped": skipped, "errors": errors}

        except Exception as exc:
            logger.error("import_job_failed", job_id=job_id, error=str(exc))

            # Step 1 — rollback the broken transaction before touching the session again
            try:
                await session.rollback()
            except Exception:
                pass

            # Step 2 — delete any rows inserted by this job (clean rollback)
            if inserted_ids:
                try:
                    await session.execute(
                        sa_delete(ModelClass).where(ModelClass.id.in_(inserted_ids))
                    )
                    await session.commit()
                except Exception:
                    pass

            # Step 3 — mark job as failed in a fresh transaction
            try:
                job.status         = ImportJobStatus.FAILED
                job.is_rolled_back = True
                job.finished_at    = datetime.utcnow()
                await session.commit()
            except Exception:
                pass

            await _broadcast_progress(redis, job_id, {"status": "failed", "error": str(exc)})
            return {"status": "failed", "error": str(exc)}


# ── ARQ worker settings ───────────────────────────────────────────────

async def startup(ctx: dict) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    settings = get_settings()
    ctx["settings"] = settings
    engine = create_async_engine(
        settings.db_url,
        pool_size=settings.db_pool_size,
        # Health-check pooled connections + retire after 30 min so an idle-closed cloud-DB
        # connection is transparently reconnected instead of failing with "connection is closed".
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    ctx["db_factory"] = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ctx["redis"] = await aioredis.from_url(settings.redis_url, decode_responses=True)
    from cafm_connector.connectors.registry import ConnectorRegistry
    ConnectorRegistry().discover_plugins()
    logger.info("arq_worker_started", concurrency=settings.job_concurrency)


async def shutdown(ctx: dict) -> None:
    if "redis" in ctx:
        await ctx["redis"].aclose()
    logger.info("arq_worker_stopped")


class WorkerSettings:
    functions      = [run_import_job]
    on_startup     = startup
    on_shutdown    = shutdown
    redis_settings = RedisSettings(
        host="PlenumRedis.uaenorth.redis.azure.net",
        port=10000,
        password=os.environ.get("REDIS_PASSWORD", ""),  # was a hardcoded live key
        ssl=True,
    )
    queue_name     = "{cafm}:queue"
    max_jobs       = get_settings().job_concurrency
    job_timeout    = get_settings().job_timeout_seconds


async def enqueue_import_job(job_id: str) -> None:
    """
    Runs the import job directly as an asyncio background task.
    Bypasses Redis queuing to avoid CROSSSLOT issues on Azure Managed Redis.
    """
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from cafm_connector.connectors.registry import ConnectorRegistry

    settings = get_settings()
    engine = create_async_engine(
        settings.db_url,
        pool_size=settings.db_pool_size,
        # Health-check pooled connections + retire after 30 min so an idle-closed cloud-DB
        # connection is transparently reconnected instead of failing with "connection is closed".
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    db_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    redis = await aioredis.from_url(settings.redis_url, decode_responses=True)

    ctx = {
        "settings": settings,
        "db_factory": db_factory,
        "redis": redis,
    }

    ConnectorRegistry().discover_plugins()
    asyncio.create_task(run_import_job(ctx, job_id))