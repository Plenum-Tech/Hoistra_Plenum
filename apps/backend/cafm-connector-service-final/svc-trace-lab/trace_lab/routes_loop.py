"""Trace Lab correction-loop routes (3 Nov 2026, migration 0013), under /api/trace-lab (trace_lab.app ROUTERS):

    GET    /lab/proposals                     what waits for a person: failure modes, spec versions, expected answers
    POST   /lab/proposals/{kind}/{id}         approve or reject one (final)
    GET    /lab/spec                          the specification in force, proposed versions and history
    GET    /lab/spec/draft                    the next version to propose, from the confirmed failure modes
    POST   /lab/spec                          propose a version                            (platform team, consultant)
    GET    /lab/spec.md                       the approved SPEC.md
    GET    /lab/corrections                   the prompt, skill or code changes made for modes and rules
    POST   /lab/corrections                   record one                                    (platform team, consultant)
    POST   /lab/modes/{lineage_id}/rule       add a confirmed failure mode's rule to SPEC.md in one click (platform team)
    POST   /lab/modes/{lineage_id}/regression a regression run of a confirmed failure mode                (curators)
    POST   /lab/runs/{code}/rerun             ask a regression run's questions again, a few per request    (curators)
    GET    /lab/runs/{code}/compare           the answers before and after, checked against the expected values
    GET    /lab/loop                          each confirmed failure mode and how far round the loop it is
    GET    /lab/sync-status                   the automatic sync of live answers
    POST   /lab/frontend-events               what a person's browser saw of an answer (any signed-in Hoistra user)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from src.services.principal import Principal

from . import agent, auth, autosync, db, labelling, loop
from .routes_labelling import _call
from .routes_runs import _wide

router = APIRouter(tags=["Trace Lab correction loop"])


class DecisionIn(BaseModel):
    decision: str = Field(..., max_length=20)
    note: str | None = Field(None, max_length=2000)


class SpecIn(BaseModel):
    rules: list[dict[str, Any]] = Field(..., max_length=200)
    intro: str | None = Field(None, max_length=20000)
    based_on: str | None = Field(None, max_length=64)


class CorrectionIn(BaseModel):
    kind: str = Field(..., max_length=20)
    reference: str = Field(..., min_length=1, max_length=500)
    mode: str | None = Field(None, max_length=64)
    spec_rule: str | None = Field(None, max_length=40)
    description: str | None = Field(None, max_length=2000)


class RegressionIn(BaseModel):
    company: str | None = Field(None, max_length=64)
    space: str = Field("full", max_length=40)


class RerunIn(BaseModel):
    start: int = Field(0, ge=0, le=10000)
    count: int = Field(loop.RERUN_MAX, ge=1, le=loop.RERUN_MAX)


class FrontendEventIn(BaseModel):
    kind: str = Field(..., max_length=20)
    turn_id: str | None = Field(None, max_length=200)
    session_id: str | None = Field(None, max_length=200)
    question: str | None = Field(None, max_length=4000)
    view: str | None = Field(None, max_length=60)
    instance: str | None = Field(None, max_length=40)
    ms_first_text: int | None = None
    ms_total: int | None = None
    error: str | None = Field(None, max_length=500)
    client_at: str | None = Field(None, max_length=40)


@router.get("/lab/proposals")
async def lab_proposals(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(loop.proposals(m))}


@router.post("/lab/proposals/{kind}/{subject_id}")
async def lab_decide(kind: str, subject_id: str, body: DecisionIn, request: Request,
                     m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    out = await _call(loop.decide(m, kind, subject_id, decision=body.decision, note=body.note))
    if (kind == "spec_version" and body.decision == "approved") or out.get("spec"):
        # After a person approves a SPEC.md version - or approves a failure mode, which adds its rule as a new approved
        # version (6 Nov 2026) - the agent writes it where every agent reads it, under test until a person makes it
        # live (Bala, 5 Nov 2026). A failure here leaves the approval standing.
        out.update(await _publish(m, request))
    return {"ok": True, **out}


async def _publish(m: labelling.Member, request: Request) -> dict[str, Any]:
    try:
        state = await loop.spec_state(m)
        if state.get("approved"):
            await _call(agent.publish_spec(state["approved"], authorization=request.headers.get("authorization")))
            return {"published": "testing"}
        return {}
    except Exception as exc:  # noqa: BLE001
        return {"published": False, "publish_error": getattr(exc, "detail", None) or str(exc)[:300]}


@router.get("/lab/spec")
async def lab_spec(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(loop.spec_state(m))}


@router.get("/lab/spec/draft")
async def lab_spec_draft(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(loop.draft_spec(m))}


@router.post("/lab/spec")
async def lab_propose_spec(body: SpecIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(loop.propose_spec(m, rules=body.rules, intro=body.intro, based_on=body.based_on))}


@router.get("/lab/spec.md")
async def lab_spec_md(m: labelling.Member = Depends(_wide)) -> Response:
    body = await _call(loop.spec_markdown(m))
    if not body:
        raise db.http_error(404, "no_spec", "No specification has been approved yet.")
    return Response(content=body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="SPEC.md"'})


@router.get("/lab/corrections")
async def lab_corrections(mode: str | None = Query(None, max_length=64),
                          m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, "corrections": await _call(loop.list_corrections(m, mode=mode))}


@router.post("/lab/corrections")
async def lab_add_correction(body: CorrectionIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(loop.add_correction(m, kind=body.kind, reference=body.reference, mode=body.mode,
                                                          spec_rule=body.spec_rule, description=body.description))}


@router.post("/lab/modes/{lineage_id}/regression")
async def lab_regression(lineage_id: str, body: RegressionIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(loop.make_regression(m, lineage_id, company=body.company, space=body.space))}


@router.post("/lab/runs/{code}/rerun")
async def lab_rerun(code: str, body: RerunIn, request: Request, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    try:
        out = await _call(loop.rerun(m, code, start=body.start, count=body.count,
                                     authorization=request.headers.get("authorization")))
    except loop.NotSafe as exc:
        raise db.http_error(409, "not_safe", "This process is not isolated enough to ask questions.",
                            problems=exc.problems)
    return {"ok": True, **out}


@router.get("/lab/runs/{code}/compare")
async def lab_compare(code: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(loop.compare(m, code))}


@router.get("/lab/loop")
async def lab_loop(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(loop.overview(m))}


@router.get("/lab/sync-status")
async def lab_sync_status(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    labelling.require_active(m)
    return {"ok": True, **autosync.status()}


@router.post("/lab/frontend-events", status_code=202)
async def lab_frontend_event(body: FrontendEventIn, principal: Principal = auth.caller,
                             _ready: Any = Depends(db.require_ready)) -> dict[str, Any]:
    """Any signed-in Hoistra user's browser. The company and person are the caller's, never the body's."""
    return {"ok": True, **await _call(loop.record_frontend_event(principal, body.model_dump()))}



# ── the agent, inside this container (Bala, 5 Nov 2026) ─────────────────────────────────────────

def _auth(request: Request) -> str | None:
    return request.headers.get("authorization")


@router.post("/lab/agent/propose-from-notes")
async def lab_agent_propose(run: str | None = Query(None, max_length=12),
                            m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """?run=R40: only the notes of the run the person has open."""
    return {"ok": True, **await _call(agent.propose_from_notes(m, run=run))}


@router.post("/lab/modes/{lineage_id}/rule")
async def lab_add_rule(lineage_id: str, request: Request, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """One click: a confirmed failure mode gets its rule in SPEC.md (a new approved version), put under test."""
    out = await _call(loop.add_rule(m, lineage_id))
    return {"ok": True, **out, **(await _publish(m, request))}


@router.post("/lab/modes/{lineage_id}/draft-fix")
async def lab_agent_draft_fix(lineage_id: str, request: Request, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(agent.draft_fix(m, lineage_id, authorization=_auth(request)))}


@router.get("/lab/changes")
async def lab_changes(request: Request, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(agent.changes(m, authorization=_auth(request)))}


@router.post("/lab/changes/{overlay_id}/approve")
async def lab_change_approve(overlay_id: str, request: Request, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(agent.approve_change(m, overlay_id, authorization=_auth(request)))}


@router.post("/lab/changes/{overlay_id}/reject")
async def lab_change_reject(overlay_id: str, request: Request, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(agent.reject_change(m, overlay_id, authorization=_auth(request)))}


@router.post("/lab/changes/{overlay_id}/live")
async def lab_change_live(overlay_id: str, request: Request, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(agent.make_live(m, overlay_id, authorization=_auth(request)))}


@router.get("/lab/changes/export.zip")
async def lab_changes_export(request: Request, m: labelling.Member = Depends(_wide)) -> Response:
    data = await _call(agent.export_zip(m, authorization=_auth(request)))
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="hoistra-instructions-for-commit.zip"'})
