"""Hoist crons - scheduled engine jobs (engines/crons/jobs.py).

    GET    /api/crons/catalogue          the jobs a company can schedule, and the cadence presets
    GET    /api/crons                    the company's jobs, each with its latest runs, and the recent runs
    GET    /api/crons/daily              each job's status day by day (runs, failures, last result)
    GET    /api/crons/{job_id}/events    who created, changed, paused, ran or removed the job, and when
    POST   /api/crons                    schedule one or more jobs on one cadence
    PATCH  /api/crons/{job_id}           re-cadence, rename, pause / resume
    DELETE /api/crons/{job_id}           remove the job (its runs go with it from the panel)
    POST   /api/crons/{job_id}/run       run it now (waits for the run)

Jobs belong to the company: everyone in it sees them on the Hoist Crons panel; only an admin
may create, change, run or remove one, because a job runs company-wide scans on the
company's data. Each run happens as the job's creator, through the route a person would call.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.auth import access
from ...engines.crons import jobs as cron_svc
from ...engines.reports import cards as card_svc
from .auth import scope

router = APIRouter(prefix="/api/crons", tags=["crons"], dependencies=[Depends(scope)])


def _bad(exc: cron_svc.CronError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                         detail={"ok": False, "error": str(exc), "reason": exc.reason})


def _admin_only(s: access.Scope) -> None:
    if not s.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={
            "ok": False, "error": "Only an admin can schedule, change or run jobs.", "reason": "admin_only"})


def _org(s: access.Scope, organization_id: UUID | None) -> UUID:
    org = access.organization_for(s, organization_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "ok": False, "error": "Choose a company first.", "reason": "no_organization"})
    return org


class CronBody(BaseModel):
    job_keys: list[str] = Field(..., min_length=1, max_length=10, description="Catalogue keys; one job each.")
    refresh: Any = Field(..., description='Preset key ("30m","1h","6h","12h","24h","daily") or '
                                         '{"every_minutes": n} | {"daily_at": "HH:MM"} | {"days": [0-6], "time": "HH:MM"}')
    timezone: str | None = None
    prompt: str | None = Field(None, max_length=4000, description="The question, for the 'question' job.")
    email: bool = Field(False, description="For the 'question' job: email each answer to you, from the platform's sender.")
    run_now: bool = False
    source_session_id: str | None = Field(None, max_length=120)
    organization_id: UUID | None = None


class CronPatch(BaseModel):
    refresh: Any = None
    timezone: str | None = None
    enabled: bool | None = None
    name: str | None = Field(None, max_length=200)


@router.get("/catalogue")
async def catalogue() -> dict[str, Any]:
    return {"jobs": cron_svc.catalogue(),
            "refresh_presets": [{k: v for k, v in p.items()} for p in card_svc.PRESETS],
            "min_every_minutes": card_svc.MIN_EVERY_MINUTES}


@router.get("")
async def list_jobs(organization_id: UUID | None = None, runs: int = Query(3, ge=0, le=10),
                    session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    org = _org(s, organization_id)
    jobs = await cron_svc.list_jobs(session, org, runs=runs)
    return {"jobs": jobs, "recent_runs": await cron_svc.recent_runs(session, org, limit=20),
            "can_manage": s.is_admin}


@router.get("/daily")
async def daily(organization_id: UUID | None = None, days: int = Query(14, ge=1, le=cron_svc.RUN_HISTORY_DAYS),
                tz: str = Query("UTC", max_length=64), session: AsyncSession = Depends(get_session),
                s: access.Scope = Depends(scope)) -> dict[str, Any]:
    try:
        return await cron_svc.daily_status(session, _org(s, organization_id), days=days, tz=tz)
    except cron_svc.CronError as exc:
        raise _bad(exc) from None


@router.get("/{job_id}/events")
async def events(job_id: UUID, organization_id: UUID | None = None, limit: int = Query(50, ge=1, le=500),
                 session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    org = _org(s, organization_id)
    return {"events": await cron_svc.job_events(session, org, job_id, limit=limit)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_jobs(body: CronBody, session: AsyncSession = Depends(get_session),
                      s: access.Scope = Depends(scope)) -> dict[str, Any]:
    _admin_only(s)
    org = _org(s, body.organization_id)
    keys = list(dict.fromkeys(k.strip() for k in body.job_keys if k and k.strip()))
    unknown = [k for k in keys if k not in cron_svc.CATALOGUE]
    if unknown:
        raise _bad(cron_svc.CronError("unknown_job", f"Unknown job(s): {', '.join(unknown)}."))
    created = []
    try:
        for k in keys:
            created.append(await cron_svc.create_job(
                session, organization_id=org, owner_user_id=s.user_id, job_key=k, refresh=body.refresh,
                tz=body.timezone, params={"prompt": body.prompt, "email": body.email} if k == "question" else None,
                run_now=body.run_now, source_session_id=body.source_session_id))
    except cron_svc.CronError as exc:
        raise _bad(exc) from None
    return {"ok": True, "jobs": created}


@router.patch("/{job_id}")
async def update_job(job_id: UUID, body: CronPatch, organization_id: UUID | None = None,
                     session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    _admin_only(s)
    try:
        job = await cron_svc.update_job(session, _org(s, organization_id), job_id, refresh=body.refresh,
                                        tz=body.timezone, enabled=body.enabled, name=body.name, user_id=s.user_id)
    except cron_svc.CronError as exc:
        raise _bad(exc) from None
    if job is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "No such job.", "reason": "job_not_found"})
    return {"ok": True, "job": job}


@router.delete("/{job_id}")
async def remove_job(job_id: UUID, organization_id: UUID | None = None,
                     session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    _admin_only(s)
    if not await cron_svc.remove_job(session, _org(s, organization_id), job_id, user_id=s.user_id):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "No such job.", "reason": "job_not_found"})
    return {"ok": True}


@router.post("/{job_id}/run")
async def run_now(job_id: UUID, organization_id: UUID | None = None,
                  session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    _admin_only(s)
    out = await cron_svc.run_job(session, job_id, trigger="manual", organization_id=_org(s, organization_id),
                                 requested_by=s.user_id)
    if out is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "No such job.", "reason": "job_not_found"})
    return out
