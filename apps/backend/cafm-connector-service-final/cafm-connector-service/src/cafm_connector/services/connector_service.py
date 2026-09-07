"""
ConnectorService — business logic layer between API routes and DB/connectors.

Responsibilities:
  - Validate + persist connector configs
  - Encrypt credentials before saving
  - Test connections
  - Manage field maps
  - Launch import jobs via the ARQ queue
  - Return structured preview data
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.connectors.base import ConnectorConfig
from cafm_connector.connectors.registry import ConnectorRegistry
from cafm_connector.core.config import Settings
from cafm_connector.core.exceptions import JobNotFoundError
from cafm_connector.core.logging import get_logger
from cafm_connector.core.types import DataSourceType, ImportJobStatus
from cafm_connector.models.db import (
    ConnectorModel, FieldMapModel, ImportErrorModel, ImportJobModel,
)
from cafm_connector.secrets.backend import SecretsBackend

logger = get_logger(__name__)


class ConnectorService:

    def __init__(
        self,
        session: AsyncSession,
        secrets: SecretsBackend,
        settings: Settings,
    ) -> None:
        self._session = session
        self._secrets = secrets
        self._settings = settings
        self._registry = ConnectorRegistry()

    # ── Connector CRUD ────────────────────────────────────────────

    async def test_connection_params(
        self,
        source_type: str,
        connection_params: dict[str, Any],
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        """Instantiate a connector and run health_check — no DB writes."""
        config = ConnectorConfig(
            name="__test__",
            source_type=DataSourceType(source_type),
            connection_params=connection_params,
            credentials=credentials,
        )
        connector = self._registry.create(config)
        start = time.perf_counter()
        try:
            await connector.connect()
            healthy = await connector.health_check()
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            await connector.disconnect()
            return {"success": healthy, "latency_ms": latency_ms, "error": None}
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            return {"success": False, "latency_ms": latency_ms, "error": str(exc)}

    async def save_connector(
        self,
        name: str,
        source_type: str,
        connection_params: dict[str, Any],
        credentials: dict[str, Any],
        options: dict[str, Any] | None = None,
        description: str | None = None,
        created_by: str | None = None,
    ) -> ConnectorModel:
        """Encrypt credentials and persist connector config."""
        encrypted = await self._secrets.encrypt(credentials) if credentials else None
        model = ConnectorModel(
            id=str(uuid4()),
            name=name,
            source_type=source_type,
            connection_params=connection_params,
            config_encrypted=encrypted,
            options=options or {},
            description=description,
            created_by=created_by,
        )
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        logger.info("connector_saved", id=model.id, name=name, type=source_type)
        return model

    async def list_connectors(self, organization_id: str | None = None) -> list[ConnectorModel]:
        """List active connectors, optionally scoped to an organization."""
        stmt = select(ConnectorModel).where(ConnectorModel.is_active == True)
        if organization_id:
            # organization_id is stored in options JSON column
            stmt = stmt.where(
                ConnectorModel.options["organization_id"].as_string() == organization_id
            )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_connector(self, connector_id: str) -> ConnectorModel | None:
        result = await self._session.execute(
            select(ConnectorModel).where(ConnectorModel.id == connector_id)
        )
        return result.scalar_one_or_none()

    async def delete_connector(self, connector_id: str) -> None:
        """Soft-delete a connector by setting is_active = False."""
        result = await self._session.execute(
            select(ConnectorModel).where(ConnectorModel.id == connector_id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.is_active = False
            await self._session.commit()
            logger.info("connector_deleted", id=connector_id)

    # ── Import preview ────────────────────────────────────────────

    async def preview_import(
        self,
        connector_id: str | None = None,
        connector_model: ConnectorModel | None = None,
        file_path: str | None = None,
        table_name: str | None = None,
        field_map: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Return first N rows (default 50) from a source — no job created."""
        if connector_model is None and connector_id:
            connector_model = await self.get_connector(connector_id)
        if connector_model is None:
            raise ValueError("connector_id or connector_model required")

        credentials: dict[str, Any] = {}
        if connector_model.config_encrypted:
            credentials = await self._secrets.decrypt(connector_model.config_encrypted)

        # Override file path for ad-hoc uploads
        params = dict(connector_model.connection_params)
        if file_path:
            params["file_path"] = file_path

        config = ConnectorConfig(
            name=connector_model.name,
            source_type=DataSourceType(connector_model.source_type),
            connection_params=params,
            credentials=credentials,
            options=connector_model.options,
        )
        connector = self._registry.create(config)
        async with connector.session():
            # Discover available tables
            inspector = connector.get_schema_inspector()
            tables = await inspector.list_tables()
            target_table = table_name or (tables[0] if tables else "")

            rows = await connector.fetch_rows(
                target_table, limit=self._settings.import_preview_rows
            )

            # Infer column/type info
            columns: list[dict] = []
            if rows:
                for col_name in rows[0].keys():
                    val = rows[0][col_name]
                    columns.append({"name": col_name, "sample_type": type(val).__name__})

        return {
            "connector_id": connector_model.id,
            "table": target_table,
            "available_tables": tables,
            "rows": rows,
            "columns": columns,
            "row_count": len(rows),
        }

    # ── Field maps ────────────────────────────────────────────────

    async def save_field_map(
        self,
        connector_id: str,
        mappings: list[dict[str, str]],
    ) -> list[FieldMapModel]:
        """
        Replace all field maps for a connector.
        mappings: [{"source_field": "...", "target_field": "...", "transform_fn": "..."}]
        """
        # Delete existing
        existing_q = await self._session.execute(
            select(FieldMapModel).where(FieldMapModel.connector_id == connector_id)
        )
        for fm in existing_q.scalars().all():
            await self._session.delete(fm)

        new_maps = [
            FieldMapModel(
                id=str(uuid4()),
                connector_id=connector_id,
                source_field=m["source_field"],
                target_field=m["target_field"],
                transform_fn=m.get("transform_fn"),
            )
            for m in mappings
        ]
        self._session.add_all(new_maps)
        await self._session.commit()
        return new_maps

    # ── Import job lifecycle ──────────────────────────────────────

    async def create_import_job(
        self,
        connector_id: str,
        table_name: str | None = None,
        conflict_mode: str = "skip",
        schedule: str = "one_off",
        cron_expr: str | None = None,
        created_by: str | None = None,
    ) -> ImportJobModel:
        """Create a job record and enqueue it in ARQ/Redis."""
        from cafm_connector.jobs.worker import enqueue_import_job

        job = ImportJobModel(
            id=str(uuid4()),
            connector_id=connector_id,
            table_name=table_name,
            status=ImportJobStatus.QUEUED,
            conflict_mode=conflict_mode,
            schedule=schedule,
            cron_expr=cron_expr,
            created_by=created_by,
        )
        self._session.add(job)
        await self._session.commit()
        await self._session.refresh(job)

        # Push to ARQ queue (Redis)
        await enqueue_import_job(job.id)

        logger.info("import_job_queued", job_id=job.id, connector_id=connector_id)
        return job

    async def get_job_status(self, job_id: str) -> dict[str, Any]:
        result = await self._session.execute(
            select(ImportJobModel).where(ImportJobModel.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise JobNotFoundError(f"Job {job_id} not found")

        duration = None
        if job.started_at and job.finished_at:
            duration = (job.finished_at - job.started_at).total_seconds()

        progress = 0.0
        if job.total_rows and job.total_rows > 0:
            progress = round(job.imported_rows / job.total_rows * 100, 1)

        return {
            "job_id": job.id,
            "connector_id": job.connector_id,
            "status": job.status,
            "total_rows": job.total_rows,
            "imported_rows": job.imported_rows,
            "skipped_rows": job.skipped_rows,
            "error_count": job.error_count,
            "progress": progress,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "duration_seconds": duration,
            "is_rolled_back": job.is_rolled_back,
        }

    async def get_job_log(
        self,
        job_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        # Verify job exists
        job_q = await self._session.execute(
            select(ImportJobModel).where(ImportJobModel.id == job_id)
        )
        if job_q.scalar_one_or_none() is None:
            raise JobNotFoundError(f"Job {job_id} not found")

        errors_q = await self._session.execute(
            select(ImportErrorModel)
            .where(ImportErrorModel.job_id == job_id)
            .offset(offset)
            .limit(limit)
        )
        errors = errors_q.scalars().all()
        return {
            "job_id": job_id,
            "errors": [
                {
                    "row_num": e.row_num,
                    "error_msg": e.error_msg,
                    "raw_data": e.raw_data,
                    "created_at": e.created_at.isoformat(),
                }
                for e in errors
            ],
            "total": len(errors),
        }
