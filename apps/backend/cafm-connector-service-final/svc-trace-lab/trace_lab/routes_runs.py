"""Trace Lab runs routes (22 Oct 2026): runs above questions, expected answers, failure modes, the labels view and
progress, under /api/trace-lab (trace_lab.app ROUTERS):

    GET    /lab/runs                          runs the caller may see, newest first (platform team and consultant: all)
    POST   /lab/runs                          a synthetic run typed on the page                (curators)
    POST   /lab/runs/sync                     make questions of newer live answers, a run per person per day
    POST   /lab/runs/import                   Excel / CSV: new questions, or expected answers by question code (curators)
    GET    /lab/runs/{code}                   one run (R3)
    GET    /lab/runs/{code}/questions         its questions (R3-1, R3-2 ...)
    GET    /lab/runs/{code}/labels            its labels view: questions x failure modes, each person's newest cell
    GET    /lab/progress                      review completion and failure-mode counts (?run=R3, default newest)
    GET    /lab/q/{code}                      one question: expected answer, answers, the active answer's steps, notes
    PUT    /lab/q/{code}/expected             a new version of the expected answer
    POST   /lab/q/{code}/traces               attach a recorded answer                              (curators)
    PUT    /lab/q/{code}/cells                the caller's positive / negative / cleared value for one failure mode
    GET    /lab/modes                         the failure modes (the labels view's columns)
    POST   /lab/modes                         add a failure mode                       (platform team, consultant)
    PUT    /lab/modes/{lineage_id}            rename or redefine one                    (platform team, consultant)
    DELETE /lab/modes/{lineage_id}            retire one                                (platform team, consultant)
    GET    /lab/platform-members              platform-wide Trace Lab roles                         (platform team)
    PUT    /lab/platform-members              give or take the consultant role by Hoistra email    (platform team)

Notes on steps use routes_labelling's /lab/turns/{turn_id}/step-labels with the question's organization_id.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from src.services.principal import Principal

from . import auth, db, golden, labelling, runs
from .routes_labelling import _call

router = APIRouter(tags=["Trace Lab runs"])


async def _wide(organization_id: str | None = Query(None, max_length=64), principal: Principal = auth.caller,
                _ready: Any = Depends(db.require_ready)) -> labelling.Member:
    """The caller, allowed to act for no company: the platform team and a consultant then see every company."""
    return await labelling.access(principal, organization_id, all_companies_ok=True)


class QuestionIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    followups: list[str] | None = Field(None, max_length=20)
    role: str | None = Field(None, max_length=200)
    intent: str | None = Field(None, max_length=500)
    scenario_ref: str | None = Field(None, max_length=200)
    expected: str | None = Field(None, max_length=8000)
    expected_id: str | None = Field(None, max_length=200)
    evidence: Any = None


class RunIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    company: str | None = Field(None, max_length=64)
    questions: list[QuestionIn] = Field(default_factory=list, max_length=2000)


class ExpectedIn(BaseModel):
    expected: str = Field(..., min_length=1, max_length=8000)
    expected_id: str | None = Field(None, max_length=200)
    evidence: Any = None


class TraceIn(BaseModel):
    turn_id: str = Field(..., min_length=1, max_length=200)


class CellIn(BaseModel):
    mode: str = Field(..., min_length=1, max_length=64)
    value: str | None = Field(None, max_length=20)


class ModeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    definition: str | None = Field(None, max_length=4000)


class PlatformMemberIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    status: str = Field(..., max_length=20)
    role: str = Field("consultant", max_length=40)


@router.get("/lab/runs")
async def lab_runs(kind: str | None = Query(None, max_length=20), q: str | None = Query(None, max_length=200),
                   company: str | None = Query(None, max_length=64), limit: int = Query(100, ge=1, le=200),
                   offset: int = Query(0, ge=0, le=1_000_000), m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.list_runs(m, kind=kind, q=q, company=company, limit=limit, offset=offset))}


@router.post("/lab/runs")
async def lab_create_run(body: RunIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    out = await _call(runs.create_run(m, title=body.title, company=body.company,
                                      questions=[x.model_dump() for x in body.questions]))
    return {"ok": True, "run": out}


@router.post("/lab/runs/sync")
async def lab_sync(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.sync_production(m))}


@router.post("/lab/runs/import")
async def lab_import(file: UploadFile = File(...), run: str | None = Form(None, max_length=20),
                     title: str | None = Form(None, max_length=200), company: str | None = Form(None, max_length=64),
                     m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    data = await file.read(golden.MAX_BYTES + 1)
    if len(data) > golden.MAX_BYTES:
        raise db.http_error(413, "file_too_large", f"The file is larger than {golden.MAX_BYTES:,} bytes.")
    filename = (file.filename or "questions.xlsx")[:255]
    out = await _call(runs.import_file(m, filename, data, run=(run or "").strip() or None,
                                       title=(title or "").strip() or None, company=(company or "").strip() or None))
    return {"ok": True, **out}


@router.get("/lab/runs/{code}")
async def lab_run(code: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, "run": await _call(runs.get_run(m, code))}


@router.get("/lab/runs/{code}/questions")
async def lab_run_questions(code: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.list_questions(m, code))}


@router.get("/lab/runs/{code}/labels")
async def lab_run_labels(code: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.matrix(m, code))}


@router.get("/lab/progress")
async def lab_progress(run: str | None = Query(None, max_length=20), m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.progress(m, run))}


@router.get("/lab/q/{code}")
async def lab_question(code: str, turn: str | None = Query(None, max_length=200),
                       m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.get_question(m, code, turn_id=turn))}


@router.put("/lab/q/{code}/expected")
async def lab_set_expected(code: str, body: ExpectedIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    out = await _call(runs.set_expected(m, code, expected=body.expected, expected_id=body.expected_id,
                                        evidence=body.evidence))
    return {"ok": True, "expected": out}


@router.post("/lab/q/{code}/traces")
async def lab_link_trace(code: str, body: TraceIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.link_trace(m, code, body.turn_id))}


@router.get("/lab/q/{code}/candidates")
async def lab_answer_candidates(code: str, q: str | None = Query(None, max_length=200), limit: int = Query(20, ge=1, le=50),
                                m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.answer_candidates(m, code, q=q, limit=limit))}


@router.put("/lab/q/{code}/cells")
async def lab_set_cell(code: str, body: CellIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, "cell": await _call(runs.set_cell(m, code, mode=body.mode, value=body.value))}


@router.get("/lab/modes")
async def lab_modes(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, "modes": await _call(runs.list_modes(m))}


@router.post("/lab/modes")
async def lab_add_mode(body: ModeIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, "mode": await _call(runs.save_mode(m, name=body.name, definition=body.definition))}


@router.put("/lab/modes/{lineage_id}")
async def lab_edit_mode(lineage_id: str, body: ModeIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    out = await _call(runs.save_mode(m, name=body.name, definition=body.definition, lineage_id=lineage_id))
    return {"ok": True, "mode": out}


@router.delete("/lab/modes/{lineage_id}")
async def lab_retire_mode(lineage_id: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(runs.retire_mode(m, lineage_id))}


@router.get("/lab/platform-members")
async def lab_platform_members(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, "members": await _call(runs.list_platform_members(m))}


@router.put("/lab/platform-members")
async def lab_decide_platform_member(body: PlatformMemberIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    out = await _call(runs.decide_platform_member(m, email=body.email, status=body.status, role=body.role))
    return {"ok": True, "member": out}
