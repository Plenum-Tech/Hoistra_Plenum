"""Trace Lab golden routes (Phase 3), included by trace_lab.app under /api/trace-lab (ROUTERS):

    GET  /golden-sets                              the company's versions, the active set, the Regression cases set
    POST /golden-sets                              upload (multipart: file, name, sheet, note; ?dry_run=true saves nothing)
    GET  /golden-sets/template.csv                 the empty upload template (header row only)
    GET  /golden-sets/templates/compliance.csv     the compliance question bank as question templates
    GET  /golden-sets/{set_id}/cases               one page of a set's cases (q, flag, limit, offset)
    POST /golden-sets/{set_id}/activate            roll back to that version
    POST /golden-sets/{set_id}/retire              retire that version
    POST /golden-runs/estimate                     cost, time and preflight before anything is created
    POST /golden-runs                              start a run (the page's own bearer; never stored)
    GET  /golden-runs                              the company's golden runs, newest first, summarised
    GET  /golden-runs/compare?base=&head=          two runs compared (flips, Wilson, pass^k, cost, like-for-like)
    GET  /golden-runs/{run_id}                     one run: summary, like-for-like record, caveats, every case
    GET  /golden-runs/{run_id}/results/{result_id} one case rep's messages (grading only after a reveal)
    POST /golden-runs/{run_id}/step                the page steps its run: one case in the background (202; 200 done)
    POST /golden-runs/{run_id}/pause | resume | cancel | regrade   (pause takes {reason: page_closed | paused})
    GET  /golden/turns/{turn_id}                   the expected outcome behind a golden answer (closed until revealed)
    POST /golden/turns/{turn_id}/reveal            open it for this reviewer (recorded once)
    GET  /golden/grader                            grader validation, pooled across companies, reported per company

Who: every route but /golden/grader needs a reviewer of the company (routes_conversations.reviewer: a company admin,
or a superadmin acting for the company), checked before readiness (db.require_ready, else 503). /golden/grader is a
reader route: a company admin sees the pooled figures and its own company's row; a platform superadmin acting for no
company sees every company's row. Run control first checks the run is this company's (golden_views.require_run).
Errors use the house envelope (db.http_error). GET routes never call a model.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from src.services.principal import Principal

from . import auth, db, golden, golden_store, golden_views, grader, grader_validation, runner
from .routes_conversations import Reviewer, reviewer

router = APIRouter(tags=["Trace Lab golden"])

_SET_MISSING = "That golden set is not in this company."
_RUN_MISSING = "That test run is not in this company."
_TURN_MISSING = "That answer is not a golden answer of this company."
PREVIEW_ROWS = 50


class GoldenRunEstimate(BaseModel):
    set_id: str | None = Field(None, max_length=64)          # default: the active set
    k: int = Field(1, ge=1, le=20)                           # repetitions; runner.max_k() may lower the ceiling
    purpose: Literal["adhoc", "baseline", "release_check", "repeat"] = "adhoc"
    context_default: str = Field("", max_length=4000)        # the page context for cases that name none


class GoldenRunStart(GoldenRunEstimate):
    usd_cap: float | None = Field(None, gt=0)
    note: str | None = Field(None, max_length=500)


class GoldenRunPause(BaseModel):
    reason: str | None = Field(None, max_length=40)          # page_closed (the page went away) or paused (a person)


class GoldenRunResume(BaseModel):
    usd_cap: float | None = Field(None, gt=0)


class GoldenRunRegrade(BaseModel):
    only: Literal["pending", "all"] = "pending"


async def _call(coro: Any, missing: str) -> Any:
    """A store or view call: NotFound becomes 404 not_found with this route's sentence, Conflict 409."""
    try:
        return await coro
    except golden_store.NotFound:
        raise db.http_error(404, "not_found", missing) from None
    except golden_store.Conflict as exc:
        raise db.http_error(409, "regression_set", str(exc)) from None


def _flag_counts(result: golden.ParseResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in result.cases:
        for f in c.flags:
            counts[f] = counts.get(f, 0) + 1
    return counts


def _preview(c: golden.CaseRow) -> dict[str, Any]:
    """A dry-run row. The expected answer is left out: the report never primes a reviewer."""
    return {"row_no": c.row_no, "case_key": c.case_key, "question": c.question[:300], "followups": list(c.followups),
            "evaluation": c.evaluation, "verified_on": c.verified_on.isoformat() if c.verified_on else None,
            "requirement_ids": list(c.requirement_ids), "values": len(c.expected_values), "flags": list(c.flags)}


def _csv(body: str, filename: str) -> Response:
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Cache-Control": "private, no-store"})


# ── golden sets ────────────────────────────────────────────────────────────────────────────

@router.get("/golden-sets")
async def list_golden_sets(who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, **(await golden_store.list_sets(who.org))}


@router.post("/golden-sets", status_code=201)
async def upload_golden_set(response: Response, file: UploadFile = File(...),
                            name: str | None = Form(None, max_length=200), sheet: str | None = Form(None, max_length=200),
                            note: str | None = Form(None, max_length=500), dry_run: bool = Query(False),
                            who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """Read the whole file first; save a new version only when every row is valid. dry_run saves nothing."""
    data = await file.read(golden.MAX_BYTES + 1)
    if len(data) > golden.MAX_BYTES:
        raise db.http_error(413, "file_too_large", f"The file is larger than {golden.MAX_BYTES:,} bytes.")
    filename = (file.filename or "golden-set.xlsx")[:255]
    try:
        result = golden.parse_upload(filename, data, sheet=(sheet or "").strip() or None)
    except golden.UploadRefused as exc:
        raise db.http_error(413 if exc.reason == "file_too_large" else 422, exc.reason, str(exc)) from None
    if result.errors:
        raise db.http_error(422, "bad_rows", "Some rows need fixing before this file can be saved.",
                            errors=result.errors[:200], warnings=result.warnings[:200])
    report = {"cases": len(result.cases), "warnings": result.warnings[:200], "flags": _flag_counts(result),
              "sheet_name": result.sheet_name, "header_row": result.header_row, "source_kind": result.source_kind}
    if dry_run:
        response.status_code = 200
        return {"ok": True, "saved": False, **report, "preview": [_preview(c) for c in result.cases[:PREVIEW_ROWS]]}
    async with db.lab_engine().begin() as conn:
        saved = await golden_store.save_version(conn, who.org, result, actor=who.principal, filename=filename,
                                                source_sha256=hashlib.sha256(data).hexdigest(), name=name, note=note)
    return {"ok": True, "saved": True, **report, "set": saved["set"], "diff": saved["diff"]}


@router.get("/golden-sets/template.csv")
async def golden_set_template(who: Reviewer = Depends(reviewer)) -> Response:
    return _csv(golden.template_csv(), "golden-set-template.csv")


@router.get("/golden-sets/templates/compliance.csv")
async def golden_set_compliance_template(who: Reviewer = Depends(reviewer)) -> Response:
    return _csv(golden.compliance_bank_template_csv(), "golden-set-compliance-questions.csv")


@router.get("/golden-sets/{set_id}/cases")
async def golden_set_cases(set_id: str, q: str | None = Query(None, max_length=200),
                           flag: str | None = Query(None, pattern="^(stale|unverified|objective|judgment|write_intent)$"),
                           limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                           who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    out = await _call(golden_store.get_cases(who.org, set_id, q=q, flag=flag, limit=limit, offset=offset), _SET_MISSING)
    return {"ok": True, **out}


@router.post("/golden-sets/{set_id}/activate")
async def activate_golden_set(set_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, "set": await _call(golden_store.activate(who.org, set_id), _SET_MISSING)}


@router.post("/golden-sets/{set_id}/retire")
async def retire_golden_set(set_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, "set": await _call(golden_store.retire(who.org, set_id), _SET_MISSING)}


# ── golden runs (estimate and compare are declared before /golden-runs/{run_id}) ───────────

@router.post("/golden-runs/estimate")
async def estimate_golden_run(body: GoldenRunEstimate, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    out = await _call(runner.estimate(org=who.org, principal=who.principal, body=body), _SET_MISSING)
    return {"ok": True, **out}


@router.post("/golden-runs", status_code=201)
async def start_golden_run(request: Request, body: GoldenRunStart, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """The page's bearer goes to the runner for this request only; it is never stored."""
    out = await _call(runner.start_run(org=who.org, principal=who.principal,
                                       authorization=request.headers.get("authorization"), body=body), _SET_MISSING)
    return {"ok": True, **out}


@router.get("/golden-runs")
async def list_golden_runs(since: datetime | None = Query(None), until: datetime | None = Query(None),
                           limit: int = Query(50, ge=1, le=100), who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    runs = await golden_views.run_summaries(who.org, since=since, until=until, limit=limit, reviewer=who.principal)
    return {"ok": True, "runs": runs}


@router.get("/golden-runs/compare")
async def compare_golden_runs(base: str = Query(..., max_length=64), head: str = Query(..., max_length=64),
                              who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return await _call(golden_views.compare(who.org, base, head), _RUN_MISSING)


@router.get("/golden-runs/{run_id}")
async def golden_run_detail(run_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return await _call(golden_views.run_detail(who.org, run_id, reviewer=who.principal), _RUN_MISSING)


@router.get("/golden-runs/{run_id}/results/{result_id}")
async def golden_run_result(run_id: str, result_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return await _call(golden_views.result_detail(who.org, run_id, result_id, reviewer=who.principal), _RUN_MISSING)


@router.post("/golden-runs/{run_id}/step", status_code=202)
async def step_golden_run(request: Request, response: Response, run_id: str,
                          who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """The page steps its run with its own fresh bearer: the next case runs in the background (202); 200 once the
    runner says the run is done."""
    run = await _call(golden_views.require_run(who.org, run_id), _RUN_MISSING)
    out = await _call(runner.step(run["id"], principal=who.principal,
                                  authorization=request.headers.get("authorization")), _RUN_MISSING)
    if out.get("done"):
        response.status_code = 200
    return {"ok": True, **out}


@router.post("/golden-runs/{run_id}/pause")
async def pause_golden_run(run_id: str, body: GoldenRunPause | None = None,
                           who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """A person pauses (reason paused), or the closing page asks for it (reason page_closed, a keepalive request)."""
    run = await _call(golden_views.require_run(who.org, run_id), _RUN_MISSING)
    reason = body.reason if body is not None and body.reason in ("page_closed", "paused") else "paused"
    return {"ok": True, **(await _call(runner.pause(run["id"], principal=who.principal, reason=reason), _RUN_MISSING))}


@router.post("/golden-runs/{run_id}/resume")
async def resume_golden_run(run_id: str, body: GoldenRunResume, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    run = await _call(golden_views.require_run(who.org, run_id), _RUN_MISSING)
    out = await _call(runner.resume(run["id"], principal=who.principal, usd_cap=body.usd_cap), _RUN_MISSING)
    return {"ok": True, **out}


@router.post("/golden-runs/{run_id}/cancel")
async def cancel_golden_run(run_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    run = await _call(golden_views.require_run(who.org, run_id), _RUN_MISSING)
    return {"ok": True, **(await _call(runner.cancel(run["id"], principal=who.principal), _RUN_MISSING))}


@router.post("/golden-runs/{run_id}/regrade", status_code=202)
async def regrade_golden_run(run_id: str, body: GoldenRunRegrade, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    run = await _call(golden_views.require_run(who.org, run_id), _RUN_MISSING)
    out = await _call(runner.regrade(run["id"], principal=who.principal, only=body.only), _RUN_MISSING)
    return {"ok": True, **out}


# ── the expected outcome behind a golden answer (blind until revealed) ─────────────────────

@router.get("/golden/turns/{turn_id}")
async def golden_turn(turn_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """{available: false} for an answer that is not a golden answer of this company (so the panel stays away)."""
    return await golden_views.expected_outcome(who.org, turn_id[:200], reviewer=who.principal)


@router.post("/golden/turns/{turn_id}/reveal")
async def reveal_golden_turn(turn_id: str, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    out = await golden_views.reveal(who.org, turn_id[:200], reviewer=who.principal)
    if not out.get("available"):
        raise db.http_error(404, "not_a_golden_turn", _TURN_MISSING)
    return out


# ── grader validation ───────────────────────────────────────────────────────────────────────

async def _reader(organization_id: str | None = Query(None, max_length=64),
                  principal: Principal = auth.caller) -> tuple[Principal, str | None]:
    return principal, auth.require_reader(principal, organization_id)


async def reader(who: tuple[Principal, str | None] = Depends(_reader),
                 _ready: Any = Depends(db.require_ready)) -> tuple[Principal, str | None]:
    """A reader (company admin, or any superadmin), checked before readiness."""
    return who


@router.get("/golden/grader")
async def golden_grader(evaluation: Literal["judgment", "objective"] = Query("judgment"),
                        who: tuple[Principal, str | None] = Depends(reader)) -> dict[str, Any]:
    """The current grader and its validation, pooled across companies (the senior's decision 1). A company sees the
    pooled figures and its own row; the platform team (a superadmin acting for no company) sees every row."""
    _principal, org = who
    version = grader.grader_version()
    v = await grader_validation.validation(grader_version=version, evaluation=evaluation)
    if org is not None:
        v["by_company"] = [c for c in v.get("by_company") or [] if c.get("organization_id") == org]
    return {"ok": True, "current": {"grader_version": version, "model": grader.grader_model()},
            "thresholds": grader_validation.thresholds(), "validation": v}
