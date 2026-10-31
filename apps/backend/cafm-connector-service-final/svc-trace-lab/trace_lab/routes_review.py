"""Trace Lab review routes (Phase 4): open coding (verdicts, comments, history) and sampling (plan, preview,
batches, manifest, map, progress, queue). trace_lab.app includes this router under /api/trace-lab (ROUTERS):

    GET    /annotations?conversation_key=&turn_id=      a conversation's verdict states, comments and block text
    PUT    /turns/{turn_id}/verdict                     save my verdict (a new version; 409 stale carries the head)
    DELETE /turns/{turn_id}/verdict?base=               clear my verdict (a tombstone version)
    GET    /turns/{turn_id}/annotation-history          every version and event on one answer
    POST   /comments                                    add a comment on a conversation (or one of its answers)
    PUT    /comments/{lineage_id}                       edit my comment
    DELETE /comments/{lineage_id}?base=                 remove my comment
    GET    /queue                                       the answers I still owe a verdict
    GET    /sampling/plan                               the five-step review plan and where it stands
    GET    /sampling/preview                            what a batch would draw (writes nothing)
    POST   /sampling/batches                            draw and record a batch
    GET    /sampling/batches                            the batches, with review counts
    GET    /sampling/batches/{batch_id}                 one batch and its items
    POST   /sampling/batches/{batch_id}/withdraw        withdraw a batch (a reason is required)
    GET    /sampling/manifest                           the record of every batch: seed, window, reasons
    GET    /map                                         one company's answers on the Map
    GET    /progress                                    review progress and the fair-sample wrong rate

Who (auth.py's one rule; this module adds no rule and no reason code): a READER is a company admin, or a superadmin
acting for a company, or - acting for none - the read-only platform view (annotations, history, batches, batch,
manifest and progress, which then span every company). A REVIEWER is the same but names one company: every write,
the queue, the plan, the preview and the Map. Both are checked before readiness (db.require_ready, else 503), so a
stranger learns nothing about Trace Lab's state. Review errors keep the house envelope (db.http_error via
review_errors.http_error_for). Nothing here spends model money; the POST /turns/{turn_id}/reveal route belongs to
the expected-outcome panel and is not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.services.principal import Principal

from . import annotations, auth, db, sampling
from .review_errors import Invalid, NotFound, ReviewError, http_error_for
from .routes_conversations import Reviewer, reviewer

router = APIRouter(tags=["Trace Lab review"])


@dataclass(frozen=True)
class Reader:
    principal: Principal
    org: str | None          # None: a superadmin acting for no company (the platform view)


async def _reader_scope(organization_id: str | None = Query(None, max_length=64),
                        principal: Principal = auth.caller) -> Reader:
    return Reader(principal, auth.require_reader(principal, organization_id))


async def reader(who: Reader = Depends(_reader_scope), _ready: Any = Depends(db.require_ready)) -> Reader:
    """Who may read: checked before readiness."""
    return who


async def _call(coro: Any) -> Any:
    try:
        return await coro
    except ReviewError as exc:
        raise http_error_for(exc) from exc


class AnchorIn(BaseModel):
    span_id: str | None = Field(None, max_length=64)
    field: str = Field(..., pattern="^(question|answer|input|output)$")
    quote: str = Field(..., min_length=1, max_length=2000)
    occurrence: int = Field(1, ge=1, le=10000)


class VerdictBody(BaseModel):
    verdict: str = Field(..., pattern="^(right|wrong)$")
    note: str | None = Field(None, max_length=2000)
    anchor: AnchorIn | None = None
    base: str | None = Field(None, max_length=64)


class CommentBody(BaseModel):
    conversation_key: str = Field(..., min_length=1, max_length=200)
    turn_id: str | None = Field(None, max_length=64)
    note: str = Field(..., min_length=1, max_length=2000)


class CommentEditBody(BaseModel):
    note: str = Field(..., min_length=1, max_length=2000)
    base: str = Field(..., max_length=64)


class BatchBody(BaseModel):
    plan_step: str = Field(..., pattern=f"^({'|'.join(sampling.PLAN_STEPS)})$")
    strategy: str = Field(..., pattern=f"^({'|'.join(sampling.STRATEGIES)})$")
    k: int = Field(..., ge=1, le=sampling.BATCH_MAX)
    seed: int = Field(sampling.DEFAULT_SEED, ge=0, le=2**31 - 1)
    since: datetime
    until: datetime
    channel: str = Field("chat", max_length=40)
    dimension: str | None = Field(None, max_length=40)
    mode_id: str | None = Field(None, max_length=64)
    random_share: float | None = Field(None, ge=0.0, le=0.5)
    reviews_required: int = Field(1, ge=1, le=3)
    source: str = Field("bow", pattern="^(bow|depth_scan)$")
    proposal_run_id: str | None = Field(None, max_length=64)
    include_view_as: bool = False
    include_deleted: bool = False


class WithdrawBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# ── open coding ────────────────────────────────────────────────────────────────────────

@router.get("/annotations")
async def get_annotations(conversation_key: str | None = Query(None, max_length=200),
                          turn_id: str | None = Query(None, max_length=64),
                          who: Reader = Depends(reader)) -> dict[str, Any]:
    if conversation_key:
        return await _call(annotations.for_conversation(who.principal, who.org, conversation_key, turn_id=turn_id))
    if turn_id:
        states = await _call(annotations.for_turns(who.principal, who.org, [turn_id]))
        if turn_id not in states:
            raise http_error_for(NotFound("turn_not_found"))
        return {"ok": True, "conversation_key": None, "turns": states, "comments": None, "comments_hidden": 0,
                "blocks": None}
    raise http_error_for(Invalid("name_a_conversation", "Name a conversation or an answer."))


@router.put("/turns/{turn_id}/verdict")
async def put_verdict(turn_id: str, body: VerdictBody, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    anchor = (annotations.Anchor(body.anchor.span_id, body.anchor.field, body.anchor.quote, body.anchor.occurrence)
              if body.anchor else None)
    row = await _call(annotations.save_verdict(who.principal, who.org, turn_id, verdict=body.verdict, note=body.note,
                                               anchor=anchor, base=body.base))
    return {"ok": True, "annotation": row}


@router.delete("/turns/{turn_id}/verdict")
async def delete_verdict(turn_id: str, base: str = Query(..., max_length=64),
                         who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, "annotation": await _call(annotations.delete_verdict(who.principal, who.org, turn_id, base=base))}


@router.get("/turns/{turn_id}/annotation-history")
async def annotation_history(turn_id: str, who: Reader = Depends(reader)) -> dict[str, Any]:
    return await _call(annotations.history(who.principal, who.org, turn_id))


@router.post("/comments", status_code=201)
async def post_comment(body: CommentBody, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    row = await _call(annotations.add_comment(who.principal, who.org, body.conversation_key, note=body.note,
                                              turn_id=body.turn_id))
    return {"ok": True, "annotation": row}


@router.put("/comments/{lineage_id}")
async def put_comment(lineage_id: str, body: CommentEditBody, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    row = await _call(annotations.edit_comment(who.principal, who.org, lineage_id, note=body.note, base=body.base))
    return {"ok": True, "annotation": row}


@router.delete("/comments/{lineage_id}")
async def delete_comment(lineage_id: str, base: str = Query(..., max_length=64),
                         who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, "annotation": await _call(annotations.delete_comment(who.principal, who.org, lineage_id, base=base))}


# ── sampling ───────────────────────────────────────────────────────────────────────────

@router.get("/queue")
async def get_queue(batch_id: str | None = Query(None, max_length=64), limit: int = Query(20, ge=1, le=50),
                    who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return await _call(sampling.queue(who.principal, who.org, batch_id=batch_id, limit=limit))


@router.get("/sampling/plan")
async def get_plan(since: datetime | None = Query(None), until: datetime | None = Query(None),
                   who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return await _call(sampling.plan_status(who.principal, who.org, since=since, until=until))


@router.get("/sampling/preview")
async def get_preview(
        plan_step: str = Query(..., pattern=f"^({'|'.join(sampling.PLAN_STEPS)})$"),
        strategy: str = Query(..., pattern=f"^({'|'.join(sampling.STRATEGIES)})$"),
        k: int = Query(..., ge=1, le=sampling.BATCH_MAX), seed: int = Query(sampling.DEFAULT_SEED, ge=0, le=2**31 - 1),
        since: datetime = Query(...), until: datetime = Query(...), channel: str = Query("chat", max_length=40),
        dimension: str | None = Query(None, max_length=40), mode_id: str | None = Query(None, max_length=64),
        random_share: float | None = Query(None, ge=0.0, le=0.5), reviews_required: int = Query(1, ge=1, le=3),
        source: str = Query("bow", pattern="^(bow|depth_scan)$"),
        proposal_run_id: str | None = Query(None, max_length=64),
        include_view_as: bool = Query(False), include_deleted: bool = Query(False),
        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """The BatchBody fields as query parameters, validated through BatchBody; writes nothing."""
    body = BatchBody(plan_step=plan_step, strategy=strategy, k=k, seed=seed, since=since, until=until, channel=channel,
                     dimension=dimension, mode_id=mode_id, random_share=random_share,
                     reviews_required=reviews_required, source=source, proposal_run_id=proposal_run_id,
                     include_view_as=include_view_as, include_deleted=include_deleted)
    return await _call(sampling.preview(who.principal, who.org, body.model_dump()))


@router.post("/sampling/batches", status_code=201)
async def post_batch(body: BatchBody, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """A drawable batch. A monitor_flagged batch is recorded only by monitoring, which calls
    sampling.create_batch directly (no HTTP route)."""
    return await _call(sampling.create_batch(who.principal, who.org, body.model_dump()))


@router.get("/sampling/batches")
async def get_batches(include_withdrawn: bool = Query(False), who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, "batches": await _call(sampling.list_batches(who.principal, who.org,
                                                                      include_withdrawn=include_withdrawn))}


@router.get("/sampling/batches/{batch_id}")
async def get_batch(batch_id: str, who: Reader = Depends(reader)) -> dict[str, Any]:
    out = await _call(sampling.batch_detail(who.principal, who.org, batch_id))
    if out is None:
        raise http_error_for(NotFound("batch_not_found"))
    return {"ok": True, **out}


@router.post("/sampling/batches/{batch_id}/withdraw")
async def post_withdraw(batch_id: str, body: WithdrawBody, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, "batch": await _call(sampling.withdraw(who.principal, who.org, batch_id, reason=body.reason))}


@router.get("/sampling/manifest")
async def get_manifest(who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, **await _call(sampling.manifest(who.principal, who.org))}


@router.get("/map")
async def get_map(since: datetime = Query(...), until: datetime = Query(...),
                  channel: str = Query("chat", max_length=40),
                  limit: int = Query(sampling.MAP_MAX, ge=1, le=sampling.MAP_MAX),
                  who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """One company's answers: the Map needs a company (a superadmin acts for one first)."""
    return await _call(sampling.map_view(who.principal, who.org, since=since, until=until, channel=channel, limit=limit))


@router.get("/progress")
async def get_progress(since: datetime | None = Query(None), until: datetime | None = Query(None),
                       block: int = Query(10, ge=5, le=50), filter_org: str | None = Query(None, max_length=64),
                       who: Reader = Depends(reader)) -> dict[str, Any]:
    return await _call(sampling.progress(who.principal, who.org, since=since, until=until, block=block,
                                         filter_org=filter_org if who.org is None else None))
