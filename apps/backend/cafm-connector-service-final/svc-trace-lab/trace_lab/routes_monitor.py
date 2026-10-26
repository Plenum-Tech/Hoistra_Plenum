"""Trace Lab Watch routes (Phase 7): monitoring batches, prevalence charts, code-check rates, the automatic-versus-people
trend, eval cases, repeat-n, eval-run grading and release checks. trace_lab.app includes this router under /api/trace-lab
(ROUTERS):

    GET    /monitor/plan                              what "Run now" would do, before it spends anything
    POST   /monitor/batches                           start a monitoring batch                      (202; spends; limited)
    POST   /monitor/batches/{batch_id}/resume         resume an interrupted batch                   (202; spends; limited)
    GET    /monitor/batches                           the batches, with the Due marker
    GET    /monitor/batches/{batch_id}                one batch and the answers it flagged
    GET    /monitor/chart?mode_id=                    the corrected failure rate of one failure mode, by period
    GET    /monitor/code-checks                       the weekly raw rate of every live code check
    GET    /monitor/automation                        checked automatically versus checked by people, by week
    POST   /monitor/batches/{batch_id}/review-batch   open the answers one judge flagged for people to review (201)
    GET    /eval-cases                                one company's eval cases
    POST   /eval-cases                                propose or draft a case from an answer or a golden case (201)
    GET    /eval-cases/{case_id}                      one case with its runs and its expected answer
    PATCH  /eval-cases/{case_id}                      edit a case (a new version)
    POST   /eval-cases/{case_id}/confirm              a person confirms a proposed case
    POST   /eval-cases/{case_id}/retire               retire a case, with a reason
    POST   /eval-cases/{case_id}/baseline             run the case five times to class it      (202; spends; limited)
    POST   /eval-cases/{case_id}/repeat               run the case n times                     (202; spends; limited)
    POST   /turns/{turn_id}/repeat                    repeat one answer n times                (202; spends; limited)
    POST   /eval-runs/{golden_run_id}/grade           make the judge calls a read left out     (202; spends; limited)
    GET    /eval-runs/{golden_run_id}                 one run's cases, reps and verdicts (a read never calls a model)
    POST   /release-checks                            run every active case before a deploy    (202; spends; limited)
    GET    /release-checks                            the company's release checks
    GET    /release-checks/{golden_run_id}?format=json|md   the report to attach to a deploy, as a download

Who (auth.py's rule; this module adds no reason code). A READER is a company admin, or a superadmin acting for a company,
or - acting for none - the platform view that spans every company: the batches, one batch, the chart and the two trends.
The PLATFORM TEAM is any superadmin: only it previews, starts and resumes monitoring and opens review batches from it, so
a company admin reading batches can never start one (403 platform_only). A superadmin acting for a company does all four
FOR THAT COMPANY (Phase 8): the batch, its verdicts and the review batches it opens carry the company, the checks and the
uniform sample are limited to that company's answers, and the company's admins then read the batch, its Due marker and its
charts (R19). Acting for no company, the batch is platform-wide (organization_id NULL) and company readers see nothing of
it. A REVIEWER names one company (a company admin, or a superadmin acting for it): eval cases, repeats, eval runs and
release checks belong to one company, and a golden run is stepped by that person's own page. The caller is checked before
readiness (db.require_ready, else 503), so a stranger learns nothing about Trace Lab's state.

Errors keep the house envelope (db.http_error): a monitor or eval-case refusal (WatchError) and a review refusal
(ReviewError) each keep their own status, reason, sentence and extra fields; a refusal from the golden runner is already
that envelope and passes through.

Spending. Every route that can spend model money (the seven marked above) allows SPEND_LIMIT calls per person per route
in any SPEND_WINDOW_S seconds, then answers 429 rate_limited with Retry-After. A call counts once it has passed the
caller check and the body check, so a stranger or a malformed request cannot use up anyone's budget; each batch, run and
grading job is also capped in dollars by its own store. The count is in this process's memory (the same window the
judge routes use).

A run starts with the caller's own bearer for that one request (the golden runner never stores it).
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Awaitable, NamedTuple

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src.services.principal import Principal

from . import auth, db, evidence, monitor, runner
from .review_errors import ReviewError, http_error_for
from .routes_conversations import Reviewer, reviewer
from .routes_judges import RateWindow
from .routes_review import Reader, reader

if TYPE_CHECKING:
    from types import ModuleType

    from .eval_cases import RunContext

router = APIRouter(tags=["Trace Lab watch"])

ID_MAX = 64
UUID_LEN = 36
TURN_ID_MAX = evidence.MAX_ID_LENGTH
SEED_MAX = 2**31 - 1
SPEND_LIMIT = 10
SPEND_WINDOW_S = 60.0
COMPANY_NAME_MAX = 120
#: eval_cases' own limits, held here because importing it loads svc-deepagents' Skill lab (see _cases). A test pins them.
REPEAT_DEFAULT_N = 10
REPEAT_MIN_N = 2
REPEAT_MAX_N = runner.HARD_MAX_K
RELEASE_DEFAULT_K = 5
RELEASE_MAX_K = 10
FILENAME_MAX = 80
GENERIC_COMPANY = "your company"

Id = Annotated[str, Field(min_length=1, max_length=UUID_LEN)]
EVALUATION = "^(objective|judgment)$"


# -- the limit on spending -------------------------------------------------------------------------------------------

SPENDING = RateWindow(SPEND_LIMIT, SPEND_WINDOW_S)


def _charge(route: str, principal: Principal) -> None:
    """Count one spending call by this person on this route, or refuse it with 429 and Retry-After."""
    wait = SPENDING.hit(f"{route}:{principal.user_id}")
    if wait is None:
        return
    secs = max(1, math.ceil(wait))
    exc = db.http_error(429, "rate_limited",
                        f"Too many requests in a minute. Try again in {secs} second{'' if secs == 1 else 's'}.",
                        retry_after_s=secs)
    exc.headers = {"Retry-After": str(secs)}
    raise exc


# -- the eval-case store, loaded on first use ------------------------------------------------------------------------

def _cases() -> "ModuleType":
    """The eval-case store, imported when a route first needs it. It imports svc-deepagents' Skill lab, and so that
    package's database module, which importing the service must not do (the Phase 2 import guard). Looked up on every
    call, so a test that replaces one of its functions is seen."""
    from . import eval_cases

    return eval_cases


async def _grade_job(job: Any) -> str:
    """The golden runner's `eval_cases` grading handler until the store is loaded: a restart settles a queued or
    interrupted grading job by this name before any route has run. Loading the store registers its own handler in
    place of this one."""
    return await _cases().grade_job(job)


if "eval_cases" not in runner.GRADE_HANDLERS:          # the store's own handler wins when it was imported first
    runner.register_grade_handler("eval_cases", _grade_job)


# -- who is calling, and what a store refusal becomes ----------------------------------------------------------------

class Starter(NamedTuple):
    """The platform team at work: a superadmin, and the company the request names (a superadmin's "View as"). `org` is
    that company, or None for a platform-wide batch."""
    principal: Principal
    org: str | None


async def _starter_scope(organization_id: str | None = Query(None, max_length=ID_MAX),
                         principal: Principal = auth.caller) -> Starter:
    org = auth.require_reader(principal, organization_id)    # a plain user, or an admin with no company: reviewers_only
    if principal.role != auth.SUPERADMIN:                    # a company admin reads the batches but never starts one
        raise db.http_error(403, "platform_only", auth.PLATFORM_ONLY)
    return Starter(principal, org)


async def starter(who: Starter = Depends(_starter_scope), _ready: Any = Depends(db.require_ready)) -> Starter:
    """Who may preview, start and resume monitoring and open its review batches: any superadmin, for the company it
    names or for the whole platform. Checked before readiness."""
    return who


async def _call(awaitable: Awaitable[Any]) -> Any:
    """Await a monitor or eval-case call; a refusal is the house envelope with its own status, reason and fields."""
    try:
        return await awaitable
    except monitor.WatchError as exc:
        raise db.http_error(exc.status, exc.reason, exc.message, **exc.extra) from None
    except ReviewError as exc:
        raise http_error_for(exc) from None


def _actor(principal: Principal) -> tuple[str, str | None]:
    return str(principal.user_id), (principal.email or None)


def _ctx(request: Request, who: Reviewer) -> "RunContext":
    """What the golden runner needs from this request: the company, the caller and the caller's own bearer."""
    return _cases().RunContext(org=who.org, principal=who.principal, authorization=request.headers.get("authorization"))


def _names(raw: str | None) -> list[str] | None:
    """A comma-separated query value as names; nothing named is None (the monitor's defaults), not an empty choice."""
    return [n for n in (p.strip() for p in (raw or "").split(",")) if n] or None


def _download_name(report: dict[str, Any], golden_run_id: str) -> str:
    """release-check-<run label>, cut down to characters a header can carry (the label is data, not trusted text)."""
    key = re.sub(r"[^A-Za-z0-9._-]+", "-", str(report.get("run_key") or golden_run_id))[:FILENAME_MAX].strip("-.")
    return f"release-check-{key or golden_run_id}"


# -- request bodies --------------------------------------------------------------------------------------------------

class BatchStart(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    sample_rate: float = Field(monitor.DEFAULT_RATE, gt=0, le=1)
    seed: int = Field(monitor.DEFAULT_SEED, ge=0, le=SEED_MAX)
    risk_groups: list[Annotated[str, Field(min_length=1, max_length=40)]] | None = Field(None, max_length=10)
    mode_ids: list[Id] | None = Field(None, max_length=50)
    usd_cap: float | None = Field(None, gt=0, le=100)


class ReviewBatch(BaseModel):
    mode_id: Id


class CaseSource(BaseModel):
    type: str = Field(..., pattern="^(live_turn|golden_case)$")
    turn_id: str | None = Field(None, max_length=TURN_ID_MAX)
    golden_case_id: str | None = Field(None, max_length=UUID_LEN)


class CaseCreate(BaseModel):
    source: CaseSource
    mode_id: str | None = Field(None, max_length=UUID_LEN)
    input: dict[str, Any] | None = None
    initial_state: dict[str, Any] | None = None
    expected: dict[str, Any] = Field(default_factory=dict)
    expected_answer: str | None = Field(None, max_length=6000)
    evaluation: str = Field("objective", pattern=EVALUATION)
    expected_values: str | None = Field(None, max_length=4000)


class CaseEdit(BaseModel):
    base: int = Field(..., ge=1)
    input: dict[str, Any] | None = None
    initial_state: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    expected_answer: str | None = Field(None, max_length=6000)
    evaluation: str | None = Field(None, pattern=EVALUATION)
    expected_values: str | None = Field(None, max_length=4000)


class CaseConfirm(BaseModel):
    base: int = Field(..., ge=1)
    expected_answer: str | None = Field(None, max_length=6000)
    evaluation: str = Field("objective", pattern=EVALUATION)
    expected_values: str | None = Field(None, max_length=4000)


class CaseRetire(BaseModel):
    base: int = Field(..., ge=1)
    reason: str = Field(..., min_length=3, max_length=500)


class RunCap(BaseModel):
    usd_cap: float | None = Field(None, gt=0, le=100)


class CaseRepeat(RunCap):
    n: int = Field(REPEAT_DEFAULT_N, ge=REPEAT_MIN_N, le=REPEAT_MAX_N)


class TurnRepeat(CaseRepeat):
    mode_ids: list[Id] = Field(..., min_length=1, max_length=10)
    expected_answer: str | None = Field(None, max_length=6000)
    evaluation: str = Field("objective", pattern=EVALUATION)


class ReleaseStart(RunCap):
    k: int = Field(RELEASE_DEFAULT_K, ge=1, le=RELEASE_MAX_K)
    include_capability: bool = True


# -- monitoring: the platform team's, for the company it names or for all of them ------------------------------------

@router.get("/monitor/plan")
async def monitor_plan(since: datetime | None = Query(None), until: datetime | None = Query(None),
                       rate: float = Query(monitor.DEFAULT_RATE, gt=0, le=1),
                       seed: int = Query(monitor.DEFAULT_SEED, ge=0, le=SEED_MAX),
                       risk_groups: str | None = Query(None, max_length=200),
                       mode_ids: str | None = Query(None, max_length=2000),
                       who: Starter = Depends(starter)) -> dict[str, Any]:
    out = await _call(monitor.plan_preview(organization_id=who.org, since=since, until=until, sample_rate=rate, seed=seed,
                                           risk_groups=_names(risk_groups), mode_ids=_names(mode_ids)))
    return {"ok": True, **out}


@router.post("/monitor/batches", status_code=202)
async def start_monitor_batch(body: BatchStart, who: Starter = Depends(starter)) -> dict[str, Any]:
    _charge("start_monitor_batch", who.principal)
    actor_id, actor_email = _actor(who.principal)
    out = await _call(monitor.start_batch(organization_id=who.org, since=body.since, until=body.until,
                                          sample_rate=body.sample_rate, seed=body.seed, risk_groups=body.risk_groups,
                                          mode_ids=body.mode_ids, usd_cap=body.usd_cap, actor_id=actor_id,
                                          actor_email=actor_email))
    return {"ok": True, **out}


@router.post("/monitor/batches/{batch_id}/resume", status_code=202)
async def resume_monitor_batch(batch_id: str = Path(..., max_length=ID_MAX),
                               who: Starter = Depends(starter)) -> dict[str, Any]:
    """Without it an interrupted batch would block its scope for good: one open batch per scope counts `interrupted`.
    A batch is resumed from the scope it was started in: a company's by a superadmin acting for it, a platform-wide one
    by a superadmin acting for none (any other scope reads as not found)."""
    _charge("resume_monitor_batch", who.principal)
    out = await _call(monitor.resume_batch(batch_id, org=who.org))
    return {"ok": True, **out}


@router.post("/monitor/batches/{batch_id}/review-batch", status_code=201)
async def open_review_batch(body: ReviewBatch, batch_id: str = Path(..., max_length=ID_MAX),
                            who: Starter = Depends(starter)) -> dict[str, Any]:
    """Acting for a company, only that company's own batch opens (the store checks it before reading anything: another
    company's, or a platform-wide one, is the same 404 as an unknown batch)."""
    actor_id, actor_email = _actor(who.principal)
    ids = await _call(monitor.open_review_batches(batch_id, body.mode_id, actor_id=actor_id, actor_email=actor_email,
                                                   org=who.org))
    return {"ok": True, "review_batch_ids": ids}


# -- monitoring: reads (a company's own, or the platform's view) -----------------------------------------------------

@router.get("/monitor/batches")
async def monitor_batches(limit: int = Query(20, ge=1, le=100), before: datetime | None = Query(None),
                          who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, **await _call(monitor.list_batches(org=who.org, limit=limit, before=before))}


@router.get("/monitor/batches/{batch_id}")
async def monitor_batch(batch_id: str = Path(..., max_length=ID_MAX), mode_id: str | None = Query(None, max_length=UUID_LEN),
                        offset: int = Query(0, ge=0), who: Reader = Depends(reader)) -> dict[str, Any]:
    out = await _call(monitor.batch_detail(batch_id, org=who.org, mode_id=mode_id, offset=offset))
    return {"ok": True, **out}


@router.get("/monitor/chart")
async def monitor_chart(mode_id: str = Query(..., max_length=UUID_LEN), periods: int = Query(12, ge=1, le=52),
                        who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, **await _call(monitor.chart(org=who.org, mode_id=mode_id, periods=periods))}


@router.get("/monitor/code-checks")
async def monitor_code_checks(periods: int = Query(12, ge=1, le=52), who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, **await _call(monitor.code_check_trend(org=who.org, periods=periods))}


@router.get("/monitor/automation")
async def monitor_automation(weeks: int = Query(12, ge=1, le=52), who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, **await _call(monitor.automation_trend(org=who.org, weeks=weeks))}


# -- eval cases (one company's) --------------------------------------------------------------------------------------

@router.get("/eval-cases")
async def list_eval_cases(status: str | None = Query(None, pattern="^(proposed|draft|baselining|active|retired|superseded)$"),
                          kind: str | None = Query(None, pattern="^(unclassified|regression|capability)$"),
                          mode_id: str | None = Query(None, max_length=UUID_LEN),
                          who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, "cases": await _call(_cases().list_cases(who.org, status=status, kind=kind, mode_id=mode_id))}


@router.post("/eval-cases", status_code=201)
async def create_eval_case(body: CaseCreate, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    actor_id, actor_email = _actor(who.principal)
    case = await _call(_cases().create_case(
        who.org, source=body.source.model_dump(), mode_id=body.mode_id, input_override=body.input,
        initial_state=body.initial_state, expected=body.expected, expected_answer=body.expected_answer,
        evaluation=body.evaluation, expected_values=body.expected_values, actor_id=actor_id, actor_email=actor_email))
    return {"ok": True, "case": case}


@router.get("/eval-cases/{case_id}")
async def eval_case(case_id: str = Path(..., max_length=ID_MAX), who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, **await _call(_cases().get_case(who.org, case_id))}


@router.patch("/eval-cases/{case_id}")
async def edit_eval_case(body: CaseEdit, case_id: str = Path(..., max_length=ID_MAX),
                         who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    actor_id, actor_email = _actor(who.principal)
    case = await _call(_cases().edit_case(
        who.org, case_id, base=body.base, input_override=body.input, initial_state=body.initial_state,
        expected=body.expected, expected_answer=body.expected_answer, evaluation=body.evaluation,
        expected_values=body.expected_values, actor_id=actor_id, actor_email=actor_email))
    return {"ok": True, "case": case}


@router.post("/eval-cases/{case_id}/confirm")
async def confirm_eval_case(body: CaseConfirm, case_id: str = Path(..., max_length=ID_MAX),
                            who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    actor_id, actor_email = _actor(who.principal)
    case = await _call(_cases().confirm_case(
        who.org, case_id, base=body.base, expected_answer=body.expected_answer, evaluation=body.evaluation,
        expected_values=body.expected_values, actor_id=actor_id, actor_email=actor_email))
    return {"ok": True, "case": case}


@router.post("/eval-cases/{case_id}/retire")
async def retire_eval_case(body: CaseRetire, case_id: str = Path(..., max_length=ID_MAX),
                           who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    case = await _call(_cases().retire_case(who.org, case_id, base=body.base, reason=body.reason))
    return {"ok": True, "case": case}


# -- runs through the golden runner (the caller's own bearer) --------------------------------------------------------

@router.post("/eval-cases/{case_id}/baseline", status_code=202)
async def baseline_eval_case(request: Request, body: RunCap, case_id: str = Path(..., max_length=ID_MAX),
                             who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    _charge("baseline_eval_case", who.principal)
    out = await _call(_cases().start_baseline(_ctx(request, who), case_id, usd_cap=body.usd_cap))
    return {"ok": True, **out}


@router.post("/eval-cases/{case_id}/repeat", status_code=202)
async def repeat_eval_case(request: Request, body: CaseRepeat, case_id: str = Path(..., max_length=ID_MAX),
                           who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    _charge("repeat_eval_case", who.principal)
    out = await _call(_cases().start_repeat(_ctx(request, who), case_id, n=body.n, usd_cap=body.usd_cap))
    return {"ok": True, **out}


@router.post("/turns/{turn_id}/repeat", status_code=202)
async def repeat_turn(request: Request, body: TurnRepeat, turn_id: str = Path(..., min_length=1, max_length=TURN_ID_MAX),
                      who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    _charge("repeat_turn", who.principal)
    actor_id, actor_email = _actor(who.principal)
    out = await _call(_cases().repeat_turn(
        _ctx(request, who), turn_id, n=body.n, mode_ids=body.mode_ids, expected_answer=body.expected_answer,
        evaluation=body.evaluation, usd_cap=body.usd_cap, actor_id=actor_id, actor_email=actor_email))
    return {"ok": True, **out}


@router.post("/eval-runs/{golden_run_id}/grade", status_code=202)
async def grade_eval_run(body: RunCap, golden_run_id: str = Path(..., max_length=ID_MAX),
                         who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    _charge("grade_eval_run", who.principal)
    actor_id, actor_email = _actor(who.principal)
    out = await _call(_cases().start_grading(who.org, golden_run_id, usd_cap=body.usd_cap, actor_id=actor_id,
                                               actor_email=actor_email))
    return {"ok": True, **out}


@router.get("/eval-runs/{golden_run_id}")
async def eval_run(golden_run_id: str = Path(..., max_length=ID_MAX), who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, **await _call(_cases().eval_run(who.org, golden_run_id))}


# -- release checks --------------------------------------------------------------------------------------------------

@router.post("/release-checks", status_code=202)
async def start_release_check(request: Request, body: ReleaseStart, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    _charge("start_release_check", who.principal)
    out = await _call(_cases().start_release_check(_ctx(request, who), k=body.k,
                                                     include_capability=body.include_capability, usd_cap=body.usd_cap))
    return {"ok": True, **out}


@router.get("/release-checks")
async def release_checks(limit: int = Query(20, ge=1, le=100), who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    return {"ok": True, "checks": await _call(_cases().list_release_checks(who.org, limit=limit))}


@router.get("/release-checks/{golden_run_id}")
async def release_check(golden_run_id: str = Path(..., max_length=ID_MAX),
                        format_: str = Query("json", alias="format", pattern="^(json|md)$"),
                        company_name: str | None = Query(None, max_length=COMPANY_NAME_MAX),
                        who: Reviewer = Depends(reviewer)) -> Response:
    """The report to attach to a deploy: JSON, or Markdown with the company's name (on one line) in the heading."""
    report = await _call(_cases().release_report(who.org, golden_run_id))
    name = _download_name(report, golden_run_id)
    headers = {"Cache-Control": "private, no-store"}
    if format_ == "md":
        company = " ".join((company_name or "").split()) or GENERIC_COMPANY
        return Response(content=_cases().render_release_md(report, company=company),
                        media_type="text/markdown; charset=utf-8",
                        headers={**headers, "Content-Disposition": f'attachment; filename="{name}.md"'})
    return JSONResponse(content={"ok": True, **report},
                        headers={**headers, "Content-Disposition": f'attachment; filename="{name}.json"'})
