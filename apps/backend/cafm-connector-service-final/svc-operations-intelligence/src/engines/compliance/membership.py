"""CCC §3.2 — vector DB membership keep/remove with audit (C4).

Add-to-vector only after explicit Keep: after draft upsert we hold the PDF out of
the vector store until confirmed; Remove leaves it out and audits.
"""
from __future__ import annotations

import json
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ...models import ApprovalsQueueItem, ComplianceCertificate
from ...shared.approvals import enqueue_approval, write_audit

log = get_logger(__name__)


def _hold_dir() -> Path:
    base = Path(settings.verification_dump_dir or "./data/verification_dumps")
    d = base.parent / "membership_hold"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hold_path_for(document_id: UUID | str, file_name: str | None = None) -> Path:
    ext = Path(file_name or "document.pdf").suffix or ".pdf"
    return _hold_dir() / f"{document_id}{ext}"


async def enqueue_membership_pending(
    session: AsyncSession,
    *,
    document_id: UUID | str,
    organization_id: UUID | None = None,
    certificate_id: UUID | None = None,
    file_name: str | None = None,
    source_file_path: str | None = None,
    hold_until_confirm: bool = True,
) -> dict[str, Any] | None:
    """Create Approvals item so chat/UI can Keep or Remove the indexed PDF.

    Always attempts hold (remove from Doc-RAG) when hold_until_confirm=True.
    Caches bytes from source_file_path (or Doc-RAG download) so Keep can re-index.
    """
    try:
        doc_uuid = UUID(str(document_id))
    except (TypeError, ValueError):
        return None

    existing = (
        await session.execute(
            select(ApprovalsQueueItem)
            .where(
                ApprovalsQueueItem.source_feature == "A",
                ApprovalsQueueItem.item_type == "vector_membership",
                ApprovalsQueueItem.status == "pending",
                ApprovalsQueueItem.related_entity_id == doc_uuid,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return {"id": str(existing.id), "already_pending": True}

    if not hold_until_confirm:
        # Auto-keep (default): the document stays indexed in the vector DB on ingest.
        # No hold, no "Confirm membership" prompt — the PM is only asked about membership
        # when they explicitly choose to add/remove, and can still Remove later via
        # decide_membership. This also avoids the "held cache unavailable" Keep failure,
        # since nothing is ever removed-then-re-indexed.
        if certificate_id:
            cert = await session.get(ComplianceCertificate, certificate_id)
            if cert:
                meta = dict(cert.raw_metadata or {})
                meta.pop("membership_pending", None)
                meta.pop("vector_held_pending_confirm", None)
                if source_file_path:
                    meta["source_file_path"] = source_file_path
                meta["vector_membership"] = {
                    "action": "keep",
                    "auto": True,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "document_id": str(doc_uuid),
                }
                cert.raw_metadata = meta
                cert.updated_at = datetime.now(timezone.utc)
        return {
            "id": None,
            "document_id": str(doc_uuid),
            "auto_kept": True,
            "vector_held_pending_confirm": False,
        }

    hold_cache: str | None = None
    hold_result: dict[str, Any] | None = None
    vector_held = False

    if hold_until_confirm:
        hold_cache = await _cache_document_bytes(
            doc_uuid, source_file_path=source_file_path, file_name=file_name
        )
        hold_result = await _delete_doc_rag(doc_uuid)
        vector_held = bool(hold_result.get("ok"))
        if not vector_held:
            log.warning(
                "membership.hold_failed",
                document_id=str(doc_uuid),
                error=hold_result.get("error"),
            )

    item = await enqueue_approval(
        session,
        source_feature="A",
        item_type="vector_membership",
        summary=f"Confirm vector DB membership — {file_name or doc_uuid}",
        severity="Review",
        organization_id=organization_id,
        related_entity_type="document",
        related_entity_id=doc_uuid,
        payload={
            "document_id": str(doc_uuid),
            "certificate_id": str(certificate_id) if certificate_id else None,
            "file_name": file_name,
            "source_file_path": source_file_path,
            "hold_cache_path": hold_cache,
            "membership_pending": True,
            "vector_held_pending_confirm": vector_held,
            "hold_result": hold_result,
        },
    )

    if certificate_id:
        cert = await session.get(ComplianceCertificate, certificate_id)
        if cert:
            meta = dict(cert.raw_metadata or {})
            meta["membership_pending"] = True
            meta["vector_held_pending_confirm"] = vector_held
            if source_file_path:
                meta["source_file_path"] = source_file_path
            if hold_cache:
                meta["hold_cache_path"] = hold_cache
            if vector_held:
                meta["vector_membership"] = {
                    "action": "held",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "prior_document_id": str(doc_uuid),
                }
            cert.raw_metadata = meta
            cert.updated_at = datetime.now(timezone.utc)

    return {
        "id": str(item.id),
        "document_id": str(doc_uuid),
        "vector_held_pending_confirm": vector_held,
        "hold_cache_path": hold_cache,
        "queue_item_id": str(item.id),
    }


async def decide_membership(
    session: AsyncSession,
    *,
    document_id: UUID,
    action: str,
    actor: str | None = None,
    organization_id: UUID | None = None,
    confirmed_by: UUID | None = None,
) -> dict[str, Any]:
    action_n = (action or "").strip().lower()
    if action_n not in {"keep", "remove"}:
        return {"ok": False, "error": "action must be keep|remove"}

    actor_s = actor or (f"user:{confirmed_by}" if confirmed_by else "user:pm")
    delete_result: dict[str, Any] | None = None
    reindex_result: dict[str, Any] | None = None
    new_document_id: str | None = None

    pending = list(
        (
            await session.execute(
                select(ApprovalsQueueItem).where(
                    ApprovalsQueueItem.source_feature == "A",
                    ApprovalsQueueItem.item_type == "vector_membership",
                    ApprovalsQueueItem.status == "pending",
                    ApprovalsQueueItem.related_entity_id == document_id,
                )
            )
        ).scalars().all()
    )
    payload0 = dict(pending[0].payload or {}) if pending else {}
    source_file_path = payload0.get("source_file_path") or payload0.get("hold_cache_path")
    was_held = bool(payload0.get("vector_held_pending_confirm"))
    file_name = payload0.get("file_name")

    certs = list(
        (
            await session.execute(
                select(ComplianceCertificate).where(
                    ComplianceCertificate.document_id == document_id
                )
            )
        ).scalars().all()
    )
    cert_id_raw = payload0.get("certificate_id")
    if not certs and cert_id_raw:
        try:
            c = await session.get(ComplianceCertificate, UUID(str(cert_id_raw)))
            if c:
                certs = [c]
        except (TypeError, ValueError):
            pass

    if action_n == "remove":
        if not was_held:
            delete_result = await _delete_doc_rag(document_id)
        else:
            delete_result = {"ok": True, "already_held": True}
        _cleanup_hold_cache(payload0.get("hold_cache_path"))
        for cert in certs:
            meta = dict(cert.raw_metadata or {})
            meta["vector_membership"] = {
                "action": "remove",
                "at": datetime.now(timezone.utc).isoformat(),
                "actor": actor_s,
            }
            meta.pop("membership_pending", None)
            meta.pop("vector_held_pending_confirm", None)
            cert.raw_metadata = meta
            cert.document_id = None
            cert.updated_at = datetime.now(timezone.utc)
    else:
        # Keep — re-index when held; else confirm retention of existing index
        if was_held:
            reindex_src = None
            for candidate in (
                source_file_path,
                payload0.get("hold_cache_path"),
                payload0.get("source_file_path"),
            ):
                if candidate and Path(str(candidate)).is_file():
                    reindex_src = str(candidate)
                    break
            if not reindex_src:
                return {
                    "ok": False,
                    "error": (
                        "Keep failed: held document cache unavailable to re-index. "
                        "Re-upload the PDF."
                    ),
                }
            reindex_result = await _reindex_doc_rag(reindex_src, preferred_name=file_name)
            if reindex_result.get("ok") and reindex_result.get("document_id"):
                new_document_id = str(reindex_result["document_id"])
                _cleanup_hold_cache(payload0.get("hold_cache_path"))
            else:
                return {
                    "ok": False,
                    "error": reindex_result.get("error")
                    or "Keep failed: could not re-index document into vector DB",
                    "reindex_result": reindex_result,
                }
        for cert in certs:
            meta = dict(cert.raw_metadata or {})
            meta["vector_membership"] = {
                "action": "keep",
                "at": datetime.now(timezone.utc).isoformat(),
                "actor": actor_s,
                "prior_document_id": str(document_id),
                "document_id": new_document_id or str(document_id),
            }
            meta.pop("membership_pending", None)
            meta.pop("vector_held_pending_confirm", None)
            if new_document_id:
                try:
                    cert.document_id = UUID(new_document_id)
                except (TypeError, ValueError):
                    pass
            cert.raw_metadata = meta
            cert.updated_at = datetime.now(timezone.utc)

    await session.execute(
        text(
            """
            INSERT INTO plenum_cafm.compliance_vector_membership_audit
                (id, document_id, action, actor, organization_id, detail)
            VALUES
                (:id, :document_id, :action, :actor, :organization_id, CAST(:detail AS jsonb))
            """
        ),
        {
            "id": str(uuid4()),
            "document_id": str(document_id),
            "action": action_n,
            "actor": actor_s,
            "organization_id": str(organization_id) if organization_id else None,
            "detail": json.dumps(
                {
                    "delete_result": delete_result,
                    "reindex_result": reindex_result,
                    "new_document_id": new_document_id,
                    "confirmed_by": str(confirmed_by) if confirmed_by else None,
                    "was_held": was_held,
                }
            ),
        },
    )

    for item in pending:
        item.status = "approved" if action_n == "keep" else "dismissed"
        item.decided_at = datetime.now(timezone.utc)
        if confirmed_by:
            item.decided_by = confirmed_by
        payload = dict(item.payload or {})
        payload["resolved_action"] = action_n
        if new_document_id:
            payload["new_document_id"] = new_document_id
        item.payload = payload

    await write_audit(
        session,
        actor=actor_s,
        action_type=f"vector_membership_{action_n}",
        source_feature="A",
        organization_id=organization_id,
        input_payload={"document_id": str(document_id), "action": action_n},
        output_payload={
            "delete_result": delete_result,
            "reindex_result": reindex_result,
            "new_document_id": new_document_id,
            "kept": action_n == "keep",
        },
    )
    await session.commit()
    return {
        "ok": True,
        "document_id": new_document_id or str(document_id),
        "action": action_n,
        "delete_result": delete_result,
        "reindex_result": reindex_result,
    }


async def _cache_document_bytes(
    document_id: UUID,
    *,
    source_file_path: str | None,
    file_name: str | None,
) -> str | None:
    dest = _hold_path_for(document_id, file_name)
    if source_file_path and Path(source_file_path).is_file():
        try:
            shutil.copy2(source_file_path, dest)
            return str(dest)
        except OSError as exc:
            log.warning("membership.cache_copy_failed", error=str(exc))
    # Download original from Doc-RAG before hold-delete
    base = (settings.doc_rag_base_url or "").rstrip("/")
    if not base:
        return str(dest) if dest.is_file() else None
    paths = [
        f"/doc-rag/documents/{document_id}/download",
        f"/api/documents/{document_id}/download",
        f"/documents/{document_id}/download",
    ]
    async with httpx.AsyncClient(timeout=120.0) as client:
        for path in paths:
            try:
                resp = await client.get(f"{base}{path}")
                if resp.status_code < 400 and resp.content:
                    dest.write_bytes(resp.content)
                    return str(dest)
            except Exception as exc:  # noqa: BLE001
                log.warning("membership.cache_download_failed", path=path, error=str(exc))
    return str(dest) if dest.is_file() else None


def _cleanup_hold_cache(path: str | None) -> None:
    if not path:
        return
    try:
        p = Path(path)
        if p.is_file() and "membership_hold" in str(p):
            p.unlink(missing_ok=True)
    except OSError:
        pass


async def _delete_doc_rag(document_id: UUID) -> dict[str, Any]:
    base = (settings.doc_rag_base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "doc_rag_base_url not configured"}
    paths = [
        f"/doc-rag/documents/{document_id}",
        f"/api/documents/{document_id}",
    ]
    async with httpx.AsyncClient(timeout=60.0) as client:
        last_err = None
        for path in paths:
            try:
                resp = await client.delete(f"{base}{path}")
                if resp.status_code < 500:
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"status_code": resp.status_code, "text": resp.text[:200]}
                    return {"ok": resp.status_code < 400, "response": data, "path": path}
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                log.warning("membership.doc_rag_delete_failed", path=path, error=str(exc))
    return {"ok": False, "error": last_err or "delete_failed"}


async def _reindex_doc_rag(
    file_path: str, *, preferred_name: str | None = None
) -> dict[str, Any]:
    """Re-upload a held PDF so Keep adds it to the vector DB (CCC §3.2)."""
    base = (settings.doc_rag_base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "doc_rag_base_url not configured"}
    path = Path(file_path)
    if not path.is_file():
        return {"ok": False, "error": f"source file not found: {file_path}"}
    name = preferred_name or path.name
    mime = mimetypes.guess_type(name)[0] or "application/pdf"
    file_bytes = path.read_bytes()
    upload_paths = ["/doc-rag/documents/upload", "/documents/upload"]
    async with httpx.AsyncClient(timeout=120.0) as client:
        last_err = None
        for up in upload_paths:
            try:
                resp = await client.post(
                    f"{base}{up}",
                    files={"file": (name, file_bytes, mime)},
                )
                if resp.status_code >= 500:
                    last_err = f"status {resp.status_code}"
                    continue
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                doc_id = (
                    data.get("document_id")
                    or data.get("id")
                    or (data.get("document") or {}).get("id")
                )
                if resp.status_code < 400 and doc_id:
                    return {"ok": True, "document_id": str(doc_id), "response": data, "path": up}
                last_err = data.get("error") or f"status {resp.status_code}"
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                log.warning("membership.doc_rag_reindex_failed", path=up, error=str(exc))
    return {"ok": False, "error": last_err or "reindex_failed"}
