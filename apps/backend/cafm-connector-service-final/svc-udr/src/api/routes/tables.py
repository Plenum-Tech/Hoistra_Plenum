"""
Direct CRUD REST endpoints — no AI, no interpretation.
These are intended for structured tool calls from the DeepAgents orchestrator
when it already knows exactly what table/record it wants.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...services.database_service import DatabaseService
from ...core.exceptions import (
    TableNotFoundError,
    ColumnNotFoundError,
    RecordNotFoundError,
    UnsafeIdentifierError,
    UnsafeQueryError,
)
from ...api.schemas.database import (
    ReadRecordsRequest,
    CreateRecordRequest,
    UpdateRecordRequest,
    DeleteRecordResponse,
    SearchRequest,
    ExecuteSelectRequest,
)
from ...core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


def _svc(session: AsyncSession = Depends(get_session)) -> DatabaseService:
    return DatabaseService(session)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (UnsafeIdentifierError, UnsafeQueryError)):
        log.warning(
            "route.security_rejection",
            exc_type=type(exc).__name__,
            detail=str(exc),
        )
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, TableNotFoundError):
        log.warning("route.table_not_found", detail=str(exc))
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (ColumnNotFoundError, RecordNotFoundError)):
        log.warning("route.not_found", exc_type=type(exc).__name__, detail=str(exc))
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    log.error("route.unexpected_error", exc_type=type(exc).__name__, detail=str(exc)[:300], exc_info=True)
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


# ── Schema introspection ──────────────────────────────────────────────────────

@router.get("/", summary="List all tables")
async def list_tables(svc: DatabaseService = Depends(_svc)) -> dict:
    log.info("route.list_tables")
    return {"tables": await svc.list_tables()}


@router.get("/{table}/schema", summary="Describe a table schema")
async def describe_table(table: str, svc: DatabaseService = Depends(_svc)) -> dict:
    log.info("route.describe_table", table=table)
    try:
        return await svc.describe_table(table)
    except Exception as exc:
        raise _http_error(exc)


# ── Read operations ───────────────────────────────────────────────────────────

@router.get("/{table}/records", summary="Read records with optional filters")
async def read_records(
    table: str,
    limit: int = 50,
    offset: int = 0,
    order_by: str | None = None,
    order_dir: str = "asc",
    svc: DatabaseService = Depends(_svc),
) -> dict:
    log.info("route.read_records", table=table, limit=limit, offset=offset, order_by=order_by)
    try:
        return await svc.read_records(
            table=table,
            limit=limit,
            offset=offset,
            order_by=order_by,
            order_dir=order_dir,
        )
    except Exception as exc:
        raise _http_error(exc)


@router.post("/{table}/records/search", summary="Search records by text")
async def search_records(
    table: str,
    body: SearchRequest,
    svc: DatabaseService = Depends(_svc),
) -> dict:
    log.info(
        "route.search_records",
        table=table,
        search_term=body.search_term[:80],
        search_columns=body.search_columns,
        limit=body.limit,
    )
    try:
        return await svc.search_records(
            table=table,
            search_term=body.search_term,
            search_columns=body.search_columns,
            limit=body.limit,
            offset=body.offset,
        )
    except Exception as exc:
        raise _http_error(exc)


@router.get("/{table}/records/{record_id}", summary="Get a single record by PK")
async def get_record(
    table: str,
    record_id: str,
    id_column: str = "id",
    svc: DatabaseService = Depends(_svc),
) -> dict:
    log.info("route.get_record", table=table, record_id=record_id, id_column=id_column)
    try:
        return await svc.get_record(table, record_id, id_column)
    except Exception as exc:
        raise _http_error(exc)


# ── Write operations ──────────────────────────────────────────────────────────

@router.post("/{table}/records", summary="Create a new record", status_code=status.HTTP_201_CREATED)
async def create_record(
    table: str,
    body: CreateRecordRequest,
    svc: DatabaseService = Depends(_svc),
) -> dict:
    log.info("route.create_record", table=table, columns=list(body.data.keys()))
    try:
        record = await svc.create_record(table, body.data)
        log.info("route.create_record.done", table=table, record_id=record.get("id"))
        return {"record": record, "message": f"Record created in {table}."}
    except Exception as exc:
        raise _http_error(exc)


@router.patch("/{table}/records/{record_id}", summary="Update an existing record")
async def update_record(
    table: str,
    record_id: str,
    body: UpdateRecordRequest,
    svc: DatabaseService = Depends(_svc),
) -> dict:
    log.info(
        "route.update_record",
        table=table,
        record_id=record_id,
        id_column=body.id_column,
        columns=list(body.data.keys()),
    )
    try:
        record = await svc.update_record(table, record_id, body.data, body.id_column)
        log.info("route.update_record.done", table=table, record_id=record_id)
        return {"record": record, "message": f"Record updated in {table}."}
    except Exception as exc:
        raise _http_error(exc)


@router.delete(
    "/{table}/records/{record_id}",
    response_model=DeleteRecordResponse,
    summary="Delete a record",
)
async def delete_record(
    table: str,
    record_id: str,
    id_column: str = "id",
    svc: DatabaseService = Depends(_svc),
) -> DeleteRecordResponse:
    log.info("route.delete_record", table=table, record_id=record_id, id_column=id_column)
    try:
        deleted = await svc.delete_record(table, record_id, id_column)
        if deleted:
            log.info("route.delete_record.done", table=table, record_id=record_id, deleted=True)
            return DeleteRecordResponse(deleted=True, message=f"Record deleted from {table}.")
        log.warning("route.delete_record.not_found", table=table, record_id=record_id)
        return DeleteRecordResponse(deleted=False, message="Record not found — nothing deleted.")
    except Exception as exc:
        raise _http_error(exc)


# ── Custom SELECT ─────────────────────────────────────────────────────────────

@router.post("/query/select", summary="Execute a custom SELECT query")
async def execute_select(
    body: ExecuteSelectRequest,
    svc: DatabaseService = Depends(_svc),
) -> dict:
    log.info(
        "route.execute_select",
        sql_preview=body.sql.strip()[:120],
        param_count=len(body.params) if body.params else 0,
    )
    try:
        rows = await svc.execute_select(body.sql, body.params)
        log.info("route.execute_select.done", rows_returned=len(rows))
        return {"rows": rows, "count": len(rows)}
    except Exception as exc:
        raise _http_error(exc)
