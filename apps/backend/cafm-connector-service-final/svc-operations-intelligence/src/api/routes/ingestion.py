"""The ingestion validation protocol, as endpoints.

    POST /api/ingestion/validate              check a document against a building → a case
    GET  /api/ingestion/cases                 cases, open ones first
    GET  /api/ingestion/cases/{id}            one case: findings, question, conversation
    POST /api/ingestion/cases/{id}/clarify    the uploader's explanation → the agent's answer
    POST /api/ingestion/cases/{id}/reassign   move it to another building and check again
    POST /api/ingestion/cases/{id}/decide     the explicit yes or no
    GET  /api/ingestion/buildings/{id}/ontology   what the check compares against
    GET  /api/ingestion/audit                 the trail, newest first

The rule the whole router exists to enforce: a document is bound to a building only after a
case for it has been decided. ``may_ingest`` on a case is that permission, and it is true in
exactly three states — accepted, reassigned, overridden — each of which required a person to
answer yes.

The protocol does not branch on role. An admin gets the same checks, the same questions and
the same explicit confirmation as a user, because selecting the wrong building is a mistake
seniority does not prevent.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.auth import access
from ...engines.auth import ingestion_audit
from ...engines.ingestion import ontology as onto
from ...engines.ingestion import validation as val
from .auth import scope

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"],
                   dependencies=[Depends(scope)])


class ValidateRequest(BaseModel):
    building_id: UUID = Field(..., description="The building the uploader selected.")
    document_name: str | None = Field(None, description="File name, as uploaded.")
    document_id: UUID | None = Field(None, description="plenum_cafm.documents row, when it exists.")
    document_sha256: str | None = None
    doc_type: str | None = Field(None, description="contract | certificate | invoice | udr | …")
    text: str | None = Field(None, description="Extracted document text, if any.")
    extracted: dict[str, Any] = Field(default_factory=dict,
                                      description="Fields an extractor already read.")
    session_id: str | None = None


class ClarifyRequest(BaseModel):
    explanation: str = Field(..., min_length=1, max_length=4000)


class ReassignRequest(BaseModel):
    building_id: UUID


class DecideRequest(BaseModel):
    approve: bool = Field(..., description="The explicit yes or no. There is no default.")
    note: str | None = Field(None, max_length=2000)


async def _allowed(session: AsyncSession, s: access.Scope) -> list[UUID] | None:
    """The buildings a suggestion may name: the caller's own, and nobody else's."""
    if not s.restricted:
        return None
    return list(s.building_ids or ())


async def _case_or_404(session: AsyncSession, case_id: UUID, s: access.Scope) -> dict[str, Any]:
    case = await val.get(session, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "No such case.",
                                                     "reason": "case_not_found"})
    if s.organization_id and case.get("organization_id") and \
            str(case["organization_id"]) != str(s.organization_id) and not s.is_superadmin:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "No such case.",
                                                     "reason": "case_not_found"})
    for key in ("selected_building_id", "final_building_id"):
        if case.get(key):
            access.assert_building(s, case[key], action="act on")
            break
    return case


@router.post("/validate", status_code=201)
async def validate_document(
    body: ValidateRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Check a document against the selected building, before anything is bound.

    Answers with a case. `verdict` is matched, uncertain or mismatch; `status` says what the
    protocol wants next; `question` is what to put to the uploader; `suggestion` names a
    building that fits better, when one does. Only a case whose `may_ingest` is true may be
    written against a building, and that takes a decision.
    """
    access.assert_can_ingest(s)
    access.assert_building(s, body.building_id, action="file documents against")
    out = await val.open_case(
        session, organization_id=s.organization_id, actor_user_id=s.user_id, actor_role=s.role,
        building_id=body.building_id, document_name=body.document_name,
        document_id=body.document_id, document_sha256=body.document_sha256,
        doc_type=body.doc_type, text_body=body.text, extracted=body.extracted,
        session_id=body.session_id, allowed_building_ids=await _allowed(session, s),
    )
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail={"ok": False, **out})
    return out


@router.get("/cases")
async def list_cases(
    status: str | None = Query(None, description="validated | needs_clarification | "
                                                 "needs_confirmation | accepted | overridden | "
                                                 "reassigned | rejected"),
    open_only: bool = Query(False, description="Only cases still waiting on somebody."),
    building_id: UUID | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    ids = await _allowed(session, s)
    if building_id is not None:
        access.assert_building(s, building_id, action="read")
        ids = [building_id]
    rows = await val.list_cases(session, organization_id=s.organization_id, building_ids=ids,
                                status=status, open_only=open_only, limit=limit)
    return {"ok": True, "count": len(rows), "cases": rows,
            "open": sum(1 for r in rows if r.get("open"))}


@router.get("/cases/{case_id}")
async def get_case(
    case_id: UUID,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """One case in full: the claims read from the document, what the building says, every
    check and how it came out, the conversation so far, and what is outstanding."""
    return {"ok": True, **await _case_or_404(session, case_id, s)}


@router.post("/cases/{case_id}/clarify")
async def clarify_case(
    case_id: UUID,
    body: ClarifyRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The uploader's reason, and what the agent makes of it.

    The answer always ends in a question: either "your explanation accounts for it — shall I
    proceed?" or "it does not — do you still want it filed?". Both need a decision.
    """
    await _case_or_404(session, case_id, s)
    out = await val.clarify(session, case_id=case_id, explanation=body.explanation,
                            actor_user_id=s.user_id, actor_role=s.role)
    if not out.get("ok"):
        raise HTTPException(status_code=409, detail={"ok": False, **out})
    return out


@router.post("/cases/{case_id}/reassign")
async def reassign_case(
    case_id: UUID,
    body: ReassignRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """File it against a different building — and check it there before agreeing.

    Accepting a suggestion is not evidence: the document is validated again against the new
    building, and if it does not match there either, it says so.
    """
    await _case_or_404(session, case_id, s)
    access.assert_building(s, body.building_id, action="file documents against")
    out = await val.reassign(session, case_id=case_id, building_id=body.building_id,
                             actor_user_id=s.user_id, actor_role=s.role,
                             allowed_building_ids=await _allowed(session, s))
    if not out.get("ok"):
        raise HTTPException(status_code=409 if out.get("error") == "case_closed" else 404,
                            detail={"ok": False, **out})
    return out


@router.post("/cases/{case_id}/decide")
async def decide_case(
    case_id: UUID,
    body: DecideRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The explicit yes or no, recorded against the name of whoever gave it.

    A yes on a case the agent never cleared is an **override**, and is recorded as one.
    """
    access.assert_can_ingest(s)
    await _case_or_404(session, case_id, s)
    out = await val.decide(session, case_id=case_id, approve=body.approve,
                           actor_user_id=s.user_id, actor_role=s.role, note=body.note)
    if not out.get("ok"):
        raise HTTPException(status_code=409, detail={"ok": False, **out})
    return out


@router.get("/buildings/{building_id}/ontology")
async def building_ontology(
    building_id: UUID,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """What a document is checked against: names, codes, place, vendors, assets, floors,
    meters, certificates, contracts and documents already on this building — and how much of
    it there is, because a building with nothing on record can confirm nothing."""
    access.assert_building(s, building_id, action="read")
    o = await onto.load(session, building_id)
    if o is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "No such building.",
                                                     "reason": "building_not_found"})
    return {"ok": True, "ontology": o.as_dict()}


@router.get("/audit")
async def ingestion_trail(
    case_id: UUID | None = None,
    building_id: UUID | None = None,
    outcome: str | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Who filed what, against which building, what warning they saw, what they said, what
    the agent made of it, and who approved the result."""
    ids: list[UUID] | None = await _allowed(session, s)
    if building_id is not None:
        access.assert_building(s, building_id, action="read")
        ids = [building_id]
    out = await ingestion_audit.list_events(
        session, organization_id=s.organization_id, building_ids=ids, outcome=outcome,
        limit=limit)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail={"ok": False, **out})
    if case_id is not None:
        # The case a decision belongs to, whether the row carries the column or only the
        # detail written beside it.
        out["entries"] = [e for e in out["entries"]
                          if str((e.get("detail") or {}).get("case_id") or "") == str(case_id)]
        out["count"] = len(out["entries"])
    return out
