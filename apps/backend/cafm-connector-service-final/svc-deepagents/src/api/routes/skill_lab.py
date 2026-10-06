"""Skill lab - measure and tune agent instructions on replayed questions (agents/skill_lab.py).

    POST /api/skill-lab/runs                         start a run: {"kind": "compare"|"optimise", "sample": 1-10}
    GET  /api/skill-lab/runs                         this company's runs, newest first
    GET  /api/skill-lab/runs/{run_id}                one run with every replayed answer and its score
    GET  /api/skill-lab/proposals                    instruction rewrites waiting for review, and active ones
    POST /api/skill-lab/proposals/{id}/approve       the rewrite becomes what the agents read
    POST /api/skill-lab/proposals/{id}/reject
    POST /api/skill-lab/docs/{skill}/{doc}/revert    back to the shipped file

Admins only: a run spends model money on replays and an approval changes what every agent reads.
A superadmin working inside another company names it as on every other route.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...agents import skill_lab
from ...services import skill_overlays
from ...services.principal import Principal, current_principal

router = APIRouter(prefix="/api/skill-lab", tags=["Skill lab"])


class StartRun(BaseModel):
    kind: str = Field("compare", pattern="^(compare|optimise)$")
    sample: int = Field(6, ge=1, le=skill_lab.MAX_SAMPLE)
    organization_id: str | None = None


def _admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Only an administrator can run or approve skill-lab work.")


def _org(principal: Principal, named: str | None) -> str | None:
    from .workflow import _resolve_acting_org
    return _resolve_acting_org(named, principal) or (str(principal.organization_id) if principal.organization_id else None)


@router.post("/runs")
async def start_run(body: StartRun, request: Request,
                    principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialised")
    out = await skill_lab.start(orchestrator, kind=body.kind, sample=body.sample, principal=principal,
                                organization_id=_org(principal, body.organization_id),
                                authorization=request.headers.get("authorization"))
    if not out.get("ok"):
        raise HTTPException(status_code=409, detail=out.get("error"))
    return out


@router.get("/runs")
async def runs(request: Request, organization_id: str | None = None,
               principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    return {"ok": True, "running": skill_lab.is_running(),
            "runs": await skill_lab.list_runs(_org(principal, organization_id))}


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, organization_id: str | None = None,
                     principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    run = await skill_lab.get_run(run_id, _org(principal, organization_id))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"ok": True, "run": run}


@router.get("/proposals")
async def proposals(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    rows = await skill_overlays.list_overlays(limit=50)
    shipped = {f"{s}/{d}": skill_overlays.doc(s, d) for s, d in skill_lab.TUNABLE}
    return {"ok": True, "proposals": rows, "tunable": sorted(f"{s}/{d}" for s, d in skill_lab.TUNABLE),
            "current": shipped}


@router.post("/proposals/{overlay_id}/approve")
async def approve(overlay_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    out = await skill_overlays.review(overlay_id, approve=True, by=principal.email)
    if not out:
        raise HTTPException(status_code=404, detail="No proposal waiting with that id")
    return {"ok": True, **out}


@router.post("/proposals/{overlay_id}/reject")
async def reject(overlay_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    out = await skill_overlays.review(overlay_id, approve=False, by=principal.email)
    if not out:
        raise HTTPException(status_code=404, detail="No proposal waiting with that id")
    return {"ok": True, **out}


@router.post("/docs/{skill}/{doc}/revert")
async def revert(skill: str, doc: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    if (skill, doc) not in skill_lab.TUNABLE:
        raise HTTPException(status_code=404, detail="Not a tunable document")
    n = await skill_overlays.retire_active(skill, doc, by=principal.email)
    return {"ok": True, "retired": n}
