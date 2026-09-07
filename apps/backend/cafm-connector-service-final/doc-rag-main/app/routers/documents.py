"""Documents router — upload, list, inspect, delete."""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import settings
from app.core.logger import logger
from app.db.models import Document, DocumentChunk
from app.db.session import SessionLocal, get_db
from app.schemas import ChunkPreview, DocumentOut, DocumentUploadResponse
from app.services.chunker import chunker
from app.services.document_classifier import document_classifier
from app.db.embedding_utils import coerce_embedding
from app.services.blob_storage import guess_mime, upload_document_blob
from app.services.embedding_service import embedding_service
from app.services.extraction_service import extraction_service

router = APIRouter(prefix="/documents", tags=["documents"])


def _run_ingestion_pipeline(document_id: str, dest_path: Path, filename: str) -> None:
    """Run extract → classify → chunk → embed in a background thread.

    Keeps the upload HTTP request short so gateways (Azure ACA ~240s, nginx) do not 504.
    """
    start = time.time()
    db = SessionLocal()
    try:
        doc_row = db.query(Document).filter(Document.id == document_id).first()
        if not doc_row:
            logger.error("Ingestion background: document not found | id={}", document_id)
            return

        extracted = extraction_service.extract(dest_path, document_id=doc_row.id)
        logger.info(
            "Ingestion stage=extract | doc_id={} | pages={} | images={} | chars={} | ms={}",
            doc_row.id,
            extracted.num_pages,
            extracted.num_images,
            len(extracted.full_text),
            int((time.time() - start) * 1000),
        )

        doc_type, doc_confidence = document_classifier.classify(extracted)
        logger.info(
            "Ingestion stage=classify | doc_id={} | type={} | confidence={:.3f}",
            doc_row.id,
            doc_type,
            doc_confidence,
        )

        doc_row.mime_type = extracted.mime_type
        doc_row.document_type = doc_type
        doc_row.num_pages = extracted.num_pages
        doc_row.status = "processing"
        db.commit()

        chunks = chunker.chunk(extracted)
        texts = [c.text_content for c in chunks]
        embeddings = embedding_service.embed_batch(texts) if texts else []

        for ch, emb in zip(chunks, embeddings, strict=False):
            db.add(
                DocumentChunk(
                    document_id=doc_row.id,
                    page_start=ch.page_start,
                    page_end=ch.page_end,
                    chunk_index=ch.chunk_index,
                    block_type=ch.block_type,
                    section_label=ch.section_label,
                    text_content=ch.text_content,
                    normalized_text=ch.normalized_text,
                    meta=ch.meta,
                    embedding=coerce_embedding(emb),
                    embedding_model=embedding_service.model
                    if not embedding_service.mock
                    else "mock",
                )
            )

        doc_row.status = "indexed"
        db.commit()
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "Ingestion complete | doc_id={} | file={} | pages={} | chunks={} | total_ms={}",
            doc_row.id,
            filename,
            extracted.num_pages,
            len(chunks),
            elapsed_ms,
        )
    except StaleDataError:
        # The ingestion_documents row was deleted by another process (e.g. a compliance
        # vector-membership Remove) while this background pipeline was still running.
        # That is an intentional abort, not an ingestion failure — do not error the doc
        # (it no longer exists) and do not raise a scary traceback.
        db.rollback()
        logger.warning(
            "Ingestion aborted: document {} (doc_id={}) was removed during ingestion "
            "(concurrent delete) — skipping.",
            filename,
            document_id,
        )
    except Exception as e:
        db.rollback()
        logger.exception("Ingestion failed for {} (doc_id={}): {}", filename, document_id, e)
        try:
            doc_row = db.query(Document).filter(Document.id == document_id).first()
            if doc_row:
                doc_row.status = "error"
                db.commit()
        except Exception:
            logger.exception("Failed to mark document error | id={}", document_id)
    finally:
        db.close()


def _run_batch_ingestion_pipeline(specs: list[tuple]) -> None:
    """Ingest MULTIPLE documents as ONE batch (7.1 AC1) with a SINGLE consolidated
    embedding call across every chunk of every document (7.1 AC4) — instead of one
    embed pass per file. ``specs`` is ``[(document_id, dest_path, filename), ...]``.
    """
    start = time.time()
    db = SessionLocal()
    try:
        # 1. extract → classify → chunk EACH document; collect every chunk, tagged by doc.
        chunk_specs: list[tuple] = []   # (document_id, Chunk)
        ok_docs: dict[str, object] = {}
        for document_id, dest_path, filename in specs:
            doc_row = db.query(Document).filter(Document.id == document_id).first()
            if not doc_row:
                continue
            try:
                extracted = extraction_service.extract(dest_path, document_id=doc_row.id)
                doc_type, _conf = document_classifier.classify(extracted)
                doc_row.mime_type = extracted.mime_type
                doc_row.document_type = doc_type
                doc_row.num_pages = extracted.num_pages
                doc_row.status = "processing"
                for ch in chunker.chunk(extracted):
                    chunk_specs.append((doc_row.id, ch))
                ok_docs[doc_row.id] = doc_row
            except Exception as e:
                logger.exception("Batch ingest: extract/chunk failed for {}: {}", filename, e)
                doc_row.status = "error"
        db.commit()

        # 2. ONE embedding call across ALL chunks of ALL documents (7.1 AC4 — LLM/API
        #    calls are consolidated across the batch, not multiplied per file).
        texts = [ch.text_content for (_id, ch) in chunk_specs]
        embeddings = embedding_service.embed_batch(texts) if texts else []

        # 3. persist every chunk.
        for (doc_id, ch), emb in zip(chunk_specs, embeddings, strict=False):
            db.add(
                DocumentChunk(
                    document_id=doc_id,
                    page_start=ch.page_start,
                    page_end=ch.page_end,
                    chunk_index=ch.chunk_index,
                    block_type=ch.block_type,
                    section_label=ch.section_label,
                    text_content=ch.text_content,
                    normalized_text=ch.normalized_text,
                    meta=ch.meta,
                    embedding=coerce_embedding(emb),
                    embedding_model=embedding_service.model if not embedding_service.mock else "mock",
                )
            )
        for doc_row in ok_docs.values():
            doc_row.status = "indexed"
        db.commit()
        logger.info(
            "Batch ingestion complete | docs={} | chunks={} | one_embed_call={} | total_ms={}",
            len(ok_docs), len(chunk_specs), bool(texts), int((time.time() - start) * 1000),
        )
    except StaleDataError:
        # One or more documents in the batch were deleted (e.g. a compliance membership
        # Remove) mid-ingest, poisoning the shared commit. A concurrent delete is not a
        # batch failure: roll back, then re-drive only the documents that still exist so
        # the survivors can be picked up again instead of the whole batch erroring.
        db.rollback()
        logger.warning(
            "Batch ingestion: a document was removed mid-batch (concurrent delete) — "
            "resetting survivors for retry."
        )
        try:
            for document_id, _dp, _fn in specs:
                row = db.query(Document).filter(Document.id == document_id).first()
                if row is not None and row.status not in ("indexed", "error"):
                    row.status = "pending"
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Batch ingestion: survivor reset failed")
    except Exception as e:
        db.rollback()
        logger.exception("Batch ingestion failed: {}", e)
        try:
            for document_id, _dp, _fn in specs:
                row = db.query(Document).filter(Document.id == document_id).first()
                if row and row.status != "indexed":
                    row.status = "error"
            db.commit()
        except Exception:
            logger.exception("Failed to mark batch docs error")
    finally:
        db.close()


async def _accept_one_upload(file: UploadFile, db: Session):
    """Validate + save one upload + create its Document row. Returns
    ``(spec, error)`` where exactly one is non-None: ``spec = (document_id, dest_path,
    filename)`` on success, else ``error = {file_name, error}``."""
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    if ext not in extraction_service.SUPPORTED_EXTENSIONS:
        await file.close()
        return None, {"file_name": filename, "error": f"Unsupported file type '{ext}'"}

    mime_type = guess_mime(filename, file.content_type)
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest_path = upload_dir / f"{uuid.uuid4().hex}{ext}"
    try:
        with dest_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        await file.close()

    # 7.1 AC3 — 100 MB per-file limit for unstructured documents.
    _max_mb = int(getattr(settings, "max_unstructured_mb", 100) or 100)
    if dest_path.stat().st_size / (1024 * 1024) > _max_mb:
        dest_path.unlink(missing_ok=True)
        return None, {"file_name": filename, "error": f"File exceeds {_max_mb}MB limit"}

    blob_url = None
    try:
        blob_url = upload_document_blob(dest_path.read_bytes(), filename, mime_type)
    except Exception as e:
        logger.warning("Blob storage skipped for {}: {}", filename, e)

    doc_row = Document(
        file_name=filename, mime_type=mime_type, document_type=None,
        source_uri=str(dest_path), blob_url=blob_url, status="extracting", num_pages=0,
    )
    db.add(doc_row)
    db.commit()
    db.refresh(doc_row)
    return (doc_row.id, dest_path, filename), None


@router.post("/upload-batch")
async def upload_documents_batch(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Ingest MANY documents as a SINGLE batch (7.1 AC1) — all chunks are embedded in one
    consolidated pass (7.1 AC4). Returns the document ids immediately; poll each until
    ``indexed``. Bad files are skipped and reported under ``skipped`` rather than failing
    the whole batch."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    specs: list[tuple] = []
    accepted: list[dict] = []
    skipped: list[dict] = []
    for file in files:
        spec, err = await _accept_one_upload(file, db)
        if err is not None:
            skipped.append(err)
            continue
        specs.append(spec)
        accepted.append({"document_id": spec[0], "file_name": spec[2], "status": "extracting"})

    if specs:
        background_tasks.add_task(_run_batch_ingestion_pipeline, specs)
    logger.info("Batch ingestion queued | accepted={} | skipped={}", len(accepted), len(skipped))
    return {"documents": accepted, "count": len(accepted), "skipped": skipped}


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Accept a file, enqueue ingestion, and return immediately.

    Poll GET /documents/{document_id} until status is ``indexed`` or ``error``.
    """
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    if ext not in extraction_service.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. "
            f"Supported: {sorted(extraction_service.SUPPORTED_EXTENSIONS)}",
        )

    # Capture before the stream is consumed/closed below.
    upload_content_type = file.content_type
    mime_type = guess_mime(filename, upload_content_type)

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = upload_dir / unique_name
    try:
        with dest_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        logger.exception("Failed to write upload: {}", e)
        raise HTTPException(status_code=500, detail="Failed to save upload")
    finally:
        await file.close()

    logger.info("File saved | name={} | path={}", filename, dest_path)

    # 7.1 AC3 — unstructured documents are limited to 100 MB per file (configurable).
    _max_unstructured_mb = int(getattr(settings, "max_unstructured_mb", 100) or 100)
    _size_mb = dest_path.stat().st_size / (1024 * 1024)
    if _size_mb > _max_unstructured_mb:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({_size_mb:.1f} MB). Unstructured documents are limited to "
                f"{_max_unstructured_mb} MB per file."
            ),
        )

    # Persist the ORIGINAL to Azure Blob so the chat can link to it for
    # view/download (Word stays Word, PDF stays PDF). Best-effort: on any
    # failure blob_url stays null and the extracted-text fallback applies.
    blob_url = None
    try:
        blob_url = upload_document_blob(dest_path.read_bytes(), filename, mime_type)
    except Exception as e:  # never block ingestion on storage
        logger.warning("Blob storage skipped for {}: {}", filename, e)

    doc_row = Document(
        file_name=filename,
        mime_type=mime_type,
        document_type=None,
        source_uri=str(dest_path),
        blob_url=blob_url,
        status="extracting",
        num_pages=0,
    )
    db.add(doc_row)
    db.commit()
    db.refresh(doc_row)

    background_tasks.add_task(_run_ingestion_pipeline, doc_row.id, dest_path, filename)
    logger.info("Ingestion queued | doc_id={} | file={}", doc_row.id, filename)

    return DocumentUploadResponse(
        document_id=doc_row.id,
        status=doc_row.status,
        file_name=filename,
        num_pages=0,
        num_chunks=0,
        document_type=None,
        processing_time_ms=0,
    )


@router.post("/backfill-blobs")
def backfill_document_blobs(limit: int = 200, db: Session = Depends(get_db)):
    """Best-effort backfill: upload originals still on local disk for documents
    whose ``blob_url`` is null, so previously-ingested documents become
    viewable/downloadable from Azure Blob too. Idempotent — only touches rows
    with a null blob_url and an existing local file. Safe to call repeatedly."""
    rows = (
        db.query(Document)
        .filter(Document.blob_url.is_(None))
        .order_by(Document.created_at.desc())
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    stored = 0
    skipped_no_file = 0
    for d in rows:
        path = Path(d.source_uri) if d.source_uri else None
        if not path or not path.exists():
            skipped_no_file += 1
            continue
        try:
            url = upload_document_blob(path.read_bytes(), d.file_name, d.mime_type)
        except Exception as e:  # never fatal
            logger.warning("Backfill upload failed | id={} | {}", d.id, e)
            url = None
        if url:
            d.blob_url = url
            if not d.mime_type:
                d.mime_type = guess_mime(d.file_name)
            stored += 1
    if stored:
        db.commit()
    logger.info(
        "Blob backfill | checked={} | stored={} | no_local_file={}",
        len(rows), stored, skipped_no_file,
    )
    return {"checked": len(rows), "stored": stored, "skipped_no_local_file": skipped_no_file}


@router.get("", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    docs = db.query(Document).order_by(Document.created_at.desc()).all()
    results: list[DocumentOut] = []
    for d in docs:
        n = db.query(DocumentChunk).filter(DocumentChunk.document_id == d.id).count()
        results.append(DocumentOut(
            id=d.id, file_name=d.file_name, mime_type=d.mime_type,
            document_type=d.document_type, status=d.status,
            num_pages=d.num_pages, num_chunks=n, created_at=d.created_at,
        ))
    return results


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Session = Depends(get_db)):
    d = db.query(Document).filter(Document.id == document_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Document not found")
    n = db.query(DocumentChunk).filter(DocumentChunk.document_id == d.id).count()
    return DocumentOut(
        id=d.id, file_name=d.file_name, mime_type=d.mime_type,
        document_type=d.document_type, status=d.status,
        num_pages=d.num_pages, num_chunks=n, created_at=d.created_at,
    )


@router.get("/{document_id}/chunks", response_model=list[ChunkPreview])
def get_document_chunks(document_id: str, limit: int = 50, db: Session = Depends(get_db)):
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .limit(limit)
        .all()
    )
    return [
        ChunkPreview(
            chunk_index=c.chunk_index,
            page_start=c.page_start,
            page_end=c.page_end,
            block_type=c.block_type,
            section_label=c.section_label,
            text_content=c.text_content,
            meta=c.meta,
        )
        for c in chunks
    ]


@router.get("/{document_id}/pages/{page}/debug")
def debug_page(document_id: str, page: int, db: Session = Depends(get_db)):
    """Everything the extractor saw for one page.

    Use this when a query returns poor citations for a specific page.
    It shows every chunk on that page grouped by block_type, and for
    image chunks it exposes the RAW vision response — so you can tell
    whether the model saw the page, whether it classified it as a
    table, whether it returned structured rows, and if not, why.

    Typical debug flow:
      1. Run a query, note a bad citation's document_id and page.
      2. GET /documents/{doc_id}/pages/{page}/debug
      3. If `image_chunks[].vision_image_type == "table"` and
         `vision_injected_table_count == 0`, the vision prompt failed —
         inspect `vision_raw_response` to see what the model actually
         returned.
      4. If there are no image_chunks at all, the image was not
         extracted from the PDF — likely a compressed / flattened PDF
         and the page needs full-page rendering.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    chunks = (
        db.query(DocumentChunk)
        .filter(
            DocumentChunk.document_id == document_id,
            DocumentChunk.page_start == page,
        )
        .order_by(DocumentChunk.chunk_index)
        .all()
    )

    def to_dict(c: DocumentChunk) -> dict:
        return {
            "chunk_id": c.id,
            "chunk_index": c.chunk_index,
            "block_type": c.block_type,
            "section_label": c.section_label,
            "text_content": c.text_content,
            "text_length": len(c.text_content or ""),
            "meta": c.meta,
        }

    by_type: dict[str, list[dict]] = {}
    for c in chunks:
        by_type.setdefault(c.block_type, []).append(to_dict(c))

    image_chunks = by_type.get("image", [])
    table_row_chunks = by_type.get("table_row", [])

    # Diagnose: was vision asked to extract tables, and did it succeed?
    diagnosis = []
    if not image_chunks and not table_row_chunks:
        diagnosis.append(
            "No image or table_row chunks on this page. Either the page "
            "contained no images / tables, or the image was not surfaced "
            "by pypdf (likely a compressed/flattened PDF). Consider "
            "re-ingesting with full-page rendering enabled."
        )
    for ic in image_chunks:
        m = ic.get("meta") or {}
        vtype = m.get("vision_image_type") or ""
        injected = m.get("vision_injected_table_count", 0)
        if vtype == "table" and injected == 0:
            diagnosis.append(
                f"Image chunk_index={ic['chunk_index']}: vision classified "
                f"the image as TABLE but returned no structured rows. "
                f"This is a vision prompt failure. See vision_raw_response "
                f"in the chunk's meta to inspect what the model returned."
            )
        elif vtype == "" and m.get("extraction_method") == "vision":
            diagnosis.append(
                f"Image chunk_index={ic['chunk_index']}: vision was called "
                f"but returned no image_type classification — possibly an "
                f"older response that pre-dates the structured prompt."
            )

    return {
        "document_id": document_id,
        "file_name": doc.file_name,
        "document_type": doc.document_type,
        "page": page,
        "chunk_count": len(chunks),
        "chunk_counts_by_type": {k: len(v) for k, v in by_type.items()},
        "chunks_by_type": by_type,
        "diagnosis": diagnosis or ["No obvious problems detected on this page."],
    }


@router.delete("/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    d = db.query(Document).filter(Document.id == document_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(d)
    db.commit()
    logger.info("Document deleted | id={}", document_id)
    return {"status": "deleted", "document_id": document_id}
