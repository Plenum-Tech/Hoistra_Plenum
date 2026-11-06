"""Skill lab - measure and tune agent instructions on replayed questions (agents/skill_lab.py).

    POST /api/skill-lab/runs                         start a run: {"kind": "compare"|"optimise", "sample": 1-10}
    GET  /api/skill-lab/runs                         this company's runs, newest first
    GET  /api/skill-lab/runs/{run_id}                one run with every replayed answer and its score
    GET  /api/skill-lab/proposals                    instruction rewrites waiting for review, and active ones
    POST /api/skill-lab/proposals                    an admin writes a rewrite themselves
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


class WriteProposal(BaseModel):
    """A rewrite an admin writes, rather than one the optimiser produced.

    The lab proposes rewrites by measuring them on replayed questions. A person reading what
    reviewers actually said has no replay behind them, and should not need one: they are allowed
    to say "the instructions should mention this" and have an admin approve it, which is the same
    decision an admin already makes on a measured proposal.

    `content` replaces the document. `append` adds to what it says now, which is the shape a
    drafted lesson arrives in -- a paragraph to add, not a rewritten document. Exactly one.
    """

    skill: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    doc: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    content: str | None = Field(None, min_length=1, max_length=120_000)
    append: str | None = Field(None, min_length=1, max_length=40_000)
    #: Why, in the proposer's words. It is kept with the proposal so the approver is not asked to
    #: judge a diff with no account of where it came from.
    note: str | None = Field(None, max_length=2_000)


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


@router.post("/proposals")
async def write_proposal(body: WriteProposal,
                         principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """An admin proposes a rewrite they wrote themselves. It waits for approval like any other.

    Refused for a document an overlay cannot reach. Only documents the agents read AT CALL TIME
    can be overlaid: a prompt assembled once at import keeps its shipped text until the process
    restarts, so a proposal against one of those would be approved and change nothing, and the
    person who wrote it would have no way of telling. `skill_lab.TUNABLE` is that list, and the
    refusal names it rather than leaving the caller to guess.
    """
    _admin(principal)
    if (body.content is None) == (body.append is None):
        raise HTTPException(status_code=400,
                            detail="Send either content (the whole document) or append (a section to add), not both.")
    if (body.skill, body.doc) not in skill_lab.TUNABLE:
        raise HTTPException(status_code=400, detail={
            "error": f"{body.skill}/{body.doc} cannot be overlaid, so approving a rewrite of it "
                     f"would change nothing the agents read.",
            "reason": "not_tunable",
            "tunable": sorted(f"{s}/{d}" for s, d in skill_lab.TUNABLE),
        })

    current = skill_overlays.doc(body.skill, body.doc)
    if body.append is not None and not current:
        # Appending to nothing would silently propose a document consisting only of the addition,
        # losing every instruction the agents run on today.
        raise HTTPException(status_code=409,
                            detail=f"{body.skill}/{body.doc} reads as empty right now, so there is nothing to "
                                   f"append to. Send the whole document as content instead.")
    content = body.content if body.content is not None else (current.rstrip() + "\n\n" + body.append.strip() + "\n")
    if content.strip() == (current or "").strip():
        raise HTTPException(status_code=409, detail="That is what the document already says.")

    oid = await skill_overlays.propose(
        skill=body.skill, doc_name=body.doc, content=content,
        # Said in the evidence so the approver can see this was written, not measured: there is no
        # score behind it and the screen must not imply there is.
        evidence={"source": "written", "written_by": principal.email, "note": body.note,
                  "mode": "append" if body.append is not None else "replace",
                  "was_chars": len(current or ""), "now_chars": len(content)},
        run_id=None, by=principal.email)
    if not oid:
        raise HTTPException(status_code=503, detail="The skill-overlay table is unavailable.")
    return {"ok": True, "id": oid, "status": "proposed", "skill": body.skill, "doc": body.doc,
            "measured": False,
            "next": "It changes nothing until an admin approves it on this page."}


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


# ── the correction loop's instruction changes (Bala, 5 Nov 2026) ─────────────────────────────────
# Trace Lab's agent proposes a change to an instruction document; a person approves it for testing (only Trace Lab's
# reruns read it), then makes it live (every agent reads it, no rebuild); the live text reaches the repo only through
# the export, at commit. Written here, in deepagents' own table, never by Trace Lab directly.

class OverlayIn(BaseModel):
    skill: str = Field(..., min_length=1, max_length=64)
    doc: str = Field(..., min_length=1, max_length=82)
    content: str = Field(..., min_length=1, max_length=200_000)
    evidence: dict[str, Any] | None = None


@router.post("/overlays")
async def propose_overlay(body: OverlayIn, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    if not skill_overlays.known_doc(body.skill, body.doc):
        raise HTTPException(status_code=404, detail="No such instruction document")
    oid = await skill_overlays.propose(skill=body.skill, doc_name=body.doc, content=body.content,
                                       evidence=body.evidence, run_id=None, by=principal.email)
    if not oid:
        raise HTTPException(status_code=503, detail="The instruction store is not available")
    return {"ok": True, "id": oid, "status": "proposed"}


@router.get("/overlays")
async def list_overlays(status: str | None = None, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    if status is not None and status not in skill_overlays.STATUSES:
        raise HTTPException(status_code=400, detail="Unknown status")
    return {"ok": True, "overlays": await skill_overlays.list_overlays(status=status, limit=200)}


@router.post("/overlays/{overlay_id}/test")
async def test_overlay(overlay_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    out = await skill_overlays.approve_for_testing(overlay_id, by=principal.email)
    if not out:
        raise HTTPException(status_code=404, detail="No proposal waiting with that id")
    return {"ok": True, **out}


@router.post("/overlays/{overlay_id}/live")
async def live_overlay(overlay_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    out = await skill_overlays.go_live(overlay_id, by=principal.email)
    if not out:
        raise HTTPException(status_code=404, detail="No change under test with that id")
    return {"ok": True, **out}


@router.post("/overlays/{overlay_id}/drop")
async def drop_overlay(overlay_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _admin(principal)
    out = await skill_overlays.drop(overlay_id, by=principal.email)
    if not out:
        raise HTTPException(status_code=404, detail="No proposal or change under test with that id")
    return {"ok": True, **out}


@router.get("/export")
async def export_live(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """The live instruction documents with their repo paths: what is committed once testing is complete."""
    _admin(principal)
    return {"ok": True, "files": await skill_overlays.export_active()}
