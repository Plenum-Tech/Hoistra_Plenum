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
import json
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from ... import database
from ...services.principal import Principal, current_principal

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/documents", tags=["Documents"])
_PERSONAL_TYPES = {"visa", "emirates_id", "passport", "labour_card"}


def _content_disposition(filename: str) -> str:
    # Quote-escape so odd filenames can't break the header.
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    return f'attachment; filename="{safe}"'


# ── Signed, short-lived download links ─────────────────────────────────────────────────────────
#
# This route answered anyone who had a document's id, and redirected to the original in a blob
# container that is itself publicly readable (both found 2 Oct 2026) - so an identity paper, a
# contract, an invoice were one URL from the internet. Now a download is two steps:
#
#   POST /api/documents/{id}/link      signed-in; checks the caller may see the document (their
#                                      company, their buildings; a personal one: admins only,
#                                      the read logged) and returns a link valid for five minutes
#   GET  /api/documents/{id}/download  needs that link's signature; streams the file through
#                                      this service - never a redirect to the public blob URL
#
# The browser can open the link in a new tab (no header needed) and it stops working minutes later.

import hashlib
import hmac
import os
import secrets
import time

LINK_TTL_SECONDS = 300
_PROCESS_KEY = secrets.token_bytes(32)


def _link_key() -> bytes:
    # The platform's own signing secret when the container has it; otherwise a per-process key,
    # so a link is never unsigned - it only stops working across a restart.
    raw = (os.environ.get("AUTH_JWT_SECRET") or os.environ.get("JWT_SECRET") or "").encode()
    return hashlib.sha256(b"document-link:" + raw).digest() if raw else _PROCESS_KEY


def sign_link(document_id: str, expires: int, user: str) -> str:
    msg = f"{document_id}|{expires}|{user}".encode()
    return hmac.new(_link_key(), msg, hashlib.sha256).hexdigest()


def verify_link(document_id: str, expires: int, user: str, sig: str, now: float | None = None) -> bool:
    if not sig or expires < int(now if now is not None else time.time()):
        return False
    return hmac.compare_digest(sign_link(document_id, expires, user), sig)


async def _owner_and_kind(session, doc_id: str) -> dict | None:
    """The document's company and building (the register row, its building's company, or the
    record citing it - the rule the document search uses) and whether it is personal."""
    return (await session.execute(text("""
        SELECT d.document_id::text AS id, d.building_id::text AS building_id, d.doc_type,
               to_jsonb(d) ->> 'sensitivity' AS sensitivity,
               COALESCE(d.organization_id::text,
                        (SELECT b.organization_id::text FROM plenum_cafm.buildings b WHERE b.building_id = d.building_id),
                        (SELECT s.organization_id::text FROM plenum_cafm.contract_sla_parameters s
                          WHERE s.document_id::text = d.document_id::text AND s.organization_id IS NOT NULL LIMIT 1),
                        (SELECT coalesce(c.organization_id, c.org_id)::text FROM plenum_cafm.compliance_certificates c
                          WHERE (c.document_id::text = d.document_id::text OR c.source_document_id::text = d.document_id::text)
                            AND coalesce(c.organization_id, c.org_id) IS NOT NULL LIMIT 1),
                        (SELECT i.organization_id::text FROM plenum_cafm.invoices i
                          WHERE i.document_id::text = d.document_id::text AND i.organization_id IS NOT NULL LIMIT 1)) AS owner_org
          FROM plenum_cafm.documents d WHERE d.document_id::text = :id"""), {"id": doc_id})).mappings().first()


def _is_personal(row) -> bool:
    return (row.get("sensitivity") or "").lower() == "personal" or (row.get("doc_type") or "").lower() in _PERSONAL_TYPES


@router.post("/{document_id}/link")
async def document_link(document_id: str, principal: Principal = Depends(current_principal)) -> dict:
    """A five-minute link to open or download a document the caller may see."""
    try:
        doc_uuid = UUID(document_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document id")
    async with database.AsyncSessionLocal() as session:
        row = await _owner_and_kind(session, str(doc_uuid))
        if row is None:
            # A file only doc-rag knows (no register row) has no owner to check against: only a
            # superadmin may open it.
            exists = (await session.execute(text("SELECT 1 FROM plenum_cafm.ingestion_documents WHERE id::text = :id"),
                                            {"id": str(doc_uuid)})).first()
            if not exists or principal.role != "superadmin":
                raise HTTPException(status_code=404, detail="Document not found")
            personal = False
        else:
            if principal.role != "superadmin" and (
                    not principal.organization_id or str(row["owner_org"] or "") != str(principal.organization_id)
                    or (row["building_id"] and not principal.allows_building(row["building_id"]))):
                raise HTTPException(status_code=404, detail="Document not found")
            personal = _is_personal(row)
            if personal and not principal.is_admin:
                raise HTTPException(status_code=404, detail="Document not found")
        if personal:
            # Every open of an identity paper is on the record, in the append-only audit trail.
            await session.execute(text("""
                INSERT INTO plenum_cafm.ops_audit_log (id, organization_id, actor, action_type, source_feature, detail, created_at)
                VALUES (gen_random_uuid(), CAST(:o AS uuid), :a, 'document.personal_open', NULL, CAST(:d AS jsonb), now())"""),
                {"o": str(principal.organization_id) if principal.organization_id else None,
                 "a": f"user:{principal.user_id}", "d": json.dumps({"document_id": str(doc_uuid), "email": principal.email})})
            await session.commit()
    expires = int(time.time()) + LINK_TTL_SECONDS
    user = str(principal.user_id)
    sig = sign_link(str(doc_uuid), expires, user)
    return {"url": f"/api/documents/{doc_uuid}/download?exp={expires}&u={user}&sig={sig}",
            "expires_in": LINK_TTL_SECONDS, "personal": personal}


async def _stream_blob(url: str):
    """The stored original, read with the storage key - not a redirect to the public URL."""
    data = await _fetch_blob(url)
    return data


@router.get("/{document_id}/download")
async def download_document(document_id: str, exp: int = 0, u: str = "", sig: str = ""):
    """Open / download a document through a signed link from POST /link (blob original or extracted text)."""
    try:
        doc_uuid = UUID(document_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document id")
    if not verify_link(str(doc_uuid), int(exp or 0), u, sig):
        raise HTTPException(status_code=403, detail="This link has expired or is not valid - open the document again.")

    async with database.AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT original_filename AS file_name, blob_url FROM plenum_cafm.ingestion_documents WHERE id::text = :id"),
            {"id": str(doc_uuid)})).mappings().first()
        graph = (await session.execute(text(
            "SELECT file_name, blob_url FROM plenum_cafm.documents WHERE document_id::text = :id"),
            {"id": str(doc_uuid)})).mappings().first()
        filename = ((row or {}).get("file_name") or (graph or {}).get("file_name") or f"document-{document_id}").strip()
        blob_url = ((row or {}).get("blob_url") or (graph or {}).get("blob_url") or "").strip()

        if blob_url:
            try:
                data = await _stream_blob(blob_url)
            except Exception as exc:  # noqa: BLE001
                log.warning("documents.download.blob_failed", document_id=str(doc_uuid), error=str(exc)[:200])
                raise HTTPException(status_code=502, detail="The stored file could not be read.")
            import mimetypes

            media = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            return StreamingResponse(io.BytesIO(data), media_type=media, headers={
                "Content-Disposition": _content_disposition(filename).replace("attachment;", "inline;", 1),
                "Content-Length": str(len(data)), "Cache-Control": "private, no-store"})

        # No stored original -> the extracted text, so the content is still readable.
        chunks = (await session.execute(text(
            "SELECT chunk_text FROM plenum_cafm.document_chunks WHERE ingestion_id::text = :id ORDER BY chunk_index"),
            {"id": str(doc_uuid)})).scalars().all()
        body = "\n\n".join(c for c in chunks if c)
        if not body:
            if not row and not graph:
                raise HTTPException(status_code=404, detail="Document not found")
            raise HTTPException(status_code=404,
                                detail="No downloadable content for this document (no stored file or extracted text).")
        txt_name = filename if filename.lower().endswith(".txt") else f"{filename}.txt"
        data = body.encode("utf-8")
        return StreamingResponse(io.BytesIO(data), media_type="text/plain; charset=utf-8", headers={
            "Content-Disposition": _content_disposition(txt_name), "Content-Length": str(len(data)),
            "X-Document-Source": "extracted-text", "Cache-Control": "private, no-store"})



# ── Index now: make a filed document's text searchable ────────────────────────────────────────
#
# Uploads are chunked into doc-rag as they arrive. Documents that reached the register another
# way - seeded, migrated, written by an engine - were never indexed: on hoistra_test, 15 files of
# the 194 in the register (2 Oct 2026), and none of Bishopsgate's. A document that is not indexed
# can be named in an answer but never quoted. This fetches the stored original and sends it
# through the same doc-rag upload an upload uses, under the register's own file name so the
# company-scoped search (svc-operations-intelligence engines/document_search.py) finds it.
# Identity papers are refused: they are personal and stay out of the search index.



async def _fetch_blob(url: str) -> bytes:
    from ...config import settings

    conn = (getattr(settings, "azure_storage_connection_string", "") or "").strip()
    if conn:
        try:
            from azure.storage.blob.aio import BlobClient  # type: ignore[import-not-found]

            async with BlobClient.from_blob_url(url, credential=None) as probe:
                account_container_blob = (probe.container_name, probe.blob_name)
            from azure.storage.blob.aio import BlobServiceClient  # type: ignore[import-not-found]

            async with BlobServiceClient.from_connection_string(conn) as svc:
                blob = svc.get_blob_client(*account_container_blob)
                stream = await blob.download_blob()
                return await stream.readall()
        except ImportError:
            pass
    import httpx

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


@router.post("/{document_id}/index")
async def index_filed_document(document_id: str, principal: Principal = Depends(current_principal)) -> dict:
    """Index a filed document's text for search (admin). Not for personal documents."""
    import mimetypes

    from ...agents.doc_rag_agent import _request as _rag_request, _SERVICE as _RAG_SERVICE, _INDEX_TIMEOUT
    from ...config import settings

    if not principal.is_admin:
        raise HTTPException(status_code=403, detail={"ok": False, "reason": "admin_required",
                                                     "error": "Only a company administrator can index documents."})
    try:
        doc_uuid = UUID(document_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document id")
    async with database.AsyncSessionLocal() as session:
        row = (await session.execute(text("""
            SELECT d.file_name, d.blob_url, d.doc_type, d.building_id::text AS building_id,
                   to_jsonb(d) ->> 'sensitivity' AS sensitivity,
                   COALESCE(d.organization_id::text,
                            (SELECT b.organization_id::text FROM plenum_cafm.buildings b WHERE b.building_id = d.building_id),
                            (SELECT s.organization_id::text FROM plenum_cafm.contract_sla_parameters s
                              WHERE s.document_id::text = d.document_id::text AND s.organization_id IS NOT NULL LIMIT 1),
                            (SELECT coalesce(c.organization_id, c.org_id)::text FROM plenum_cafm.compliance_certificates c
                              WHERE c.document_id::text = d.document_id::text
                                AND coalesce(c.organization_id, c.org_id) IS NOT NULL LIMIT 1)) AS owner_org,
                   (SELECT count(*) FROM plenum_cafm.ingestion_documents i JOIN plenum_cafm.document_chunks k
                      ON k.ingestion_id = i.id WHERE i.original_filename = d.file_name) AS chunks
              FROM plenum_cafm.documents d WHERE d.document_id::text = :id"""), {"id": str(doc_uuid)})).mappings().first()
    # A document of another company, or outside the caller's buildings, does not exist for them.
    if row is None or (principal.role != "superadmin" and (
            not principal.organization_id or str(row["owner_org"] or "") != str(principal.organization_id)
            or (row["building_id"] and not principal.allows_building(row["building_id"])))):
        raise HTTPException(status_code=404, detail={"ok": False, "reason": "not_found", "error": "No such document."})
    if (row["sensitivity"] or "").lower() == "personal" or (row["doc_type"] or "").lower() in _PERSONAL_TYPES:
        raise HTTPException(status_code=422, detail={"ok": False, "reason": "personal",
                                                     "error": "A personal document is not added to search."})
    if int(row["chunks"] or 0) > 0:
        return {"ok": True, "already_indexed": True, "chunks": int(row["chunks"])}
    if not row["blob_url"] or not row["file_name"]:
        raise HTTPException(status_code=422, detail={"ok": False, "reason": "no_original",
                                                     "error": "No stored original to index - upload the file again."})
    try:
        data = await _fetch_blob(row["blob_url"])
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.index.fetch_failed", document_id=str(doc_uuid), error=str(exc)[:200])
        raise HTTPException(status_code=502, detail={"ok": False, "reason": "fetch_failed",
                                                     "error": "The stored original could not be read."})
    mime = mimetypes.guess_type(row["file_name"])[0] or "application/octet-stream"
    resp = await _rag_request("POST", settings.doc_rag_base_url, "/doc-rag/documents/upload", service=_RAG_SERVICE,
                              timeout=_INDEX_TIMEOUT, files={"file": (row["file_name"], data, mime)}, max_attempts=2)
    out = resp.json() if hasattr(resp, "json") else {}
    log.info("documents.index.queued", document_id=str(doc_uuid), file=row["file_name"], by=str(principal.user_id))
    return {"ok": True, "queued": True, "file": row["file_name"], "doc_rag": out,
            "note": "Indexing runs in the background; the text is searchable in a minute or two."}
