"""Azure Blob upload helper for Energy monthly PDF (and future Feature C artefacts)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


async def upload_bytes(
    data: bytes,
    *,
    blob_name: str,
    content_type: str = "application/pdf",
) -> str | None:
    """
    Upload to Azure Blob when connection string is configured.
    Returns blob URL, or None if Azure is not configured / upload fails.
    """
    conn = (settings.azure_storage_connection_string or "").strip()
    if not conn:
        return None
    try:
        from azure.storage.blob.aio import BlobServiceClient
    except ImportError:
        log.warning("energy.blob.azure_sdk_missing")
        return None

    container = settings.azure_blob_container_name
    try:
        async with BlobServiceClient.from_connection_string(conn) as client:
            bc = client.get_blob_client(container=container, blob=blob_name)
            await bc.upload_blob(
                data,
                overwrite=True,
                content_settings={"content_type": content_type},
            )
            return bc.url
    except Exception as exc:  # noqa: BLE001
        log.warning("energy.blob.upload_failed", error=str(exc)[:300])
        return None


async def upload_energy_pdf(local_path: Path, *, site_id: str, report_month: str) -> str:
    """
    Prefer Azure Blob URL; fall back to local path string for Saved Space download.
    """
    data = local_path.read_bytes()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    blob_name = f"energy-reports/{month}/{site_id}_{report_month}_{uuid4().hex[:8]}.pdf"
    url = await upload_bytes(data, blob_name=blob_name, content_type="application/pdf")
    if url:
        return url
    return str(local_path)
