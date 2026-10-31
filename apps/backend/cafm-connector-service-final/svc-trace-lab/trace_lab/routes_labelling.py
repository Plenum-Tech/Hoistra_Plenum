"""Trace Lab step labelling routes (20 Oct 2026), the standalone app's only screens for now. trace_lab.app includes this
router under /api/trace-lab (ROUTERS):

    GET    /lab/me                                  who the caller is in Trace Lab (no access until an admin turns it on)
    GET    /lab/members                             the company's people and their Trace Lab roles     (company admins)
    GET    /lab/members/{user_id}                   one person's access, for Hoistra's Users & access  (company admins)
    PUT    /lab/members/{user_id}                   turn access on or off, set the Trace Lab role      (company admins)
    GET    /lab/questions                           the company's answered questions, newest first     (active members)
    GET    /lab/questions/{key}                     one conversation, every step of the active answer  (active members)
    GET    /lab/turns/{turn_id}/step-labels         everyone's labels on one answer's steps             (active members)
    PUT    /lab/turns/{turn_id}/step-labels         a new label (whole step or selected text) or an edit (active members)
    DELETE /lab/turns/{turn_id}/step-labels         remove one of the caller's labels                    (active members)
    PUT    /lab/turns/{turn_id}/step-label-groups   several steps labelled together, each with what should happen and
                                                    when                                                 (active members)
    DELETE /lab/turns/{turn_id}/step-label-groups/{group_id}  remove one of the caller's groups          (active members)
    GET    /lab/step-labels                         every label, filtered (admins: everyone's; others: their own)
    GET    /lab/step-labels.csv                     the same as a CSV download

Who may do what is labelling.access; errors use the house envelope. Nothing here spends model money.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.services.principal import Principal

from . import auth, db, labelling
from .review_errors import ReviewError, http_error_for

router = APIRouter(tags=["Trace Lab step labelling"])


async def _member(organization_id: str | None = Query(None, max_length=64), principal: Principal = auth.caller,
                  _ready: Any = Depends(db.require_ready)) -> labelling.Member:
    return await labelling.access(principal, organization_id)


async def _any_company(organization_id: str | None = Query(None, max_length=64), principal: Principal = auth.caller,
                       _ready: Any = Depends(db.require_ready)) -> labelling.Member:
    """The notes lists: the platform team and a consultant read every company's notes without naming one."""
    return await labelling.access(principal, organization_id, all_companies_ok=True)


async def _call(coro):
    try:
        return await coro
    except ReviewError as exc:
        raise http_error_for(exc) from None


class MemberBody(BaseModel):
    role: str = Field(..., max_length=40)
    status: str = Field(..., max_length=20)
    base: str | None = Field(None, max_length=64)


class LabelBody(BaseModel):
    step_key: str | None = Field(None, min_length=1, max_length=300)
    label: str | None = Field(None, max_length=20)
    note: str | None = Field(None, max_length=4000)
    step_title: str | None = Field(None, max_length=1000)
    quote: str | None = Field(None, max_length=4000)
    lineage_id: str | None = Field(None, max_length=64)
    base: str | None = Field(None, max_length=64)
    expected: str | None = Field(None, max_length=4000)
    only_if: str | None = Field(None, max_length=4000)


class GroupItem(BaseModel):
    step_key: str = Field(..., min_length=1, max_length=300)
    step_title: str | None = Field(None, max_length=1000)
    expected: str | None = Field(None, max_length=4000)
    only_if: str | None = Field(None, max_length=4000)


class GroupBody(BaseModel):
    label: str | None = Field(None, max_length=20)
    note: str | None = Field(None, max_length=4000)
    items: list[GroupItem] = Field(..., min_length=1, max_length=60)


@router.get("/lab/me")
async def lab_me(organization_id: str | None = Query(None, max_length=64), principal: Principal = auth.caller,
                 _ready: Any = Depends(db.require_ready)) -> dict[str, Any]:
    m = await labelling.access(principal, organization_id, register=True, all_companies_ok=True)
    return {"ok": True, "me": labelling.me_out(m)}


@router.get("/lab/members")
async def lab_members(m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    return {"ok": True, "members": await _call(labelling.list_members(m))}


@router.get("/lab/members/{user_id}")
async def lab_member(user_id: str, m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    return {"ok": True, "member": await _call(labelling.member_of(m, user_id))}


@router.put("/lab/members/{user_id}")
async def lab_decide_member(user_id: str, body: MemberBody, m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    out = await _call(labelling.decide_member(m, user_id, role=body.role, status=body.status, base=body.base))
    return {"ok": True, "member": out}


@router.get("/lab/questions")
async def lab_questions(q: str | None = Query(None, max_length=200), since: datetime | None = Query(None),
                        until: datetime | None = Query(None), cursor: str | None = Query(None, max_length=400),
                        limit: int = Query(30, ge=1, le=100), m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    try:
        return await labelling.list_questions(m, q=q, since=since, until=until, cursor=cursor, limit=limit)
    except labelling.conversations.BadRequest as exc:
        raise db.http_error(400, exc.reason, exc.error) from None


@router.get("/lab/questions/{key:path}")
async def lab_question(key: str, turn: str | None = Query(None, max_length=80),
                       m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    conv = await labelling.get_question(m, key, turn_id=turn)
    if conv is None:
        raise db.http_error(404, "conversation_not_found", "That conversation is not in this company's records.")
    return {"ok": True, "conversation": conv}


@router.get("/lab/turns/{turn_id}/step-labels")
async def lab_turn_labels(turn_id: str, m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    return {"ok": True, "labels": await _call(labelling.labels_for_turn(m, turn_id)), "me": m.user_id}


@router.put("/lab/turns/{turn_id}/step-labels")
async def lab_save_label(turn_id: str, body: LabelBody, m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    out = await _call(labelling.save_label(m, turn_id, step_key=body.step_key, label=body.label, note=body.note,
                                           step_title=body.step_title, quote=body.quote, lineage_id=body.lineage_id,
                                           base=body.base, expected=body.expected, only_if=body.only_if))
    return {"ok": True, "label": out}


@router.delete("/lab/turns/{turn_id}/step-labels")
async def lab_clear_label(turn_id: str, lineage_id: str = Query(..., min_length=1, max_length=64),
                          base: str = Query(..., max_length=64), m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    return {"ok": True, "label": await _call(labelling.clear_label(m, turn_id, lineage_id=lineage_id, base=base))}


@router.put("/lab/turns/{turn_id}/step-label-groups")
async def lab_save_group(turn_id: str, body: GroupBody, m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    out = await _call(labelling.save_group(m, turn_id, label=body.label, note=body.note,
                                           items=[i.model_dump() for i in body.items]))
    return {"ok": True, "labels": out}


@router.delete("/lab/turns/{turn_id}/step-label-groups/{group_id}")
async def lab_clear_group(turn_id: str, group_id: str, m: labelling.Member = Depends(_member)) -> dict[str, Any]:
    return {"ok": True, **await _call(labelling.clear_group(m, turn_id, group_id=group_id))}


def _list_args(reviewer: str | None, role: str | None, label: str | None, since: datetime | None,
               until: datetime | None, q: str | None) -> dict[str, Any]:
    return {"reviewer": reviewer, "role": role, "label": label, "since": since, "until": until, "q": q}


@router.get("/lab/step-labels")
async def lab_all_labels(reviewer: str | None = Query(None, max_length=320), role: str | None = Query(None, max_length=40),
                         label: str | None = Query(None, max_length=20), since: datetime | None = Query(None),
                         until: datetime | None = Query(None), q: str | None = Query(None, max_length=200),
                         limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0, le=1_000_000),
                         m: labelling.Member = Depends(_any_company)) -> dict[str, Any]:
    out = await _call(labelling.list_labels(m, **_list_args(reviewer, role, label, since, until, q), limit=limit,
                                            offset=offset))
    return {"ok": True, **out}


@router.get("/lab/step-labels.csv")
async def lab_all_labels_csv(reviewer: str | None = Query(None, max_length=320), role: str | None = Query(None, max_length=40),
                             label: str | None = Query(None, max_length=20), since: datetime | None = Query(None),
                             until: datetime | None = Query(None), q: str | None = Query(None, max_length=200),
                             m: labelling.Member = Depends(_any_company)) -> Response:
    out = await _call(labelling.list_labels(m, **_list_args(reviewer, role, label, since, until, q),
                                            limit=labelling.LIST_MAX, offset=0))
    return Response(labelling.csv_text(out["labels"]), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="trace-lab-step-labels.csv"'})
