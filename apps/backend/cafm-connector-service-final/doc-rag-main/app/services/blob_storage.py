"""Azure Blob Storage for original uploaded documents (Word / PDF / Excel / …).

Documents are stored so the chat can link to the ORIGINAL file for view /
download — a Word doc stays a Word doc, a PDF stays a PDF. The stored URL goes
into ``ingestion_documents.blob_url`` and the document-download endpoint
redirects the browser to it (same pattern as migration import files).

Degrades gracefully: when ``AZURE_STORAGE_CONNECTION_STRING`` is not configured
(or the SDK is missing / the upload fails), ``upload_document_blob`` returns
``None`` and the caller leaves ``blob_url`` null — the extracted-text fallback
still serves the content, so ingestion never breaks because of storage.
"""
from __future__ import annotations

import mimetypes
import os
import uuid

from app.core.config import settings
from app.core.logger import logger

# Original-format MIME types so the browser/Office opens the file correctly.
_EXT_MIME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".json": "application/json",
    ".xml": "application/xml",
}


def guess_mime(filename: str, fallback: str | None = None) -> str:
    """Best-effort MIME type from the filename extension."""
    ext = os.path.splitext(filename or "")[1].lower()
    return (
        _EXT_MIME.get(ext)
        or (fallback or "").strip()
        or mimetypes.guess_type(filename or "")[0]
        or "application/octet-stream"
    )


def upload_document_blob(
    file_bytes: bytes,
    filename: str,
    content_type: str | None = None,
) -> str | None:
    """Upload original file bytes to Azure Blob; return the blob URL, or ``None``.

    Never raises — a storage failure must not break document ingestion.
    """
    conn = (settings.azure_storage_connection_string or "").strip()
    if not conn:
        return None
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
    except Exception as exc:  # SDK not installed
        logger.warning("azure-storage-blob not available; storing locally only: {}", exc)
        return None
    try:
        safe = (filename or "document").replace(" ", "_")
        blob_name = f"{settings.azure_blob_docs_prefix}{uuid.uuid4().hex}_{safe}"
        mime = guess_mime(filename, content_type)
        client = BlobServiceClient.from_connection_string(conn)
        blob_client = client.get_container_client(
            settings.azure_blob_container_name
        ).get_blob_client(blob_name)
        blob_client.upload_blob(
            file_bytes,
            overwrite=True,
            content_settings=ContentSettings(content_type=mime),
        )
        logger.info(
            "Document stored in Azure Blob | name={} | size={} | mime={}",
            blob_name,
            len(file_bytes),
            mime,
        )
        return blob_client.url
    except Exception as exc:  # keep local copy; leave blob_url null
        logger.warning("Blob upload failed (keeping local copy): {}", exc)
        return None
