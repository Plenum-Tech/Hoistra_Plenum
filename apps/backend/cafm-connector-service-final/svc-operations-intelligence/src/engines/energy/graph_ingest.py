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


#: Column spellings that might hold a work order's human reference.
_WO_CODE_COLS = ("wo_code", "work_order_code", "code", "reference", "wo_ref")


async def building_from_work_orders(
    session: AsyncSession, wo_codes: list[Any]
) -> dict[str, Any]:
    """The building the billed work orders sit on — when they agree on exactly one.

    An invoice rarely names a building. It names the work it is billing for, and that work
    is already placed. So the invoice belongs where its work orders are, which is stronger
    evidence than an address block on the letterhead — that address is as often the
    vendor's own.

    Work orders spanning two buildings resolve to nothing. An invoice covering a campus is
    a real thing, and picking one of its buildings would put the whole invoice value
    against a building that only earned part of it.
    """
    codes = [str(c).strip() for c in (wo_codes or []) if str(c or "").strip()]
    if not codes:
        return {"building_id": None, "reason": "no_work_orders"}

    shape = await graph_shape(session)
    wo = shape["work_orders"]
    if not wo["exists"] or "building_id" not in wo["columns"]:
        return {"building_id": None, "reason": "work_orders_not_on_the_graph"}

    code_col = next((c for c in _WO_CODE_COLS if c in wo["columns"]), None)
    if not code_col:
        return {"building_id": None, "reason": "work_orders_have_no_code_column"}

    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(
                        f"""SELECT DISTINCT building_id::text AS bid
                            FROM plenum_cafm.work_orders
                            WHERE building_id IS NOT NULL
                              AND upper({code_col}::text) = ANY(:codes)"""
                    ),
                    {"codes": [c.upper() for c in codes]},
                )
            ).all()
    except Exception as exc:  # noqa: BLE001
        log.warning("graph_ingest.wo_lookup_failed", error=str(exc)[:200])
        return {"building_id": None, "reason": "work_order_lookup_failed"}

    found = [r[0] for r in rows if r[0]]
    if len(found) == 1:
        return {"building_id": found[0], "reason": "work_orders_agree"}
    if len(found) > 1:
        return {
            "building_id": None,
            "reason": "work_orders_span_buildings",
            "candidates": found[:10],
        }
    return {"building_id": None, "reason": "work_orders_have_no_building"}


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
    work_order_codes: list[Any] | None = None,
    doc_type: str | None = None,
    title: str | None = None,
    file_name: str | None = None,
    blob_url: str | None = None,
) -> dict[str, Any]:
    """Resolve the building, then record the document against it. One call per ingested file.

    The return is what the ingesting row stores alongside itself, so a link can always be
    read back with the basis it was made on rather than appearing as a bare foreign key.
    """
    resolved = await resolve_building_for(
        session,
        building_name=building_name,
        building_reference=building_reference,
        site_name=site_name,
        site_id=site_id,
    )

    # Nothing the document itself said placed it. An invoice or a contract may still be
    # placeable through the work it bills, which is already on the graph. Tried second
    # because a document naming its own building is more direct evidence than an inference
    # from its line items.
    if not resolved["building_id"] and work_order_codes:
        via_wo = await building_from_work_orders(session, work_order_codes)
        if via_wo["building_id"]:
            resolved = {
                "building_id": via_wo["building_id"],
                "building_label": None,
                "outcome": "resolved",
                "reason": via_wo["reason"],
            }
        elif via_wo["reason"] != "no_work_orders":
            # Keep the more informative refusal: "these WOs are in two buildings" tells
            # someone what to do; "nothing matched" does not.
            resolved = {
                **resolved,
                "reason": via_wo["reason"],
                "outcome": (
                    "review" if via_wo["reason"] == "work_orders_span_buildings"
                    else resolved["outcome"]
                ),
            }
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
