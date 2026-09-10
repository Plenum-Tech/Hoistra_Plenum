"""Bind ingested documents to the building they were filed against.

The ingest panel lets someone choose a building and attach files to it. For a long time that
choice travelled as prose — a message reading "Ingest 3 documents for Riverside Court." and a
context paragraph mentioning the id — and whether anything came of it depended on an agent
reading the sentence and deciding to act. Two silent failures: no building selected, so the
id was never sent at all; or it was sent and nothing used it. Both end with documents that
belong to no building, appear in no drawer, and report success.

The caller knows the answer, so the caller makes the link. This is the one implementation,
used by both ingest paths — the inline flow for a handful of files and the background worker
for a bulk batch — because two copies of it would be one copy and one that quietly stopped
matching.

Binding is deliberately separate from extraction. Threading a building id down through
doc-rag indexing, the compliance extractor and the contract extractor would require each of
them to carry it correctly; here the flow reports which documents it made and one UPDATE
closes the loop.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text

from .. import database

log = structlog.get_logger(__name__)

#: Where a document id shows up in a tool call's output. Extractors are not consistent about
#: the key, and a document that was created but reported under an unexpected name would go
#: unbound — so all the spellings in use are read.
_DOC_ID_KEYS = ("document_id", "documentId", "doc_id", "id")


def document_ids_from(tool_calls: Any) -> list[str]:
    """Every document id an ingest reported creating: deduplicated, in the order seen.

    Only well-formed UUIDs survive. Tool outputs carry all sorts of ids — a migration id, a
    batch id, a queue item — and anything that is not a uuid is not a document id worth
    trying to bind.
    """
    found: list[str] = []
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        out = call.get("output")
        holders = [out]
        if isinstance(out, dict):
            # Extractors report their write under `upsert`; the id of the document it was
            # read from sits inside rather than beside it.
            for nested in ("upsert", "document", "compliance"):
                if isinstance(out.get(nested), dict):
                    holders.append(out[nested])
        for holder in holders:
            if not isinstance(holder, dict):
                continue
            for key in _DOC_ID_KEYS:
                raw = holder.get(key)
                if not raw:
                    continue
                try:
                    val = str(UUID(str(raw)))
                except (ValueError, AttributeError, TypeError):
                    continue
                if val not in found:
                    found.append(val)
    return found


async def building_exists(building_id: str) -> bool:
    async with database.AsyncSessionLocal() as session:
        row = await session.execute(
            text("SELECT 1 FROM plenum_cafm.buildings WHERE building_id::text = :b"),
            {"b": building_id},
        )
        return row.first() is not None


async def bind_documents_to_building(
    building_id: str, document_ids: list[str],
) -> dict[str, int]:
    """Set building_id on those documents, and on any certificate read off one of them.

    Certificates come along because a certificate extracted from a document belongs to the
    same building by construction. Leaving them out would put a document in the drawer with
    the certificate it evidences missing from the column beside it.

    Rows already pointing at this building are not counted as bound — re-running an ingest
    should report nothing new rather than the same number twice.
    """
    if not document_ids:
        return {"documents": 0, "certificates": 0}
    async with database.AsyncSessionLocal() as session:
        docs = await session.execute(
            text("""UPDATE plenum_cafm.documents
                       SET building_id = :b::uuid
                     WHERE document_id::text = ANY(:ids)
                       AND (building_id IS NULL OR building_id::text <> :b)"""),
            {"b": building_id, "ids": document_ids},
        )
        certs = await session.execute(
            text("""UPDATE plenum_cafm.compliance_certificates
                       SET building_id = :b::uuid
                     WHERE COALESCE(document_id, source_document_id)::text = ANY(:ids)
                       AND (building_id IS NULL OR building_id::text <> :b)"""),
            {"b": building_id, "ids": document_ids},
        )
        await session.commit()
        return {"documents": docs.rowcount or 0, "certificates": certs.rowcount or 0}


async def bind_and_log(
    building_id: str | None, tool_calls: Any, *, where: str, session_id: str = "",
) -> dict[str, Any]:
    """Bind, and never let a binding failure take the ingest down with it.

    The file is indexed either way. An unbound document can be bound later from the row it
    already is; an upload that 500s because the link failed is gone.
    """
    if not building_id:
        return {}
    ids = document_ids_from(tool_calls)
    if not ids:
        log.warning("ingest.nothing_to_bind", where=where, session_id=session_id,
                    building_id=building_id)
        return {"documents": 0, "certificates": 0, "candidates": 0}
    try:
        bound = await bind_documents_to_building(building_id, ids)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        log.warning("ingest.bind_failed", where=where, session_id=session_id,
                    building_id=building_id, error=str(exc)[:200])
        return {"error": str(exc)[:120], "candidates": len(ids)}
    log.info("ingest.bound_to_building", where=where, session_id=session_id,
             building_id=building_id, candidates=len(ids), **bound)
    return dict(bound, candidates=len(ids))
