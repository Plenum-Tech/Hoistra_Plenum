"""
svc-ingestion/src/shared/ingest.py

Stage 1 — Ingest.

Responsibilities (same for every agent type):
  1. Validate file — type, size, page count
  2. Compute SHA-256 hash and check for deduplication
  3. Upload original file to Azure Blob Storage
  4. Create ingestion_documents record (status=queued)
  5. Enqueue extraction job via ARQ

Returns IngestResult immediately. Extraction runs asynchronously.
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from arq import ArqRedis
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from cafm_shared.metrics import documents_ingested
from models.ingestion import IngestionDocument
from shared.intermediate_schema import AgentId, SourceType

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Max file sizes per source type (bytes)
MAX_FILE_SIZES: dict[SourceType, int] = {
    SourceType.PDF:    32 * 1024 * 1024,   # 32 MB
    SourceType.EXCEL:  50 * 1024 * 1024,   # 50 MB
    SourceType.WORD:   50 * 1024 * 1024,   # 50 MB
    SourceType.CSV:   200 * 1024 * 1024,   # 200 MB
    SourceType.XML:   100 * 1024 * 1024,   # 100 MB
    SourceType.JSON:  100 * 1024 * 1024,   # 100 MB
}

MAX_PDF_PAGES = 100

# Allowed MIME types per source type
ALLOWED_MIMES: dict[SourceType, set[str]] = {
    SourceType.PDF:   {"application/pdf"},
    SourceType.EXCEL: {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/vnd.ms-excel.sheet.macroEnabled.12",
    },
    SourceType.WORD: {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    },
    SourceType.CSV:  {"text/csv", "text/plain", "application/csv"},
    SourceType.XML:  {"application/xml", "text/xml"},
    SourceType.JSON: {"application/json", "text/json"},
}

# File extensions per source type (fallback when MIME is ambiguous)
ALLOWED_EXTENSIONS: dict[SourceType, set[str]] = {
    SourceType.PDF:   {".pdf"},
    SourceType.EXCEL: {".xlsx", ".xls", ".xlsm"},
    SourceType.WORD:  {".docx", ".doc"},
    SourceType.CSV:   {".csv", ".tsv"},
    SourceType.XML:   {".xml"},
    SourceType.JSON:  {".json", ".jsonl"},
}


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class IngestResult:
    ingestion_id: uuid.UUID
    dedup_hit: bool          # True if this file was already ingested before
    blob_url: str | None     # None when Azure credentials are not configured
    status: str              # "queued" | "duplicate"


# ── Validation helpers ────────────────────────────────────────────────────────


class IngestValidationError(Exception):
    """Raised when a file fails pre-ingest validation."""


def _validate_file(
    file_bytes: bytes,
    filename: str,
    source_type: SourceType,
) -> int | None:
    """
    Validate file type and size.
    Returns page_count for PDFs, None for all other types.
    Raises IngestValidationError on failure.
    """
    ext = os.path.splitext(filename.lower())[1]
    allowed_exts = ALLOWED_EXTENSIONS.get(source_type, set())
    if ext not in allowed_exts:
        raise IngestValidationError(
            f"File extension '{ext}' is not allowed for source_type '{source_type.value}'. "
            f"Allowed: {sorted(allowed_exts)}"
        )

    max_bytes = MAX_FILE_SIZES.get(source_type, 100 * 1024 * 1024)
    if len(file_bytes) > max_bytes:
        raise IngestValidationError(
            f"File size {len(file_bytes):,} bytes exceeds limit of "
            f"{max_bytes:,} bytes for source_type '{source_type.value}'"
        )

    if len(file_bytes) == 0:
        raise IngestValidationError("File is empty")

    page_count: int | None = None
    if source_type == SourceType.PDF:
        page_count = _count_pdf_pages(file_bytes)
        if page_count > MAX_PDF_PAGES:
            raise IngestValidationError(
                f"PDF has {page_count} pages, exceeding the limit of {MAX_PDF_PAGES}"
            )

    return page_count


def _count_pdf_pages(pdf_bytes: bytes) -> int:
    """
    Count PDF pages by scanning for /Type /Page markers.
    Avoids a heavy dependency — accurate enough for limit checks.
    """
    count = pdf_bytes.count(b"/Type /Page") + pdf_bytes.count(b"/Type/Page")
    return max(count, 1)  # at least 1 if we can't detect


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Blob upload ───────────────────────────────────────────────────────────────


async def _upload_to_blob(
    file_bytes: bytes,
    source_type: SourceType,
    filename: str,
    ingestion_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    connection_string: str,
    container_name: str,
) -> str:
    """
    Upload file to Azure Blob Storage.
    Path: {source_type}-raw/{tenant_id or 'default'}/{yyyy-mm}/{ingestion_id}{ext}
    Returns the blob URL.
    """
    from azure.storage.blob.aio import BlobServiceClient

    ext = os.path.splitext(filename)[1].lower()
    tenant_segment = str(tenant_id) if tenant_id else "default"
    month_segment = datetime.now(timezone.utc).strftime("%Y-%m")
    blob_name = (
        f"{source_type.value}-raw/{tenant_segment}/{month_segment}/{ingestion_id}{ext}"
    )

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    async with BlobServiceClient.from_connection_string(connection_string) as client:
        blob_client = client.get_blob_client(container=container_name, blob=blob_name)
        await blob_client.upload_blob(
            io.BytesIO(file_bytes),
            overwrite=True,
            content_settings={"content_type": mime_type},
        )
        return blob_client.url


# ── Dedup check ───────────────────────────────────────────────────────────────


async def _find_duplicate(
    db: AsyncSession,
    file_hash: str,
) -> IngestionDocument | None:
    """Returns existing IngestionDocument if this file hash was already ingested."""
    result = await db.execute(
        select(IngestionDocument)
        .where(IngestionDocument.file_hash_sha256 == file_hash)
        .where(IngestionDocument.status.in_(["queued", "extracting", "review", "accepted"]))
        .order_by(IngestionDocument.uploaded_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Core Stage 1 function ─────────────────────────────────────────────────────


async def ingest_document(
    *,
    file_bytes: bytes,
    filename: str,
    source_type: SourceType,
    agent_id: AgentId,
    tenant_id: uuid.UUID | None = None,
    uploaded_by: uuid.UUID | None = None,
    db: AsyncSession,
    arq_redis: ArqRedis,
    azure_connection_string: str = "",
    azure_container_name: str = "plenum-agentic-ai-attachments",
) -> IngestResult:
    """
    Stage 1 — Ingest a document.

    Validates the file, deduplicates, uploads to Azure Blob,
    creates the ingestion_documents record, and enqueues the extraction job.

    Args:
        file_bytes: Raw file content.
        filename: Original filename (used for extension + MIME detection).
        source_type: Detected or declared source type.
        agent_id: Agent that will process this document.
        tenant_id: Optional tenant UUID for multi-tenancy blob paths.
        uploaded_by: UUID of the user uploading the file.
        db: AsyncSession from the FastAPI DI or ARQ worker context.
        arq_redis: ARQ Redis pool for job enqueueing.
        azure_connection_string: Azure Blob connection string (empty = skip upload).
        azure_container_name: Azure Blob container name.

    Returns:
        IngestResult with ingestion_id, dedup_hit flag, blob_url, and status.

    Raises:
        IngestValidationError: If the file fails validation.
    """
    ingestion_id = uuid.uuid4()

    with tracer.start_as_current_span("ingestion.stage1.ingest") as span:
        span.set_attribute("cafm.ingestion_id", str(ingestion_id))
        span.set_attribute("cafm.source_type", source_type.value)
        span.set_attribute("cafm.agent_id", agent_id.value)
        span.set_attribute("cafm.file_size_bytes", len(file_bytes))

        try:
            # ── Step 1: Validate ──────────────────────────────────────────────
            page_count = _validate_file(file_bytes, filename, source_type)
            if page_count is not None:
                span.set_attribute("cafm.page_count", page_count)

            logger.info(
                "ingest_stage1_validated",
                ingestion_id=str(ingestion_id),
                filename=filename,
                source_type=source_type.value,
                size_bytes=len(file_bytes),
                page_count=page_count,
            )

            # ── Step 2: SHA-256 dedup check ───────────────────────────────────
            file_hash = _sha256(file_bytes)
            existing = await _find_duplicate(db, file_hash)
            dedup_hit = existing is not None
            span.set_attribute("cafm.dedup_hit", dedup_hit)

            if dedup_hit:
                logger.info(
                    "ingest_stage1_dedup_hit",
                    ingestion_id=str(existing.id),
                    file_hash=file_hash,
                    filename=filename,
                )
                documents_ingested.add(
                    1,
                    attributes={
                        "agent_id": agent_id.value,
                        "source_type": source_type.value,
                        "status": "duplicate",
                    },
                )
                span.set_status(StatusCode.OK)
                return IngestResult(
                    ingestion_id=existing.id,
                    dedup_hit=True,
                    blob_url=existing.blob_url,
                    status="duplicate",
                )

            # ── Step 3: Upload to Azure Blob ──────────────────────────────────
            blob_url: str | None = None
            if azure_connection_string:
                try:
                    blob_url = await _upload_to_blob(
                        file_bytes=file_bytes,
                        source_type=source_type,
                        filename=filename,
                        ingestion_id=ingestion_id,
                        tenant_id=tenant_id,
                        connection_string=azure_connection_string,
                        container_name=azure_container_name,
                    )
                    logger.info(
                        "ingest_stage1_blob_uploaded",
                        ingestion_id=str(ingestion_id),
                        blob_url=blob_url,
                    )
                except Exception as blob_exc:
                    # Log but don't fail — blob upload is best-effort in dev
                    logger.warning(
                        "ingest_stage1_blob_upload_failed",
                        ingestion_id=str(ingestion_id),
                        error=str(blob_exc),
                    )
            else:
                logger.warning(
                    "ingest_stage1_blob_skipped",
                    ingestion_id=str(ingestion_id),
                    reason="AZURE_STORAGE_CONNECTION_STRING not set",
                )

            # ── Step 4: Create ingestion_documents record ─────────────────────
            doc = IngestionDocument(
                id=ingestion_id,
                tenant_id=tenant_id,
                source_type=source_type.value,
                agent_id=agent_id.value,
                original_filename=filename,
                blob_url=blob_url,
                file_hash_sha256=file_hash,
                page_count=page_count,
                status="queued",
                uploaded_by=uploaded_by,
                uploaded_at=datetime.now(timezone.utc),
            )
            db.add(doc)
            await db.commit()
            await db.refresh(doc)

            logger.info(
                "ingest_stage1_record_created",
                ingestion_id=str(ingestion_id),
                status="queued",
            )

            # ── Step 5: Enqueue extraction job ────────────────────────────────
            await arq_redis.enqueue_job(
                "extract_document",
                str(ingestion_id),
                _queue_name="cafm:ingestion:queue",
            )

            logger.info(
                "ingest_stage1_job_enqueued",
                ingestion_id=str(ingestion_id),
                agent_id=agent_id.value,
            )

            documents_ingested.add(
                1,
                attributes={
                    "agent_id": agent_id.value,
                    "source_type": source_type.value,
                    "status": "queued",
                },
            )

            span.set_status(StatusCode.OK)
            return IngestResult(
                ingestion_id=ingestion_id,
                dedup_hit=False,
                blob_url=blob_url,
                status="queued",
            )

        except IngestValidationError as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            logger.warning(
                "ingest_stage1_validation_failed",
                ingestion_id=str(ingestion_id),
                filename=filename,
                error=str(exc),
            )
            raise

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            logger.error(
                "ingest_stage1_failed",
                ingestion_id=str(ingestion_id),
                filename=filename,
                error=str(exc),
            )
            await db.rollback()
            raise
