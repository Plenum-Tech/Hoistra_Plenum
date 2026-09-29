"""A finished migration's workbook extras are read by the platform, not by whoever is watching.

The contract terms, invoices and plant telemetry a single end-to-end workbook carries are read
after the write (workbook_extras). That used to happen only when the Migration page or the chat
saw the run finish - and a run of a large workbook takes longer than the chat waits, so a
company's contracts were never read and the Vendors page stayed empty although every table
was written. Now the service sweeps for finished runs itself, and the page and the chat ask
the same runner, which reads each run once:

  process(...)   claim the run (plenum_cafm.workbook_extras_runs), fetch the stored upload
                 from the schema mapper, run workbook_extras, record the outcome
  status(...)    what happened to one run, for the Migration page's chip
  latest(...)    a company's latest run and how many contracts still wait for confirmation,
                 for the Vendors page's highlight
  run_forever    the sweep: finished runs with no row yet, and failed ones (three tries)
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from . import workbook_extras

log = get_logger(__name__)

#: A claim older than this is a run that died with its process; it may be taken again.
STALE_MINUTES = 30
MAX_ATTEMPTS = 3


def summarise(res: dict[str, Any]) -> dict[str, Any]:
    """The counts a status line needs - not the per-invoice lists."""
    inv = res.get("invoices") or []
    return {
        "found": bool(res.get("found")),
        "reason": res.get("reason"),
        "contracts": len(res.get("contracts") or []),
        "invoices": len(inv),
        "lines_held": sum(int(x.get("held") or 0) for x in inv),
        "lines_matched": sum(int(x.get("matched") or 0) for x in inv),
        "skipped": len(res.get("skipped") or []),
        "skip_reasons": sorted({str(x.get("reason")) for x in (res.get("skipped") or []) if x.get("reason")}),
        "telemetry": {k: v for k, v in (res.get("telemetry") or {}).items() if k != "skipped"},
    }


def _row(r: Any) -> dict[str, Any] | None:
    if not r:
        return None
    d = dict(r._mapping)
    for k in ("started_at", "finished_at", "completed_at"):
        if d.get(k) is not None and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


async def status(session: AsyncSession, migration_id: str) -> dict[str, Any] | None:
    r = (await session.execute(text(
        """SELECT migration_id, organization_id, status, trigger, attempts, summary, error, started_at, finished_at
             FROM plenum_cafm.workbook_extras_runs WHERE migration_id = :m"""), {"m": str(migration_id)})).first()
    return _row(r)


async def _claim(session: AsyncSession, migration_id: str, org: str | None, trigger: str, force: bool) -> bool:
    # Taken when there is no row, when the last try failed (the sweep stops after three), when a
    # claim went stale, or - asked for explicitly - when it finished and a person wants it read
    # again. Never while another reader holds it.
    got = (await session.execute(text(
        f"""INSERT INTO plenum_cafm.workbook_extras_runs (migration_id, organization_id, status, trigger)
            VALUES (:m, :o, 'running', :t)
            ON CONFLICT (migration_id) DO UPDATE
               SET status = 'running', trigger = EXCLUDED.trigger, started_at = now(), finished_at = NULL,
                   error = NULL, attempts = plenum_cafm.workbook_extras_runs.attempts + 1,
                   organization_id = coalesce(EXCLUDED.organization_id, plenum_cafm.workbook_extras_runs.organization_id)
             WHERE (plenum_cafm.workbook_extras_runs.status = 'failed'
                    AND (:force OR plenum_cafm.workbook_extras_runs.attempts < {MAX_ATTEMPTS}))
                OR (plenum_cafm.workbook_extras_runs.status = 'running'
                    AND plenum_cafm.workbook_extras_runs.started_at < now() - interval '{STALE_MINUTES} minutes')
                OR (:force AND plenum_cafm.workbook_extras_runs.status <> 'running')
            RETURNING migration_id"""),
        {"m": str(migration_id), "o": org, "t": trigger, "force": bool(force)})).first()
    await session.commit()
    return got is not None


async def _finish(session: AsyncSession, migration_id: str, st: str, summary: dict | None, error: str | None) -> None:
    import json

    await session.execute(text(
        """UPDATE plenum_cafm.workbook_extras_runs
              SET status = :s, summary = CAST(:j AS jsonb), error = :e, finished_at = now()
            WHERE migration_id = :m"""),
        {"m": str(migration_id), "s": st, "j": json.dumps(summary) if summary is not None else None,
         "e": (error or None) and error[:500]})
    await session.commit()


async def _source(migration_id: str) -> bytes | None:
    import httpx

    url = f"{settings.schema_mapper_base_url.rstrip('/')}/api/migration/{migration_id}/source"
    async with httpx.AsyncClient(timeout=180) as client:
        res = await client.get(url)
    if res.status_code == 404:
        return None
    res.raise_for_status()
    return res.content


async def process(session: AsyncSession, *, migration_id: str, organization_id: UUID | str | None,
                  trigger: str, force: bool = False) -> dict[str, Any]:
    """Read one finished run's workbook extras, once. Answers the run's status row - with the
    full engine result under "result" when this call did the reading."""
    org = str(organization_id) if organization_id else None
    if not await _claim(session, migration_id, org, trigger, force):
        return {"ok": True, "claimed": False, **(await status(session, migration_id) or {})}
    log.info("workbook_extras_run.start", migration_id=str(migration_id), trigger=trigger)
    try:
        content = await _source(str(migration_id))
        if content is None:
            res: dict[str, Any] = {"ok": True, "found": False, "reason": "the migration's source file was not stored"}
        else:
            try:
                res = await workbook_extras.run(session, content=content, organization_id=UUID(org) if org else None)
            except Exception as exc:  # noqa: BLE001 - a CSV, or a workbook openpyxl cannot read
                if "zip" not in str(exc).lower():
                    raise
                res = {"ok": True, "found": False, "reason": "the source is not a workbook"}
        summary = summarise(res)
        await _finish(session, migration_id, "done" if summary["found"] else "none", summary, None)
        log.info("workbook_extras_run.done", migration_id=str(migration_id), **{k: v for k, v in summary.items()
                                                                                if k in ("contracts", "invoices", "lines_held")})
        return {"ok": True, "claimed": True, "result": res, **(await status(session, migration_id) or {})}
    except Exception as exc:  # noqa: BLE001 - recorded, so the page shows it and the sweep retries
        await session.rollback()
        await _finish(session, migration_id, "failed", None, f"{type(exc).__name__}: {exc}")
        log.warning("workbook_extras_run.failed", migration_id=str(migration_id), error=str(exc)[:300])
        return {"ok": False, "claimed": True, **(await status(session, migration_id) or {})}


async def latest(session: AsyncSession, organization_id: UUID | str) -> dict[str, Any]:
    """A company's latest read, and how many of its contract term sets still wait for a named
    person to confirm them - what the Vendors page highlights."""
    r = (await session.execute(text(
        """SELECT r.migration_id, r.status, r.trigger, r.attempts, r.summary, r.error, r.started_at, r.finished_at,
                  m.source_filename, m.completed_at
             FROM plenum_cafm.workbook_extras_runs r
             LEFT JOIN plenum_cafm.migration_jobs m ON m.id::text = r.migration_id
            WHERE r.organization_id = :o AND r.status <> 'predates'
            ORDER BY r.started_at DESC LIMIT 1"""), {"o": str(organization_id)})).first()
    drafts = (await session.execute(text(
        """SELECT count(*) FILTER (WHERE status = 'draft'), count(*) FILTER (WHERE status = 'confirmed')
             FROM plenum_cafm.contract_sla_parameters WHERE organization_id::text = :o"""),
        {"o": str(organization_id)})).first()
    out = _row(r) or {}
    if out.get("source_filename"):
        # The stored name carries an upload id in front of the file's own name.
        name = str(out["source_filename"])
        out["source_filename"] = name.split("_", 1)[1] if len(name) > 37 and name[36] == "_" else name
    return {"ok": True, "run": out or None, "drafts_awaiting": int(drafts[0] or 0) if drafts else 0,
            "confirmed": int(drafts[1] or 0) if drafts else 0}


async def sweep_once() -> int:
    """Finished runs nobody has read yet, and failed reads still under three tries. Returns
    how many it read."""
    from ...db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(
            f"""SELECT m.id::text, m.organization_id::text
                  FROM plenum_cafm.migration_jobs m
                  LEFT JOIN plenum_cafm.workbook_extras_runs r ON r.migration_id = m.id::text
                 WHERE m.status = 'complete'
                   AND (r.migration_id IS NULL
                        OR (r.status = 'failed' AND r.attempts < {MAX_ATTEMPTS}
                            AND r.finished_at < now() - interval '5 minutes')
                        OR (r.status = 'running' AND r.started_at < now() - interval '{STALE_MINUTES} minutes'))
                 ORDER BY m.completed_at NULLS LAST
                 LIMIT 5"""))).all()
    n = 0
    for mid, org in rows:
        async with AsyncSessionLocal() as session:
            out = await process(session, migration_id=mid, organization_id=org, trigger="sweep")
            n += 1 if out.get("claimed") else 0
    return n


async def run_forever(stop: asyncio.Event) -> None:
    every = max(15, int(settings.workbook_extras_sweep_seconds))
    log.info("workbook_extras_sweep.start", every_seconds=every)
    while not stop.is_set():
        try:
            await sweep_once()
        except Exception as exc:  # noqa: BLE001 - a bad tick is not a dead sweep
            log.warning("workbook_extras_sweep.tick_failed", error=str(exc)[:300])
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            pass
    log.info("workbook_extras_sweep.stop")
