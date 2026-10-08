"""Hoist crons - engine jobs a company schedules from the chat, run by the service's own clock.

The Home page's Hoist Crons panel showed what the engines had done (approvals raised,
anomalies detected) but nothing could be put on it: the energy anomaly scan, the compliance
expiry scan and the rest ran when someone pressed a button or an upload triggered them. A job
here is one catalogue entry on a cadence. The catalogue is fixed - a job is a route this
service already has, not arbitrary code - and each run calls that route AS the job's creator,
with a short-lived token minted for them (the report cards' mechanism, engines/reports/cards),
so a job reads and writes exactly what its creator may and nothing past it.

The cadence is the report cards' too: an interval of five minutes or more, a time each day, or
chosen days at a time, in the creator's zone. The report scheduler's loop runs due jobs on the
same tick as due cards, claiming each with FOR UPDATE SKIP LOCKED so two replicas never run one
job twice. Every run is a row - a failed one too.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ..reports import cards as card_engine

log = get_logger(__name__)

#: Runs kept per job: every run of the last RUN_HISTORY_DAYS days (the day-by-day status reads
#: them), capped so a 15-minute job cannot grow without bound.
RUN_HISTORY_DAYS = 30
MAX_RUNS_KEPT = 3000
#: A run still marked running after this long is presumed dead and put back on its cadence.
STALE_RUN_MINUTES = 30
#: Jobs a company may have at once. A cadence is a cost; a hundred of them is a mistake.
MAX_JOBS_PER_ORG = 40

#: The jobs a company can schedule. ``call`` is the route the job runs - this service's own,
#: called as the creator - or ``question`` for a prompt asked of the orchestrator.
CATALOGUE: dict[str, dict[str, Any]] = {
    "energy_anomaly_scan": {
        "label": "Energy anomaly scan", "module": "Energy",
        "description": "Runs every anomaly rule over the latest meter readings and raises what it finds for review.",
        "call": ("POST", "/api/energy/anomalies/scan-all", {}, None), "suggest": {"every_minutes": 60},
        "building": "query",
    },
    "energy_chiller_scan": {
        "label": "Chiller efficiency scan", "module": "Energy",
        "description": "Compares every chiller's readings with its design kW/RT over the last 14 days.",
        "call": ("POST", "/api/energy/chillers/scan", None, {}), "suggest": {"daily_at": "06:00"},
    },
    "energy_condition_scan": {
        "label": "Asset condition scan", "module": "Assets",
        "description": "Bands every asset Threat / Watch / In control from its section's energy and open anomalies.",
        "call": ("POST", "/api/energy/condition/scan", {}, None), "suggest": {"daily_at": "06:30"},
        "building": "query",
    },
    "energy_meter_gaps": {
        "label": "Meter gap check", "module": "Energy",
        "description": "Finds meters whose readings have stopped or have holes in the series.",
        "call": ("POST", "/api/energy/gaps/detect", {}, None), "suggest": {"every_minutes": 360},
        "building": "query",
    },
    "energy_benchmarks": {
        "label": "Benchmark validation", "module": "Energy",
        "description": "Re-derives each building's EUI from its readings and positions it against its benchmark.",
        "call": ("POST", "/api/energy/benchmarks/validate", {}, None), "suggest": {"daily_at": "02:00"},
        "building": "query",
    },
    "compliance_expiry_scan": {
        "label": "Compliance expiry scan", "module": "Compliance",
        "description": "Re-reads every certificate's status against today - lapsed, expiring in 30/60/90 days, blocked vendors.",
        "call": ("POST", "/api/compliance/scan", None, {"scope": "all"}), "suggest": {"daily_at": "07:00"},
        "building": "body",
    },
    "compliance_reverify": {
        "label": "Certificate re-verification", "module": "Compliance",
        "description": "Checks certificates that are due a check against their public registers again.",
        "call": ("POST", "/api/compliance/reverify", None, {"limit": 50}), "suggest": {"days": [1], "time": "07:00"},
    },
    "vendor_scorecards_monthly": {
        "label": "Monthly vendor scorecards", "module": "Vendors",
        "description": "Cuts last month's scorecard for every vendor with scored work orders - SLA, quality, "
                       "cost - the month-end job. For one building: the vendors working there.",
        "call": ("POST", "/api/contract-performance/scorecards/monthly-all", {}, None),
        "suggest": {"monthly_day": 1, "time": "06:00"}, "building": "query",
    },
    # Maintenance watch (engines/maintenance/watch.py): each reports, and raises each finding
    # in the Approvals queue once - never a work order or purchase order of its own.
    "maintenance_sla_watch": {
        "label": "Work-order SLA watch", "module": "Maintenance",
        "description": "Open work orders past their SLA or due within 24 hours, by vendor and priority. "
                       "Each breach goes to Approvals to chase.",
        "call": ("POST", "/api/maintenance/sla-watch", {}, None), "suggest": {"every_minutes": 60},
        "building": "query",
    },
    "maintenance_ppm_due": {
        "label": "PPM due list", "module": "Maintenance",
        "description": "Maintenance plans overdue or due in the next 14 days. Each overdue plan with no "
                       "open work order goes to Approvals to raise.",
        "call": ("POST", "/api/maintenance/ppm-due", {}, None), "suggest": {"daily_at": "07:00"},
        "building": "query",
    },
    "maintenance_ppm_missed": {
        "label": "Missed PPM follow-up", "module": "Maintenance",
        "description": "PPM visits marked Missed, or Deferred past their new date, and not rebooked - per "
                       "vendor. Each goes to Approvals to rebook.",
        "call": ("POST", "/api/maintenance/ppm-missed", {}, None), "suggest": {"daily_at": "08:00"},
        "building": "query",
    },
    "maintenance_parts_reorder": {
        "label": "Spare parts reorder", "module": "Maintenance",
        "description": "Parts at or below their reorder level, with how many to order and from whom. "
                       "Each goes to Approvals to order. Stock is company-wide.",
        "call": ("POST", "/api/maintenance/parts-reorder", {}, None), "suggest": {"days": [1, 4], "time": "07:00"},
    },
    "maintenance_monthly_summary": {
        "label": "Monthly maintenance summary", "module": "Maintenance",
        "description": "Last month's work orders raised and closed, SLA hit rate and PPM completion, by "
                       "building and vendor. A vendor below target goes to Approvals for review.",
        "call": ("POST", "/api/maintenance/monthly-summary", {}, None),
        "suggest": {"monthly_day": 1, "time": "06:00"}, "building": "query",
    },
    "question": {
        "label": "Ask a question", "module": "Orchestrator",
        "description": "Asks the orchestrator your question on the schedule and keeps each answer.",
        "call": "question", "suggest": {"daily_at": "08:00"}, "needs_prompt": True, "building": "prompt",
    },
}


#: "Not given" for a PATCH field whose None means something (building: None = company-wide).
UNSET: Any = object()


class CronError(ValueError):
    """A job the service will not schedule. ``reason`` is stable for clients."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


#: Addresses a job may mail its result to, besides its creator.
MAX_RECIPIENTS = 10
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def clean_recipients(value: Any) -> list[str]:
    """A job's extra recipients: a list, or one string split on commas, semicolons or spaces.
    Lower-cased, de-duplicated, every one a plausible address - a typo is refused, never dropped."""
    items = re.split(r"[,;\s]+", value) if isinstance(value, str) else list(value or [])
    out: list[str] = []
    for a in items:
        a = str(a or "").strip().lower()
        if not a:
            continue
        if len(a) > 254 or not _EMAIL_RE.match(a):
            raise CronError("bad_recipient", f"{a!r} is not an email address.")
        if a not in out:
            out.append(a)
    if len(out) > MAX_RECIPIENTS:
        raise CronError("too_many_recipients", f"A job can email at most {MAX_RECIPIENTS} addresses.")
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(v) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v else None)


def catalogue() -> list[dict[str, Any]]:
    """What a client may offer: the jobs, and the cadences (the report cards' presets)."""
    return [{"key": k, "label": v["label"], "module": v["module"], "description": v["description"],
             "suggested_refresh": v["suggest"], "suggested_label": card_engine.refresh_label(v["suggest"]),
             "needs_prompt": bool(v.get("needs_prompt")),
             # Whether the job can be scheduled for one building, not only company-wide.
             "per_building": bool(v.get("building"))} for k, v in CATALOGUE.items()]


async def resolve_building(session: AsyncSession, organization_id: UUID, building_id: Any) -> dict[str, str]:
    """A job's building: one of the company's own, by id. Returns {building_id, building_name}."""
    try:
        bid = UUID(str(building_id))
    except (TypeError, ValueError):
        raise CronError("bad_building", "building_id is not a building's id.") from None
    r = (await session.execute(text("""
        SELECT building_id, COALESCE(NULLIF(name, ''), building_code, building_id::text) AS name
          FROM plenum_cafm.buildings
         WHERE building_id = :b AND (organization_id IS NULL OR organization_id::text = :o)"""),
        {"b": bid, "o": str(organization_id)})).mappings().first()
    if r is None:
        raise CronError("unknown_building", "That building is not one of this company's.")
    return {"building_id": str(r["building_id"]), "building_name": str(r["name"])}


def _row(r: dict) -> dict[str, Any]:
    spec = CATALOGUE.get(r["job_key"], {})
    refresh = r["refresh"] if isinstance(r["refresh"], dict) else json.loads(r["refresh"] or "{}")
    params = r["params"] if isinstance(r["params"], dict) else json.loads(r["params"] or "{}")
    summary = r["last_summary"]
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except ValueError:
            summary = None
    return {
        "id": str(r["id"]), "job_key": r["job_key"], "name": r["name"],
        "label": spec.get("label") or r["job_key"], "module": spec.get("module") or "",
        "params": params, "refresh": refresh, "refresh_label": card_engine.refresh_label(refresh),
        "timezone": r["timezone"], "enabled": r["enabled"], "status": r["status"],
        "next_run_at": _iso(r["next_run_at"]), "last_run_at": _iso(r["last_run_at"]),
        "last_tried_at": _iso(r["last_tried_at"]), "last_error": r["last_error"],
        "last_summary": summary, "owner_user_id": str(r["owner_user_id"]),
        "owner_email": r.get("owner_email"), "created_at": _iso(r["created_at"]),
        # Who asked for it, and who last changed it - kept as the email too, so the record
        # still reads after the account is renamed or removed.
        "created_by": {"user_id": str(r["created_by_user_id"]) if r.get("created_by_user_id") else None,
                       "email": r.get("created_by_email")},
        "updated_by": {"user_id": str(r["updated_by_user_id"]) if r.get("updated_by_user_id") else None,
                       "email": r.get("updated_by_email"), "at": _iso(r.get("updated_at"))},
    }


async def _email(session: AsyncSession, user_id: UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await session.execute(text("SELECT email FROM plenum_cafm.users WHERE id = :i"),
                                  {"i": user_id})).scalar_one_or_none()


async def record_event(session: AsyncSession, *, job_id: UUID, organization_id: UUID, action: str,
                       user_id: UUID | None, email: str | None = None, details: dict | None = None) -> None:
    """One row in the job's history - who did what, when. Append-only; the caller commits."""
    await session.execute(text("""
        INSERT INTO plenum_cafm.hoist_cron_events (id, job_id, organization_id, action, user_id, user_email, details, at)
        VALUES (:id, :j, :o, :a, :u, :e, CAST(:d AS jsonb), now())"""),
        {"id": uuid4(), "j": job_id, "o": organization_id, "a": action, "u": user_id,
         "e": email if email is not None else await _email(session, user_id),
         "d": json.dumps(details or {}, default=str)})


async def job_events(session: AsyncSession, organization_id: UUID, job_id: UUID, *, limit: int = 50) -> list[dict]:
    n = max(1, min(int(limit), 500))
    rows = (await session.execute(text("""
        SELECT action, user_id, user_email, details, at FROM plenum_cafm.hoist_cron_events
         WHERE job_id = :j AND organization_id = :o ORDER BY at DESC LIMIT :n"""),
        {"j": job_id, "o": organization_id, "n": n})).mappings().all()
    return [{"action": r["action"], "user_id": str(r["user_id"]) if r["user_id"] else None,
             "user_email": r["user_email"],
             "details": r["details"] if isinstance(r["details"], dict) else json.loads(r["details"] or "{}"),
             "at": _iso(r["at"])} for r in rows]


async def job_runs(session: AsyncSession, organization_id: UUID, job_id: UUID, *, limit: int = 100) -> list[dict]:
    """One job's runs, newest first - the detail page's history."""
    n = max(1, min(int(limit), 500))
    rows = (await session.execute(text("""
        SELECT * FROM plenum_cafm.hoist_cron_runs WHERE job_id = :j AND organization_id = :o
         ORDER BY finished_at DESC LIMIT :n"""), {"j": job_id, "o": organization_id, "n": n})).mappings().all()
    return [_run_row(dict(r)) for r in rows]


async def stats(session: AsyncSession, organization_id: UUID, *, tz: str = "UTC") -> dict:
    """The page's tiles: runs today (in the reader's zone), failures among them, emails this week."""
    try:
        zone = card_engine.parse_timezone(tz)
    except card_engine.RefreshError:
        zone = "UTC"
    r = (await session.execute(text("""
        SELECT count(*) FILTER (WHERE (r.finished_at AT TIME ZONE :tz)::date = (now() AT TIME ZONE :tz)::date) AS runs_today,
               count(*) FILTER (WHERE (r.finished_at AT TIME ZONE :tz)::date = (now() AT TIME ZONE :tz)::date AND NOT r.ok) AS failed_today,
               count(*) FILTER (WHERE r.finished_at >= now() - interval '7 days' AND r.summary ? 'emailed_to'
                                  AND coalesce(r.summary->>'email_status', '') <> 'failed') AS emails_week
          FROM plenum_cafm.hoist_cron_runs r
          JOIN plenum_cafm.hoist_cron_jobs j ON j.id = r.job_id AND j.removed_at IS NULL
         WHERE r.organization_id = :o"""), {"o": organization_id, "tz": zone})).mappings().first()
    return {"runs_today": int(r["runs_today"] or 0), "failed_today": int(r["failed_today"] or 0),
            "emails_week": int(r["emails_week"] or 0), "timezone": zone}


async def daily_status(session: AsyncSession, organization_id: UUID, *, days: int = 14, tz: str = "UTC",
                       job_id: UUID | None = None) -> dict:
    """Each job's runs rolled up by day in ``tz``: how many ran, how many failed, the last result.

    A day with no run is listed too (runs 0) - for a job that should have run, an empty day is
    the finding."""
    from zoneinfo import ZoneInfo

    d = max(1, min(int(days), RUN_HISTORY_DAYS))
    try:
        zone = card_engine.parse_timezone(tz)
    except card_engine.RefreshError as exc:
        raise CronError(exc.reason, str(exc)) from None
    rows = (await session.execute(text("""
        SELECT r.job_id, (r.finished_at AT TIME ZONE :tz)::date AS day,
               count(*) AS runs, count(*) FILTER (WHERE r.ok) AS ok, count(*) FILTER (WHERE NOT r.ok) AS failed,
               (array_agg(r.summary ORDER BY r.finished_at DESC))[1] AS last_summary,
               (array_agg(r.error ORDER BY r.finished_at DESC))[1] AS last_error,
               max(r.finished_at) AS last_at
          FROM plenum_cafm.hoist_cron_runs r
         WHERE r.organization_id = :o AND r.finished_at >= now() - make_interval(days => :d)
         GROUP BY r.job_id, day"""), {"o": organization_id, "tz": zone, "d": d})).mappings().all()
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date()
    span = [today.fromordinal(today.toordinal() - i).isoformat() for i in range(d - 1, -1, -1)]
    by_job: dict[str, dict[str, dict]] = {}
    for r in rows:
        summary = r["last_summary"]
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except ValueError:
                summary = None
        status = "ok" if not r["failed"] else ("failed" if not r["ok"] else "partial")
        by_job.setdefault(str(r["job_id"]), {})[r["day"].isoformat()] = {
            "date": r["day"].isoformat(), "runs": r["runs"], "ok": r["ok"], "failed": r["failed"],
            "status": status, "last_summary": summary, "last_error": r["last_error"], "last_at": _iso(r["last_at"])}
    out = []
    for j in await list_jobs(session, organization_id, runs=0):
        if job_id is not None and j["id"] != str(job_id):
            continue
        per_day = by_job.get(j["id"], {})
        out.append({"job_id": j["id"], "name": j["name"], "refresh_label": j["refresh_label"],
                    "enabled": j["enabled"], "created_by": j["created_by"],
                    "days": [per_day.get(day) or {"date": day, "runs": 0, "ok": 0, "failed": 0, "status": "none"}
                             for day in span]})
    return {"timezone": zone, "days": span, "jobs": out}


_JOB_COLUMNS = """j.id, j.job_key, j.name, j.params, j.refresh, j.timezone, j.enabled, j.status, j.next_run_at,
                  j.last_run_at, j.last_tried_at, j.last_error, j.last_summary, j.owner_user_id, j.created_at,
                  j.created_by_user_id, j.created_by_email, j.updated_by_user_id, j.updated_by_email, j.updated_at"""


async def list_jobs(session: AsyncSession, organization_id: UUID, *, runs: int = 3) -> list[dict[str, Any]]:
    rows = (await session.execute(text(f"""
        SELECT {_JOB_COLUMNS}, u.email AS owner_email
          FROM plenum_cafm.hoist_cron_jobs j
          LEFT JOIN plenum_cafm.users u ON u.id = j.owner_user_id
         WHERE j.organization_id = :o AND j.removed_at IS NULL
         ORDER BY j.created_at"""), {"o": organization_id})).mappings().all()
    out = [_row(dict(r)) for r in rows]
    if out and runs:
        ids = [UUID(j["id"]) for j in out]
        rr = (await session.execute(text("""
            SELECT * FROM (
              SELECT r.*, row_number() OVER (PARTITION BY r.job_id ORDER BY r.finished_at DESC) AS rn
                FROM plenum_cafm.hoist_cron_runs r WHERE r.job_id = ANY(:ids)) x
             WHERE rn <= :n ORDER BY finished_at DESC"""), {"ids": ids, "n": int(runs)})).mappings().all()
        by_job: dict[str, list] = {}
        for r in rr:
            by_job.setdefault(str(r["job_id"]), []).append(_run_row(dict(r)))
        for j in out:
            j["runs"] = by_job.get(j["id"], [])
    return out


def _run_row(r: dict) -> dict[str, Any]:
    summary = r.get("summary")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except ValueError:
            summary = None
    return {"id": str(r["id"]), "job_id": str(r["job_id"]), "trigger": r["trigger"], "ok": r["ok"],
            "started_at": _iso(r["started_at"]), "finished_at": _iso(r["finished_at"]),
            "duration_ms": r["duration_ms"], "summary": summary, "answer": r.get("answer"), "error": r.get("error"),
            # Who asked for this run: a person's "Run now", or the schedule. run_as is whose
            # rights it ran with - the job's owner either way.
            "requested_by": r.get("requested_by_email") or ("the schedule" if r.get("trigger") == "schedule" else None),
            "requested_by_user_id": str(r["requested_by_user_id"]) if r.get("requested_by_user_id") else None,
            "run_as_user_id": str(r["run_as_user_id"]) if r.get("run_as_user_id") else None}


async def recent_runs(session: AsyncSession, organization_id: UUID, *, limit: int = 20) -> list[dict[str, Any]]:
    n = max(1, min(int(limit), 100))
    rows = (await session.execute(text("""
        SELECT r.*, j.job_key, j.name FROM plenum_cafm.hoist_cron_runs r
          JOIN plenum_cafm.hoist_cron_jobs j ON j.id = r.job_id
         WHERE r.organization_id = :o AND j.removed_at IS NULL ORDER BY r.finished_at DESC LIMIT :n"""),
        {"o": organization_id, "n": n})).mappings().all()
    out = []
    for r in rows:
        d = _run_row(dict(r))
        d["job_key"], d["name"] = r["job_key"], r["name"]
        d["label"] = CATALOGUE.get(r["job_key"], {}).get("label") or r["job_key"]
        out.append(d)
    return out


def default_name(job_key: str, params: dict[str, Any]) -> str:
    """A job's name when none was given: the catalogue label, or the question, and its building."""
    spec = CATALOGUE.get(job_key, {})
    base = ("Ask: " + str(params.get("prompt") or "")[:80]) if spec.get("needs_prompt") else spec.get("label", job_key)
    return base + (" · " + params["building_name"] if params.get("building_name") else "")


async def create_job(session: AsyncSession, *, organization_id: UUID, owner_user_id: UUID, job_key: str,
                     refresh: Any, tz: str | None = None, name: str | None = None,
                     params: dict[str, Any] | None = None, run_now: bool = False,
                     source_session_id: str | None = None) -> dict[str, Any]:
    spec = CATALOGUE.get(job_key or "")
    if spec is None:
        raise CronError("unknown_job", f"Unknown job {job_key!r}. Choose one of: {', '.join(CATALOGUE)}.")
    try:
        ref = card_engine.parse_refresh(refresh if refresh is not None else spec["suggest"])
        zone = card_engine.parse_timezone(tz)
    except card_engine.RefreshError as exc:
        raise CronError(exc.reason, str(exc)) from None
    params = dict(params or {})
    raw_building = params.get("building_id")
    if raw_building and not spec.get("building"):
        raise CronError("company_wide_only", f"{spec['label']} runs company-wide; it cannot be scheduled for one building.")
    # recipients: other people each run's result is mailed to, on any job.
    recipients = clean_recipients(params.get("recipients"))
    if spec.get("needs_prompt"):
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise CronError("no_prompt", "A question job needs the question to ask.")
        # email: send each answer to the job's creator ("send me a compliance summary every
        # Monday") over the platform's own mail transport, from its own sender address.
        params = {"prompt": prompt[:4000], "email": bool(params.get("email")), "recipients": recipients}
    else:
        params = {"recipients": recipients}
    if raw_building:
        # building_id: the job covers this one building - its meters, certificates, vendors.
        params.update(await resolve_building(session, organization_id, raw_building))
    n = (await session.execute(text("""SELECT count(*) FROM plenum_cafm.hoist_cron_jobs
                                         WHERE organization_id = :o AND removed_at IS NULL"""),
                               {"o": organization_id})).scalar_one()
    if n >= MAX_JOBS_PER_ORG:
        raise CronError("too_many_jobs", f"A company can have at most {MAX_JOBS_PER_ORG} scheduled jobs.")
    jid, now = uuid4(), _now()
    creator_email = await _email(session, owner_user_id)
    label = (name or "").strip() or default_name(job_key, params)
    await session.execute(text("""
        INSERT INTO plenum_cafm.hoist_cron_jobs
               (id, organization_id, owner_user_id, job_key, name, params, refresh, timezone, enabled,
                status, next_run_at, source_session_id, created_at, updated_at,
                created_by_user_id, created_by_email, updated_by_user_id, updated_by_email)
        VALUES (:id, :o, :u, :k, :n, CAST(:p AS jsonb), CAST(:r AS jsonb), :tz, true, 'pending', :next, :sid, :now, :now,
                :u, :ce, :u, :ce)"""),
        {"id": jid, "o": organization_id, "u": owner_user_id, "k": job_key, "n": label[:200],
         "p": json.dumps(params), "r": json.dumps(ref), "tz": zone,
         "next": now if run_now else card_engine.next_run_at(ref, zone, now), "sid": source_session_id, "now": now,
         "ce": creator_email})
    await record_event(session, job_id=jid, organization_id=organization_id, action="created", user_id=owner_user_id,
                       email=creator_email, details={"job_key": job_key, "refresh": ref, "timezone": zone,
                                                     "run_now": run_now, "source_session_id": source_session_id})
    await session.commit()
    log.info("hoist_cron.created", job_id=str(jid), job_key=job_key, refresh=ref, org=str(organization_id))
    return await get_job(session, organization_id, jid)


async def get_job(session: AsyncSession, organization_id: UUID, job_id: UUID) -> dict[str, Any] | None:
    r = (await session.execute(text(f"""
        SELECT {_JOB_COLUMNS}, u.email AS owner_email FROM plenum_cafm.hoist_cron_jobs j
          LEFT JOIN plenum_cafm.users u ON u.id = j.owner_user_id
         WHERE j.id = :i AND j.organization_id = :o AND j.removed_at IS NULL"""),
        {"i": job_id, "o": organization_id})).mappings().first()
    return _row(dict(r)) if r else None


async def update_job(session: AsyncSession, organization_id: UUID, job_id: UUID, *, refresh: Any = None,
                     tz: str | None = None, enabled: bool | None = None, name: str | None = None,
                     user_id: UUID | None = None, prompt: str | None = None,
                     email: bool | None = None, recipients: Any = None,
                     building: Any = UNSET) -> dict[str, Any] | None:
    job = await get_job(session, organization_id, job_id)
    if job is None:
        return None
    was_default = str(job["name"] or "") == default_name(job["job_key"], job["params"] or {})
    # A question job's question and its email setting are editable too; an engine job has neither.
    # Any job's recipients are.
    params = dict(job["params"] or {})
    if recipients is not None:
        recipients = clean_recipients(recipients)
        params["recipients"] = recipients
    building_changed = False
    if building is not UNSET:
        params.pop("building_id", None)
        params.pop("building_name", None)
        if building:
            if not CATALOGUE.get(job["job_key"], {}).get("building"):
                raise CronError("company_wide_only", "This job runs company-wide; it cannot be limited to one building.")
            params.update(await resolve_building(session, organization_id, building))
        building_changed = (params.get("building_id") != (job["params"] or {}).get("building_id"))
        if not CATALOGUE.get(job["job_key"], {}).get("needs_prompt"):
            raise CronError("not_a_question", "Only a question job has a question or an email setting.")
        if prompt is not None:
            p = prompt.strip()
            if not p:
                raise CronError("no_prompt", "A question job needs the question to ask.")
            params["prompt"] = p[:4000]
        if email is not None:
            params["email"] = bool(email)
    # A name nobody chose follows the question and the building.
    if name is None and was_default and (prompt is not None or building_changed):
        name = default_name(job["job_key"], params)
    if recipients is not None or prompt is not None or email is not None or building_changed:
        await session.execute(text("UPDATE plenum_cafm.hoist_cron_jobs SET params = CAST(:p AS jsonb) WHERE id = :i"),
                              {"p": json.dumps(params), "i": job_id})
    ref, zone = job["refresh"], job["timezone"]
    try:
        if refresh is not None:
            ref = card_engine.parse_refresh(refresh)
        if tz is not None:
            zone = card_engine.parse_timezone(tz)
    except card_engine.RefreshError as exc:
        raise CronError(exc.reason, str(exc)) from None
    on = job["enabled"] if enabled is None else bool(enabled)
    now = _now()
    # A new cadence, or a resume, starts from now - not from the old schedule's last tick.
    nxt = card_engine.next_run_at(ref, zone, now) if on else None
    status = job["status"]
    if enabled is not None:
        status = "pending" if on and status in ("paused", "error") else ("paused" if not on else status)
    await session.execute(text("""
        UPDATE plenum_cafm.hoist_cron_jobs
           SET refresh = CAST(:r AS jsonb), timezone = :tz, enabled = :on, status = :st, next_run_at = :next,
               name = COALESCE(:n, name), updated_at = :now, updated_by_user_id = :u, updated_by_email = :ue
         WHERE id = :i AND organization_id = :o"""),
        {"r": json.dumps(ref), "tz": zone, "on": on, "st": status, "next": nxt,
         "n": (name or "").strip()[:200] or None, "now": now, "i": job_id, "o": organization_id,
         "u": user_id, "ue": await _email(session, user_id)})
    action = ("paused" if enabled is False and job["enabled"]
              else "resumed" if enabled is True and not job["enabled"] else "changed")
    await record_event(session, job_id=job_id, organization_id=organization_id, action=action, user_id=user_id,
                       details={k: v for k, v in {"refresh": ref if refresh is not None else None, "timezone": tz,
                                                  "enabled": enabled, "name": name, "prompt": prompt,
                                                  "email": email, "recipients": recipients,
                                                  "building": (params.get("building_name") or "all buildings")
                                                  if building_changed else None}.items()
                                 if v is not None})
    await session.commit()
    return await get_job(session, organization_id, job_id)


async def remove_job(session: AsyncSession, organization_id: UUID, job_id: UUID, *,
                     user_id: UUID | None = None) -> bool:
    res = await session.execute(text("""
        UPDATE plenum_cafm.hoist_cron_jobs SET removed_at = now(), enabled = false, next_run_at = NULL, updated_at = now(),
               removed_by_user_id = :u, updated_by_user_id = :u
         WHERE id = :i AND organization_id = :o AND removed_at IS NULL"""),
        {"i": job_id, "o": organization_id, "u": user_id})
    if res.rowcount:
        await record_event(session, job_id=job_id, organization_id=organization_id, action="removed", user_id=user_id)
    await session.commit()
    return bool(res.rowcount)


# ── running a job ────────────────────────────────────────────────────────────────

def _self_base() -> str:
    return (os.environ.get("OPERATIONS_INTELLIGENCE_BASE_URL")
            or f"http://127.0.0.1:{int(getattr(settings, 'service_port', 8009))}").rstrip("/")


def summarise(body: Any) -> dict[str, Any]:
    """The figures a job reported, a few keys, for the panel's one line.

    Every route answers in its own shape; what they share is that the facts worth a line are
    the top-level numbers and counts. Lists become their length, a nested ``summary`` is
    lifted one level, long text is dropped."""
    if not isinstance(body, dict):
        return {"result": len(body)} if isinstance(body, list) else {}
    out: dict[str, Any] = {}
    sources = [body]
    if isinstance(body.get("summary"), dict):
        sources.insert(0, body["summary"])
    for src in sources:
        for k, v in src.items():
            # report_lines are the run's report, not a figure.
            if len(out) >= 8 or k in out or k == "report_lines":
                continue
            if isinstance(v, bool) or isinstance(v, (int, float)):
                out[k] = v
            elif isinstance(v, list):
                out[k] = len(v)
            elif isinstance(v, str) and len(v) <= 60 and k in ("status", "message", "outcome"):
                out[k] = v
    return out


async def _call(job: dict[str, Any], token: str, organization_id: Any = None, *,
                http: httpx.AsyncClient | None = None) -> tuple[dict, str | None]:
    """Run the job's route as its creator. Returns (summary, answer)."""
    spec = CATALOGUE[job["job_key"]]
    headers = {"Authorization": f"Bearer {token}"}
    timeout = float(getattr(settings, "report_run_timeout_seconds", 180))
    building = (job.get("params") or {}).get("building_id")
    if spec["call"] == "question":
        prompt = job["params"].get("prompt") or ""
        if building:
            prompt = (f"About the building {job['params'].get('building_name') or building} only "
                      f"(building_id {building}): {prompt}")
        card = type("C", (), {"prompt": prompt, "id": UUID(job["id"]), "name": job["name"],
                              "refresh": job["refresh"], "timezone": job["timezone"],
                              "source_page": "Hoist Crons (a scheduled job)"})()
        body = await card_engine._ask_orchestrator(card, token, organization_id=organization_id, http=http)  # noqa: SLF001 - the card engine's own call
        if body.get("success") is False:
            raise RuntimeError(str(body.get("error") or "the orchestrator returned no answer"))
        answer = str(body.get("answer") or "")
        if not answer:
            raise RuntimeError("the orchestrator returned an empty answer")
        # The dashboard the chat renders for this answer (KPIs, groups, actions, pipeline) rides
        # on the run, so the Hoist Crons page shows the same cards the chat does, not a wall
        # of text (asked for 4 Oct 2026). summaryLine ignores it: it is not a number.
        summary: dict[str, Any] = {"tools": len(body.get("tool_calls") or [])}
        rich = card_engine.rich_from_tool_calls(body.get("tool_calls") or [])
        # A scheduled question always comes back as a dashboard: the orchestrator's cards when
        # it built some, else cards read from the answer's own figures and tables.
        if not (rich and any(rich.get(k) for k in ("kpis", "groups", "actions"))):
            rich = card_engine.cards_from_answer(answer, prompt)
        if rich:
            summary["rich"] = rich
        return summary, answer[:6000]
    method, path, query, payload = spec["call"]
    # Name the job's company on every call. The token's own company is the default, but a job
    # a superadmin scheduled for another company must scan that company, not none.
    if organization_id is not None:
        query = {**(query or {}), "organization_id": str(organization_id)}
        if isinstance(payload, dict):
            payload = {**payload, "organization_id": str(organization_id)}
    # A building's job names its building the way its route takes one.
    if building and spec.get("building") == "query":
        query = {**(query or {}), "building_id": str(building)}
    elif building and spec.get("building") == "body":
        payload = {**(payload or {}), "building_id": str(building)}
    client = http or httpx.AsyncClient(base_url=_self_base(), timeout=timeout)
    try:
        resp = await client.request(method, path, params=query or None, json=payload, headers=headers, timeout=timeout)
    finally:
        if http is None:
            await client.aclose()
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise RuntimeError(f"{path} answered {resp.status_code}: {detail}")
    try:
        body = resp.json()
    except ValueError:
        return {}, None
    summary = summarise(body)
    # A route that writes its own report (the maintenance watch) has it kept on the run and
    # mailed under the figures.
    lines = body.get("report_lines") if isinstance(body, dict) else None
    if isinstance(lines, list) and lines:
        return summary, (result_text(summary) + "\n\nDetails:\n" + "\n".join(str(x) for x in lines[:60]))[:6000]
    return summary, None


def result_text(summary: dict[str, Any]) -> str:
    """An engine job's figures as lines a person reads: ``alerts_created: 25`` → ``Alerts created: 25``."""
    lines = [f"{str(k).replace('_', ' ').capitalize()}: {v}" for k, v in (summary or {}).items()
             if k not in ("ok", "tools") and not str(k).startswith("email")]
    return "\n".join(lines) or "The job ran and reported no figures."


def report_url(job_id: Any) -> str:
    """Where the job's own page is: Administration › Hoist Crons › the job, after sign-in."""
    base = (getattr(settings, "public_app_url", None) or getattr(settings, "frontend_public_url", None) or "").rstrip("/")
    return f"{base}/?cron={job_id}" if base else ""


async def _week(session: AsyncSession | None, job_id: Any) -> dict[str, int] | None:
    """The job's last 7 days before this run: runs and failures."""
    if session is None:
        return None
    try:
        r = (await session.execute(text("""
            SELECT count(*) AS runs, count(*) FILTER (WHERE NOT ok) AS failed FROM plenum_cafm.hoist_cron_runs
             WHERE job_id = :j AND finished_at >= now() - interval '7 days'"""), {"j": UUID(str(job_id))})).mappings().first()
        return {"runs": int(r["runs"] or 0), "failed": int(r["failed"] or 0)} if r else None
    except Exception:  # noqa: BLE001 - the email goes without the line
        return None


def _esc(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


_SEV_COLOUR = {"critical": "#c0392b", "warning": "#b7791f", "info": "#2b6cb0", "ok": "#1f7a4d"}


def cards_html(rich: dict[str, Any]) -> tuple[str, str]:
    """(html, text) for a run's dashboard: KPI tiles, priority actions, owner groups - the same
    cards the chat and the Hoist Crons page show, so the email carries the details, not only
    the prose (asked for 4 Oct 2026)."""
    kpis = [k for k in (rich.get("kpis") or []) if isinstance(k, dict)]
    actions = [a for a in (rich.get("actions") or []) if isinstance(a, dict)]
    groups = [g for g in (rich.get("groups") or []) if isinstance(g, dict)]
    html, text = [], []

    def colour(sev: Any, default: str = "#8a8a84") -> str:
        return _SEV_COLOUR.get(str(sev or ""), default)

    if kpis:
        tiles = "".join(
            f"<td style='padding:0 8px 8px 0;vertical-align:top'><div style='border:1px solid #e6e4df;border-left:3px solid "
            f"{colour(k.get('severity'))};border-radius:8px;padding:10px 12px;min-width:110px'>"
            f"<div style='font-size:22px;font-weight:600;color:{colour(k.get('severity'), '#1a1a18')}'>{_esc(k.get('count'))}</div>"
            f"<div style='font-size:12px'>{_esc(k.get('label'))}</div>"
            + (f"<div style='font-size:11px;color:#8a8a84'>{_esc(k.get('sublabel'))}</div>" if k.get("sublabel") else "")
            + "</div></td>"
            for k in kpis[:8])
        html.append(f"<table style='border-collapse:collapse'><tr>{tiles}</tr></table>")
        text.append("Key figures: " + " · ".join(f"{k.get('count')} {k.get('label')}" for k in kpis[:8]))
    if actions:
        html.append("<h3 style='font-size:13px;margin:18px 0 8px;color:#6b6b66;text-transform:uppercase;letter-spacing:.08em'>"
                    "Priority actions</h3><ol style='margin:0;padding-left:18px;font-size:13px;line-height:1.6'>"
                    + "".join(f"<li><span style='color:{colour(a.get('severity'), '#1a1a18')};font-weight:600'>"
                              f"{_esc(str(a.get('severity') or '').upper())}</span> {_esc(a.get('title'))}</li>" for a in actions[:6])
                    + "</ol>")
        text.append("Priority actions:\n" + "\n".join(f"  {i + 1}. {a.get('title')}" for i, a in enumerate(actions[:6])))
    for g in groups[:6]:
        pts = [p for p in (g.get("points") or []) if p]
        html.append(f"<div style='border:1px solid #e6e4df;border-left:3px solid {colour(g.get('severity'))};"
                    f"border-radius:8px;padding:10px 12px;margin-top:10px'><div style='font-size:14px;font-weight:600'>{_esc(g.get('owner'))}</div>"
                    + (f"<div style='font-size:12px;color:{colour(g.get('severity'), '#6b6b66')}'>{_esc(g.get('headline'))}</div>" if g.get("headline") else "")
                    + ("<ul style='margin:6px 0 0;padding-left:18px;font-size:12.5px;line-height:1.55'>"
                       + "".join(f"<li>{_esc(p)}</li>" for p in pts[:8]) + "</ul>" if pts else "")
                    + "</div>")
        text.append(str(g.get("owner") or "") + (f" — {g.get('headline')}" if g.get("headline") else "")
                    + "\n" + "\n".join(f"  - {p}" for p in pts[:8]))
    return "".join(html), "\n\n".join(text)


def report_email(job: dict[str, Any], result: str, *, failed: bool, when: datetime | None = None,
                 took_ms: int | None = None, week: dict[str, int] | None = None,
                 url: str = "", rich: dict[str, Any] | None = None) -> tuple[str, str, str]:
    """Subject, plain text and HTML for one run's report: what ran, what it found, how the
    last week went, and the link to the job's page with every run."""
    when = when or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = when.astimezone(ZoneInfo(job.get("timezone") or "UTC"))
    except Exception:  # noqa: BLE001
        local = when
    stamp = local.strftime("%d %b %Y %H:%M")
    subject = f"Hoistra · {job['name']}{' failed' if failed else ''} · {local.strftime('%d %b %Y')}"
    took = f", took {took_ms / 1000:.1f} s" if took_ms is not None else ""
    head = f"{job['name']} — {'FAILED' if failed else 'ran'} {stamp} ({job.get('timezone') or 'UTC'}){took}"
    if week is not None:
        n, f = week["runs"] + 1, week["failed"] + (1 if failed else 0)
        week_line = f"Last 7 days: {n} run{'s' if n != 1 else ''}, {f} failed"
    else:
        week_line = ""
    sched = f"Scheduled on Hoist Crons: {job.get('refresh_label') or ''} ({job.get('timezone') or 'UTC'})."
    cards_h, cards_t = cards_html(rich) if (rich and not failed) else ("", "")
    text_body = "\n".join(x for x in [
        head, "", "What it found:" if not failed else "What went wrong:", (cards_t + "\n\n" if cards_t else "") + result.strip(), "",
        week_line, "", (f"See the full report and every run: {url}" if url else ""), "—", sched,
        "Pause, change or remove it under Administration › Hoist Crons.",
    ] if x is not None).replace("\n\n\n", "\n\n")
    tone = "#c0392b" if failed else "#1f7a4d"
    # The figures ("Name: value") as a table; a report's detail lines, under "Details:", as a list.
    figures, _, details = result.strip().partition("\n\nDetails:\n")
    rows = "".join(
        f"<tr><td style='padding:6px 12px 6px 0;color:#6b6b66'>{_esc(k)}</td>"
        f"<td style='padding:6px 0;font-weight:600'>{_esc(v)}</td></tr>"
        for k, v in (line.split(": ", 1) for line in figures.splitlines() if ": " in line))
    found = (f"<table style='border-collapse:collapse;font-size:14px'>{rows}</table>" if rows and not failed
             else f"<div style='white-space:pre-wrap;font-size:14px;line-height:1.55'>{_esc(figures)}</div>")
    if details.strip():
        found += ("<h3 style='font-size:13px;margin:18px 0 8px;color:#6b6b66;text-transform:uppercase;letter-spacing:.08em'>"
                  "Details</h3><ul style='margin:0;padding-left:18px;font-size:13px;line-height:1.6'>"
                  + "".join(f"<li>{_esc(x.strip())}</li>" for x in details.splitlines() if x.strip()) + "</ul>")
    button = (f"<p style='margin:22px 0'><a href='{_esc(url)}' style='background:#f05a28;color:#fff;padding:10px 18px;"
              f"border-radius:7px;text-decoration:none;font-weight:600'>See the full report</a></p>" if url else "")
    html = (
        "<div style='font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1a1a18;max-width:620px'>"
        f"<div style='font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#f05a28'>Hoist Crons · run report</div>"
        f"<h2 style='margin:6px 0 4px;font-size:20px'>{_esc(job['name'])}</h2>"
        f"<div style='font-size:13px;color:#6b6b66'><span style='color:{tone};font-weight:600'>"
        f"{'Failed' if failed else 'Ran'}</span> {_esc(stamp)} ({_esc(job.get('timezone') or 'UTC')}){_esc(took)}</div>"
        f"<h3 style='font-size:13px;margin:18px 0 8px;color:#6b6b66;text-transform:uppercase;letter-spacing:.08em'>"
        f"{'What went wrong' if failed else 'What it found'}</h3>{cards_h}"
        + ("<h3 style='font-size:13px;margin:18px 0 8px;color:#6b6b66;text-transform:uppercase;letter-spacing:.08em'>"
           "The answer</h3>" if cards_h else "")
        + found
        + (f"<p style='font-size:13px;color:#6b6b66;margin:16px 0 0'>{_esc(week_line)}</p>" if week_line else "")
        + button
        + f"<hr style='border:none;border-top:1px solid #e6e4df;margin:20px 0 10px'>"
        f"<div style='font-size:12px;color:#8a8a84'>{_esc(sched)} Pause, change or remove it under "
        "Administration › Hoist Crons. Sent from admin@hoistra.ai.</div></div>")
    return subject, text_body, html


async def _email_answer(session: AsyncSession, job: dict[str, Any], organization_id: UUID, owner_id: UUID,
                        answer: str, *, to_owner: bool = True, failed: bool = False,
                        took_ms: int | None = None, rich: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mail a run's report - a question's answer, an engine job's figures, or why it failed,
    with a link to the job's page - to the creator (when they asked) and to every one of the
    job's recipients. Never fails the run: a send that does not go is written on the run's
    summary, and the result is on the panel either way."""
    from ...shared.approvals import send_platform_email

    to: list[str] = []
    if to_owner:
        own = await _email(session, owner_id)
        if own:
            to.append(own.lower())
    for a in (job.get("params") or {}).get("recipients") or []:
        if a not in to:
            to.append(a)
    if not to:
        return {"email_status": "no_address"}
    subject, body, html = report_email(job, answer, failed=failed, took_ms=took_ms,
                                       week=await _week(session, job["id"]), url=report_url(job["id"]), rich=rich)
    sent, bad, errors = [], [], []
    for addr in to:
        try:
            res = await send_platform_email(session, to_address=addr, subject=subject[:200], body=body,
                                            html_body=html, organization_id=organization_id, commit=True)
            st = str(res.get("status") or ("sent" if res.get("ok") else "failed"))
            (bad if st == "failed" else sent).append(addr)
        except Exception as exc:  # noqa: BLE001
            log.warning("hoist_cron.email_failed", job_id=job["id"], to=addr, error=str(exc)[:200])
            bad.append(addr)
            errors.append(str(exc)[:200])
    out: dict[str, Any] = {"emailed_to": ", ".join(to),
                           "email_status": "sent" if not bad else ("failed" if not sent else "partial")}
    if bad:
        out["email_failed"] = ", ".join(bad)
    if errors:
        out["email_error"] = errors[0]
    return out


async def run_job(session: AsyncSession, job_id: UUID, *, trigger: str = "schedule", claimed: bool = False,
                  organization_id: UUID | None = None, http: httpx.AsyncClient | None = None,
                  requested_by: UUID | None = None) -> dict[str, Any] | None:
    """One run of one job, recorded whatever happens."""
    where = "id = :i AND removed_at IS NULL" + (" AND organization_id = :o" if organization_id else "")
    params = {"i": job_id, **({"o": organization_id} if organization_id else {})}
    r = (await session.execute(text(f"SELECT * FROM plenum_cafm.hoist_cron_jobs WHERE {where}"), params)).mappings().first()
    if r is None:
        return None
    job = _row(dict(r))
    org = r["organization_id"]
    if not claimed:
        got = (await session.execute(text("""
            UPDATE plenum_cafm.hoist_cron_jobs SET status = 'running', last_tried_at = now(), updated_at = now()
             WHERE id = :i AND status <> 'running' RETURNING id"""), {"i": job_id})).scalar_one_or_none()
        await session.commit()
        if got is None:
            return {"ok": False, "error": "This job is already running."}
    requested_email = await _email(session, requested_by) if requested_by else None
    if requested_by is not None:
        await record_event(session, job_id=job_id, organization_id=org, action="run_requested",
                           user_id=requested_by, email=requested_email)
        await session.commit()
    t0 = _now()
    summary, answer, error = {}, None, None
    try:
        token = await card_engine._owner_token(session, r["owner_user_id"])  # noqa: SLF001
        # Nothing is written until the run is over, so no transaction is held across the call
        # (the report cards' lesson: an idle transaction here once queued a whole service).
        await session.commit()
        summary, answer = await _call(job, token, org, http=http)
    except card_engine.OwnerUnavailable as exc:
        error = f"The job's creator can no longer be acted for ({exc}); the job is paused."
    except Exception as exc:  # noqa: BLE001 - every failure is a run row, never a lost run
        error = str(exc)[:1000]
    to_owner = bool(job["params"].get("email"))
    if to_owner or job["params"].get("recipients"):
        took = int((_now() - t0).total_seconds() * 1000)
        if error is not None:
            mail = await _email_answer(session, job, org, r["owner_user_id"], error,
                                       to_owner=to_owner, failed=True, took_ms=took)
        else:
            mail = await _email_answer(session, job, org, r["owner_user_id"], answer or result_text(summary),
                                       to_owner=to_owner, took_ms=took,
                                       rich=summary.get("rich") if isinstance(summary, dict) else None)
        summary = {**summary, **mail}
    finished = _now()
    run_id = uuid4()
    enabled = bool(r["enabled"]) and "paused" not in (error or "")
    status = "ready" if error is None else ("paused" if "paused" in error else "error")
    ref = job["refresh"] or {"every_minutes": 60}
    await session.execute(text("""
        INSERT INTO plenum_cafm.hoist_cron_runs (id, job_id, organization_id, trigger, started_at, finished_at,
                                                 duration_ms, ok, summary, answer, error,
                                                 run_as_user_id, requested_by_user_id, requested_by_email)
        VALUES (:id, :j, :o, :t, :s, :f, :d, :ok, CAST(:sum AS jsonb), :a, :e, :ru, :rb, :rbe)"""),
        {"id": run_id, "j": job_id, "o": org, "t": trigger, "s": t0, "f": finished,
         "d": int((finished - t0).total_seconds() * 1000), "ok": error is None, "sum": json.dumps(summary, default=str),
         "a": answer, "e": error, "ru": r["owner_user_id"], "rb": requested_by, "rbe": requested_email})
    await session.execute(text("""
        UPDATE plenum_cafm.hoist_cron_jobs
           SET status = :st, enabled = :on, last_tried_at = :f, last_error = :e,
               last_run_at = CASE WHEN :ok THEN :f ELSE last_run_at END,
               last_summary = CASE WHEN :ok THEN CAST(:sum AS jsonb) ELSE last_summary END,
               next_run_at = :next, updated_at = :f
         WHERE id = :j"""),
        {"st": status, "on": enabled, "f": finished, "e": error, "ok": error is None,
         "sum": json.dumps(summary, default=str), "j": job_id,
         "next": card_engine.next_run_at(ref, job["timezone"] or "UTC", finished) if enabled else None})
    await session.execute(text("""
        DELETE FROM plenum_cafm.hoist_cron_runs WHERE job_id = :j AND (
               finished_at < now() - make_interval(days => :days)
            OR id NOT IN (SELECT id FROM plenum_cafm.hoist_cron_runs WHERE job_id = :j
                           ORDER BY finished_at DESC LIMIT :n))"""),
        {"j": job_id, "n": MAX_RUNS_KEPT, "days": RUN_HISTORY_DAYS})
    await session.commit()
    log.info("hoist_cron.run", job_id=str(job_id), job_key=job["job_key"], ok=error is None, trigger=trigger,
             error=(error or "")[:160])
    return {"ok": error is None, "id": str(run_id), "summary": summary, "answer": answer, "error": error,
            "finished_at": _iso(finished)}


CLAIM_SQL = """
    UPDATE plenum_cafm.hoist_cron_jobs
       SET status = 'running', last_tried_at = now(), updated_at = now()
     WHERE id = (
            SELECT id FROM plenum_cafm.hoist_cron_jobs
             WHERE enabled AND removed_at IS NULL AND status <> 'running'
               AND next_run_at IS NOT NULL AND next_run_at <= now()
             ORDER BY next_run_at
             LIMIT 1
             FOR UPDATE SKIP LOCKED)
 RETURNING id
"""

RECOVER_SQL = """
    UPDATE plenum_cafm.hoist_cron_jobs
       SET status = 'pending', updated_at = now(),
           last_error = 'The previous run did not finish; it will run again.'
     WHERE status = 'running' AND removed_at IS NULL
       AND last_tried_at < now() - make_interval(mins => :stale)
 RETURNING id
"""


async def claim_due(session: AsyncSession) -> UUID | None:
    row = (await session.execute(text(CLAIM_SQL))).scalar_one_or_none()
    await session.commit()
    return UUID(str(row)) if row else None


async def recover_stale(session: AsyncSession) -> int:
    rows = (await session.execute(text(RECOVER_SQL), {"stale": STALE_RUN_MINUTES})).scalars().all()
    await session.commit()
    return len(rows)
