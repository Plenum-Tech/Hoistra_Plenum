"""Trace Lab: conversations, runs and model calls, read only. trace_lab.app includes this router under
/api/trace-lab (ROUTERS):

    GET /api/trace-lab/conversations              filtered, cursor-paged conversation list with a total
    GET /api/trace-lab/conversations/updates      new turns since a watermark (the 45 s check)
    GET /api/trace-lab/conversations/facets       counts by channel, channel source, session kind and model
    GET /api/trace-lab/conversations/{key}        one conversation: every turn header + the active turn
    GET /api/trace-lab/turns/{turn_id}            one normalized turn + its conversation key
    GET /api/trace-lab/runs                       the "All live traffic" pseudo-run (golden runs: Phase 3)
    GET /api/trace-lab/model-calls                model-call rows + the aggregate for the same filter
    GET /api/trace-lab/model-calls/{span_id}      one model-call card

Every route: a signed-in reviewer of the company (auth.require_reviewer: a company admin, or a superadmin
acting for the company), then Trace Lab ready (db.require_ready, else 503). Errors use the house envelope
(db.http_error). Nothing here writes or spends model money.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from src.services.principal import Principal

from . import auth, conversations, db

router = APIRouter(tags=["Trace Lab conversations"])


@dataclass(frozen=True)
class Reviewer:
    principal: Principal
    org: str


async def _scope(organization_id: str | None = Query(None, max_length=64),
                 principal: Principal = auth.caller) -> Reviewer:
    return Reviewer(principal, auth.require_reviewer(principal, organization_id))


async def reviewer(who: Reviewer = Depends(_scope), _ready: Any = Depends(db.require_ready)) -> Reviewer:
    """Who may read: checked before readiness, so a stranger learns nothing about Trace Lab's state."""
    return who


def _bad(exc: conversations.BadRequest):
    return db.http_error(400, exc.reason, exc.error)


@router.get("/conversations")
async def list_conversations(
        since: datetime | None = Query(None), until: datetime | None = Query(None),
        run: str = Query("live", max_length=40), channel: str | None = Query(None, max_length=200),
        channel_source: str | None = Query(None, max_length=60), session_kind: str | None = Query(None, max_length=60),
        status: str | None = Query(None, pattern="^(ok|error)$"), model: str | None = Query(None, max_length=120),
        q: str | None = Query(None, max_length=200), user: str | None = Query(None, max_length=200),
        reviewed: str = Query("any", max_length=20), deleted: str = Query("hide", pattern="^(hide|include)$"),
        cursor: str | None = Query(None, max_length=400), limit: int = Query(50, ge=1, le=100),
        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(since=since, until=until, run=run, channel=channel, channel_source=channel_source,
                                      session_kind=session_kind, status=status, model=model, q=q, user=user,
                                      reviewed=reviewed, deleted=deleted)
        return await conversations.list_conversations(who.principal, who.org, f, cursor=cursor, limit=limit)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/conversations/updates")
async def conversation_updates(
        watermark: datetime = Query(...), until: datetime | None = Query(None), run: str = Query("live", max_length=40),
        channel: str | None = Query(None, max_length=200), channel_source: str | None = Query(None, max_length=60),
        session_kind: str | None = Query(None, max_length=60), status: str | None = Query(None, pattern="^(ok|error)$"),
        model: str | None = Query(None, max_length=120), q: str | None = Query(None, max_length=200),
        user: str | None = Query(None, max_length=200), deleted: str = Query("hide", pattern="^(hide|include)$"),
        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(until=until, run=run, channel=channel, channel_source=channel_source,
                                      session_kind=session_kind, status=status, model=model, q=q, user=user,
                                      deleted=deleted)
        return await conversations.conversation_updates(who.principal, who.org, f, watermark=watermark)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/conversations/facets")
async def conversation_facets(
        since: datetime | None = Query(None), until: datetime | None = Query(None),
        run: str = Query("live", max_length=40), deleted: str = Query("hide", pattern="^(hide|include)$"),
        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(since=since, until=until, run=run, deleted=deleted)
        return await conversations.facets(who.principal, who.org, f)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/conversations/{key:path}")
async def get_conversation(key: str, turn: str | None = Query(None, max_length=80),
                           deleted: str = Query("hide", pattern="^(hide|include)$"),
                           who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    conv = await conversations.get_conversation(who.principal, who.org, key, turn_id=turn,
                                                include_deleted=deleted == "include")
    if conv is None:
        raise db.http_error(404, "conversation_not_found", "That conversation is not in this company's records.")
    return {"ok": True, "conversation": conv}


@router.get("/turns/{turn_id}")
async def get_turn(turn_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    out = await conversations.get_turn(who.principal, who.org, turn_id)
    if out is None:
        raise db.http_error(404, "turn_not_found", "That answer is not in this company's records.")
    return {"ok": True, **out}


@router.get("/runs")
async def list_runs(since: datetime | None = Query(None), until: datetime | None = Query(None),
                    who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(since=since, until=until)
        return await conversations.list_runs(who.principal, who.org, since=f.since, until=f.until)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/model-calls")
async def list_model_calls(
        since: datetime | None = Query(None), until: datetime | None = Query(None),
        run: str = Query("live", max_length=40), channel: str | None = Query(None, max_length=200),
        channel_source: str | None = Query(None, max_length=60), status: str | None = Query(None, pattern="^(ok|error)$"),
        model: str | None = Query(None, max_length=120), agent: str | None = Query(None, max_length=60),
        kind: str | None = Query(None, pattern="^(llm|router)$"), q: str | None = Query(None, max_length=200),
        cursor: str | None = Query(None, max_length=400), limit: int = Query(100, ge=1, le=200),
        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    try:
        f = conversations.make_filter(since=since, until=until, run=run, channel=channel, channel_source=channel_source,
                                      status=status, model=model, q=q, agent=agent, kind=kind, calls=True)
        return await conversations.list_model_calls(who.principal, who.org, f, cursor=cursor, limit=limit)
    except conversations.BadRequest as exc:
        raise _bad(exc) from None


@router.get("/model-calls/{span_id}")
async def get_model_call(span_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    out = await conversations.get_model_call(who.principal, who.org, span_id)
    if out is None:
        raise db.http_error(404, "model_call_not_found", "That model call is not in this company's records.")
    return {"ok": True, **out}
