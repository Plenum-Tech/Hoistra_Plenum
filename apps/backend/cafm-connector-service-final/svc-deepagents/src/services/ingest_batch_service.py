"""CRUD for bulk ingest batch jobs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, update

from .. import database
from ..models.ingest_batch import IngestBatch

log = structlog.get_logger(__name__)


def _session_factory():
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory not initialized")
    return database.AsyncSessionLocal


async def init_ingest_batch_tables() -> None:
    from ..models.ingest_batch import Base
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("ingest_batch.tables.ready")


async def create_ingest_batch(
    *,
    session_id: str,
    organization_id: str,
    cmms_name: str,
    file_paths: list[str],
) -> dict[str, Any]:
    items = [
        {
            "index": i,
            "file_path": p,
            "file_name": p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
            "status": "queued",
            "kind": None,
            "summary": "",
            "error": None,
        }
        for i, p in enumerate(file_paths)
    ]
    batch = IngestBatch(
        id=str(uuid.uuid4()),
        session_id=session_id,
        organization_id=organization_id,
        cmms_name=cmms_name or "Custom",
        status="pending",
        total_files=len(file_paths),
        completed_count=0,
        failed_count=0,
        items=items,
    )
    async with _session_factory()() as session:
        session.add(batch)
        await session.commit()
        await session.refresh(batch)
    log.info(
        "ingest_batch.created",
        batch_id=batch.id,
        session_id=session_id,
        total_files=len(file_paths),
    )
    return batch.to_dict()


async def get_ingest_batch(batch_id: str) -> dict[str, Any] | None:
    async with _session_factory()() as session:
        result = await session.execute(
            select(IngestBatch).where(IngestBatch.id == batch_id)
        )
        row = result.scalar_one_or_none()
        return row.to_dict() if row else None


async def list_ingest_batches_for_session(
    session_id: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    async with _session_factory()() as session:
        result = await session.execute(
            select(IngestBatch)
            .where(IngestBatch.session_id == session_id)
            .order_by(IngestBatch.created_at.desc())
            .limit(min(max(limit, 1), 50))
        )
        rows = result.scalars().all()
        return [r.to_dict() for r in rows]


async def set_batch_status(
    batch_id: str,
    status: str,
    *,
    error_message: str | None = None,
) -> None:
    values: dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc),
    }
    if error_message is not None:
        values["error_message"] = error_message
    async with _session_factory()() as session:
        await session.execute(
            update(IngestBatch).where(IngestBatch.id == batch_id).values(**values)
        )
        await session.commit()


async def update_batch_item(
    batch_id: str,
    item_index: int,
    *,
    status: str,
    kind: str | None = None,
    summary: str = "",
    error: str | None = None,
    increment: bool = False,
    succeeded: bool = False,
) -> dict[str, Any] | None:
    async with _session_factory()() as session:
        result = await session.execute(
            select(IngestBatch).where(IngestBatch.id == batch_id)
        )
        batch = result.scalar_one_or_none()
        if not batch:
            return None
        items = list(batch.items or [])
        if 0 <= item_index < len(items):
            item = dict(items[item_index])
            item["status"] = status
            if kind:
                item["kind"] = kind
            if summary:
                item["summary"] = summary
            if error:
                item["error"] = error
            items[item_index] = item
            batch.items = items
        if increment:
            if succeeded:
                batch.completed_count += 1
            else:
                batch.failed_count += 1
        done = batch.completed_count + batch.failed_count
        if done >= batch.total_files and batch.status == "running":
            if batch.failed_count == batch.total_files:
                batch.status = "failed"
            elif batch.failed_count > 0:
                batch.status = "completed"
            else:
                batch.status = "completed"
        batch.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(batch)
        return batch.to_dict()


async def cancel_ingest_batch(batch_id: str) -> dict[str, Any] | None:
    async with _session_factory()() as session:
        result = await session.execute(
            select(IngestBatch).where(IngestBatch.id == batch_id)
        )
        batch = result.scalar_one_or_none()
        if not batch:
            return None
        if batch.status in ("completed", "failed", "cancelled"):
            return batch.to_dict()
        batch.status = "cancelled"
        batch.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(batch)
        return batch.to_dict()
