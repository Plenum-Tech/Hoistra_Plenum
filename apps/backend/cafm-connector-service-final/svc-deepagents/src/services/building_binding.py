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

import hashlib
from pathlib import Path
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

#: A tool that consumes a whole uploaded file without indexing it, and what that file
#: therefore is. Only tools that write no ingestion_documents row of their own belong here:
#: everything routed through doc-rag already records what it indexed, and a second opinion
#: about a row that exists is how one file becomes two.
_FILE_TOOL_DOC_TYPES: dict[str, str] = {"ingest_meter_readings": "meter_readings"}


def _doc_types_by_file(tool_calls: Any) -> dict[str, str]:
    """Filename -> document type, for the files an unindexed tool reported consuming."""
    out: dict[str, str] = {}
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        doc_type = _FILE_TOOL_DOC_TYPES.get(str(call.get("tool") or ""))
        if not doc_type:
            continue
        inp = call.get("input")
        name = str((inp or {}).get("file") or "").strip() if isinstance(inp, dict) else ""
        if name:
            out[name] = doc_type
    return out


async def register_uploads(
    session_id: str, file_paths: list[str] | None, tool_calls: Any = None
) -> int:
    """Give every uploaded file a row, for the engines that do not write one.

    Returns how many rows this created. Files already registered are left alone — this
    fills gaps, it does not restate what an engine has already said about its own work.

    The one-hour window matches documents_from_session(): a filename is only unique within
    an upload, and a row from hours ago belongs to a different one.
    """
    names = [Path(p).name for p in (file_paths or []) if str(p).strip()]
    if not names:
        return 0
    types = _doc_types_by_file(tool_calls)
    made = 0
    async with database.AsyncSessionLocal() as session:
        for name in names:
            res = await session.execute(
                text(
                    # CAST(:n AS varchar) at BOTH occurrences, and not a bare :n. The name
                    # is emitted as one parameter, and Postgres deduced its type twice from
                    # two contexts that disagreed — text from the SELECT list, varchar from
                    # the comparison against the column — and refused the statement with
                    # "inconsistent types deduced for parameter $1". An explicit cast leaves
                    # the parameter unknown at each site and both resolve to the same type.
                    # It compiles cleanly either way, so this only ever fails against a real
                    # server; here it failed on every upload and was swallowed by design.
                    """INSERT INTO plenum_cafm.ingestion_documents
                           (original_filename, source_type, agent_id, status, document_type)
                       SELECT CAST(:n AS varchar), 'document', 'uploader', 'received',
                              CAST(:dt AS varchar)
                        WHERE NOT EXISTS (
                              SELECT 1 FROM plenum_cafm.ingestion_documents
                               WHERE original_filename = CAST(:n AS varchar)
                                 AND uploaded_at > now() - interval '1 hour')"""
                ),
                {"n": name, "dt": types.get(name)},
            )
            made += res.rowcount or 0
        await session.commit()
    return made


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


#: Columns that point at a document without declaring a foreign key. Nothing in the schema
#: constrains these, so a deleted document silently orphans every one of them unless they
#: are re-pointed by name. Fixed list, filtered through information_schema before it reaches
#: SQL — the repo rule about never naming a table this file did not choose itself.
#: `contracts` and `invoices` are views over these tables and follow them.
#: `udr_run_versions.document_ids` is an array and is deliberately left alone: rewriting one
#: element of a recorded run would edit history rather than repair a link.
_DOCUMENT_REFERENCES: tuple[tuple[str, str], ...] = (
    ("compliance_certificates", "document_id"),
    ("compliance_certificates", "source_document_id"),
    ("compliance_vector_membership_audit", "document_id"),
    ("contract_documents", "document_id"),
    ("contract_sla_parameters", "document_id"),
    ("invoice_verifications", "document_id"),
    ("ppm_visits", "source_document_id"),
)

#: A saved upload is "{session_id}_{original name}". The prefix changes on every upload of
#: the same file, which is exactly why the duplicates were not visible as duplicates.
_SESSION_PREFIX = (
    "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_"
)

_FROM = """
      FROM plenum_cafm.documents d
      LEFT JOIN plenum_cafm.ingestion_documents i ON i.id = d.document_id
     WHERE d.building_id = CAST(:b AS uuid)
       AND d.file_name IS NOT NULL
"""
_IDS = ("array_agg(d.document_id::text ORDER BY d.uploaded_at NULLS LAST, "
        "d.document_id) AS ids")

#: Same bytes, whatever the file was called.
_GROUP_BY_CONTENT = f"""
    SELECT i.file_hash_sha256 AS group_key, {_IDS}
    {_FROM}  AND i.file_hash_sha256 IS NOT NULL
     GROUP BY 1
    HAVING count(*) > 1
"""

#: Same original name, where nothing proves the files differ. count(DISTINCT h) ignores
#: nulls: {{null, 'abc'}} counts 1 and merges — which is how a row written before hashing
#: joins a freshly hashed one — while {{'abc', 'def'}} counts 2 and is left alone, because
#: those are two different files that happen to share a name.
_GROUP_BY_NAME = f"""
    SELECT regexp_replace(d.file_name, '{_SESSION_PREFIX}', '') AS group_key, {_IDS}
    {_FROM}
     GROUP BY 1
    HAVING count(*) > 1
       AND count(DISTINCT i.file_hash_sha256) <= 1
"""


def file_sha256(path: str) -> str | None:
    """The content hash of an uploaded file, or None if it cannot be read.

    None rather than an exception: a hash is what lets two uploads be recognised as one
    file, and failing to compute it should cost that recognition, never the upload.
    """
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError as exc:
        log.warning("ingest.hash_failed", path=str(path)[-80:], error=str(exc)[:200])
        return None


async def stamp_upload_hashes(session_id: str, file_paths: list[str] | None) -> int:
    """Record each uploaded file's content hash against its ingestion row.

    Whoever wrote the row — doc-rag for what it indexed, register_uploads for what nothing
    claimed — the bytes are only ever here, so the hash is computed here and written back.
    Only a row with no hash is stamped: an existing value was computed from the same bytes
    and overwriting it could only ever replace it with itself or with a mistake.
    """
    paths = [p for p in (file_paths or []) if str(p).strip()]
    if not paths:
        return 0
    stamped = 0
    async with database.AsyncSessionLocal() as session:
        for path in paths:
            digest = file_sha256(path)
            if not digest:
                continue
            res = await session.execute(
                text(
                    """UPDATE plenum_cafm.ingestion_documents
                          SET file_hash_sha256 = CAST(:h AS varchar)
                        WHERE original_filename = CAST(:n AS varchar)
                          AND file_hash_sha256 IS NULL"""
                ),
                {"h": digest, "n": Path(path).name},
            )
            stamped += res.rowcount or 0
        await session.commit()
    return stamped


async def _referencing_columns(session) -> list[tuple[str, str]]:
    """The soft references this database actually has, out of the ones listed above."""
    rows = (
        await session.execute(
            text(
                """SELECT table_name, column_name
                     FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm'"""
            )
        )
    ).all()
    have = {(str(t), str(c)) for t, c in rows}
    return [pair for pair in _DOCUMENT_REFERENCES if pair in have]


async def collapse_duplicate_documents(building_id: str) -> dict[str, Any]:
    """One file filed twice against one building becomes one document.

    Two passes. By content: rows that have a hash, grouped by it — the same bytes are one
    document however the file was named. Then by name: grouped by the original filename,
    merging only where the group holds at most one distinct hash.

    That second condition is the whole rule for the transition. count(DISTINCT h) ignores
    nulls, so a row written before hashes existed ({null}) joins a freshly hashed one
    ({'abc'}) under the same name, while two rows with different hashes never merge however
    they are named. An earlier version keyed on `hash, else name` and so refused to merge
    precisely that pair: one old row, one new, the same file.

    Neither pass includes doc_type. That is a derived classification and an unstable one:
    the same certificate has been filed as `compliance_certificate` on one upload and left
    NULL on another, and keying on it would split one file into two documents for a reason
    that says nothing about the file.

    The earliest row is kept and its gaps filled from the ones it absorbs, so a duplicate is
    never traded for a document that has lost its file.
    """
    async with database.AsyncSessionLocal() as session:
        columns = await _referencing_columns(session)
        removed = repointed = 0
        group_count = 0
        # Content first, so a merge that can be proven from the bytes happens before names
        # are consulted at all.
        for sql in (_GROUP_BY_CONTENT, _GROUP_BY_NAME):
            groups = (
                await session.execute(text(sql), {"b": building_id})
            ).all()
            group_count += len(groups)
            for group_key, ids in groups:
                keep, drop = str(ids[0]), [str(i) for i in ids[1:]]
                # Fill the survivor's gaps before the others go: a later upload may carry the
                # blob_url or the doc_type that the first one never got.
                await session.execute(
                    text(
                        """UPDATE plenum_cafm.documents k
                              SET blob_url  = COALESCE(k.blob_url,  f.blob_url),
                                  doc_type  = COALESCE(k.doc_type,  f.doc_type),
                                  title     = COALESCE(k.title,     f.title),
                                  file_name = COALESCE(k.file_name, f.file_name)
                             FROM (SELECT max(blob_url) AS blob_url, max(doc_type) AS doc_type,
                                          max(title) AS title, max(file_name) AS file_name
                                     FROM plenum_cafm.documents
                                    WHERE document_id::text = ANY(:ids)) f
                            WHERE k.document_id::text = :keep"""
                    ),
                    {"ids": drop, "keep": keep},
                )
                for table, column in columns:
                    res = await session.execute(
                        text(
                            f"""UPDATE plenum_cafm.{table}
                                   SET {column} = CAST(:keep AS uuid)
                                 WHERE {column}::text = ANY(:ids)"""
                        ),
                        {"keep": keep, "ids": drop},
                    )
                    repointed += res.rowcount or 0
                res = await session.execute(
                    text("DELETE FROM plenum_cafm.documents WHERE document_id::text = ANY(:ids)"),
                    {"ids": drop},
                )
                removed += res.rowcount or 0
                log.info("ingest.documents_collapsed", building_id=building_id,
                         kept=keep, removed=len(drop), group=str(group_key)[:60])
        await session.commit()
        return {"groups": group_count, "documents_removed": removed,
                "references_repointed": repointed}


async def bind_and_log(
    building_id: str | None, tool_calls: Any, *, where: str, session_id: str = "",
    file_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Bind, and never let a binding failure take the ingest down with it.

    The file is indexed either way. An unbound document can be bound later from the row it
    already is; an upload that 500s because the link failed is gone.
    """
    # Registration first, and whether or not a building was chosen: a file that arrived
    # should have a row saying so. Without one there is nothing for the drawer to list and
    # nothing here to bind — which is how a half-hourly CSV wrote 48 readings while the
    # building it was filed against showed no document at all.
    try:
        made = await register_uploads(session_id, file_paths, tool_calls)
        if made:
            log.info("ingest.registered_uploads", where=where, session_id=session_id,
                     created=made)
        # The bytes are only ever here. Without this every row's file_hash_sha256 stays
        # NULL, which is how the same file came to be filed four times.
        hashed = await stamp_upload_hashes(session_id, file_paths)
        if hashed:
            log.info("ingest.hashes_stamped", where=where, session_id=session_id,
                     stamped=hashed)
    except Exception as exc:  # noqa: BLE001 — see the docstring below
        log.warning("ingest.register_failed", where=where, session_id=session_id,
                    error=str(exc)[:200])
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
    # Re-uploading a file is not filing a second document. This runs after binding, so the
    # rows being compared are all on the building by then.
    try:
        collapsed = await collapse_duplicate_documents(building_id)
        if collapsed.get("documents_removed"):
            log.info("ingest.duplicates_collapsed", where=where, session_id=session_id,
                     building_id=building_id, **collapsed)
        bound = dict(bound, **{"collapsed_" + k: v for k, v in collapsed.items()})
    except Exception as exc:  # noqa: BLE001 — a duplicate is a blemish; losing the upload
        # over tidying one is not a trade worth making.
        log.warning("ingest.collapse_failed", where=where, session_id=session_id,
                    building_id=building_id, error=str(exc)[:200])
    log.info("ingest.bound_to_building", where=where, session_id=session_id,
             building_id=building_id, candidates=len(ids), **bound)
    return dict(bound, candidates=len(ids))
