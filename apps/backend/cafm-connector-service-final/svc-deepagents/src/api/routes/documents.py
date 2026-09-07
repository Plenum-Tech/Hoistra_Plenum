"""
Document access router — open / download the documents referenced in answers.

`get_asset_documents` cites each linked document as `doc:<id>`; the frontend
resolves that to `GET /api/documents/<id>/download`. This guarantees a working
link even when the original binary was never stored:

  - blob_url present  → redirect to the stored original file.
  - blob_url missing  → stream the extracted text (from document_chunks) as a
                        .txt so the content is always accessible.
"""
import io
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import text

from ... import database

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/documents", tags=["Documents"])


def _content_disposition(filename: str) -> str:
    # Quote-escape so odd filenames can't break the header.
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return f'attachment; filename="{safe}"'


@router.get("/{document_id}/download")
async def download_document(document_id: str):
    """Open / download an ingested document by id (blob original or extracted text)."""
    try:
        doc_uuid = UUID(document_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document id")

    async with database.AsyncSessionLocal() as session:
        try:
            row = (
                await session.execute(
                    text(
                        "SELECT original_filename AS file_name, blob_url "
                        "FROM plenum_cafm.ingestion_documents WHERE id::text = :id"
                    ),
                    {"id": str(doc_uuid)},
                )
            ).mappings().first()
        except Exception as exc:  # pragma: no cover - defensive
            log.error("documents.download.lookup_error", error=str(exc)[:200])
            raise HTTPException(status_code=500, detail="Document lookup failed")

        if not row:
            raise HTTPException(status_code=404, detail="Document not found")

        filename = (row.get("file_name") or f"document-{document_id}").strip()
        blob_url = (row.get("blob_url") or "").strip()

        # Original file in blob storage → hand the browser straight to it.
        if blob_url:
            return RedirectResponse(url=blob_url, status_code=307)

        # No stored original → serve the extracted text so the content is still
        # downloadable (Feature: documents must always be accessible).
        try:
            chunks = (
                await session.execute(
                    text(
                        "SELECT chunk_text FROM plenum_cafm.document_chunks "
                        "WHERE ingestion_id::text = :id ORDER BY chunk_index"
                    ),
                    {"id": str(doc_uuid)},
                )
            ).scalars().all()
        except Exception as exc:  # pragma: no cover - defensive
            log.error("documents.download.chunks_error", error=str(exc)[:200])
            raise HTTPException(status_code=500, detail="Could not read document content")

        body = "\n\n".join(c for c in chunks if c)
        if not body:
            raise HTTPException(
                status_code=404,
                detail="No downloadable content for this document (no stored file or extracted text).",
            )

        txt_name = filename if filename.lower().endswith(".txt") else f"{filename}.txt"
        data = body.encode("utf-8")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": _content_disposition(txt_name),
                "Content-Length": str(len(data)),
                "X-Document-Source": "extracted-text",
            },
        )
