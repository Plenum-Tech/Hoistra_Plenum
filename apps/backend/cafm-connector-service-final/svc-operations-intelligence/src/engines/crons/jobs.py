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
    },
    "energy_meter_gaps": {
        "label": "Meter gap check", "module": "Energy",
        "description": "Finds meters whose readings have stopped or have holes in the series.",
        "call": ("POST", "/api/energy/gaps/detect", {}, None), "suggest": {"every_minutes": 360},
    },
    "energy_benchmarks": {
        "label": "Benchmark validation", "module": "Energy",
        "description": "Re-derives each building's EUI from its readings and positions it against its benchmark.",
        "call": ("POST", "/api/energy/benchmarks/validate", {}, None), "suggest": {"daily_at": "02:00"},
    },
    "compliance_expiry_scan": {
        "label": "Compliance expiry scan", "module": "Compliance",
        "description": "Re-reads every certificate's status against today - lapsed, expiring in 30/60/90 days, blocked vendors.",
        "call": ("POST", "/api/compliance/scan", None, {"scope": "all"}), "suggest": {"daily_at": "07:00"},
    },
    "compliance_reverify": {
        "label": "Certificate re-verification", "module": "Compliance",
        "description": "Checks certificates that are due a check against their public registers again.",
        "call": ("POST", "/api/compliance/reverify", None, {"limit": 50}), "suggest": {"days": [1], "time": "07:00"},
    },
    "question": {
        "label": "Ask a question", "module": "Orchestrator",
        "description": "Asks the orchestrator your question on the schedule and keeps each answer.",
        "call": "question", "suggest": {"daily_at": "08:00"}, "needs_prompt": True,
    },
}


class CronError(ValueError):
    """A job the service will not schedule. ``reason`` is stable for clients."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(v) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v else None)


def catalogue() -> list[dict[str, Any]]:
    """What a client may offer: the jobs, and the cadences (the report cards' presets)."""
    return [{"key": k, "label": v["label"], "module": v["module"], "description": v["description"],
             "suggested_refresh": v["suggest"], "suggested_label": card_engine.refresh_label(v["suggest"]),
             "needs_prompt": bool(v.get("needs_prompt"))} for k, v in CATALOGUE.items()]


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


async def daily_status(session: AsyncSession, organization_id: UUID, *, days: int = 14, tz: str = "UTC") -> dict:
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
    if spec.get("needs_prompt"):
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise CronError("no_prompt", "A question job needs the question to ask.")
        # email: send each answer to the job's creator ("send me a compliance summary every
        # Monday") over the platform's own mail transport, from its own sender address.
        params = {"prompt": prompt[:4000], "email": bool(params.get("email"))}
    else:
        params = {}
    n = (await session.execute(text("""SELECT count(*) FROM plenum_cafm.hoist_cron_jobs
                                         WHERE organization_id = :o AND removed_at IS NULL"""),
                               {"o": organization_id})).scalar_one()
    if n >= MAX_JOBS_PER_ORG:
        raise CronError("too_many_jobs", f"A company can have at most {MAX_JOBS_PER_ORG} scheduled jobs.")
    jid, now = uuid4(), _now()
    creator_email = await _email(session, owner_user_id)
    label =(name or "").strip() or (spec["label"] if not spec.get("needs_prompt")
                                     else "Ask: " + params["prompt"][:80])
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
                     user_id: UUID | None = None) -> dict[str, Any] | None:
    job = await get_job(session, organization_id, job_id)
    if job is None:
        return None
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
                                                  "enabled": enabled, "name": name}.items() if v is not None})
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
            if len(out) >= 8 or k in out:
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
    if spec["call"] == "question":
        card = type("C", (), {"prompt": job["params"].get("prompt") or "", "id": UUID(job["id"]), "name": job["name"],
                              "refresh": job["refresh"], "timezone": job["timezone"],
                              "source_page": "Hoist Crons (a scheduled job)"})()
        body = await card_engine._ask_orchestrator(card, token, http=http)  # noqa: SLF001 - the card engine's own call
        if body.get("success") is False:
            raise RuntimeError(str(body.get("error") or "the orchestrator returned no answer"))
        answer = str(body.get("answer") or "")
        if not answer:
            raise RuntimeError("the orchestrator returned an empty answer")
        return {"tools": len(body.get("tool_calls") or [])}, answer[:6000]
    method, path, query, payload = spec["call"]
    # Name the job's company on every call. The token's own company is the default, but a job
    # a superadmin scheduled for another company must scan that company, not none.
    if organization_id is not None:
        query = {**(query or {}), "organization_id": str(organization_id)}
        if isinstance(payload, dict):
            payload = {**payload, "organization_id": str(organization_id)}
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
        return summarise(resp.json()), None
    except ValueError:
        return {}, None


async def _email_answer(session: AsyncSession, job: dict[str, Any], organization_id: UUID, owner_id: UUID,
                        answer: str) -> dict[str, Any]:
    """Mail a question job's answer to its creator. Never fails the run: a send that does not
    go is written on the run's summary, and the answer is on the panel either way."""
    from ...shared.approvals import send_platform_email

    to = await _email(session, owner_id)
    if not to:
        return {"email_status": "no_address"}
    day = datetime.now(timezone.utc).strftime("%d %b %Y")
    subject = f"Hoistra · {job['name']} · {day}"
    body = (answer.strip() + "\n\n—\n"
            f"Scheduled on Hoist Crons: {job['name']}, {job['refresh_label']} ({job['timezone']}).\n"
            "Pause or remove it from the Hoist Crons panel on Home.")
    try:
        res = await send_platform_email(session, to_address=to, subject=subject[:200], body=body,
                                        organization_id=organization_id, commit=True)
        return {"emailed_to": to, "email_status": str(res.get("status") or ("sent" if res.get("ok") else "failed"))}
    except Exception as exc:  # noqa: BLE001
        log.warning("hoist_cron.email_failed", job_id=job["id"], error=str(exc)[:200])
        return {"emailed_to": to, "email_status": "failed", "email_error": str(exc)[:200]}


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
    if error is None and answer and job["params"].get("email"):
        summary = {**summary, **(await _email_answer(session, job, org, r["owner_user_id"], answer))}
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
