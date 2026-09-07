"""
Data Import & Connector routes — US-01 backend endpoints.

POST   /api/v1/connectors/test           — test connection (DB/network only)
POST   /api/v1/connectors                — save connector config
GET    /api/v1/connectors                — list connectors (scoped by org)
GET    /api/v1/connectors/{id}           — get single connector
DELETE /api/v1/connectors/{id}           — delete connector
GET    /api/v1/schema/tables             — list all plenum_cafm tables + columns
POST   /api/v1/imports/file/run          — upload file + run import (file-based)
POST   /api/v1/imports/preview           — preview rows from saved connector
POST   /api/v1/imports/field-map         — save column mappings
POST   /api/v1/imports/run               — trigger import job (DB/network)
PUT    /api/v1/imports/{jobId}/cancel    — cancel a queued/running job
GET    /api/v1/imports/{jobId}/status    — job progress
GET    /api/v1/imports/{jobId}/log       — per-row error log
"""

from __future__ import annotations

import os
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from cafm_connector.api.dependencies import get_current_user, get_service, get_settings
from cafm_connector.api.schemas.connectors import (
    APIResponse,
    ConnectorCreateRequest,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorTestRequest,
    ConnectorTestResponse,
    FieldMapRequest,
    FieldMapResponse,
    ImportPreviewRequest,
    ImportPreviewResponse,
    ImportRunRequest,
    ImportRunResponse,
    JobLogResponse,
    JobStatusResponse,
)
from cafm_connector.core.config import Settings
from cafm_connector.core.exceptions import JobNotFoundError
from cafm_connector.core.types import ImportJobStatus
from cafm_connector.models.db import ImportJobModel
from cafm_connector.services.connector_service import ConnectorService

router = APIRouter(tags=["Data Connectors & Imports"])


# ── POST /connectors/test ─────────────────────────────────────────────

@router.post(
    "/connectors/test",
    response_model=ConnectorTestResponse,
    summary="Test a DB/network connection before saving",
)
async def test_connector(
    body: ConnectorTestRequest,
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    result = await svc.test_connection_params(
        source_type=body.source_type,
        connection_params=body.connection_params,
        credentials=body.credentials,
    )
    return ConnectorTestResponse(**result)


# ── POST /connectors ──────────────────────────────────────────────────

@router.post(
    "/connectors",
    response_model=APIResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a connector config (credentials encrypted)",
)
async def create_connector(
    body: ConnectorCreateRequest,
    user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    try:
        model = await svc.save_connector(
            name=body.name,
            source_type=body.source_type,
            connection_params=body.connection_params,
            credentials=body.credentials,
            options=body.options,
            description=body.description,
            created_by=getattr(user, "sub", None),
        )
        return APIResponse(
            message=f"Connector '{body.name}' saved",
            data=ConnectorResponse(
                id=model.id,
                name=model.name,
                source_type=model.source_type,
                description=model.description,
                is_active=model.is_active,
                created_at=model.created_at.isoformat(),
            ),
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"A connector named '{body.name}' already exists. Choose a different name.",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── GET /connectors ───────────────────────────────────────────────────

@router.get(
    "/connectors",
    response_model=ConnectorListResponse,
    summary="List all saved connectors (filter by organization_id)",
)
async def list_connectors(
    organization_id: str | None = Query(None, description="Filter by organization"),
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    models = await svc.list_connectors(organization_id=organization_id)
    return ConnectorListResponse(
        connectors=[
            ConnectorResponse(
                id=m.id,
                name=m.name,
                source_type=m.source_type,
                description=m.description,
                is_active=m.is_active,
                created_at=m.created_at.isoformat(),
            )
            for m in models
        ],
        total=len(models),
    )


# ── GET /connectors/{id} ──────────────────────────────────────────────

@router.get(
    "/connectors/{connector_id}",
    response_model=ConnectorResponse,
    summary="Get a single connector by ID",
)
async def get_connector(
    connector_id: str,
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    model = await svc.get_connector(connector_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id} not found")
    return ConnectorResponse(
        id=model.id,
        name=model.name,
        source_type=model.source_type,
        description=model.description,
        is_active=model.is_active,
        created_at=model.created_at.isoformat(),
    )


# ── DELETE /connectors/{id} ───────────────────────────────────────────

@router.delete(
    "/connectors/{connector_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a connector (soft-delete)",
)
async def delete_connector(
    connector_id: str,
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    model = await svc.get_connector(connector_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Connector {connector_id} not found")
    await svc.delete_connector(connector_id)


# ── GET /schema/tables ────────────────────────────────────────────────

@router.get(
    "/schema/tables",
    summary="List all tables in plenum_cafm schema with their columns",
)
async def get_schema_tables(
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    result = await svc._session.execute(text("""
        SELECT
            t.table_name,
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default,
            CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END AS is_primary_key
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON t.table_name = c.table_name
            AND t.table_schema = c.table_schema
        LEFT JOIN (
            SELECT ku.table_name, ku.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage ku
                ON tc.constraint_name = ku.constraint_name
                AND tc.table_schema = ku.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
            AND tc.table_schema = 'plenum_cafm'
        ) pk ON pk.table_name = t.table_name AND pk.column_name = c.column_name
        WHERE t.table_schema = 'plenum_cafm'
        AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name, c.ordinal_position
    """))

    rows = result.fetchall()

    tables: dict = {}
    for row in rows:
        table_name = row.table_name
        if table_name not in tables:
            tables[table_name] = {"name": table_name, "columns": []}
        tables[table_name]["columns"].append({
            "name": row.column_name,
            "type": row.data_type,
            "nullable": row.is_nullable == "YES",
            "default": row.column_default,
            "primary_key": row.is_primary_key,
        })

    return {
        "schema": "plenum_cafm",
        "total_tables": len(tables),
        "tables": list(tables.values()),
    }


# ── POST /imports/file/run ────────────────────────────────────────────

@router.post(
    "/imports/file/run",
    response_model=ImportRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a file and immediately trigger an import job (CSV/Excel/JSON/XML/Parquet)",
    description=(
        "Single endpoint for file-based imports. "
        "The frontend prepares the file client-side (mapping, cleaning) and submits it here. "
        "The file is uploaded to Azure Blob Storage, a ConnectorModel is created, "
        "and the import job is enqueued. Returns job_id for progress polling."
    ),
)
async def run_file_import(
    file: UploadFile = File(..., description="The prepared file to import"),
    source_type: str = File(..., description="csv | excel | json | xml | parquet"),
    organization_id: str | None = File(None, description="Organization UUID — required for org-scoped tables"),
    target_table: str = File("assets", description=(
        "Target table to import into. Options: "
        "organizations | users | roles | permissions | user_roles | role_permissions | "
        "locations | asset_categories | assets | asset_documents | asset_readings | "
        "maintenance_plans | technicians | technician_skills | vendors | vendor_contacts | "
        "vendor_contracts | sla_policies | work_orders | work_order_tasks | "
        "work_order_comments | work_order_attachments | work_order_history | "
        "maintenance_history | spare_parts | inventory_transactions | "
        "work_order_parts | notifications | audit_logs"
    )),
    conflict_mode: str = File("skip", description="skip | overwrite | flag"),
    schedule: str = File("one_off", description="one_off | cron"),
    cron_expr: str | None = File(None, description="Cron expression (only if schedule=cron)"),
    delimiter: str = File(",", description="CSV only — column delimiter"),
    encoding: str = File("utf-8", description="CSV only — file encoding"),
    sheet_name: str | None = File(None, description="Excel only — sheet name"),
    root_key: str | None = File(None, description="JSON only — dotted path to records array"),
    record_tag: str | None = File(None, description="XML only — element tag per row"),
    user=Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    svc: ConnectorService = Depends(get_service),
):
    from cafm_connector.storage.blob import get_blob_storage, ALLOWED_EXTENSIONS
    from cafm_connector.models.db import UploadedFileModel

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {exc}")

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        blob_storage = get_blob_storage(settings)
        blob_result = await blob_storage.upload(
            file_bytes=file_bytes,
            original_filename=filename,
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Azure Blob upload failed: {exc}")

    upload_record = UploadedFileModel(
        id=str(uuid4()),
        original_filename=blob_result.original_filename,
        content_type=blob_result.content_type,
        file_extension=blob_result.file_extension,
        file_size_bytes=blob_result.file_size_bytes,
        blob_name=blob_result.blob_name,
        blob_url=blob_result.blob_url,
        uploaded_by=getattr(user, "sub", None),
    )
    svc._session.add(upload_record)
    await svc._session.flush()

    connection_params: dict = {"file_path": blob_result.blob_url}
    if source_type == "csv":
        connection_params.update({"delimiter": delimiter, "encoding": encoding})
    elif source_type == "excel" and sheet_name:
        connection_params["sheet_name"] = sheet_name
    elif source_type == "json" and root_key:
        connection_params["root_key"] = root_key
    elif source_type == "xml" and record_tag:
        connection_params["record_tag"] = record_tag

    connector_name = f"{source_type}_{os.path.splitext(filename)[0]}_{uuid4().hex[:8]}"

    try:
        connector = await svc.save_connector(
            name=connector_name,
            source_type=source_type,
            connection_params=connection_params,
            credentials={},
            options={"organization_id": organization_id, "target_table": target_table},
            description=f"File import: {filename} → {target_table}",
            created_by=getattr(user, "sub", None),
        )
        upload_record.connector_id = connector.id
        await svc._session.commit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to create connector: {exc}")

    try:
        job = await svc.create_import_job(
            connector_id=connector.id,
            conflict_mode=conflict_mode,
            schedule=schedule,
            cron_expr=cron_expr,
            created_by=getattr(user, "sub", None),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to enqueue job: {exc}")

    return ImportRunResponse(
        job_id=job.id,
        status=job.status,
        queued_at=job.created_at.isoformat(),
    )


# ── POST /imports/preview ─────────────────────────────────────────────

@router.post(
    "/imports/preview",
    response_model=ImportPreviewResponse,
    summary="Preview first 50 rows from a saved connector",
)
async def preview_import(
    body: ImportPreviewRequest,
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    try:
        result = await svc.preview_import(
            connector_id=body.connector_id,
            table_name=body.table_name,
            field_map=body.field_map,
        )
        from cafm_connector.api.schemas.connectors import ColumnInfo
        return ImportPreviewResponse(
            connector_id=result["connector_id"],
            table=result["table"],
            available_tables=result["available_tables"],
            rows=result["rows"],
            columns=[ColumnInfo(**c) for c in result["columns"]],
            row_count=result["row_count"],
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── POST /imports/field-map ───────────────────────────────────────────

@router.post(
    "/imports/field-map",
    response_model=FieldMapResponse,
    summary="Save source→target field mapping for a connector",
)
async def save_field_map(
    body: FieldMapRequest,
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    saved = await svc.save_field_map(
        connector_id=body.connector_id,
        mappings=[m.model_dump() for m in body.mappings],
    )
    return FieldMapResponse(connector_id=body.connector_id, mappings_saved=len(saved))


# ── POST /imports/run ─────────────────────────────────────────────────

@router.post(
    "/imports/run",
    response_model=ImportRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger an async import job from a saved connector (DB/network)",
)
async def run_import(
    body: ImportRunRequest,
    user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    try:
        job = await svc.create_import_job(
            connector_id=body.connector_id,
            table_name=body.table_name,
            conflict_mode=body.conflict_mode,
            schedule=body.schedule,
            cron_expr=body.cron_expr,
            created_by=getattr(user, "sub", None),
        )
        return ImportRunResponse(
            job_id=job.id,
            status=job.status,
            queued_at=job.created_at.isoformat(),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── PUT /imports/{jobId}/cancel ───────────────────────────────────────

@router.put(
    "/imports/{job_id}/cancel",
    response_model=APIResponse,
    summary="Cancel a queued or running import job",
)
async def cancel_job(
    job_id: str,
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    result = await svc._session.execute(
        select(ImportJobModel).where(ImportJobModel.id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status in (ImportJobStatus.COMPLETED, ImportJobStatus.FAILED, ImportJobStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already in terminal state: {job.status}",
        )
    job.status = ImportJobStatus.CANCELLED
    await svc._session.commit()
    return APIResponse(message=f"Job {job_id} cancelled successfully")


# ── GET /imports/{jobId}/status ───────────────────────────────────────

@router.get(
    "/imports/{job_id}/status",
    response_model=JobStatusResponse,
    summary="Poll import job progress",
)
async def get_job_status(
    job_id: str,
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    try:
        data = await svc.get_job_status(job_id)
        return JobStatusResponse(**data)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


# ── GET /imports/{jobId}/log ──────────────────────────────────────────

@router.get(
    "/imports/{job_id}/log",
    response_model=JobLogResponse,
    summary="Fetch per-row error log for a job",
)
async def get_job_log(
    job_id: str,
    limit: int = 100,
    offset: int = 0,
    _user=Depends(get_current_user),
    svc: ConnectorService = Depends(get_service),
):
    try:
        data = await svc.get_job_log(job_id, limit=limit, offset=offset)
        from cafm_connector.api.schemas.connectors import ErrorLogEntry
        return JobLogResponse(
            job_id=data["job_id"],
            errors=[ErrorLogEntry(**e) for e in data["errors"]],
            total=data["total"],
        )
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")