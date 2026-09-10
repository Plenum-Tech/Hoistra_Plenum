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


async def documents_from_session(session_id: str) -> list[str]:
    """The documents this upload produced, found by the mark the uploader leaves.

    Every file is saved as "{session_id}_{filename}" before it is handed on, and doc-rag
    records that name verbatim. So the rows belonging to one upload can be identified without
    asking any engine to report them — which matters because they do not all report the same
    way, and the ones that do not fail silently.

    Recent rows only: a session id can be reused, and re-binding a document somebody filed
    hours ago against a building they have since corrected would be worse than missing it.
    """
    sid = (session_id or "").strip()
    if not sid:
        return []
    async with database.AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    """SELECT id::text FROM plenum_cafm.ingestion_documents
                        WHERE original_filename LIKE :prefix
                          AND uploaded_at > now() - interval '1 hour'"""
                ),
                {"prefix": sid + "\\_%"},
            )
        ).scalars().all()
    return [str(r) for r in rows]


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
        return {"documents": 0, "certificates": 0, "created": 0}
    async with database.AsyncSessionLocal() as session:
        # The row is created if the engine that owns this file has not written it yet.
        # It runs after the ingest sequence returns, so on a first upload there is nothing
        # to update and the link would be lost to a race — while the endpoint, right here,
        # knows both the document and the building it was filed against.
        #
        # The later upsert fills only what is missing, so it completes this row rather than
        # replacing it, and cannot blank the building.
        created = await session.execute(
            text(
                """INSERT INTO plenum_cafm.documents
                       (document_id, building_id, doc_type, file_name, uploaded_at)
                   SELECT i.id, CAST(:b AS uuid),
                          NULLIF(i.document_type, ''), i.original_filename, now()
                     FROM plenum_cafm.ingestion_documents i
                    WHERE i.id::text = ANY(:ids)
                      AND NOT EXISTS (SELECT 1 FROM plenum_cafm.documents d
                                       WHERE d.document_id = i.id)
                   ON CONFLICT (document_id) DO NOTHING"""
            ),
            {"b": building_id, "ids": document_ids},
        )
        docs = await session.execute(
            # CAST(:b AS uuid), not :b::uuid. SQLAlchemy's text() mis-parses a bind
            # parameter followed immediately by a cast and emits SQL Postgres rejects with
            # "syntax error at or near :" — which the caller logged and swallowed, so the
            # binding silently never happened while everything reported success.
            text("""UPDATE plenum_cafm.documents
                       SET building_id = CAST(:b AS uuid)
                     WHERE document_id::text = ANY(:ids)
                       AND (building_id IS NULL OR building_id::text <> :b)"""),
            {"b": building_id, "ids": document_ids},
        )
        certs = await session.execute(
            text("""UPDATE plenum_cafm.compliance_certificates
                       SET building_id = CAST(:b AS uuid)
                     WHERE COALESCE(document_id, source_document_id)::text = ANY(:ids)
                       AND (building_id IS NULL OR building_id::text <> :b)"""),
            {"b": building_id, "ids": document_ids},
        )
        await session.commit()
        return {"documents": docs.rowcount or 0, "certificates": certs.rowcount or 0,
                "created": created.rowcount or 0}


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
    # Everything that arrived under this session, whether or not an engine mentioned it.
    # Union rather than fallback: the tool calls sometimes name a document the session
    # lookup cannot see (an id reused from an earlier upload), and the session lookup
    # routinely names ones the tool calls omit.
    try:
        for extra in await documents_from_session(session_id):
            if extra not in ids:
                ids.append(extra)
    except Exception as exc:  # noqa: BLE001 — a lookup failure must not lose the ingest
        log.warning("ingest.session_lookup_failed", where=where, session_id=session_id,
                    error=str(exc)[:200])
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
