"""The conversation that decides whether a held document is filed — and the filing itself.

The check lives in operations-intelligence, which holds the building's ontology and writes
the audit trail. The binding lives here, because this is the service that took the upload
and knows which rows it produced. So this router is the one surface the interface talks to
for a held document: it forwards each step of the protocol to the authority, and when a
decision releases a document it performs the bind that was withheld.

    GET  /api/ingestion/cases                     what is waiting on somebody
    GET  /api/ingestion/cases/{id}                one case, with its conversation
    POST /api/ingestion/cases/{id}/clarify        the uploader's reason → the agent's answer
    POST /api/ingestion/cases/{id}/reassign       file it against another building instead
    POST /api/ingestion/cases/{id}/decide         the explicit yes or no → bind, or not

Everything is called with the caller's own bearer token, so the boundary is the same one
every other route has: a case on a building you are not allocated to does not exist as far
as you are concerned.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...services import building_binding
from ...services import ingestion_validation as gate
from ...services.principal import Principal, current_principal

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/ingestion", tags=["ingestion-validation"])


class ClarifyBody(BaseModel):
    explanation: str = Field(..., min_length=1, max_length=4000,
                             description="Why this document belongs to the selected building.")


class ReassignBody(BaseModel):
    building_id: str


class DecideBody(BaseModel):
    approve: bool = Field(..., description="The explicit yes or no. There is no default.")
    note: str | None = Field(None, max_length=2000)


def _auth(request: Request) -> str | None:
    return request.headers.get("authorization")


def _fail(out: dict[str, Any]) -> None:
    if out.get("ok"):
        return
    reason = out.get("reason") or out.get("error") or "case_error"
    status = 503 if reason == "validation_unavailable" else (
        404 if reason == "case_not_found" else 409)
    raise HTTPException(status_code=status, detail=out)


async def _release(case: dict[str, Any]) -> dict[str, Any]:
    """Bind the documents this case was holding, now that somebody has said yes.

    The documents are found by the mark every upload leaves on its files — the session id —
    so a decision taken an hour or a day later still finds the rows it is about.
    """
    building = case.get("final_building_id") or case.get("selected_building_id")
    session_id = case.get("session_id")
    if not building or not session_id:
        return {"documents": 0, "certificates": 0, "note": "nothing to bind"}
    ids = await building_binding.documents_from_session(session_id, within_hours=72)
    if not ids:
        return {"documents": 0, "certificates": 0, "note": "no document rows for this session"}
    bound = await building_binding.bind_documents_to_building(str(building), ids)
    log.info("ingestion_case.released", case_id=case.get("id"), building_id=str(building),
             documents=bound.get("documents"))
    return bound


@router.get("/cases")
async def list_cases(
    request: Request,
    open_only: bool = True,
    building_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Documents held for a check, and recently decided ones."""
    out = await gate.list_cases(open_only=open_only, building_id=building_id, limit=limit,
                                authorization=_auth(request))
    _fail(out)
    return out


@router.get("/cases/{case_id}")
async def get_case(
    case_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """One case: what the document claimed, what the building says, every check, the
    conversation so far, and what is outstanding."""
    out = await gate.get_case(case_id, authorization=_auth(request))
    _fail(out)
    return out


@router.post("/cases/{case_id}/clarify")
async def clarify_case(
    case_id: str,
    body: ClarifyBody,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Give the reason, and hear what the check makes of it.

    Nothing is filed by this call whatever the assessment says: it ends in a question, and
    a person answers that with /decide.
    """
    out = await gate.clarify(case_id, body.explanation, authorization=_auth(request))
    _fail(out)
    return out


@router.post("/cases/{case_id}/reassign")
async def reassign_case(
    case_id: str,
    body: ReassignBody,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """File it against a different building — and check it there before agreeing."""
    if not principal.allows_building(body.building_id):
        raise HTTPException(status_code=403, detail={
            "ok": False, "reason": "building_not_allocated", "building_id": body.building_id,
            "error": "You are not allocated to that building, so you cannot file against it."})
    out = await gate.reassign(case_id, body.building_id, authorization=_auth(request))
    _fail(out)
    return out


@router.post("/cases/{case_id}/decide")
async def decide_case(
    case_id: str,
    body: DecideBody,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """The explicit yes or no — and, on a yes, the filing that was withheld.

    The decision is recorded by the authority first and the bind follows it, so a document
    can never be bound without a decision behind it. A bind that fails after an approval is
    reported rather than hidden: the case stands approved and the documents can be bound
    again, where the reverse — a bound document with no decision — could not be undone.
    """
    if not principal.can_ingest:
        raise HTTPException(status_code=403, detail={
            "ok": False, "reason": "cannot_ingest",
            "error": "Your account can view its buildings but cannot ingest data."})
    out = await gate.decide(case_id, body.approve, body.note, authorization=_auth(request))
    _fail(out)
    if out.get("may_ingest"):
        try:
            out["bound"] = await _release(out)
        except Exception as exc:  # noqa: BLE001
            log.warning("ingestion_case.release_failed", case_id=case_id, error=str(exc)[:200])
            out["bound"] = {"error": str(exc)[:200]}
            out["message"] = (str(out.get("message") or "") +
                              " The decision was recorded; the filing itself did not complete "
                              "and can be retried.").strip()
    return out
