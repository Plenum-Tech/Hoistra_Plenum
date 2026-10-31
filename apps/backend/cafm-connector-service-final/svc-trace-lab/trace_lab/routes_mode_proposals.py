"""Trace Lab failure-mode proposals (Phase 5, the senior's decision 3): a company admin PROPOSES a failure mode, the
platform team decides. trace_lab.app includes this router under /api/trace-lab (ROUTERS):

    GET    /mode-proposals?status=            proposals: my company's (an admin, or a superadmin acting for a company)
                                              or every company's (the platform team, acting for none)
    POST   /mode-proposals                    propose a mode from my company's own Wrong notes (stored as `proposed`)
    POST   /mode-proposals/{mode_id}/accept   platform only: it becomes a candidate and its notes are filed
    POST   /mode-proposals/{mode_id}/reject   platform only: kept as `rejected`, with the reason

Who (auth.py's one rule; this module adds none): reading is a READER (a company admin, or a superadmin acting for a
company, or - acting for none - the platform view of every company); proposing is a REVIEWER (it names one company);
accepting and rejecting need the PLATFORM (a superadmin acting for no company, so an admin can never confirm). The
caller is checked before readiness (db.require_ready, else 503), so a stranger learns nothing about Trace Lab's state.
Refusals keep the house envelope (db.http_error via review_errors.http_error_for). The rest of the taxonomy routes
(modes, labels, proposals queues) are a later task; none of their paths is used here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from src.services.principal import Principal

from . import auth, db, taxonomy
from .review_errors import ReviewError, http_error_for
from .routes_conversations import Reviewer, reviewer
from .routes_review import Reader, reader

router = APIRouter(tags=["Trace Lab failure-mode proposals"])

NAME_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"
LEVEL_PATTERN = f"^({'|'.join(taxonomy.LEVELS)})$"
AGENT_PATTERN = f"^({'|'.join(taxonomy.AGENTS)})$"
STATUS_PATTERN = f"^({'|'.join(taxonomy.PROPOSAL_FILTERS)})$"
ID_MAX = 64


@dataclass(frozen=True)
class Platform:
    principal: Principal


async def _platform_scope(organization_id: str | None = Query(None, max_length=ID_MAX),
                          principal: Principal = auth.caller) -> Platform:
    auth.require_platform(principal, organization_id)
    return Platform(principal)


async def platform(who: Platform = Depends(_platform_scope), _ready: Any = Depends(db.require_ready)) -> Platform:
    """Who may decide: a superadmin acting for no company, checked before readiness."""
    return who


async def _call(coro: Any) -> Any:
    try:
        return await coro
    except ReviewError as exc:
        raise http_error_for(exc) from exc


class ProposalBody(BaseModel):
    name: str = Field(..., pattern=NAME_PATTERN)
    definition: str = Field(..., min_length=10, max_length=1000)
    boundary: str = Field("", max_length=1000)
    level: str = Field(..., pattern=LEVEL_PATTERN)
    agent: str = Field(..., pattern=AGENT_PATTERN)
    nearest_mode_id: str | None = Field(None, max_length=ID_MAX)
    created_from: list[str] = Field(..., min_length=1, max_length=50)


class EditsBody(BaseModel):
    name: str | None = Field(None, pattern=NAME_PATTERN)
    definition: str | None = Field(None, min_length=10, max_length=1000)
    boundary: str | None = Field(None, max_length=1000)
    level: str | None = Field(None, pattern=LEVEL_PATTERN)
    agent: str | None = Field(None, pattern=AGENT_PATTERN)
    nearest_mode_id: str | None = Field(None, max_length=ID_MAX)


class AcceptBody(BaseModel):
    base_version: int | None = Field(None, ge=1)
    edits: EditsBody | None = None


class RejectBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=taxonomy.REASON_MAX)
    base_version: int | None = Field(None, ge=1)


@router.get("/mode-proposals")
async def list_proposals(status: str = Query("proposed", pattern=STATUS_PATTERN),
                         who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, "proposals": await _call(taxonomy.list_proposals(who.principal, who.org, status=status))}


@router.post("/mode-proposals", status_code=201)
async def propose_mode(body: ProposalBody, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    draft = {"name": body.name, "definition": body.definition, "boundary": body.boundary, "level": body.level,
             "agent": body.agent}
    if body.nearest_mode_id:
        draft["nearest_mode_id"] = body.nearest_mode_id
    out = await _call(taxonomy.create(who.principal, draft, created_from=body.created_from, org=who.org))
    return {"ok": True, **out}


@router.post("/mode-proposals/{mode_id}/accept")
async def accept_proposal(mode_id: str = Path(..., max_length=ID_MAX), body: AcceptBody | None = None,
                          who: Platform = Depends(platform)) -> dict[str, Any]:
    edits = body.edits.model_dump(exclude_none=True) if body and body.edits else None
    out = await _call(taxonomy.accept_proposal(who.principal, mode_id,
                                               base_version=body.base_version if body else None, edits=edits))
    return {"ok": True, **out}


@router.post("/mode-proposals/{mode_id}/reject")
async def reject_proposal(body: RejectBody, mode_id: str = Path(..., max_length=ID_MAX),
                          who: Platform = Depends(platform)) -> dict[str, Any]:
    out = await _call(taxonomy.reject_proposal(who.principal, mode_id, body.reason, base_version=body.base_version))
    return {"ok": True, **out}
