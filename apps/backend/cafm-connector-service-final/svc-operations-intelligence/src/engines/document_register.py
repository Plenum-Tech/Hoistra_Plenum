"""The document register, managed: a building's documents, their type, their sensitivity, their links.

A document reaches a building three ways: filed against it at upload (documents.building_id), cited by
a record on it (a contract, certificate, warranty, work order - engines/document_search.py), or linked
here by a person (document_links). This module is the third, plus what a person sets on a document:

    DOC_TYPES           the building document types an upload or an admin may choose
    list_documents      a building's documents however they reach it, with type, sensitivity, index state
    link / unlink       place a document on (another) building, or take it off - audited, never deleted
    set_meta            change a document's type or mark it personal - audited

A personal document (visa, Emirates ID, passport, labour card) is an identity paper: it stays out of
open search, an admin within the building scope reads it with numbers masked, and every read is logged
(document_search.py). Personal types are personal from the moment they are typed.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..shared.approvals import write_audit
from .value_ledger import _grab_rows

log = get_logger(__name__)

#: key -> (label, group, personal by default)
DOC_TYPES: dict[str, tuple[str, str, bool]] = {
    "contract": ("Contract", "Commercial", False),
    "invoice": ("Invoice", "Commercial", False),
    "compliance_certificate": ("Compliance certificate", "Compliance", False),
    "inspection_report": ("Inspection / service report", "Maintenance", False),
    "warranty": ("Warranty", "Assets", False),
    "om_manual": ("O&M manual / procedure", "Assets", False),
    "drawing": ("Drawing / plan", "Building", False),
    "risk_assessment": ("Risk assessment / method statement", "Compliance", False),
    "staff_list": ("Staff / operatives list", "People on site", False),
    "access_pass": ("Access pass / e-pass register", "People on site", False),
    "visa": ("Visa", "People on site", True),
    "emirates_id": ("Emirates ID", "People on site", True),
    "passport": ("Passport", "People on site", True),
    "labour_card": ("Labour card", "People on site", True),
    "other": ("Other", "Other", False),
}
LINKABLE = ("building", "asset", "vendor", "work_order", "contract")
#: The names the ingest writers use for the same types.
ALIASES = {"vendor_invoice": "invoice", "service_contract": "contract", "certificate": "compliance_certificate",
           "report": "inspection_report", "manual": "om_manual"}


def type_key(doc_type: str | None) -> str:
    k = (doc_type or "").strip().lower()
    return ALIASES.get(k, k)
_IDENTITY_NO = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{5,}\b", re.I)


def doc_types() -> list[dict[str, Any]]:
    return [{"key": k, "label": v[0], "group": v[1], "personal": v[2]} for k, v in DOC_TYPES.items()]


def is_personal(doc_type: str | None, sensitivity: str | None) -> bool:
    if (sensitivity or "").lower() == "personal":
        return True
    return bool(DOC_TYPES.get(type_key(doc_type), ("", "", False))[2])


def mask_numbers(text_: str) -> str:
    """An identity number shows its last four characters only: 784-1987-1234567-1 -> ****567-1."""
    return _IDENTITY_NO.sub(lambda m: "****" + m.group(0)[-4:], text_ or "")


class RegisterError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


async def _document(session: AsyncSession, document_id: str) -> dict[str, Any] | None:
    rows = await _grab_rows(session, """
        SELECT d.*, d.document_id::text AS document_id, d.building_id::text AS building_id,
               d.organization_id::text AS organization_id
          FROM plenum_cafm.documents d WHERE d.document_id::text = :d""", {"d": str(document_id)})
    if isinstance(rows, dict):
        raise RegisterError("unreadable", rows.get("error") or "the register could not be read")
    return rows[0] if rows else None


async def owned_document(session: AsyncSession, document_id: str, *, organization_id: UUID | None,
                         building_ids: tuple[UUID, ...] | None) -> dict[str, Any]:
    """The document if the caller's company owns it (its own company, its building's, or the record
    citing it - the same rule search uses), else not found."""
    from .document_search import _columns, _scope

    cols = await _columns(session)
    scope_sql, p = _scope(organization_id, building_ids, cols)
    rows = await _grab_rows(session, f"SELECT d.document_id::text AS id FROM plenum_cafm.documents d "
                                     f"WHERE d.document_id::text = :doc{scope_sql}", {"doc": str(document_id), **p})
    if isinstance(rows, dict) or not rows:
        raise RegisterError("not_found", "No such document in your company's register.")
    doc = await _document(session, document_id)
    if doc is None:
        raise RegisterError("not_found", "No such document in your company's register.")
    return doc


async def list_documents(session: AsyncSession, *, building_id: UUID, organization_id: UUID | None,
                         building_ids: tuple[UUID, ...] | None, include_personal: bool) -> dict[str, Any]:
    """Every document on a building - filed on it, cited by a record on it, or linked to it."""
    from .document_search import _columns, _owner_exprs, _scope

    cols = await _columns(session)
    scope_sql, p = _scope(organization_id, building_ids, cols)
    _, bld_expr = _owner_exprs(cols)
    has_links = ("document_links", "document_id") in cols
    linked_here = ("EXISTS (SELECT 1 FROM plenum_cafm.document_links l WHERE l.document_id = d.document_id"
                   " AND l.entity_type = 'building' AND l.entity_id = :b AND l.removed_at IS NULL)") if has_links else "FALSE"
    sens = "d.sensitivity" if ("documents", "sensitivity") in cols else "NULL"
    rows = await _grab_rows(session, f"""
        SELECT d.document_id::text AS document_id, d.title, d.file_name, d.doc_type, {sens} AS sensitivity,
               coalesce(d.uploaded_at, d.created_at) AS added,
               d.building_id::text = :b AS filed_here,
               {linked_here} AS linked_here,
               (SELECT count(*) FROM plenum_cafm.ingestion_documents i JOIN plenum_cafm.document_chunks k
                  ON k.ingestion_id = i.id WHERE i.original_filename = d.file_name) AS chunks
          FROM plenum_cafm.documents d
         WHERE ({bld_expr} = :b OR {linked_here}){scope_sql}
         ORDER BY coalesce(d.uploaded_at, d.created_at) DESC NULLS LAST LIMIT 300""", {"b": str(building_id), **p})
    if isinstance(rows, dict):
        return {"ok": False, "error": rows.get("error")}
    out = []
    hidden = 0
    for r in rows:
        personal = is_personal(r["doc_type"], r["sensitivity"])
        if personal and not include_personal:
            hidden += 1
            continue
        out.append({"document_id": r["document_id"], "title": r["title"] or r["file_name"], "file": r["file_name"],
                    "type": type_key(r["doc_type"]) or None,
                    "type_label": DOC_TYPES.get(type_key(r["doc_type"]), (r["doc_type"] or "Unclassified",))[0],
                    "personal": personal, "indexed": int(r["chunks"] or 0) > 0,
                    "how": "filed here" if r["filed_here"] else "linked here" if r["linked_here"] else "via a record on this building",
                    "added": r["added"].isoformat() if r["added"] else None})
    return {"ok": True, "building_id": str(building_id), "documents": out, "personal_hidden": hidden,
            "types": doc_types()}


async def link(session: AsyncSession, *, document_id: str, entity_type: str, entity_id: str, relation: str | None,
               organization_id: UUID | None, user_id: UUID | None) -> dict[str, Any]:
    if entity_type not in LINKABLE:
        raise RegisterError("bad_entity", f"A document links to one of: {', '.join(LINKABLE)}.")
    existing = (await session.execute(text("""
        SELECT id FROM plenum_cafm.document_links WHERE document_id::text = :d AND entity_type = :t
           AND entity_id = :e AND removed_at IS NULL"""), {"d": document_id, "t": entity_type, "e": str(entity_id)})).first()
    if existing:
        return {"ok": True, "link_id": str(existing[0]), "created": False}
    row = (await session.execute(text("""
        INSERT INTO plenum_cafm.document_links (document_id, entity_type, entity_id, relation, organization_id, created_by)
        VALUES (CAST(:d AS uuid), :t, :e, :r, CAST(:o AS uuid), CAST(:u AS uuid)) RETURNING id"""),
        {"d": document_id, "t": entity_type, "e": str(entity_id), "r": (relation or None),
         "o": str(organization_id) if organization_id else None, "u": str(user_id) if user_id else None})).first()
    await write_audit(session, actor=f"user:{user_id}" if user_id else "user", action_type="document.link",
                      source_feature=None, organization_id=organization_id,
                      detail={"document_id": document_id, "entity_type": entity_type, "entity_id": str(entity_id),
                              "relation": relation})
    await session.commit()
    return {"ok": True, "link_id": str(row[0]), "created": True}


async def unlink(session: AsyncSession, *, document_id: str, entity_type: str, entity_id: str,
                 organization_id: UUID | None, user_id: UUID | None) -> dict[str, Any]:
    res = await session.execute(text("""
        UPDATE plenum_cafm.document_links SET removed_at = now(), removed_by = CAST(:u AS uuid)
         WHERE document_id::text = :d AND entity_type = :t AND entity_id = :e AND removed_at IS NULL"""),
        {"d": document_id, "t": entity_type, "e": str(entity_id), "u": str(user_id) if user_id else None})
    if res.rowcount:
        await write_audit(session, actor=f"user:{user_id}" if user_id else "user", action_type="document.unlink",
                          source_feature=None, organization_id=organization_id,
                          detail={"document_id": document_id, "entity_type": entity_type, "entity_id": str(entity_id)})
    await session.commit()
    return {"ok": True, "removed": int(res.rowcount or 0)}


async def set_meta(session: AsyncSession, *, document_id: str, doc_type: str | None, personal: bool | None,
                   organization_id: UUID | None, user_id: UUID | None, before: dict[str, Any]) -> dict[str, Any]:
    if doc_type is not None and doc_type not in DOC_TYPES:
        raise RegisterError("bad_type", "Choose one of: " + ", ".join(DOC_TYPES))
    new_type = doc_type if doc_type is not None else before.get("doc_type")
    if personal is None:
        sensitivity = "personal" if is_personal(new_type, before.get("sensitivity")) else (before.get("sensitivity") or "standard")
    else:
        sensitivity = "personal" if personal else "standard"
    # A personal type is personal: it cannot be marked standard while it is typed as an identity paper.
    if DOC_TYPES.get(new_type or "", ("", "", False))[2]:
        sensitivity = "personal"
    await session.execute(text("""
        UPDATE plenum_cafm.documents SET doc_type = COALESCE(:t, doc_type), sensitivity = :s,
               organization_id = COALESCE(organization_id, CAST(:o AS uuid))
         WHERE document_id::text = :d"""),
        {"t": doc_type, "s": sensitivity, "d": document_id, "o": str(organization_id) if organization_id else None})
    await write_audit(session, actor=f"user:{user_id}" if user_id else "user", action_type="document.classify",
                      source_feature=None, organization_id=organization_id,
                      detail={"document_id": document_id, "from": {"type": before.get("doc_type"),
                                                                   "sensitivity": before.get("sensitivity")},
                              "to": {"type": new_type, "sensitivity": sensitivity}})
    await session.commit()
    return {"ok": True, "document_id": document_id, "type": new_type, "sensitivity": sensitivity}
