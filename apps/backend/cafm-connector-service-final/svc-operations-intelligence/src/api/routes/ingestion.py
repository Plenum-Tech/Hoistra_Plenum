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
from ...engines.ingestion import document_facts as facts_svc
from ...engines.ingestion import tabular_mapping as tab_svc
from ...engines.ingestion import validation as val
from .auth import scope

class ImportPlanRequest(BaseModel):
    """A spreadsheet's headers, and optionally how many rows sit under them."""
    headers: list[str] = Field(default_factory=list, max_length=2000)
    row_count: int | None = Field(None, ge=0)
    domains: list[str] | None = Field(
        None, description="Narrow the search when the uploader has already said what the file "
                          "is — an asset register whose 'Date' column should not be weighed "
                          "against a compliance issue date.")


class ExtractionPlanRequest(BaseModel):
    """The document text to plan against. Text, not a file: planning is a read over
    words and must not depend on having somewhere to put an upload."""
    text: str = Field(default="", max_length=2_000_000)


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


@router.get("/extraction-rules")
async def extraction_rules(s: access.Scope = Depends(scope)):
    """What every ingested document is read for, per domain.

    A document is not one kind of thing. An FM contract names the supplier, the assets it
    covers, the PPM frequency it commits to and the certificates the contractor must hold —
    four domains in one file. Ingestion used to pick ONE from the filename and run ONE
    extractor, so three of those were never read and nothing said so.

    `writes` says whether anything can persist that domain yet: compliance and vendors have
    extractors behind them, the other three return their facts and hold them. Stated here so
    the page does not imply a fact will be stored when it will only be shown.
    See engines/ingestion/document_facts.py.
    """
    return facts_svc.domains()


@router.post("/extraction-plan")
async def extraction_plan(
    body: ExtractionPlanRequest,
    s: access.Scope = Depends(scope),
):
    """Which domains this document actually speaks to, and for the rest, why not.

    Model-free and cheap: it is the honest answer to "will this document give me my vendor
    data" before anything is extracted, and it is what an ingest receipt should show. A
    domain that is absent comes back WITH the reason, because "we looked and this says
    nothing about energy" and "energy was never checked" are different statements.
    """
    return facts_svc.extraction_plan(body.text)


@router.post("/import-plan")
async def import_plan(body: ImportPlanRequest, s: access.Scope = Depends(scope)):
    """Which table each column of a CSV or Excel sheet is destined for, before it writes.

    The migration flow's canonical registry covered assets, work orders, parts, scheduled PM
    and users. Energy was not in it at all, so an MPAN column had no target and a half-hourly
    export became rows nobody could query. Headers are matched against the SAME catalogue the
    document extractor uses, because a field's destination has to be declared once.

    Three things this refuses to do quietly. A header that matches nothing is returned by
    name, never guessed into a column. Two headers aimed at one column are held back, because
    the second write lands on top of the first and the import still reports success. And a
    header that matches two domains equally is left for the sheet's own shape to decide, or
    reported unresolved — picking the first in dictionary order is a coin toss wearing a
    confidence score. See engines/ingestion/tabular_mapping.py.
    """
    return tab_svc.import_plan(body.headers, row_count=body.row_count, domains=body.domains)


@router.get("/extraction-rules/validate")
async def extraction_rules_validate(
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Does every field in the catalogue name a column that exists on THIS database?

    A rule pointing nowhere extracts into nothing and reports success. The two databases
    disagree on shape, so this is per-deployment rather than a fact about the file.
    """
    return await facts_svc.validate_targets(session)


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
