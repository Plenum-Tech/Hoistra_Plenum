"""Put an ingested document into the building graph.

Two steps that every ingestion path needs and none of them had:

* ``record_document`` — a row in ``plenum_cafm.documents`` for the file itself, linked to the
  building it belongs to. The graph reads ``documents ──< certificates``, so a certificate
  with no document row is a leaf hanging off nothing.
* ``resolve_building_for`` — the building a document belongs to, decided by
  ``building_resolver`` and recorded WITH ITS REASON. A link that was inferred from a site
  with one building is a different claim from one matched on an exact code, and the record
  says which it was.

Both are best-effort by construction: a document that cannot be placed is still ingested,
with the reason stored, because a certificate that exists is worth more than a certificate
rejected for want of a building. Nothing here raises into an ingest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import building_resolver
from .building_rollup import graph_shape

log = get_logger(__name__)


async def resolve_building_for(
    session: AsyncSession,
    *,
    building_name: Any = None,
    building_reference: Any = None,
    site_name: Any = None,
    site_id: Any = None,
) -> dict[str, Any]:
    """Which building this document belongs to, with the reason it was decided that way.

    Returns ``{building_id, outcome, reason}``. ``building_id`` is None for anything but a
    clean resolution — an ambiguous match is not written as a link, it is left for review.
    """
    try:
        index = await building_resolver.load_building_index(session)
    except Exception as exc:  # noqa: BLE001 — never break an ingest
        log.warning("graph_ingest.index_failed", error=str(exc)[:200])
        return {"building_id": None, "outcome": "unmatched", "reason": "index_unavailable"}

    row, reason = building_resolver.choose_building(
        index,
        name=building_name,
        code=building_reference,
        site_name=site_name,
        site_id=site_id,
    )
    outcome = building_resolver.resolution_outcome(reason)
    return {
        "building_id": row["building_id"] if row and outcome == "resolved" else None,
        "building_label": row["label"] if row else None,
        "outcome": outcome,
        "reason": reason,
    }


async def record_document(
    session: AsyncSession,
    *,
    document_id: Any = None,
    building_id: Any = None,
    doc_type: str | None = None,
    title: str | None = None,
    file_name: str | None = None,
    blob_url: str | None = None,
) -> dict[str, Any]:
    """Upsert the file's row in plenum_cafm.documents. Returns {document_id, created}.

    ``document_id`` is reused when the caller already has one — the ingestion pipeline
    assigns an id per file before extraction, and the graph must point at that same file
    rather than a second identity for it.
    """
    shape = await graph_shape(session)
    if not shape["documents"]["exists"]:
        return {"document_id": None, "created": False, "skipped": "documents table absent"}

    did = str(document_id).strip() if document_id else str(uuid.uuid4())
    key = shape["documents"]["key"]
    params = {
        "did": did,
        "bid": str(building_id) if building_id else None,
        "dt": (doc_type or None),
        "t": (title or None),
        "fn": (file_name or None),
        "url": (blob_url or None),
        "now": datetime.now(timezone.utc),
    }
    try:
        async with session.begin_nested():
            existing = (
                await session.execute(
                    text(f"SELECT 1 FROM plenum_cafm.documents WHERE {key}::text = :did"),
                    {"did": did},
                )
            ).first()
            if existing:
                # Only fill what is missing — a later pass must not blank a value an
                # earlier, better-informed one wrote.
                await session.execute(
                    text(
                        f"""UPDATE plenum_cafm.documents SET
                                building_id = COALESCE(building_id, CAST(:bid AS UUID)),
                                doc_type    = COALESCE(doc_type, :dt),
                                title       = COALESCE(title, :t),
                                file_name   = COALESCE(file_name, :fn),
                                blob_url    = COALESCE(blob_url, :url)
                            WHERE {key}::text = :did"""
                    ),
                    params,
                )
                return {"document_id": did, "created": False}

            await session.execute(
                text(
                    f"""INSERT INTO plenum_cafm.documents
                            ({key}, building_id, doc_type, title, file_name, blob_url, uploaded_at)
                        VALUES (CAST(:did AS UUID), CAST(:bid AS UUID), :dt, :t, :fn, :url, :now)"""
                ),
                params,
            )
        return {"document_id": did, "created": True}
    except Exception as exc:  # noqa: BLE001 — a document row must never fail an ingest
        log.warning("graph_ingest.record_document_failed", error=str(exc)[:200], document_id=did)
        return {"document_id": did, "created": False, "error": str(exc)[:200]}


async def attach_to_graph(
    session: AsyncSession,
    *,
    document_id: Any = None,
    building_name: Any = None,
    building_reference: Any = None,
    site_name: Any = None,
    site_id: Any = None,
    doc_type: str | None = None,
    title: str | None = None,
    file_name: str | None = None,
    blob_url: str | None = None,
) -> dict[str, Any]:
    """Resolve the building, then record the document against it. One call per ingested file.

    The return is what the certificate row stores alongside itself, so a link can always be
    read back with the basis it was made on rather than appearing as a bare foreign key.
    """
    resolved = await resolve_building_for(
        session,
        building_name=building_name,
        building_reference=building_reference,
        site_name=site_name,
        site_id=site_id,
    )
    doc = await record_document(
        session,
        document_id=document_id,
        building_id=resolved["building_id"],
        doc_type=doc_type,
        title=title,
        file_name=file_name,
        blob_url=blob_url,
    )
    return {
        "building_id": resolved["building_id"],
        "building_label": resolved.get("building_label"),
        "building_link_outcome": resolved["outcome"],
        "building_link_reason": resolved["reason"],
        "document_id": doc.get("document_id"),
        "document_created": doc.get("created", False),
    }
