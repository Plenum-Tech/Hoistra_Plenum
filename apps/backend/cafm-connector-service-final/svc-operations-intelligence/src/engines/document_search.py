"""Search inside the documents a question is about - scoped to the caller's company, cited by page.

A fact an answer needs is either in the records (a count, a date, a status) or in a document (a
contract clause, a report's recommendation, a warranty term, a procedure). The chat's document
search (doc-rag) searched every indexed chunk of every company, with no login and no company
filter, and nothing tied a chunk to the record it was about (2 Oct 2026). This read starts from
the document REGISTER instead:

    1. the documents linked to what the question is about - a vendor's contracts and certificates,
       a contract reference, a building's documents, a document type - and only those the caller's
       company (and buildings) own;
    2. their indexed text (document_chunks, joined through ingestion_documents by file name),
       ranked against the question with Postgres full-text search, a keyword match as fallback;
    3. the passages with file, page and heading - and, as plainly, the linked documents that are on
       file but NOT indexed, so an answer says so instead of searching the whole corpus or guessing.

A question that names nothing (the chat's general document search, which used to go straight to
doc-rag) searches every INDEXED document the caller's company and buildings own - the same register
scope, just no link filter - so a company never reads another company's documents.

Reads only, every section in its own savepoint. Nothing here calls a model.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from .value_ledger import _grab_rows

log = get_logger(__name__)

MAX_DOCUMENTS = 20
#: The indexed documents a company-wide search ranks across (no entity named).
MAX_COMPANY_DOCUMENTS = 400
MAX_PASSAGES = 8
PASSAGE_CHARS = 700
#: Words that carry no meaning for a search over FM documents.
_STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are", "what", "which", "does",
         "do", "with", "about", "from", "by", "me", "show", "tell", "give", "our", "we", "it", "this", "that",
         "say", "says", "document", "documents", "contract", "vendor", "please", "any", "all", "there"}


def search_terms(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\.]+", question or "")
    out: list[str] = []
    for w in words:
        lw = w.lower().strip(".")
        # Three letters or more, or a short code with a digit in it - P1, P2, L4 carry the question.
        if len(lw) < 3 and not re.search(r"\d", lw):
            continue
        if lw not in _STOP and lw not in out:
            out.append(lw)
    return out[:12]


#: The records that point at a register document, and the columns that carry the link. A company
#: owns its buildings, its users reach them through their allocation, and a document belongs to the
#: company of its building or of the record that cites it - so a register row with no company of
#: its own (134 of 194 on hoistra_test, every contract among them, 2 Oct 2026) is still placed.
_LINKS: list[tuple[str, str]] = [
    ("contract_sla_parameters", "document_id"), ("contracts", "document_id"),
    ("compliance_certificates", "document_id"), ("invoices", "document_id"),
    ("asset_warranties", "document_id"), ("asset_documents", "document_id"),
    ("work_orders", "document_id"), ("maintenance_plans", "document_id"),
    ("inspections", "document_id"), ("ppm_visits", "source_document_id"),
    ("work_order_attachments", "document_id"),
]
_COLS: dict[str, set[tuple[str, str]]] = {}


async def _columns(session: AsyncSession) -> set[tuple[str, str]]:
    """(table, column) pairs that exist here - the link columns differ by database and migration."""
    if "v" not in _COLS:
        rows = await _grab_rows(session, """
            SELECT table_name, column_name FROM information_schema.columns
             WHERE table_schema = 'plenum_cafm'
               AND column_name IN ('document_id', 'source_document_id', 'organization_id', 'org_id',
                                   'building_id', 'asset_id', 'vendor_id', 'vendor_name', 'sensitivity')""", {})
        _COLS["v"] = set() if isinstance(rows, dict) else {(r["table_name"], r["column_name"]) for r in rows}
    return _COLS["v"]


def _owner_exprs(cols: set[tuple[str, str]]) -> tuple[str, str]:
    """SQL for a register row's effective company and building: its own, else its building's
    company, else those of the records that cite it."""
    org_parts, bld_parts = ["d.organization_id::text"], ["d.building_id::text"]
    org_parts.append("(SELECT b.organization_id::text FROM plenum_cafm.buildings b WHERE b.building_id = d.building_id)")
    if ("document_links", "document_id") in cols:
        org_parts.append("(SELECT l.organization_id::text FROM plenum_cafm.document_links l WHERE l.document_id = d.document_id"
                         " AND l.removed_at IS NULL AND l.organization_id IS NOT NULL LIMIT 1)")
    for table, col in _LINKS:
        if (table, col) not in cols:
            continue
        ref = f"x.{col}::text = d.document_id::text"
        if (table, "organization_id") in cols:
            org = "x.organization_id" if (table, "org_id") not in cols else "coalesce(x.organization_id, x.org_id)"
            org_parts.append(f"(SELECT {org}::text FROM plenum_cafm.{table} x WHERE {ref} AND {org} IS NOT NULL LIMIT 1)")
        if (table, "building_id") in cols:
            bld_parts.append(f"(SELECT x.building_id::text FROM plenum_cafm.{table} x WHERE {ref} AND x.building_id IS NOT NULL LIMIT 1)")
    return "COALESCE(" + ", ".join(org_parts) + ")", "COALESCE(" + ", ".join(bld_parts) + ")"


def _scope(organization_id: UUID | None, building_ids: tuple[UUID, ...] | None,
           cols: set[tuple[str, str]]) -> tuple[str, dict[str, Any]]:
    """The register rows the caller may read: their company, and their buildings when allocated."""
    sql, p = "", {}
    org_expr, bld_expr = _owner_exprs(cols)
    if organization_id is not None:
        sql += f" AND {org_expr} = :ds_org"
        p["ds_org"] = str(organization_id)
    if building_ids is not None:
        if not building_ids:
            return " AND FALSE", {}
        linked = (" OR EXISTS (SELECT 1 FROM plenum_cafm.document_links l WHERE l.document_id = d.document_id"
                  " AND l.entity_type = 'building' AND l.removed_at IS NULL AND l.entity_id = ANY(:ds_b))"
                  if ("document_links", "document_id") in cols else "")
        sql += f" AND ({bld_expr} IS NULL OR {bld_expr} = ANY(:ds_b){linked})"
        p["ds_b"] = [str(b) for b in building_ids]
    return sql, p


async def linked_documents(session: AsyncSession, *, organization_id: UUID | None, building_ids: tuple[UUID, ...] | None,
                           vendor: str | None = None, contract_ref: str | None = None, building_id: UUID | None = None,
                           doc_type: str | None = None, document_ids: list[str] | None = None,
                           asset: str | None = None, include_personal: bool = False,
                           indexed_only: bool = False, limit: int = MAX_DOCUMENTS) -> list[dict[str, Any]] | dict:
    """The register documents the question is about, newest first."""
    from .document_register import DOC_TYPES

    cols = await _columns(session)
    scope_sql, p = _scope(organization_id, building_ids, cols)
    has_links = ("document_links", "document_id") in cols
    links: list[str] = []
    if not include_personal:
        # An identity paper stays out of open search (document_register.py).
        sens = "lower(coalesce(d.sensitivity, '')) = 'personal' OR " if ("documents", "sensitivity") in cols else ""
        scope_sql += f" AND NOT ({sens}lower(coalesce(d.doc_type, '')) = ANY(:ds_ptypes))"
        p["ds_ptypes"] = [k for k, v in DOC_TYPES.items() if v[2]]
    if asset:
        # An asset's paper: its certificates, warranties, documents, and the maintenance records
        # (work orders, PPM visits, inspections, plans) that cite a report or job sheet.
        via = [f"SELECT x.{c}::text FROM plenum_cafm.{t} x WHERE x.asset_id::text IN (SELECT a.id::text FROM "
               "plenum_cafm.assets a WHERE lower(a.asset_code) = lower(:ds_asset) OR a.asset_name ILIKE :ds_asset_like)"
               f" AND x.{c} IS NOT NULL"
               for t, c in _LINKS if (t, c) in cols and (t, "asset_id") in cols]
        if has_links:
            via.append("SELECT l.document_id::text FROM plenum_cafm.document_links l WHERE l.entity_type = 'asset' "
                       "AND l.removed_at IS NULL AND l.entity_id IN (SELECT a.id::text FROM plenum_cafm.assets a "
                       "WHERE lower(a.asset_code) = lower(:ds_asset) OR a.asset_name ILIKE :ds_asset_like)")
        if via:
            links.append("d.document_id::text IN (" + " UNION ".join(via) + ")")
            p["ds_asset"], p["ds_asset_like"] = asset.strip(), "%" + asset.strip() + "%"
    if document_ids:
        links.append("d.document_id::text = ANY(:ds_ids)")
        p["ds_ids"] = [str(x) for x in document_ids][:MAX_DOCUMENTS]
    if contract_ref:
        links.append("""d.document_id::text IN (SELECT s.document_id::text FROM plenum_cafm.contract_sla_parameters s
                                                 WHERE lower(s.contract_ref) = lower(:ds_cref) AND s.document_id IS NOT NULL)""")
        p["ds_cref"] = contract_ref.strip()
    if vendor:
        # A vendor's contracts (their SLA parameters name the source document) and its certificates.
        links.append("""(d.document_id::text IN (SELECT s.document_id::text FROM plenum_cafm.contract_sla_parameters s
                          JOIN plenum_cafm.vendors v ON v.id::text = s.vendor_id::text
                         WHERE v.vendor_name ILIKE :ds_vendor AND s.document_id IS NOT NULL)
                     OR d.document_id::text IN (SELECT c.document_id::text FROM plenum_cafm.compliance_certificates c
                         WHERE (c.vendor_name ILIKE :ds_vendor OR c.vendor_id::text IN
                                (SELECT v.id::text FROM plenum_cafm.vendors v WHERE v.vendor_name ILIKE :ds_vendor))
                           AND c.document_id IS NOT NULL)
                     OR d.title ILIKE :ds_vendor OR d.file_name ILIKE :ds_vendor""" + (
            " OR d.document_id IN (SELECT l.document_id FROM plenum_cafm.document_links l WHERE l.entity_type = 'vendor'"
            " AND l.removed_at IS NULL AND l.entity_id IN (SELECT v.id::text FROM plenum_cafm.vendors v"
            " WHERE v.vendor_name ILIKE :ds_vendor))" if has_links else "") + ")")
        p["ds_vendor"] = "%" + vendor.strip() + "%"
    if building_id:
        links.append(_owner_exprs(cols)[1] + " = :ds_bid" + (
            " OR d.document_id IN (SELECT l.document_id FROM plenum_cafm.document_links l WHERE l.entity_type = 'building'"
            " AND l.removed_at IS NULL AND l.entity_id = :ds_bid)" if has_links else ""))
        p["ds_bid"] = str(building_id)
    type_sql = ""
    if doc_type:
        type_sql = " AND (lower(coalesce(d.doc_type, '')) LIKE :ds_type OR lower(coalesce(d.title, '') || ' ' || coalesce(d.file_name, '')) LIKE :ds_type)"
        p["ds_type"] = "%" + doc_type.strip().lower() + "%"
    where = (" AND (" + " OR ".join(links) + ")") if links else ""
    if indexed_only:
        where += (" AND EXISTS (SELECT 1 FROM plenum_cafm.ingestion_documents i JOIN plenum_cafm.document_chunks k"
                  " ON k.ingestion_id = i.id WHERE i.original_filename = d.file_name)")
    return await _grab_rows(session, f"""
        SELECT d.document_id::text AS document_id, d.title, d.file_name, d.doc_type, d.building_id::text AS building_id,
               {"d.sensitivity" if ("documents", "sensitivity") in cols else "NULL"} AS sensitivity,
               (SELECT count(*) FROM plenum_cafm.ingestion_documents i
                  JOIN plenum_cafm.document_chunks k ON k.ingestion_id = i.id
                 WHERE i.original_filename = d.file_name) AS chunks
          FROM plenum_cafm.documents d
         WHERE TRUE{scope_sql}{where}{type_sql}
         ORDER BY coalesce(d.uploaded_at, d.created_at) DESC NULLS LAST
         LIMIT {int(limit)}""", p)


async def search(session: AsyncSession, *, question: str, organization_id: UUID | None,
                 building_ids: tuple[UUID, ...] | None = None, vendor: str | None = None, contract_ref: str | None = None,
                 building_id: UUID | None = None, doc_type: str | None = None,
                 document_ids: list[str] | None = None, asset: str | None = None,
                 include_personal: bool = False, user_id: UUID | None = None) -> dict[str, Any]:
    about = {k: v for k, v in {"vendor": vendor, "contract_ref": contract_ref, "asset": asset,
                               "building_id": str(building_id) if building_id else None,
                               "doc_type": doc_type, "document_ids": document_ids}.items() if v}
    # Nothing named -> every indexed document the caller's company owns (the scope still applies).
    company_wide = not (vendor or contract_ref or building_id or document_ids or asset)
    docs = await linked_documents(session, organization_id=organization_id, building_ids=building_ids, vendor=vendor,
                                  contract_ref=contract_ref, building_id=building_id, doc_type=doc_type,
                                  document_ids=document_ids, asset=asset, include_personal=include_personal,
                                  indexed_only=company_wide,
                                  limit=MAX_COMPANY_DOCUMENTS if company_wide else MAX_DOCUMENTS)
    if isinstance(docs, dict):
        return {"ok": False, "about": about, "error": docs.get("error")}
    indexed = [d for d in docs if int(d["chunks"] or 0) > 0]
    out: dict[str, Any] = {
        "ok": True, "question": question, "about": about or {"scope": "your company's indexed documents"},
        "documents_found": len(docs),
        "searched": [{"title": d["title"] or d["file_name"], "file": d["file_name"], "type": d["doc_type"]} for d in indexed],
        "not_indexed": [{"title": d["title"] or d["file_name"], "file": d["file_name"], "type": d["doc_type"],
                         "document_id": d["document_id"]} for d in docs if not int(d["chunks"] or 0)],
        "passages": [],
    }
    if company_wide:
        # Dozens of file names would only crowd the answer; the passages carry their own.
        out["searched"] = out["searched"][:MAX_DOCUMENTS]
    if not docs and company_wide:
        out["note"] = ("No document your company owns is indexed for search. Say so; do not answer from other sources "
                       "as if from a document.")
        return out
    if not docs:
        out["note"] = ("No document on file is linked to " + (", ".join(f"{k} {v}" for k, v in about.items()) or "this question")
                       + " for your company. Say so; do not search other documents.")
        return out
    if not indexed:
        out["note"] = ("The linked documents are on file but their text is not indexed, so nothing in them can be quoted. "
                       "Say so, name them, and use what the records extracted from them (e.g. a contract's clause and page).")
        return out
    terms = search_terms(question)
    files = [d["file_name"] for d in indexed]
    rows = await _grab_rows(session, f"""
        SELECT i.original_filename AS file, k.page_start, k.page_end, k.heading, k.chunk_text,
               ts_rank(to_tsvector('english', coalesce(k.chunk_text, '')), plainto_tsquery('english', :ds_q)) AS rank,
               (SELECT count(*) FROM unnest(CAST(:ds_terms AS text[])) t WHERE k.chunk_text ILIKE '%' || t || '%') AS hits
          FROM plenum_cafm.document_chunks k
          JOIN plenum_cafm.ingestion_documents i ON i.id = k.ingestion_id
         WHERE i.original_filename = ANY(:ds_files)
         ORDER BY rank DESC, hits DESC, k.page_start NULLS LAST
         LIMIT {MAX_PASSAGES * 3}""", {"ds_q": " ".join(terms) or question[:200], "ds_terms": terms or [question[:60]],
                                       "ds_files": files})
    if isinstance(rows, dict):
        out["ok"], out["error"] = False, rows.get("error")
        return out
    from .document_register import is_personal, mask_numbers

    title = {d["file_name"]: d["title"] or d["file_name"] for d in indexed}
    doc_id = {d["file_name"]: d["document_id"] for d in indexed}
    personal_files = {d["file_name"] for d in indexed if is_personal(d.get("doc_type"), d.get("sensitivity"))}
    hits = [r for r in rows if (r["rank"] or 0) > 0 or (r["hits"] or 0) > 0][:MAX_PASSAGES]
    out["passages"] = [{"document": title.get(r["file"], r["file"]), "file": r["file"], "document_id": doc_id.get(r["file"]),
                        "page": r["page_start"] if r["page_start"] == r["page_end"] or not r["page_end"]
                        else f"{r['page_start']}-{r['page_end']}",
                        "heading": r["heading"],
                        "text": (mask_numbers if r["file"] in personal_files else str)(
                            re.sub(r"\s+", " ", r["chunk_text"] or "")[:PASSAGE_CHARS]),
                        "personal": r["file"] in personal_files,
                        "score": round(float(r["rank"] or 0), 4)} for r in hits]
    read_personal = sorted({x["file"] for x in out["passages"] if x["personal"]})
    if read_personal:
        # Every read of an identity paper is on the record: who, which documents, for what question.
        try:
            from ..shared.approvals import write_audit
            async with session.begin_nested():
                await write_audit(session, actor=f"user:{user_id}" if user_id else "user",
                                  action_type="document.personal_read", source_feature=None,
                                  organization_id=organization_id,
                                  detail={"files": read_personal, "question": question[:300]})
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - an unrecorded read must not be served
            log.error("document_search.personal_read_unlogged", error=str(exc)[:200])
            out["passages"] = [x for x in out["passages"] if not x["personal"]]
            out["note"] = "Personal documents were found but their read could not be recorded, so they are withheld."
    if not out["passages"]:
        out["note"] = ("The linked documents were searched and nothing in them matches the question. "
                       "Say so; do not quote other documents.")
    return out
