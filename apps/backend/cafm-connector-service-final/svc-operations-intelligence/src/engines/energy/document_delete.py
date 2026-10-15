"""Removing a document, and everything that was read out of it.

A document in this platform is not one row. It is a row in ``documents`` (the graph's
record that it exists and which building it was filed against), a row in
``ingestion_documents`` (the file that was uploaded, with its blob), the chunks and
embeddings the assistant answers from, and — the reason this module exists — whatever
extraction turned it into: a certificate on Compliance, contract terms and invoice lines on
Vendors, a PPM visit on Maintenance.

Two facts about this schema shape everything below.

**The two halves share one uuid.** ``documents.document_id`` IS ``ingestion_documents.id``:
every writer sets it that way (``building_binding`` inserts ``SELECT i.id``, ``repair_links``
joins ``i.id = r.document_id``). So one id reaches the graph side and the file side, and the
whole removal is one transaction rather than two services guessing at each other by filename.

**Nothing in the database enforces the rest.** Of 188 foreign keys in this schema, not one
points at ``plenum_cafm.documents``. Every ``document_id`` on every other table is a loose
uuid with no constraint behind it. There is no ``ON DELETE CASCADE`` to lean on and no error
raised if a reference is left behind — the certificate simply stays on Compliance, pointing
at a document that no longer exists, and the screen has no way to know. So the cascade is
written out by hand in ``_CASCADE``, and ``tests/unit/test_c_document_delete.py`` is what
keeps it honest: a table dropped from that list fails a test instead of quietly outliving
the document.

``ingestion_documents`` is the exception — it *does* have foreign keys, and deleting it
takes ``corrections_log``, ``ingestion_audit_log`` and ``review_queue`` with it and unlinks
``claude_api_usage`` and ``inspections``. None of that appears in the SQL here, so it is
counted deliberately and reported as ``database_cascades``. A delete that quietly removes an
audit log is worse than one that says it will.

Two-phase, like ``delete_building``: a call without ``confirm`` executes no writes and
reports what it would remove, so the dialog can say "3 certificates and 16 contract terms"
instead of "are you sure?". That dry run is the only thing that runs against production
before a person has read the numbers, so it is tested for writing nothing at all.

The original file in blob storage is **kept**. Everything the platform holds about the
document goes, and no screen can reach it any more, but the PDF itself survives so a delete
made in error can be re-ingested. There is no undo for the rows.
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...shared.approvals import write_audit

log = get_logger(__name__)

#: Identifiers are only ever taken from the literals in this module, and are checked against
#: this before they are formatted into SQL. The document id never is — it is bound.
_SAFE = re.compile(r"^[a-z_][a-z0-9_]*$")


class Link(NamedTuple):
    """One table that names a document, and the columns it could name it with.

    ``columns`` is a preference list, not a guess: a table is matched on *every* candidate
    it actually has, because which one is populated depends on which ingest wrote the row.
    """

    table: str
    columns: tuple[str, ...]
    label: str


#: Everything that carries this document's id, in the order it is removed: what was read out
#: of the document first, then the document, then the file it came from. Children first means
#: a failure partway can never leave a row whose document is already gone — which is the
#: dangling state this module exists to prevent.
#:
#: Each entry is a screen. Drop one and the document vanishes from Buildings while its
#: extract lives on where a person will still read it as current.
_CASCADE: tuple[Link, ...] = (
    # The assistant answers out of these. Left behind, it keeps citing a deleted document.
    Link("document_chunks", ("ingestion_id", "document_id"), "extracted text and embeddings"),
    Link("compliance_vector_membership_audit", ("document_id",), "vector membership rows"),
    # Compliance. Both columns: which one is set depends on the ingest that wrote the cert.
    Link("compliance_certificates", ("document_id", "source_document_id"), "certificates"),
    # Vendors. plenum_cafm.contracts is a VIEW over contract_sla_parameters, so when this
    # document was a contract's only source the contract leaves the Vendors page with it.
    Link("contract_sla_parameters", ("document_id",), "contract terms"),
    # The contract↔document link only. A contract evidenced by another document survives.
    Link("contract_documents", ("document_id",), "contract document links"),
    Link("invoice_verifications", ("document_id",), "invoice verifications"),
    Link("ppm_visits", ("source_document_id",), "PPM visits"),
    # The graph's record of the document, then the ingested file itself.
    Link("documents", ("document_id",), "the document record"),
    Link("ingestion_documents", ("id",), "the ingested file"),
)

#: What the database's own foreign keys do when ingestion_documents goes. Counted for the
#: report, never deleted here — Postgres does it, and the reader should know before deciding.
_FK_DELETES: tuple[Link, ...] = (
    Link("corrections_log", ("ingestion_id",), "correction history"),
    Link("ingestion_audit_log", ("ingestion_id",), "ingestion audit log"),
    Link("review_queue", ("ingestion_id",), "review queue entries"),
)
_FK_UNLINKS: tuple[Link, ...] = (
    Link("claude_api_usage", ("ingestion_id",), "API usage rows"),
    Link("inspections", ("ingestion_id",), "inspections"),
)

_ALL_LINKS = _CASCADE + _FK_DELETES + _FK_UNLINKS


async def _columns(session: AsyncSession) -> dict[str, set[str]]:
    """Which of these tables this database has, and what columns they carry.

    Read rather than assumed, because deployments genuinely differ: ``01_schema.sql`` gives
    ``document_chunks`` a ``document_id``, while ``05_docrag_compat.sql`` drops that table
    and recreates it with ``ingestion_id``. Assuming either one leaves the embeddings behind
    on half the deployments, and leaving embeddings behind is invisible — the assistant goes
    on answering out of a document nobody can find.

    Not cached. A shape read once per process is right for a rollup that renders a number;
    this decides what a destructive statement matches on.
    """
    wanted = sorted({link.table for link in _ALL_LINKS})
    try:
        rows = (
            await session.execute(
                text(
                    """SELECT table_name, column_name FROM information_schema.columns
                        WHERE table_schema = 'plenum_cafm' AND table_name = ANY(:t)"""
                ),
                {"t": wanted},
            )
        ).all()
    except Exception as exc:  # noqa: BLE001
        log.warning("document_delete.shape_failed", error=str(exc)[:200])
        return {}
    out: dict[str, set[str]] = {}
    for table, column in rows:
        out.setdefault(str(table), set()).add(str(column))
    return out


def _match(link: Link, have: set[str]) -> str | None:
    """``WHERE`` clause matching this document on every column the table actually has."""
    cols = [c for c in link.columns if c in have and _SAFE.match(c)]
    if not cols:
        return None
    return " OR ".join(f"{c}::text = :d" for c in cols)


async def _count(session: AsyncSession, table: str, where: str, doc: str) -> int:
    try:
        n = (
            await session.execute(
                text(f"SELECT count(*) FROM plenum_cafm.{table} WHERE {where}"), {"d": doc}
            )
        ).scalar()
        return int(n or 0)
    except Exception as exc:  # noqa: BLE001 — one unreadable table must not lose the plan
        log.warning("document_delete.count_failed", table=table, error=str(exc)[:200])
        return 0


async def delete_document(
    session: AsyncSession,
    document_id: str,
    *,
    confirm: bool = False,
    actor: str = "hoistra-ui",
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Remove a document and everything read out of it. Reports and changes nothing unless
    ``confirm`` is true.

    Unlike ``delete_building``, which detaches its children and keeps them, this deletes
    them. The reasoning differs because the records differ: a certificate that outlives its
    building is still the record of an inspection that happened, but a certificate extracted
    from a document that should never have been ingested is not a record of anything — it is
    an extraction artefact, and the only way to be rid of it is to delete it. That is also
    what makes this the cure for a bad ingest, where a non-FM document arrives as a contract
    full of invented platform defaults.

    The original file is kept in blob storage. There is no undo for the rows.
    """
    raw = str(document_id or "").strip()
    try:
        doc = str(UUID(raw))
    except (ValueError, AttributeError, TypeError):
        return {"ok": False, "status": 400,
                "errors": {"document_id": "Not a document id."}}

    have = await _columns(session)

    row = None
    if "documents" in have:
        try:
            row = (
                await session.execute(
                    text(
                        """SELECT document_id::text AS document_id, building_id::text AS building_id,
                                  doc_type, file_name
                             FROM plenum_cafm.documents WHERE document_id::text = :d"""
                    ),
                    {"d": doc},
                )
            ).mappings().first()
        except Exception as exc:  # noqa: BLE001
            log.warning("document_delete.lookup_failed", error=str(exc)[:200])

    # A document that was ingested but never bound to a building has no `documents` row yet;
    # it is still a real document and still deletable, so the file record answers for it.
    if row is None and "ingestion_documents" in have:
        try:
            row = (
                await session.execute(
                    text(
                        """SELECT id::text AS document_id, NULL AS building_id,
                                  NULL AS doc_type, original_filename AS file_name
                             FROM plenum_cafm.ingestion_documents WHERE id::text = :d"""
                    ),
                    {"d": doc},
                )
            ).mappings().first()
        except Exception as exc:  # noqa: BLE001
            log.warning("document_delete.ingestion_lookup_failed", error=str(exc)[:200])

    if row is None:
        return {"ok": False, "status": 404,
                "errors": {"document_id": f"No document {doc}."}}

    # ── what it would remove ────────────────────────────────────────────────
    removes: dict[str, int] = {}
    labels: dict[str, str] = {}
    unavailable: list[str] = []
    plans: list[tuple[Link, str]] = []
    for link in _CASCADE:
        where = _match(link, have.get(link.table, set()))
        if where is None:
            unavailable.append(link.table)
            continue
        plans.append((link, where))
        n = await _count(session, link.table, where, doc)
        if n:
            removes[link.table] = n
            labels[link.table] = link.label

    cascades: dict[str, dict[str, int]] = {"deleted": {}, "unlinked": {}}
    for kind, group in (("deleted", _FK_DELETES), ("unlinked", _FK_UNLINKS)):
        for link in group:
            where = _match(link, have.get(link.table, set()))
            if where is None:
                continue
            n = await _count(session, link.table, where, doc)
            if n:
                cascades[kind][link.table] = n

    plan = {
        "ok": True,
        "document_id": doc,
        "file_name": row.get("file_name"),
        "doc_type": row.get("doc_type"),
        "building_id": row.get("building_id"),
        "removes": removes,
        "removes_total": sum(removes.values()),
        "labels": labels,
        "database_cascades": cascades,
        "unavailable": unavailable,
        "blob_url_kept": True,
    }

    if not confirm:
        return {**plan, "status": 200, "dry_run": True,
                "message": "Nothing was changed. Re-send with confirm=true to delete."}

    # ── the removal ─────────────────────────────────────────────────────────
    deleted: dict[str, int] = {}
    try:
        for link, where in plans:
            res = await session.execute(
                text(f"DELETE FROM plenum_cafm.{link.table} WHERE {where}"), {"d": doc}
            )
            n = int(getattr(res, "rowcount", 0) or 0)
            if n:
                deleted[link.table] = n
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        log.warning("document_delete.failed", error=str(exc)[:250], document_id=doc)
        return {**plan, "ok": False, "status": 400,
                "errors": {"_": f"Could not delete: {str(exc)[:200]}"}}

    # The rows are gone; the audit row is what is left to say they existed, so it carries
    # the counts and the document's identity rather than only its id.
    audit_error = None
    try:
        async with session.begin_nested():
            await write_audit(
                session,
                actor=actor,
                action_type="document.delete",
                source_feature="C",
                organization_id=organization_id,
                input_payload={"document_id": doc},
                output_payload={"deleted": deleted, "file_name": row.get("file_name")},
                detail={"deleted": deleted, "database_cascades": cascades,
                        "building_id": row.get("building_id"),
                        "doc_type": row.get("doc_type"),
                        "blob_url_kept": True},
            )
    except Exception as exc:  # noqa: BLE001 — the delete happened; saying so must not undo it
        audit_error = str(exc)[:200]
        log.error("document_delete.audit_failed", error=audit_error, document_id=doc)

    await session.commit()

    total = sum(deleted.values())
    name = row.get("file_name") or doc[:8]
    warnings: list[str] = []
    if audit_error:
        warnings.append(f"Deleted, but the audit row could not be written: {audit_error}")
    if unavailable:
        warnings.append(
            "Not present in this database, so not checked: " + ", ".join(sorted(unavailable))
        )

    log.info("document_delete.done", document_id=doc, rows=total, tables=len(deleted))
    return {
        **plan,
        "status": 200,
        "deleted": deleted,
        "deleted_total": total,
        "warnings": warnings,
        "message": f"Deleted {name} and {total} row{'' if total == 1 else 's'} read out of it.",
    }
