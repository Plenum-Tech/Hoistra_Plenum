"""
Azure Blob Storage backend for uploaded import files.

Uploaded files (CSV, Excel, JSON, XML, Parquet) are persisted here so
the import worker can download them later when the job actually runs.

Usage:
    blob = AzureBlobStorage(settings)
    result = await blob.upload(file_bytes, original_filename, content_type)
    # result.blob_url  → stored in DB + connector connection_params["file_path"]
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cafm_connector.core.config import Settings
from cafm_connector.core.logging import get_logger

logger = get_logger(__name__)

# Allowed extensions for file-based connectors
ALLOWED_EXTENSIONS: set[str] = {".csv", ".xlsx", ".xls", ".json", ".xml", ".parquet"}


@dataclass
class BlobUploadResult:
    blob_name: str       # full path inside the container, e.g. "cafm-imports/uuid_filename.csv"
    blob_url: str        # https://plenumstorage.blob.core.windows.net/container/blob_name
    original_filename: str
    content_type: str | None
    file_extension: str
    file_size_bytes: int


class AzureBlobStorage:
    """
    Wraps azure-storage-blob BlobServiceClient.
    All uploads go to:
        <container>/<uploads_prefix><uuid>_<original_filename>
    """

    def __init__(self, settings: Settings) -> None:
        self._connection_string = settings.azure_storage_connection_string
        self._container = settings.azure_blob_container_name
        self._prefix = settings.azure_blob_uploads_prefix  # e.g. "cafm-imports/"

    def _get_client(self):
        """Lazily import and return a BlobServiceClient."""
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as e:
            raise RuntimeError(
                "Install 'azure-storage-blob' to use Azure Blob Storage: "
                "pip install azure-storage-blob"
            ) from e
        return BlobServiceClient.from_connection_string(self._connection_string)

    def _build_blob_name(self, original_filename: str) -> str:
        """Generate a unique blob name to avoid collisions."""
        ext = os.path.splitext(original_filename)[1].lower()
        unique_id = uuid.uuid4().hex
        safe_name = original_filename.replace(" ", "_")
        return f"{self._prefix}{unique_id}_{safe_name}"

    async def upload(
        self,
        file_bytes: bytes,
        original_filename: str,
        content_type: str | None = None,
    ) -> BlobUploadResult:
        """
        Upload file bytes to Azure Blob Storage.
        Returns a BlobUploadResult with the public blob URL.
        """
        ext = os.path.splitext(original_filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"File type '{ext}' not allowed. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        blob_name = self._build_blob_name(original_filename)

        try:
            client = self._get_client()
            container_client = client.get_container_client(self._container)
            blob_client = container_client.get_blob_client(blob_name)

            blob_client.upload_blob(
                file_bytes,
                overwrite=True,
                content_settings=_build_content_settings(content_type, ext),
            )

            blob_url = blob_client.url
            file_size = len(file_bytes)

            logger.info(
                "blob_uploaded",
                blob_name=blob_name,
                size_bytes=file_size,
                original=original_filename,
            )

            return BlobUploadResult(
                blob_name=blob_name,
                blob_url=blob_url,
                original_filename=original_filename,
                content_type=content_type,
                file_extension=ext,
                file_size_bytes=file_size,
            )

        except Exception as exc:
            logger.error("blob_upload_failed", error=str(exc), filename=original_filename)
            raise RuntimeError(f"Azure Blob upload failed: {exc}") from exc

    async def download(self, blob_name: str) -> bytes:
        """Download blob content as bytes (used by the import worker)."""
        try:
            client = self._get_client()
            blob_client = client.get_container_client(self._container).get_blob_client(blob_name)
            return blob_client.download_blob().readall()
        except Exception as exc:
            raise RuntimeError(f"Azure Blob download failed for '{blob_name}': {exc}") from exc

    async def delete(self, blob_name: str) -> None:
        """Delete a blob — called if the connector/upload record is removed."""
        try:
            client = self._get_client()
            blob_client = client.get_container_client(self._container).get_blob_client(blob_name)
            blob_client.delete_blob(delete_snapshots="include")
            logger.info("blob_deleted", blob_name=blob_name)
        except Exception as exc:
            logger.warning("blob_delete_failed", blob_name=blob_name, error=str(exc))


# ── helpers ────────────────────────────────────────────────────────────

def _build_content_settings(content_type: str | None, ext: str):
    """Build Azure ContentSettings based on content_type or extension."""
    try:
        from azure.storage.blob import ContentSettings
    except ImportError:
        return None

    _EXT_MIME = {
        ".csv":     "text/csv",
        ".xlsx":    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls":     "application/vnd.ms-excel",
        ".json":    "application/json",
        ".xml":     "application/xml",
        ".parquet": "application/octet-stream",
    }
    mime = content_type or _EXT_MIME.get(ext, "application/octet-stream")
    return ContentSettings(content_type=mime)


def get_blob_storage(settings: Settings) -> AzureBlobStorage:
    """Factory — returns an AzureBlobStorage instance."""
    return AzureBlobStorage(settings)
