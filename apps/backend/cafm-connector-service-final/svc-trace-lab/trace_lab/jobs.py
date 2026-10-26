"""Long Trace Lab work as resumable rows in tracelab.jobs, on Trace Lab's own database.

A job is claimed atomically, heartbeats while it runs, reserves model spend before each call and
checkpoints after each item in the SAME transaction as the item's result row, so a restart loses
at most the item in flight. At boot (the lifespan, once the schema is ready) a running job whose
heartbeat is older than STALE_AFTER_S becomes `interrupted`; it keeps its scope until it is
resumed or discarded. Golden runs are stepped by the browser, one case per request, so params
never carry a credential (the database refuses one too: jobs_params_no_secrets).

Every query runs on db.lab_engine(); nothing here touches the application database.
"""
from __future__ import annotations

import json
import os
import socket
import uuid
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import db

log = structlog.get_logger(__name__)

STALE_AFTER_S = 120
KINDS = ("golden_run", "golden_grade", "judge_run", "proposal_grouping", "proposal_depth_scan",
         "proposal_cells", "monitor_batch", "company_delete")
STATUSES = ("queued", "running", "paused", "done", "failed", "capped", "interrupted", "cancelled")
OPEN_STATUSES = ("queued", "running", "paused", "interrupted")
FINISH_STATUSES = ("done", "failed", "capped", "paused", "cancelled")
UNCAPPED_KINDS = frozenset({"company_delete"})
SECRET_KEYS = frozenset({"authorization", "bearer", "token", "access_token", "refresh_token", "api_key", "password"})
INTERRUPTED_TEXT = "The service restarted while this was running."
_ZERO = Decimal("0")


class JobConflict(Exception):
    """Another open job (queued, running, paused or interrupted) holds this scope. Routes answer 409."""


class CapReached(Exception):
    """The next call would take spend past usd_cap; the job should finish as capped."""


class JobNotRunning(Exception):
    """The job is not running (stopped, finished or interrupted), so nothing may be reserved."""


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k in ("id", "organization_id", "created_by"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d


def _uuid(v: Any) -> str | None:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        return None


def _is_scope_conflict(exc: IntegrityError) -> bool:
    return "ux_jobs_one_open_per_scope" in str(getattr(exc, "orig", exc))


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def create(*, kind: str, scope_key: str, organization_id: str | None, params: dict[str, Any] | None,
                 usd_cap: Decimal | None, usd_estimate: Decimal | None = None, total: int | None = None,
                 actor_id: str | None, actor_email: str | None = None) -> dict[str, Any]:
    """A queued job. ValueError before any database work for a bad kind, a credential in params or
    a model-spending job with no cap. JobConflict when an open job already holds scope_key."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    p = dict(params or {})
    bad = sorted(k for k in p if str(k).lower() in SECRET_KEYS)
    if bad:
        raise ValueError(f"job params never carry credentials: {bad}")
    if kind not in UNCAPPED_KINDS and usd_cap is None:
        raise ValueError("a job that spends model money needs usd_cap")
    try:
        async with db.lab_engine().begin() as conn:
            row = (await conn.execute(text(
                "INSERT INTO tracelab.jobs (kind, scope_key, organization_id, params, usd_cap, usd_estimate,"
                " total, created_by, created_by_email)"
                " VALUES (:kind, :scope, CAST(:org AS uuid), CAST(:params AS jsonb), :cap, :est, :total,"
                " CAST(:by AS uuid), :email) RETURNING *"),
                {"kind": kind, "scope": scope_key, "org": organization_id, "params": json.dumps(p, default=str),
                 "cap": usd_cap, "est": usd_estimate, "total": total, "by": actor_id,
                 "email": actor_email})).mappings().one()
    except IntegrityError as exc:
        if _is_scope_conflict(exc):
            raise JobConflict(scope_key) from None
        raise
    return _row(row)


async def claim(job_id: str, *, worker: str | None = None) -> dict[str, Any] | None:
    """Atomically take a queued, paused or interrupted job. None: someone else has it, or it ended."""
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE tracelab.jobs SET status = 'running', worker = :w, heartbeat_at = now(),"
            " started_at = coalesce(started_at, now()), updated_at = now()"
            " WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'paused', 'interrupted') RETURNING *"),
            {"id": job_id, "w": worker or worker_id()})).mappings().first()
    return _row(row) if row else None


async def heartbeat(job_id: str, *, progress_patch: dict[str, Any] | None = None) -> bool:
    """Mark the job alive. True means stop: a stop was requested, or the job is no longer running."""
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE tracelab.jobs SET heartbeat_at = now(), updated_at = now(),"
            " progress = progress || CAST(:patch AS jsonb)"
            " WHERE id = CAST(:id AS uuid) AND status = 'running' RETURNING stop_requested"),
            {"id": job_id, "patch": json.dumps(progress_patch or {}, default=str)})).first()
    return True if row is None else bool(row[0])


async def reserve(job_id: str, usd: Decimal) -> None:
    """Reserve spend for a call about to be made. CapReached past usd_cap; JobNotRunning otherwise."""
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE tracelab.jobs SET usd_reserved = usd_reserved + :usd, updated_at = now()"
            " WHERE id = CAST(:id AS uuid) AND status = 'running'"
            " AND (usd_cap IS NULL OR usd_spent + usd_reserved + :usd <= usd_cap) RETURNING id"),
            {"id": job_id, "usd": usd})).first()
        if row is not None:
            return
        status = (await conn.execute(text("SELECT status FROM tracelab.jobs WHERE id = CAST(:id AS uuid)"),
                                     {"id": job_id})).scalar()
    if status == "running":
        raise CapReached(job_id)
    raise JobNotRunning(job_id)


async def release(job_id: str, usd: Decimal) -> None:
    """Give back a reservation whose call produced no result row (the call failed)."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(
            "UPDATE tracelab.jobs SET usd_reserved = greatest(usd_reserved - :usd, 0), updated_at = now()"
            " WHERE id = CAST(:id AS uuid)"), {"id": job_id, "usd": usd})


async def checkpoint(conn, job_id: str, *, usd: Decimal | None, released: Decimal, done_delta: int = 1,
                     progress_patch: dict[str, Any] | None = None) -> None:
    """In the SAME transaction (conn, on the lab engine) as the item's result row: done += done_delta,
    usd_spent += usd, usd_reserved -= released, progress ||= patch, heartbeat."""
    await conn.execute(text(
        "UPDATE tracelab.jobs SET done = done + :dd, usd_spent = usd_spent + :usd,"
        " usd_reserved = greatest(usd_reserved - :rel, 0), progress = progress || CAST(:patch AS jsonb),"
        " heartbeat_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": job_id, "dd": int(done_delta), "usd": usd if usd is not None else _ZERO, "rel": released,
         "patch": json.dumps(progress_patch or {}, default=str)})


async def add_spend(job_id: str, usd: Decimal) -> bool:
    """Spend outside an item transaction. False once usd_spent has reached usd_cap."""
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE tracelab.jobs SET usd_spent = usd_spent + :usd, heartbeat_at = now(), updated_at = now()"
            " WHERE id = CAST(:id AS uuid) RETURNING (usd_cap IS NULL OR usd_spent < usd_cap) AS under_cap"),
            {"id": job_id, "usd": usd})).first()
    return bool(row and row[0])


async def finish(job_id: str, status: str, error: str | None = None) -> dict[str, Any] | None:
    """End (or pause) an open job. paused keeps it resumable; the others free its scope."""
    if status not in FINISH_STATUSES:
        raise ValueError(f"a job cannot finish as {status!r}")
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE tracelab.jobs SET status = :st, error = :err, usd_reserved = 0, worker = NULL,"
            " finished_at = CASE WHEN :st = 'paused' THEN NULL ELSE now() END, updated_at = now()"
            " WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'running', 'paused', 'interrupted') RETURNING *"),
            {"id": job_id, "st": status, "err": (error or None) and str(error)[:2000]})).mappings().first()
    return _row(row) if row else None


async def request_stop(job_id: str) -> bool:
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE tracelab.jobs SET stop_requested = true, updated_at = now()"
            " WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'running', 'paused', 'interrupted') RETURNING id"),
            {"id": job_id})).first()
    return row is not None


async def resume(job_id: str, *, usd_cap: Decimal | None = None) -> dict[str, Any] | None:
    """Put a paused, interrupted or capped job back in the queue, optionally with a new cap.
    JobConflict when another job has opened in the same scope meanwhile."""
    try:
        async with db.lab_engine().begin() as conn:
            row = (await conn.execute(text(
                "UPDATE tracelab.jobs SET status = 'queued', stop_requested = false, error = NULL, worker = NULL,"
                " usd_cap = coalesce(:cap, usd_cap), finished_at = NULL, updated_at = now()"
                " WHERE id = CAST(:id AS uuid) AND status IN ('paused', 'interrupted', 'capped') RETURNING *"),
                {"id": job_id, "cap": usd_cap})).mappings().first()
    except IntegrityError as exc:
        if _is_scope_conflict(exc):
            raise JobConflict(job_id) from None
        raise
    return _row(row) if row else None


async def discard(job_id: str, *, error: str = "Discarded by a person after an interruption.") -> dict[str, Any] | None:
    """Give up on an open job that is not running: it becomes failed and frees its scope."""
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE tracelab.jobs SET status = 'failed', error = :err, usd_reserved = 0, finished_at = now(),"
            " updated_at = now() WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'paused', 'interrupted')"
            " RETURNING *"), {"id": job_id, "err": error[:2000]})).mappings().first()
    return _row(row) if row else None


async def mark_interrupted(stale_after_s: int = STALE_AFTER_S) -> list[str]:
    """The boot sweep: running jobs whose heartbeat is older than stale_after_s become interrupted.
    Called by the lifespan only when the schema is ready."""
    async with db.lab_engine().begin() as conn:
        rows = (await conn.execute(text(
            "UPDATE tracelab.jobs SET status = 'interrupted', updated_at = now(), error = coalesce(error, :msg)"
            " WHERE status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < now() - make_interval(secs => :stale))"
            " RETURNING id"), {"stale": float(stale_after_s), "msg": INTERRUPTED_TEXT})).all()
    ids = [str(r[0]) for r in rows]
    if ids:
        log.warning("trace_lab.jobs.interrupted", count=len(ids), job_ids=ids[:20])
    return ids


async def get(job_id: str, *, organization_id: str | None, platform: bool = False) -> dict[str, Any] | None:
    """One job. organization_id names its company; None means a platform-scope job. platform=True
    reads any job (the platform owner)."""
    jid = _uuid(job_id)
    if jid is None:
        return None
    where = "" if platform else " AND organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)"
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(f"SELECT * FROM tracelab.jobs WHERE id = CAST(:id AS uuid){where}"),
                                  {"id": jid, "org": organization_id})).mappings().first()
    return _row(row) if row else None


async def list_for(*, organization_id: str | None, kind: str | None = None, limit: int = 50,
                   platform: bool = False) -> list[dict[str, Any]]:
    """The newest jobs of one company (organization_id None: platform-scope jobs), or of every
    company when platform=True. limit is clamped to 1..500."""
    where = ["TRUE"] if platform else ["organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)"]
    if kind:
        where.append("kind = :kind")
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            f"SELECT * FROM tracelab.jobs WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT :lim"),
            {"org": organization_id, "kind": kind, "lim": max(1, min(int(limit), 500))})).mappings().all()
    return [_row(r) for r in rows]
