"""The document register: search inside a record's documents, and manage a building's documents.

    GET    /api/documents/search?q=&vendor=&contract_ref=&asset=&building_id=&doc_type=
    GET    /api/documents/types                         the building document types
    GET    /api/documents?building_id=                  every document on a building, however it got there
    PATCH  /api/documents/{document_id}                 set its type, mark it personal (admin)
    POST   /api/documents/{document_id}/links           link it to a building / asset / vendor (admin)
    DELETE /api/documents/{document_id}/links           end that link (admin)

Signed-in callers only; the register narrows to the caller's company and buildings before any row
is read (engines/document_search.py, engines/document_register.py). A personal document (visa,
Emirates ID, passport, labour card) is listed and searched for an admin only, numbers masked,
every read logged. Every change is audited.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines import document_register as reg
from ...engines import document_search as doc_svc
from ...engines.auth import access
from .auth import scope

router = APIRouter(prefix="/api/documents", tags=["documents"], dependencies=[Depends(scope)])


def _bad(exc: reg.RegisterError) -> HTTPException:
    code = 404 if exc.reason == "not_found" else 422
    return HTTPException(status_code=code, detail={"ok": False, "error": str(exc), "reason": exc.reason})


@router.get("/search")
async def search_documents(
    q: str = Query(..., min_length=2, max_length=500, description="The question, as asked."),
    vendor: str | None = Query(None, max_length=120, description="A vendor's contracts and certificates."),
    contract_ref: str | None = Query(None, max_length=80),
    asset: str | None = Query(None, max_length=120, description="An asset code or name: its certificates, warranties, reports."),
    building_id: UUID | None = Query(None),
    doc_type: str | None = Query(None, max_length=60, description="contract, certificate, invoice, report, manual ..."),
    document_id: list[str] | None = Query(None, description="Search these register documents."),
    organization_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
) -> dict[str, Any]:
    org = access.organization_for(s, organization_id)
    if building_id is not None:
        access.assert_building(s, building_id, action="read")
    return await doc_svc.search(session, question=q, organization_id=org, building_ids=s.building_ids,
                                vendor=vendor, contract_ref=contract_ref, building_id=building_id,
                                doc_type=doc_type, document_ids=document_id, asset=asset,
                                include_personal=s.is_admin, user_id=s.user_id)


@router.get("/types")
async def document_types() -> dict[str, Any]:
    return {"types": reg.doc_types(), "linkable": list(reg.LINKABLE)}


@router.get("")
async def list_documents(
    building_id: UUID = Query(...),
    organization_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
) -> dict[str, Any]:
    org = access.organization_for(s, organization_id)
    access.assert_building(s, building_id, action="read")
    out = await reg.list_documents(session, building_id=building_id, organization_id=org,
                                   building_ids=s.building_ids, include_personal=s.is_admin)
    out["can_manage"] = s.is_admin
    return out


class DocumentPatch(BaseModel):
    doc_type: str | None = Field(None, max_length=40, description="A key from GET /api/documents/types.")
    personal: bool | None = Field(None, description="Mark it an identity paper (an identity type always is).")


@router.patch("/{document_id}")
async def classify_document(document_id: str, body: DocumentPatch, organization_id: UUID | None = Query(None),
                            session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    access.assert_admin(s, action="classify documents")
    org = access.organization_for(s, organization_id)
    try:
        doc = await reg.owned_document(session, document_id, organization_id=org, building_ids=s.building_ids)
        return await reg.set_meta(session, document_id=doc["document_id"], doc_type=body.doc_type,
                                  personal=body.personal, organization_id=org, user_id=s.user_id, before=doc)
    except reg.RegisterError as exc:
        raise _bad(exc) from None


class LinkBody(BaseModel):
    entity_type: str = Field("building", max_length=20)
    entity_id: str = Field(..., max_length=80, description="The building, asset or vendor id.")
    relation: str | None = Field(None, max_length=80, description="e.g. 'staff access list', 'warranty'.")


@router.post("/{document_id}/links", status_code=201)
async def link_document(document_id: str, body: LinkBody, organization_id: UUID | None = Query(None),
                        session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    access.assert_admin(s, action="link documents")
    org = access.organization_for(s, organization_id)
    if body.entity_type == "building":
        access.assert_building(s, body.entity_id, action="link documents to")
    try:
        doc = await reg.owned_document(session, document_id, organization_id=org, building_ids=s.building_ids)
        return await reg.link(session, document_id=doc["document_id"], entity_type=body.entity_type,
                              entity_id=body.entity_id, relation=body.relation, organization_id=org, user_id=s.user_id)
    except reg.RegisterError as exc:
        raise _bad(exc) from None


@router.delete("/{document_id}/links")
async def unlink_document(document_id: str, entity_id: str = Query(..., max_length=80),
                          entity_type: str = Query("building", max_length=20), organization_id: UUID | None = Query(None),
                          session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    access.assert_admin(s, action="unlink documents")
    org = access.organization_for(s, organization_id)
    try:
        doc = await reg.owned_document(session, document_id, organization_id=org, building_ids=s.building_ids)
        return await reg.unlink(session, document_id=doc["document_id"], entity_type=entity_type,
                                entity_id=entity_id, organization_id=org, user_id=s.user_id)
    except reg.RegisterError as exc:
        raise _bad(exc) from None
