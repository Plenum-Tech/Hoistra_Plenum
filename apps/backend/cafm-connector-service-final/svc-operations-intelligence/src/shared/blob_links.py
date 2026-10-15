"""Short-lived signed links to blobs, and credentialed reads of blob URLs.

The attachments container was readable - and listable - by anyone with its address (access level
`container`, found 5 Oct 2026). Everything the platform hands a browser is now a read-only SAS link
that expires in minutes, and every server-side read of a stored blob URL goes through the account's
credentials, so the container can be private. A URL that is not one of this account's blobs, or a
deployment with no account key, comes back unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

#: How long a link handed to a browser works. Long enough to click, short enough not to be a share.
LINK_MINUTES = 15


def _parts(conn: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for kv in (conn or "").split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def split_blob_url(url: str) -> tuple[str, str, str] | None:
    """(account, container, blob) for an https://<account>.blob.core.windows.net/<container>/<blob> URL."""
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return None
    if u.scheme not in ("https", "http") or not u.netloc.endswith(".blob.core.windows.net"):
        return None
    path = unquote(u.path.lstrip("/"))
    if "/" not in path:
        return None
    container, blob = path.split("/", 1)
    return u.netloc.split(".", 1)[0], container, blob


def signed_url(url: Any, conn: str, *, minutes: int = LINK_MINUTES, now: datetime | None = None) -> Any:
    """`url` with a read-only SAS valid for `minutes`, or `url` unchanged when it is not this
    account's blob or no key is configured. A URL that already carries a query string is re-signed
    on its path (an old SAS is dropped)."""
    bits = split_blob_url(url) if isinstance(url, str) else None
    p = _parts(conn)
    if not bits or not p.get("AccountKey") or bits[0] != p.get("AccountName"):
        return url
    account, container, blob = bits
    try:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas
    except ImportError:
        return url
    t = now or datetime.now(timezone.utc)
    sas = generate_blob_sas(account_name=account, container_name=container, blob_name=blob, account_key=p["AccountKey"],
                            permission=BlobSasPermissions(read=True), start=t - timedelta(minutes=2), expiry=t + timedelta(minutes=minutes))
    return str(url).split("?", 1)[0] + "?" + sas


async def read_blob_url(url: str, conn: str) -> bytes:
    """The bytes behind a stored blob URL, read with the account's credentials (not anonymously)."""
    from azure.storage.blob.aio import BlobClient, BlobServiceClient

    bits = split_blob_url(url)
    if conn and bits:
        async with BlobServiceClient.from_connection_string(conn) as svc:
            stream = await svc.get_blob_client(container=bits[1], blob=bits[2]).download_blob()
            return await stream.readall()
    # a SAS URL (it carries its own credential) or no connection string: as given
    async with BlobClient.from_blob_url(url) as bc:
        stream = await bc.download_blob()
        return await stream.readall()
