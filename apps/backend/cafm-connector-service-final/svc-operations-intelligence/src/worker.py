"""ARQ worker — compliance scan, vendor scorecards, energy gap retries + monthly reports."""
from __future__ import annotations

from datetime import date, timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import text

from .config import settings
from .core.logging import configure_logging, get_logger
from .db import AsyncSessionLocal, init_db

log = get_logger(__name__)


async def startup(ctx: dict) -> None:
    configure_logging()
    await init_db()
    log.info("worker.startup", service=settings.service_name)


async def shutdown(ctx: dict) -> None:
    log.info("worker.shutdown")


async def nightly_compliance_scan(ctx: dict) -> dict:
    from .engines.compliance.scan import run_compliance_scan

    async with AsyncSessionLocal() as session:
        result = await run_compliance_scan(session, scope="all")
        log.info(
            "worker.nightly_scan.done",
            **{
                k: result.get(k)
                for k in (
                    "scan_run_id",
                    "building_scanned",
                    "vendor_scanned",
                    "alerts_created",
                    "blocks_set",
                )
            },
        )
        return result


async def reverify_due_certificates(ctx: dict) -> dict:
    """CCC §8.6 — daily pass over certificates due for 90-day re-verification."""
    from .engines.compliance.reverify import reverify_due_certificates as _reverify

    async with AsyncSessionLocal() as session:
        result = await _reverify(session, limit=100)
        log.info(
            "worker.reverify.done",
            due_count=result.get("due_count"),
            processed=result.get("processed"),
        )
        return result


async def weekly_verification_dumps(ctx: dict) -> dict:
    """CCC §8.2 — ingest weekly register dumps when files are present."""
    from .engines.compliance.register_dump import weekly_dump_cron

    async with AsyncSessionLocal() as session:
        result = await weekly_dump_cron(session)
        log.info(
            "worker.verification_dumps.done",
            ingested=result.get("ingested"),
            files=len(result.get("files") or []),
        )
        return result


async def monthly_vendor_scorecards(ctx: dict) -> dict:
    from .engines.contract_performance.scoring import generate_monthly_scorecard

    today = date.today()
    first_this = date(today.year, today.month, 1)
    score_month = (first_this - timedelta(days=1)).replace(day=1)

    async with AsyncSessionLocal() as session:
        vendor_ids: list = []
        try:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT DISTINCT vendor_id
                        FROM plenum_cafm.vendor_wo_scores
                        WHERE score_month = :m AND vendor_id IS NOT NULL
                        """
                    ),
                    {"m": score_month},
                )
            ).fetchall()
            vendor_ids = [r[0] for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("worker.scorecards.vendor_query_failed", error=str(exc)[:200])

        generated = []
        for vid in vendor_ids:
            try:
                result = await generate_monthly_scorecard(
                    session, vendor_id=vid, score_month=score_month
                )
                generated.append({"vendor_id": str(vid), "ok": result.get("ok")})
            except Exception as exc:  # noqa: BLE001
                generated.append(
                    {"vendor_id": str(vid), "ok": False, "error": str(exc)[:200]}
                )

        log.info(
            "worker.monthly_scorecards.done",
            score_month=score_month.isoformat(),
            count=len(generated),
        )
        return {"score_month": score_month.isoformat(), "generated": generated}


async def energy_simulator_tick(ctx: dict) -> dict:
    """Every 30 minutes — append one half-hour to meters flagged for simulation.

    Only touches meters whose raw_metadata says {"simulate": true}, so a real DCC-fed
    meter can never be overwritten with demo data.
    """
    from .engines.energy.simulator import simulate_half_hour

    async with AsyncSessionLocal() as session:
        result = await simulate_half_hour(session)
        log.info("worker.energy_simulator.done", **result)
        return result


async def energy_gap_retries(ctx: dict) -> dict:
    """Every 10 minutes — retry missing HH readings up to 3× then escalate."""
    from .engines.energy.meters import process_gap_retries

    async with AsyncSessionLocal() as session:
        result = await process_gap_retries(session)
        log.info("worker.energy_gap_retries.done", **result)
        return result


async def energy_anomaly_scan(ctx: dict) -> dict:
    """Daily — weekend spike / baseline drift / asset spike detection."""
    from .engines.energy.anomalies import scan_all_active_meters

    async with AsyncSessionLocal() as session:
        result = await scan_all_active_meters(session)
        log.info(
            "worker.energy_anomaly_scan.done",
            meters_scanned=result.get("meters_scanned"),
        )
        return result


async def monthly_energy_reports(ctx: dict) -> dict:
    """1st of month — energy reports for preceding month per site with profile."""
    from .engines.energy.reports import generate_monthly_energy_report

    today = date.today()
    first_this = date(today.year, today.month, 1)
    report_month = (first_this - timedelta(days=1)).replace(day=1)

    async with AsyncSessionLocal() as session:
        site_ids: list = []
        try:
            rows = (
                await session.execute(
                    text("SELECT DISTINCT site_id FROM plenum_cafm.building_energy_profiles")
                )
            ).fetchall()
            site_ids = [r[0] for r in rows]
        except Exception as exc:  # noqa: BLE001
            log.warning("worker.energy_reports.site_query_failed", error=str(exc)[:200])

        generated = []
        for sid in site_ids:
            try:
                result = await generate_monthly_energy_report(
                    session, site_id=sid, report_month=report_month
                )
                generated.append({"site_id": str(sid), "ok": result.get("ok")})
            except Exception as exc:  # noqa: BLE001
                generated.append({"site_id": str(sid), "ok": False, "error": str(exc)[:200]})

        log.info(
            "worker.energy_reports.done",
            report_month=report_month.isoformat(),
            count=len(generated),
        )
        return {"report_month": report_month.isoformat(), "generated": generated}


class WorkerSettings:
    functions = [
        nightly_compliance_scan,
        monthly_vendor_scorecards,
        energy_gap_retries,
        energy_anomaly_scan,
        monthly_energy_reports,
        reverify_due_certificates,
        weekly_verification_dumps,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    cron_jobs = [
        cron(nightly_compliance_scan, hour=settings.compliance_scan_hour_local, minute=0),
        cron(monthly_vendor_scorecards, day=1, hour=6, minute=0),
        cron(energy_gap_retries, minute={0, 10, 20, 30, 40, 50}),
        # Half-hourly demo feed; a no-op unless meters are flagged simulate.
        cron(energy_simulator_tick, minute={0, 30}),
        cron(energy_anomaly_scan, hour=8, minute=0),
        cron(monthly_energy_reports, day=1, hour=7, minute=0),
        # CCC §8.6 — run after nightly scan
        cron(reverify_due_certificates, hour=3, minute=15),
        # CCC §8.2 — weekly dump ingest (Sunday 04:00)
        cron(weekly_verification_dumps, weekday=6, hour=4, minute=0),
    ]
