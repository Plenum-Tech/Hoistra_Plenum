"""Trace Lab judges and code checks (Phase 6). trace_lab.app includes this router under /api/trace-lab (ROUTERS):

    GET    /checks                                   the code checks, by name
    GET    /checks/turns/{turn_id}                   every live code check on one answer (the asking company's own)
    GET    /judges                                   the judges table (summaries for a company, everything for the owner)
    GET    /modes/{mode_id}/judge-readiness          labels against the minimum, the split, the judges, the next step
    POST   /modes/{mode_id}/splits                   plan and store a new split          (201)
    GET    /modes/{mode_id}/splits/active            the active split with its members
    POST   /judges                                   register the next version           (201)
    GET    /judges/{judge_id}                        one judge: prompt, runs, iterations, warnings
    POST   /judges/{judge_id}/preview                one judgement on one answer         (spends; limited)
    GET    /judges/{judge_id}/estimate               what a run would ask the model and cost
    POST   /judges/{judge_id}/runs                   start or resume a dev or test run   (202; spends; limited)
    GET    /judge-runs/{run_id}                      one run: metrics, disagreements, its job
    PATCH  /judge-runs/{run_id}/disagreements/{turn_id}   triage one dev disagreement
    POST   /judges/{judge_id}/freeze                 freeze the judge, one way
    PATCH  /judges/{judge_id}/decision               use or do not use, after the held-out test
    PATCH  /judges/{judge_id}/threshold              the monitoring threshold of the judge in use
    GET    /failure-report                           one entry per confirmed failure mode (?format=json|md)

Who (auth.py's one rule; this module adds no rule and no reason code). The OWNER is the platform team, a superadmin
acting for no company: judge prompts, splits, run details and the failure report hold excerpts from every company's
answers, so they are the owner's alone (a plain user reads reviewers_only, anyone else platform_only). A READER is a
company admin, or a superadmin acting for a company, or the platform view: the checks list, one answer's code checks
(only an answer of the asking company; the platform sees any) and the judges table, where a company reads summaries
without prompts, model, hash or notes. The caller is checked before readiness (db.require_ready, else 503), so a
stranger learns nothing about Trace Lab's state. Refusals keep the house envelope (db.http_error): a judges-store
refusal keeps its own status, reason, sentence and extra fields.

Spending. The two routes that call the judge model (preview and runs) allow SPEND_LIMIT calls per person per route in
any SPEND_WINDOW_S seconds, then answer 429 rate_limited with Retry-After. A run is also capped in dollars by the store
and refused when it cannot be capped (over_cap, model_unpriced). The count is in this process's memory, and a call
counts only once it has passed the caller check and the body check, so a stranger or a malformed request cannot use up
the owner's budget.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.services.principal import Principal

from . import auth, checks, db, evidence, judges
from .routes_mode_proposals import Platform
from .routes_review import Reader, reader

router = APIRouter(tags=["Trace Lab judges"])

ID_MAX = 64
TURN_ID_MAX = evidence.MAX_ID_LENGTH
SPEND_LIMIT = 10
SPEND_WINDOW_S = 60.0


# -- who may call ----------------------------------------------------------------------------------------------------

async def _owner_scope(organization_id: str | None = Query(None, max_length=ID_MAX),
                       principal: Principal = auth.caller) -> Platform:
    auth.require_reader(principal, organization_id)          # a plain user, or an admin with no company: reviewers_only
    auth.require_platform(principal, organization_id)        # then anyone but the platform team: platform_only
    return Platform(principal)


async def owner(who: Platform = Depends(_owner_scope), _ready: Any = Depends(db.require_ready)) -> Platform:
    """Who may build, run and read the owner's side of a judge: checked before readiness."""
    return who


async def _call(awaitable: Awaitable[Any]) -> Any:
    """Await a store call; a judges refusal is the house envelope with its own status, reason and extra fields."""
    try:
        return await awaitable
    except judges.JudgeError as exc:
        raise db.http_error(exc.status, exc.reason, exc.message, **exc.extra) from None


# -- the limit on spending -------------------------------------------------------------------------------------------

class RateWindow:
    """At most `limit` accepted hits per key in any `window_s` seconds (a sliding window). A refused hit is not
    recorded, so asking again does not lengthen the wait. Keys with no hit left in the window are dropped once the
    table grows past SWEEP_AT. No awaits: safe in one event loop."""

    SWEEP_AT = 256

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """None when the call may go ahead (and is counted); else the seconds until the oldest hit leaves the window."""
        now = self.clock()
        cutoff = now - self.window_s
        if len(self._hits) > self.SWEEP_AT:
            for idle in [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]:
                del self._hits[idle]
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return hits[0] + self.window_s - now
        hits.append(now)
        return None

    def reset(self) -> None:
        self._hits.clear()


SPENDING = RateWindow(SPEND_LIMIT, SPEND_WINDOW_S)


def _charge(route: str, who: Platform) -> None:
    """Count one spending call by this person on this route, or refuse it with 429 and Retry-After."""
    wait = SPENDING.hit(f"{route}:{who.principal.user_id}")
    if wait is None:
        return
    secs = max(1, math.ceil(wait))
    exc = db.http_error(429, "rate_limited",
                        f"Too many requests in a minute. Try again in {secs} second{'' if secs == 1 else 's'}.",
                        retry_after_s=secs)
    exc.headers = {"Retry-After": str(secs)}
    raise exc


# -- request bodies --------------------------------------------------------------------------------------------------

class SplitBody(BaseModel):
    fractions: list[float] | None = Field(None, min_length=3, max_length=3)
    seed: int = Field(judges.SPLIT_SEED, ge=0, le=2**31 - 1)
    override_min_labels: bool = False
    override_reason: str | None = Field(None, max_length=1000)


class PartsIn(BaseModel):
    task: str | None = Field(None, max_length=2000)
    fail_rule: str = Field(..., min_length=10, max_length=2000)
    pass_rule: str = Field(..., min_length=10, max_length=2000)
    edge_cases: str | None = Field(None, max_length=4000)


class ExampleIn(BaseModel):
    turn_id: str = Field(..., min_length=1, max_length=TURN_ID_MAX)
    role: str = Field(..., pattern="^(clear_fail|clear_pass|borderline)$")
    critique: str = Field(..., min_length=10, max_length=2000)
    verdict: str = Field(..., pattern="^(Pass|Fail)$")


class JudgeBody(BaseModel):
    mode_id: str = Field(..., min_length=1, max_length=ID_MAX)
    kind: str = Field(..., pattern="^(llm|code)$")
    change_note: str = Field("", max_length=2000)
    parts: PartsIn | None = None
    examples: list[ExampleIn] | None = Field(None, max_length=3)
    check_key: str | None = Field(None, max_length=ID_MAX)


class PreviewBody(BaseModel):
    turn_id: str = Field(..., min_length=1, max_length=TURN_ID_MAX)


class RunBody(BaseModel):
    split: str = Field(..., pattern="^(dev|test)$")
    usd_cap: float | None = Field(None, gt=0, le=100)


class TriageBody(BaseModel):
    triage: str = Field(..., pattern="^(judge_wrong|label_wrong|definition_unclear)$")
    note: str | None = Field(None, max_length=2000)
    base: int = Field(..., ge=1)


class FreezeBody(BaseModel):
    stop_reason: str = Field(..., max_length=2000)
    base: int = Field(..., ge=1)


class DecisionBody(BaseModel):
    decision: str = Field(..., pattern="^(use|do_not_use)$")
    note: str | None = Field(None, max_length=2000)
    base: int = Field(..., ge=1)


class ThresholdBody(BaseModel):
    threshold: float | None = None
    base: int = Field(..., ge=1)


# -- code checks (a company's own answers) ---------------------------------------------------------------------------

@router.get("/checks")
async def list_checks(_who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, "checks": [{"key": c.key, "version": c.version, "evaluator_key": c.evaluator_key,
                                    "label": c.label, "description": c.description, "scope": c.scope}
                                   for c in checks.CHECKS.values()]}


def _no_turn():
    return db.http_error(404, "turn_not_found", "Answer not found.")


@router.get("/checks/turns/{turn_id}")
async def turn_checks(turn_id: str = Path(..., min_length=1, max_length=TURN_ID_MAX),
                      who: Reader = Depends(reader)) -> dict[str, Any]:
    if who.org is not None:                              # a company: look at whose it is before opening it
        fact = (await judges.turn_facts([turn_id])).get(turn_id)
        if fact is None or str(fact.get("organization_id") or "") != who.org:
            raise _no_turn()
    turn = (await judges.load_judge_turns([turn_id])).get(turn_id)
    if turn is None or (who.org is not None and str(turn.organization_id or "") != who.org):
        raise _no_turn()
    results = checks.run_live_checks(turn)
    return {"ok": True, "turn_id": turn_id,
            "results": [{"key": k, "version": checks.CHECKS[k].version, "status": r.status, "findings": list(r.findings)}
                        for k, r in results.items()]}


# -- splits and readiness (the owner's) ------------------------------------------------------------------------------

@router.get("/modes/{mode_id}/judge-readiness")
async def judge_readiness(mode_id: str = Path(..., max_length=ID_MAX), who: Platform = Depends(owner)) -> dict[str, Any]:
    return {"ok": True, **await _call(judges.readiness(who.principal, mode_id))}


@router.post("/modes/{mode_id}/splits", status_code=201)
async def create_split(body: SplitBody, mode_id: str = Path(..., max_length=ID_MAX),
                       who: Platform = Depends(owner)) -> dict[str, Any]:
    split = await _call(judges.create_split(
        who.principal, mode_id, fractions=tuple(body.fractions) if body.fractions else judges.SPLIT_FRACTIONS,
        seed=body.seed, override_min_labels=body.override_min_labels, override_reason=body.override_reason))
    return {"ok": True, "split": split}


@router.get("/modes/{mode_id}/splits/active")
async def active_split(mode_id: str = Path(..., max_length=ID_MAX), _who: Platform = Depends(owner)) -> dict[str, Any]:
    return {"ok": True, "split": await _call(judges.get_active_split(mode_id))}


# -- judges ----------------------------------------------------------------------------------------------------------

@router.get("/judges")
async def list_judges(mode_id: str | None = Query(None, max_length=ID_MAX), who: Reader = Depends(reader)) -> dict[str, Any]:
    """The platform view (no company) reads everything; a company reads summaries only."""
    return {"ok": True, **await _call(judges.list_judges(who.principal, who.org, mode_id=mode_id, full=who.org is None))}


@router.post("/judges", status_code=201)
async def create_judge(body: JudgeBody, who: Platform = Depends(owner)) -> dict[str, Any]:
    judge, warnings = await _call(judges.register(
        who.principal, mode_id=body.mode_id, kind=body.kind, change_note=body.change_note,
        parts=body.parts.model_dump() if body.parts else None,
        examples=[e.model_dump() for e in body.examples] if body.examples else None, check_key=body.check_key))
    return {"ok": True, "judge": judge, "warnings": warnings}


@router.get("/judges/{judge_id}")
async def get_judge(judge_id: str = Path(..., max_length=ID_MAX), _who: Platform = Depends(owner)) -> dict[str, Any]:
    return {"ok": True, **await _call(judges.get_judge(judge_id))}


@router.post("/judges/{judge_id}/preview")
async def preview_judge(body: PreviewBody, judge_id: str = Path(..., max_length=ID_MAX),
                        who: Platform = Depends(owner)) -> dict[str, Any]:
    _charge("preview_judge", who)
    return {"ok": True, **await _call(judges.preview(who.principal, judge_id, body.turn_id))}


@router.get("/judges/{judge_id}/estimate")
async def estimate_judge_run(judge_id: str = Path(..., max_length=ID_MAX), split: str = Query("dev", pattern="^(dev|test)$"),
                             _who: Platform = Depends(owner)) -> dict[str, Any]:
    return {"ok": True, **await _call(judges.estimate(judge_id, split))}


@router.post("/judges/{judge_id}/runs", status_code=202)
async def start_judge_run(body: RunBody, judge_id: str = Path(..., max_length=ID_MAX),
                          who: Platform = Depends(owner)) -> dict[str, Any]:
    _charge("start_judge_run", who)
    return {"ok": True, **await _call(judges.start_run(who.principal, judge_id, split=body.split, usd_cap=body.usd_cap))}


@router.get("/judge-runs/{run_id}")
async def get_judge_run(run_id: str = Path(..., max_length=ID_MAX), _who: Platform = Depends(owner)) -> dict[str, Any]:
    return {"ok": True, "run": await _call(judges.get_run(run_id))}


@router.patch("/judge-runs/{run_id}/disagreements/{turn_id}")
async def triage_disagreement(body: TriageBody, run_id: str = Path(..., max_length=ID_MAX),
                              turn_id: str = Path(..., min_length=1, max_length=TURN_ID_MAX),
                              who: Platform = Depends(owner)) -> dict[str, Any]:
    run = await _call(judges.triage(who.principal, run_id, turn_id, triage=body.triage, note=body.note, base=body.base))
    return {"ok": True, "run": run}


@router.post("/judges/{judge_id}/freeze")
async def freeze_judge(body: FreezeBody, judge_id: str = Path(..., max_length=ID_MAX),
                       who: Platform = Depends(owner)) -> dict[str, Any]:
    return {"ok": True, "judge": await _call(judges.freeze(who.principal, judge_id, stop_reason=body.stop_reason,
                                                           base=body.base))}


@router.patch("/judges/{judge_id}/decision")
async def set_judge_decision(body: DecisionBody, judge_id: str = Path(..., max_length=ID_MAX),
                             who: Platform = Depends(owner)) -> dict[str, Any]:
    return {"ok": True, "judge": await _call(judges.set_decision(who.principal, judge_id, decision=body.decision,
                                                                 note=body.note, base=body.base))}


@router.patch("/judges/{judge_id}/threshold")
async def set_judge_threshold(body: ThresholdBody, judge_id: str = Path(..., max_length=ID_MAX),
                              who: Platform = Depends(owner)) -> dict[str, Any]:
    return {"ok": True, "judge": await _call(judges.set_threshold(who.principal, judge_id, threshold=body.threshold,
                                                                  base=body.base))}


# -- the failure report (the owner's) --------------------------------------------------------------------------------

@router.get("/failure-report")
async def failure_report(format_: str = Query("json", alias="format", pattern="^(json|md)$"),
                         who: Platform = Depends(owner)) -> Any:
    out = await _call(judges.failure_report(who.principal, fmt=format_))
    if format_ == "md":
        return Response(content=out, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="failure-report.md"'})
    return {"ok": True, **out}
