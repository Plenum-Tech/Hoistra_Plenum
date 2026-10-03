"""The file behind a certificate, read out of Azure Blob with the storage account's own key.

The vault's download icon used to send the browser to `blob_url` itself. That only works
because the `plenum-agentic-ai-attachments` container allows anonymous reads — on 25 Sep 2026
an unauthenticated request listed its contents — so every certificate, contract and invoice
in it is public to anyone holding (or listing) the URL. Downloading through the service
instead means the caller's scope is checked first, and the container can be made private
without breaking a single download.

Resolution order for the file, stopping at the first document row found:
  1. ingestion_documents  for document_id / source_document_id / raw_metadata.graph_document_id
  2. plenum_cafm.documents for the same ids
A document row is used only when it belongs to the certificate's own company — a
certificate is client-writable, so the ids on it are claims, and following one to another
company's document would stream that company's file. An ingestion row records no company,
so it is used when the plenum_cafm.documents row with its id is this company's (see
choose_document). `raw_metadata.blob_url` is never a source for the same reason: it is a
free-text field the upsert accepts.

Only a URL on the configured storage account is fetched; anything else is refused, so a
stored URL can never make this service fetch an arbitrary address. A document with no
stored original but with extracted text is served as that text, as the old
svc-deepagents download route did.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)


class CertificateFileError(Exception):
    """Why a file could not be served; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _account_name(conn: str) -> str | None:
    m = re.search(r"AccountName=([^;]+)", conn or "", re.I)
    return m.group(1).strip() if m else None


def parse_blob_url(url: str, *, account: str | None) -> tuple[str, str] | None:
    """(container, blob name) for a URL on this storage account, else None. Pure."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return None
    if p.scheme != "https" or not account:
        return None
    if (p.hostname or "").lower() != f"{account.lower()}.blob.core.windows.net":
        return None
    parts = unquote(p.path).lstrip("/").split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def download_filename(cert: Any, blob_name: str, stored_name: str | None) -> str:
    """A name a person recognises: the uploader's filename, else the blob's own. Pure."""
    name = (stored_name or "").strip() or blob_name.rsplit("/", 1)[-1]
    # Blobs are written as "<uuid>_<original>"; the uuid means nothing to a reader.
    name = re.sub(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_", "", name)
    return name.replace('"', "").replace("\r", "").replace("\n", "") or f"certificate-{cert.id}"


def _same_company(row_org: Any, row_building: Any, cert: Any, *, building_is_ours: bool = False) -> bool:
    """The document belongs to the certificate's company. Pure.

    A row that records a company must record this one. A row with none (common on graph
    documents) is accepted only when it sits on the certificate's own building — the
    building is the tenant boundary those rows do carry — or when neither side names a
    company at all.
    """
    cert_org = cert.organization_id or getattr(cert, "org_id", None)
    if row_org:
        return bool(cert_org) and str(row_org) == str(cert_org)
    # The certificate's building_id is itself client-writable, so it only counts when the
    # buildings table says that building is this company's (building_is_ours).
    cert_bld = getattr(cert, "building_id", None)
    if row_building and cert_bld:
        return building_is_ours and str(row_building) == str(cert_bld)
    return not cert_org


async def _building_is_ours(session: AsyncSession, cert: Any) -> bool:
    """Whether plenum_cafm.buildings records the certificate's building as its company's."""
    cert_org = cert.organization_id or getattr(cert, "org_id", None)
    bld = getattr(cert, "building_id", None)
    if not (cert_org and bld):
        return False
    try:
        async with session.begin_nested():
            org = (await session.execute(
                text("SELECT COALESCE(to_jsonb(b)->>'organization_id', to_jsonb(b)->>'org_id') "
                     "FROM plenum_cafm.buildings b WHERE b.building_id::text = :id"),
                {"id": str(bld)},
            )).scalar()
    except Exception as exc:  # noqa: BLE001
        log.warning("certificate_file.building_lookup_failed", error=str(exc)[:200])
        return False
    return bool(org) and str(org) == str(cert_org)


async def _first_document(session: AsyncSession, cert: Any) -> dict[str, Any] | None:
    """{id, blob_url, name, table} for the certificate's own document, or None."""
    meta = cert.raw_metadata if isinstance(cert.raw_metadata, dict) else {}
    ids = [str(x) for x in (cert.document_id, cert.source_document_id, meta.get("graph_document_id")) if x]
    # to_jsonb for the company column: its name differs between these two tables across
    # tenants (organization_id / org_id), and naming a missing column aborts the statement.
    lookups = (
        ("ingestion_documents",
         "SELECT d.id::text AS id, d.blob_url, d.original_filename AS name, "
         "COALESCE(to_jsonb(d)->>'organization_id', to_jsonb(d)->>'org_id') AS org, "
         "to_jsonb(d)->>'building_id' AS bld "
         "FROM plenum_cafm.ingestion_documents d WHERE d.id::text = :id"),
        ("documents",
         "SELECT d.document_id::text AS id, d.blob_url, d.file_name AS name, "
         "COALESCE(to_jsonb(d)->>'organization_id', to_jsonb(d)->>'org_id') AS org, "
         "to_jsonb(d)->>'building_id' AS bld "
         "FROM plenum_cafm.documents d WHERE d.document_id::text = :id"),
    )
    rows: list[tuple[str, dict[str, Any]]] = []
    for table, sql in lookups:
        for doc_id in ids:
            try:
                async with session.begin_nested():
                    row = (await session.execute(text(sql), {"id": doc_id})).mappings().first()
            except Exception as exc:  # noqa: BLE001 — a missing table is "not here", not a 500
                log.warning("certificate_file.lookup_failed", table=table, error=str(exc)[:200])
                row = None
            if row:
                rows.append((table, dict(row)))
    found, refused = choose_document(rows, cert, building_is_ours=await _building_is_ours(session, cert))
    if found is None and refused:
        raise CertificateFileError(
            403, "The document linked to this certificate belongs to another company, so it is not served.")
    return found


def choose_document(
    rows: list[tuple[str, dict[str, Any]]], cert: Any, *, building_is_ours: bool = False,
) -> tuple[dict[str, Any] | None, bool]:
    """(document, whether any row was refused) from the rows the certificate's ids reached.

    `rows` is [(table, row)] in lookup order. The first acceptable row with a stored
    original wins; else the first acceptable row at all, which is served as its text.

    doc-rag's ingestion_documents has no company or building column, so the row holding the
    uploaded original can never prove whose it is — and refusing it served every uploaded
    certificate as extracted text. documents.document_id IS ingestion_documents.id (every
    writer agrees), so the graph row with the same id, which does record the building,
    vouches for it. An ingestion row that names a company or building of its own is judged
    on that alone.
    """
    def ours(row: dict[str, Any]) -> bool:
        return _same_company(row.get("org"), row.get("bld"), cert, building_is_ours=building_is_ours)

    vouched = {str(r["id"]) for t, r in rows if t == "documents" and ours(r)}
    fallback: dict[str, Any] | None = None
    refused = False
    for table, row in rows:
        ok = ours(row) or (table == "ingestion_documents" and not row.get("org")
                           and not row.get("bld") and str(row["id"]) in vouched)
        if not ok:
            refused = True
            log.warning("certificate_file.cross_company_document_refused",
                        certificate_id=str(cert.id), document_id=str(row["id"]), table=table)
            continue
        found = {"id": row["id"], "blob_url": (row.get("blob_url") or "").strip(),
                 "name": row.get("name"), "table": table}
        if found["blob_url"]:
            return found, refused
        fallback = fallback or found
    return fallback, refused


async def _extracted_text(session: AsyncSession, doc_id: str) -> str:
    try:
        async with session.begin_nested():
            chunks = (await session.execute(
                text("SELECT chunk_text FROM plenum_cafm.document_chunks "
                     "WHERE ingestion_id::text = :id ORDER BY chunk_index"),
                {"id": doc_id},
            )).scalars().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("certificate_file.chunks_failed", error=str(exc)[:200])
        return ""
    return "\n\n".join(c for c in chunks if c)


async def open_certificate_file(session: AsyncSession, cert: Any) -> dict[str, Any]:
    """{stream, filename, content_type, size} for the certificate's file, or raise."""
    doc = await _first_document(session, cert)
    if not doc:
        raise CertificateFileError(404, "No file is stored for this certificate — only its fields were recorded.")
    url, stored_name = doc["blob_url"], doc.get("name")
    if not url:
        # No original kept: the extracted text is still the certificate's content.
        body = await _extracted_text(session, doc["id"])
        if not body:
            raise CertificateFileError(404, "No file is stored for this certificate — only its fields were recorded.")
        data = body.encode("utf-8")
        name = download_filename(cert, "", stored_name)
        name = name if name.lower().endswith(".txt") else f"{name}.txt"

        async def text_stream():
            yield data

        return {"stream": text_stream(), "filename": name,
                "content_type": "text/plain; charset=utf-8", "size": len(data)}

    conn = (settings.azure_storage_connection_string or "").strip()
    target = parse_blob_url(url, account=_account_name(conn))
    if not conn:
        raise CertificateFileError(503, "Azure Blob is not configured on this service (AZURE_STORAGE_CONNECTION_STRING).")
    if not target:
        raise CertificateFileError(404, "The stored file is not in this platform's storage account, so it is not served.")
    container, blob_name = target

    try:
        from azure.core.exceptions import ResourceNotFoundError
        # The sync client, not azure.storage.blob.aio: the async one needs aiohttp, which
        # this image does not install, and the first download ever to reach it (25 Sep)
        # died with "Unable to create async transport". The sync one rides on requests.
        from azure.storage.blob import BlobServiceClient
    except ImportError:  # pragma: no cover
        raise CertificateFileError(503, "The Azure Blob SDK is not installed on this service.")

    content_type, size, chunks = await open_blob(
        BlobServiceClient, ResourceNotFoundError, conn, container, blob_name)
    return {
        "stream": chunks,
        "filename": download_filename(cert, blob_name, stored_name),
        "content_type": content_type,
        "size": size,
    }


async def open_blob(
    client_cls: Any, not_found: type[BaseException], conn: str, container: str, blob_name: str,
) -> tuple[str, int | None, Iterator[bytes]]:
    """(content_type, size, chunks) for one blob, read with the sync SDK off the event loop.

    The first request runs in a worker thread; `chunks` is a plain iterator, which
    StreamingResponse drains in its threadpool, so no network read blocks the loop. The
    client is closed once the chunks are exhausted, or at once if the blob cannot be opened.
    """
    def start():
        client = client_cls.from_connection_string(conn)
        try:
            return client, client.get_blob_client(container=container, blob=blob_name).download_blob()
        except BaseException:
            client.close()
            raise

    try:
        client, downloader = await asyncio.to_thread(start)
    except not_found:
        raise CertificateFileError(404, "The file is recorded but no longer in Blob storage.")
    except Exception as exc:  # noqa: BLE001 — incl. a client that cannot be built at all
        log.warning("certificate_file.download_failed", error=str(exc)[:300])
        raise CertificateFileError(502, "Blob storage did not return the file.")

    props = downloader.properties
    content_type = getattr(props.content_settings, "content_type", None) or "application/octet-stream"

    def chunks() -> Iterator[bytes]:
        try:
            yield from downloader.chunks()
        finally:
            client.close()

    return content_type, getattr(props, "size", None), chunks()
