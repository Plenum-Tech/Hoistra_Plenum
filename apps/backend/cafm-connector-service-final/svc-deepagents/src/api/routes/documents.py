"""
Document access router — open / download the documents referenced in answers.

`get_asset_documents` cites each linked document as `doc:<id>`; the frontend
resolves that to `GET /api/documents/<id>/download`. This guarantees a working
link even when the original binary was never stored:

  - ingestion_documents.blob_url  → redirect to the stored original file.
  - document_chunks               → stream the extracted text as a .txt, so the
                                    content is accessible with no original kept.
  - plenum_cafm.documents.blob_url → redirect; the graph's own table, which is
                                    where a file lands when doc-rag never indexed
                                    it and where the test portfolio keeps all of
                                    them. Asked last because the ingestion row
                                    carries the filename the uploader used.

All three, because building_tree._openable reads all three when it decides whether
to render an open control — and a route that answered a narrower question put a
live-looking link on rows that replied "Document not found".
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

        filename = ((row or {}).get("file_name") or f"document-{document_id}").strip()
        blob_url = ((row or {}).get("blob_url") or "").strip()

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
            # The graph's own table, asked last and only when nothing above answered.
            #
            # A document has two homes. doc-rag writes ingestion_documents for what it
            # indexed; the graph writes plenum_cafm.documents for what a certificate, a
            # contract or an invoice reaches a building through — and the test portfolio
            # keeps its files there and nowhere else. This route read only the first, so a
            # row with a perfectly good blob_url in the second answered "Document not
            # found". building_tree._openable reads BOTH when it decides whether to show an
            # open control, and says in its docstring that it mirrors this route, so every
            # such row rendered a live link straight onto a 404.
            #
            # Asked last because the ingestion row is the richer record: it carries the
            # filename the uploader actually used, where the graph row may carry none.
            try:
                graph = (
                    await session.execute(
                        text(
                            "SELECT file_name, blob_url FROM plenum_cafm.documents "
                            "WHERE document_id::text = :id"
                        ),
                        {"id": str(doc_uuid)},
                    )
                ).mappings().first()
            except Exception as exc:  # pragma: no cover - defensive
                log.error("documents.download.graph_lookup_error", error=str(exc)[:200])
                graph = None
            graph_url = ((graph or {}).get("blob_url") or "").strip()
            if graph_url:
                return RedirectResponse(url=graph_url, status_code=307)
            if not row and not graph:
                raise HTTPException(status_code=404, detail="Document not found")
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
