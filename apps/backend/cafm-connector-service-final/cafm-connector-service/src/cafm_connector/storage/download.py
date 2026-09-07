"""
Blob download helper for file-based connectors.

When a connector's file_path is an Azure Blob URL (starts with https://),
this module downloads the file to a local temp path so the connector can
read it normally with pandas / lxml / pyarrow etc.

The caller is responsible for deleting the temp file after use.
"""

from __future__ import annotations

import os
import tempfile

from cafm_connector.core.logging import get_logger

logger = get_logger(__name__)

# File extensions we support for blob downloads
_BLOB_SCHEMES = ("https://", "http://")


def is_blob_url(file_path: str) -> bool:
    """Return True if the path is a remote URL rather than a local filesystem path."""
    return any(file_path.startswith(s) for s in _BLOB_SCHEMES)


def download_to_temp(blob_url: str) -> str:
    """
    Download a file from a URL (Azure Blob or any HTTPS URL) to a
    local temp file.  Returns the temp file path.

    The caller MUST delete the file when done:
        path = download_to_temp(url)
        try:
            ...use path...
        finally:
            os.unlink(path)
    """
    import urllib.request

    # Derive a sensible suffix from the URL
    url_path = blob_url.split("?")[0]   # strip SAS token query params
    ext = os.path.splitext(url_path)[1].lower() or ".tmp"

    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        logger.debug("blob_download_start", url=blob_url[:80], tmp=tmp_path)
        urllib.request.urlretrieve(blob_url, tmp_path)
        size = os.path.getsize(tmp_path)
        logger.info("blob_download_complete", size_bytes=size, tmp=tmp_path)
        return tmp_path
    except Exception as exc:
        # Clean up on failure
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to download blob '{blob_url[:80]}': {exc}") from exc
