"""Hoist Traces - the record of every turn, for the dashboard (agents/trace.py).

    GET  /api/traces/turns?since&until&q&status&model&user&limit&offset   the runs
    GET  /api/traces/turns/{turn_id}                                     one run with its span tree
    GET  /api/traces/stats?since&until                                   cost, calls, latency, errors
    POST /api/traces/turns/{turn_id}/feedback {rating: up|down|null, comment}
    GET  /api/traces/export?since&until&rated_only                       JSONL dataset (admin)

An admin reads the company's turns; anyone else only their own; a superadmin the company they are
acting for. Nothing here is public.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...agents import auto_teach, trace
from ...http_client import caller_authorization, caller_organization_id
from ..deps import get_orchestrator
from ...services.principal import Principal, caller_principal, current_principal

router = APIRouter(prefix="/api/traces", tags=["Traces"])


def _act(principal: Principal, organization_id: str | None) -> str | None:
    caller_principal.set(principal)
    if organization_id and principal.role != "superadmin" and str(principal.organization_id) != str(organization_id):
        raise HTTPException(status_code=403, detail={"ok": False, "error": "Not your company."})
    org = str(organization_id) if organization_id else (str(principal.organization_id) if principal.organization_id else None)
    caller_organization_id.set(org)
    return org


@router.get("/turns")
async def list_turns(since: datetime | None = Query(None), until: datetime | None = Query(None),
                     q: str | None = Query(None, max_length=200), status: str | None = Query(None, pattern="^(ok|error)$"),
                     model: str | None = Query(None, max_length=120), user: str | None = Query(None, max_length=200),
                     limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                     organization_id: str | None = Query(None),
                     principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _act(principal, organization_id)
    rows = await trace.list_turns(principal, org, since=since, until=until, q=q, status=status, model=model, user=user,
                                  limit=limit, offset=offset)
    return {"ok": True, "available": trace.ready(), "turns": rows, "can_manage": principal.is_admin}


@router.get("/turns/{turn_id}")
async def get_turn(turn_id: str, organization_id: str | None = Query(None),
                   principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _act(principal, organization_id)
    t = await trace.get_turn(principal, org, turn_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Turn not found"})
    return {"ok": True, "turn": t}


@router.get("/stats")
async def get_stats(since: datetime | None = Query(None), until: datetime | None = Query(None),
                    organization_id: str | None = Query(None),
                    principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _act(principal, organization_id)
    return await trace.stats(principal, org, since=since, until=until)


class Feedback(BaseModel):
    rating: str | None = Field(None, pattern="^(up|down)$")
    comment: str | None = Field(None, max_length=2000)


@router.post("/turns/{turn_id}/feedback")
async def set_feedback(turn_id: str, body: Feedback, organization_id: str | None = Query(None),
                       principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    org = _act(principal, organization_id)
    if not await trace.set_feedback(principal, org, turn_id, body.rating, body.comment):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Turn not found"})
    # A thumbs-down with a reason teaches by itself (agents/auto_teach.py).
    taught: list[str] = []
    if body.rating == "down" and body.comment:
        turn = await trace.get_turn(principal, org, turn_id)
        t = auto_teach.teaching_from_feedback((turn or {}).get("question") or "", body.comment)
        if t:
            taught = await auto_teach.teach([t], principal=principal, org=org, source_thread=(turn or {}).get("session_id"),
                                            subject=auto_teach.subject_of(turn))
    return {"ok": True, "taught": taught}


class Correction(BaseModel):
    span_id: str | None = None
    mode: str = Field("suggestion", pattern="^(query|tool|plan|route|agent|model|suggestion)$")
    text: str | None = Field(None, max_length=1200)
    route: str | None = Field(None, max_length=60)
    args: dict[str, Any] | None = None
    # structured, so the record engine compiles them rather than reading them in prose
    exclude: list[str] | None = Field(None, max_length=20)
    period: str | None = Field(None, max_length=40)
    field: str | None = Field(None, max_length=60)


class RerunBody(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)
    corrections: list[Correction] = Field(default_factory=list, max_length=12)


@router.post("/turns/{turn_id}/rerun")
async def rerun_turn(turn_id: str, body: RerunBody, request: Request, organization_id: str | None = Query(None),
                     principal: Principal = Depends(current_principal), orchestrator=Depends(get_orchestrator)) -> dict[str, Any]:
    """Replay a recorded turn with the reader's corrections applied at the steps they were raised
    on (agents/rerun.py). The result is a new turn in the same thread, traced and linked back."""
    org = _act(principal, organization_id)
    caller_authorization.set(request.headers.get("authorization"))
    turn = await trace.get_turn(principal, org, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Turn not found"})
    if not body.corrections:
        raise HTTPException(status_code=422, detail={"ok": False, "error": "Say what to correct first."})
    corrections = [c.model_dump() for c in body.corrections]
    out = await orchestrator.rerun_turn(turn, corrections, session_id=body.session_id)
    if not out.get("ok"):
        raise HTTPException(status_code=422, detail={"ok": False, "error": out.get("error") or "Could not re-run this turn."})
    # Every correction that re-ran the turn is remembered for the next similar question
    # (agents/auto_teach.py) - the re-run fixed one answer; the teaching fixes the rest.
    texts = [t for t in (auto_teach.teaching_from_correction(turn.get("question") or "", c) for c in corrections) if t]
    out["taught"] = await auto_teach.teach(texts, principal=principal, org=org, source_thread=body.session_id,
                                           subject=auto_teach.subject_of(turn))
    return out


@router.get("/export")
async def export_dataset(since: datetime | None = Query(None), until: datetime | None = Query(None),
                         rated_only: bool = Query(False), limit: int = Query(5000, ge=1, le=20000),
                         organization_id: str | None = Query(None),
                         principal: Principal = Depends(current_principal)):
    """One JSON object per line: the turn (question, answer, totals, feedback) and its spans."""
    org = _act(principal, organization_id)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail={"ok": False, "error": "Admins export the dataset."})

    async def body():
        async for row in trace.export_rows(principal, org, since=since, until=until, rated_only=rated_only, limit=limit):
            yield json.dumps(row, default=str) + "\n"

    name = "hoist-traces-" + datetime.utcnow().strftime("%Y%m%d-%H%M") + ".jsonl"
    return StreamingResponse(body(), media_type="application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"})
