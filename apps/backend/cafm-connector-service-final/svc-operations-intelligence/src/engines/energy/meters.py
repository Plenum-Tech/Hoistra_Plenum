"""Feature C — smart meter ingest, half-hourly gaps, retry escalation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import EnergyMeter, MeterReading, MeterReadingGap
from ...shared.approvals import enqueue_approval, write_audit

log = get_logger(__name__)

HH_MINUTES = 30
GAP_FLAG_CONSECUTIVE = 2  # 2+ consecutive missing half-hours
MAX_RETRIES = 3
RETRY_INTERVAL_MINUTES = 10
# One INSERT … ON CONFLICT per batch instead of a SELECT+INSERT per CSV row.
_UPSERT_BATCH = 1000


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def coerce_reading_at(value: Any) -> datetime | None:
    """Normalise a CSV/API timestamp to a timezone-aware half-hour boundary."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return _aware(value).replace(second=0, microsecond=0)


def find_half_hour_gaps(
    reading_times: list[datetime],
    *,
    window_start: datetime,
    window_end: datetime,
    period_minutes: int = HH_MINUTES,
) -> list[tuple[datetime, datetime, int]]:
    """
    Return gaps of 2+ consecutive missing half-hour periods.
    Each tuple: (gap_start, gap_end, missing_periods).
    """
    step = timedelta(minutes=period_minutes)
    present = {_aware(t).replace(second=0, microsecond=0) for t in reading_times}
    # Align to half-hour
    cur = _aware(window_start).replace(second=0, microsecond=0)
    minute = (cur.minute // period_minutes) * period_minutes
    cur = cur.replace(minute=minute)
    end = _aware(window_end)

    gaps: list[tuple[datetime, datetime, int]] = []
    run_start: datetime | None = None
    run_count = 0

    while cur < end:
        if cur not in present:
            if run_start is None:
                run_start = cur
                run_count = 1
            else:
                run_count += 1
        else:
            if run_start is not None and run_count >= GAP_FLAG_CONSECUTIVE:
                gaps.append((run_start, cur, run_count))
            run_start = None
            run_count = 0
        cur += step

    if run_start is not None and run_count >= GAP_FLAG_CONSECUTIVE:
        gaps.append((run_start, cur, run_count))
    return gaps


def meter_to_dict(m: EnergyMeter) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "organization_id": str(m.organization_id) if m.organization_id else None,
        "site_id": str(m.site_id) if m.site_id else None,
        "asset_id": str(m.asset_id) if m.asset_id else None,
        "meter_type": m.meter_type,
        "mpan": m.mpan,
        "mprn": m.mprn,
        "dcc_device_id": m.dcc_device_id,
        "tariff_gbp_per_kwh": float(m.tariff_gbp_per_kwh),
        "carbon_kg_per_kwh": float(m.carbon_kg_per_kwh),
        "is_sub_meter": m.is_sub_meter,
        "asset_type_benchmark_kwh": float(m.asset_type_benchmark_kwh) if m.asset_type_benchmark_kwh is not None else None,
        "active": m.active,
    }


async def upsert_meter(
    session: AsyncSession,
    data: dict[str, Any],
) -> dict[str, Any]:
    meter_id = data.get("id")
    row: EnergyMeter | None = None
    if meter_id:
        row = await session.get(EnergyMeter, UUID(str(meter_id)))
    if row is None and data.get("mpan"):
        row = (
            await session.execute(select(EnergyMeter).where(EnergyMeter.mpan == data["mpan"]).limit(1))
        ).scalar_one_or_none()
    if row is None and data.get("mprn"):
        row = (
            await session.execute(select(EnergyMeter).where(EnergyMeter.mprn == data["mprn"]).limit(1))
        ).scalar_one_or_none()

    if row is None:
        row = EnergyMeter(id=uuid4())
        session.add(row)

    for field in (
        "organization_id",
        "site_id",
        "asset_id",
    ):
        if data.get(field):
            setattr(row, field, UUID(str(data[field])))
    row.meter_type = data.get("meter_type") or row.meter_type or "electricity"
    row.mpan = data.get("mpan", row.mpan)
    row.mprn = data.get("mprn", row.mprn)
    row.dcc_device_id = data.get("dcc_device_id", row.dcc_device_id)
    if data.get("tariff_gbp_per_kwh") is not None:
        row.tariff_gbp_per_kwh = Decimal(str(data["tariff_gbp_per_kwh"]))
    if data.get("carbon_kg_per_kwh") is not None:
        row.carbon_kg_per_kwh = Decimal(str(data["carbon_kg_per_kwh"]))
    if data.get("is_sub_meter") is not None:
        row.is_sub_meter = bool(data["is_sub_meter"])
    if data.get("asset_type_benchmark_kwh") is not None:
        row.asset_type_benchmark_kwh = Decimal(str(data["asset_type_benchmark_kwh"]))
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await session.commit()
    return {"ok": True, "meter": meter_to_dict(row)}


async def ingest_readings(
    session: AsyncSession,
    *,
    meter_id: UUID,
    readings: list[dict[str, Any]],
    organization_id: UUID | None = None,
    source: str = "dcc",
    detect_gaps: bool = True,
) -> dict[str, Any]:
    """
    Ingest half-hourly readings into MeterReading (UDR entity).
    Flags 2+ consecutive missing periods; schedules retry escalation path.

    Uses a single window lookup + batched INSERT … ON CONFLICT so a typical
    meter CSV (thousands of half-hours) is one round-trip per 1000 rows, not
    one SELECT per row against Azure Postgres.
    """
    t0 = monotonic()
    meter = await session.get(EnergyMeter, meter_id)
    if not meter:
        return {"ok": False, "error": "meter_not_found"}

    # Last write wins when the CSV repeats a timestamp.
    by_time: dict[datetime, dict[str, Any]] = {}
    for r in readings:
        at = coerce_reading_at(r.get("reading_at"))
        if at is None:
            continue
        by_time[at] = r
    if not by_time:
        return {"ok": True, "meter_id": str(meter_id), "inserted": 0, "gaps_flagged": []}

    times = list(by_time.keys())
    window_start = min(times)
    window_end = max(times)
    existing_times = {
        _aware(t).replace(second=0, microsecond=0)
        for t in (
            await session.execute(
                select(MeterReading.reading_at).where(
                    MeterReading.meter_id == meter_id,
                    MeterReading.reading_at >= window_start,
                    MeterReading.reading_at <= window_end,
                )
            )
        ).scalars().all()
        if t is not None
    }
    inserted = sum(1 for t in times if t not in existing_times)

    org_id = organization_id or meter.organization_id
    rows = [
        {
            "id": uuid4(),
            "organization_id": org_id,
            "meter_id": meter_id,
            "asset_id": meter.asset_id,
            "reading_at": at,
            "period_minutes": int(r.get("period_minutes") or HH_MINUTES),
            "consumption_kwh": Decimal(str(r.get("consumption_kwh") or 0)),
            "source": source,
            "quality_flag": r.get("quality_flag") or "ok",
        }
        for at, r in by_time.items()
    ]
    for i in range(0, len(rows), _UPSERT_BATCH):
        chunk = rows[i : i + _UPSERT_BATCH]
        stmt = pg_insert(MeterReading).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["meter_id", "reading_at"],
            set_={
                "consumption_kwh": stmt.excluded.consumption_kwh,
                "quality_flag": stmt.excluded.quality_flag,
                "source": stmt.excluded.source,
                "period_minutes": stmt.excluded.period_minutes,
            },
        )
        await session.execute(stmt)

    gap_rows: list[dict[str, Any]] = []
    if detect_gaps:
        gap_window_start = window_start - timedelta(hours=1)
        gap_window_end = window_end + timedelta(hours=1)
        existing_in_window = list(
            (
                await session.execute(
                    select(MeterReading.reading_at).where(
                        MeterReading.meter_id == meter_id,
                        MeterReading.reading_at >= gap_window_start,
                        MeterReading.reading_at <= gap_window_end,
                    )
                )
            ).scalars().all()
        )
        already_open = {
            (
                _aware(g.gap_start).replace(second=0, microsecond=0),
                _aware(g.gap_end).replace(second=0, microsecond=0),
            )
            for g in (
                await session.execute(
                    select(MeterReadingGap).where(
                        MeterReadingGap.meter_id == meter_id,
                        MeterReadingGap.status.in_(["open", "retrying"]),
                    )
                )
            ).scalars().all()
        }
        for g_start, g_end, count in find_half_hour_gaps(
            existing_in_window, window_start=gap_window_start, window_end=gap_window_end
        ):
            if (g_start, g_end) in already_open:
                continue
            gap = MeterReadingGap(
                id=uuid4(),
                organization_id=org_id,
                meter_id=meter_id,
                gap_start=g_start,
                gap_end=g_end,
                missing_periods=count,
                retry_count=0,
                status="open",
            )
            session.add(gap)
            await session.flush()
            already_open.add((g_start, g_end))
            gap_rows.append(
                {
                    "id": str(gap.id),
                    "gap_start": g_start.isoformat(),
                    "gap_end": g_end.isoformat(),
                    "missing_periods": count,
                }
            )

    await write_audit(
        session,
        actor="system",
        action_type="energy.readings.ingest",
        source_feature="C",
        organization_id=org_id,
        detail={"meter_id": str(meter_id), "inserted": inserted, "gaps": len(gap_rows)},
    )
    await session.commit()
    log.info(
        "energy.readings.ingest",
        meter_id=str(meter_id),
        rows=len(rows),
        inserted=inserted,
        gaps=len(gap_rows),
        ms=int((monotonic() - t0) * 1000),
    )
    return {
        "ok": True,
        "meter_id": str(meter_id),
        "inserted": inserted,
        "gaps_flagged": gap_rows,
    }


async def process_gap_retries(session: AsyncSession) -> dict[str, Any]:
    """
    Every ~10 min worker: re-pull HH from DCC/MPRN for open gaps (up to 3 tries),
    re-ingest retrieved readings, resolve if filled, else escalate after MAX_RETRIES.
    """
    from .dcc_client import pull_meter_window

    now = datetime.now(timezone.utc)
    q = select(MeterReadingGap).where(
        MeterReadingGap.status.in_(["open", "retrying"])
    ).limit(200)
    gaps = list((await session.execute(q)).scalars().all())
    retried = 0
    resolved = 0
    escalated = 0

    for gap in gaps:
        if gap.last_retry_at and (now - _aware(gap.last_retry_at)) < timedelta(
            minutes=RETRY_INTERVAL_MINUTES
        ):
            continue

        meter = await session.get(EnergyMeter, gap.meter_id)
        if not meter:
            gap.status = "escalated"
            gap.escalated_at = now
            escalated += 1
            continue

        gap.retry_count = int(gap.retry_count or 0) + 1
        gap.last_retry_at = now
        gap.status = "retrying"
        gap.updated_at = now
        retried += 1

        pulled: list[dict[str, Any]] = []
        try:
            pulled = await pull_meter_window(
                meter_id=meter.id,
                meter_type=meter.meter_type,
                mpan=meter.mpan,
                mprn=meter.mprn,
                dcc_device_id=meter.dcc_device_id,
                window_start=gap.gap_start,
                window_end=gap.gap_end,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "energy.gap.dcc_pull_failed",
                gap_id=str(gap.id),
                error=str(exc)[:200],
            )

        if pulled:
            # Inline upsert without nested commit — avoid re-detecting this gap
            for r in pulled:
                at = r.get("reading_at")
                if isinstance(at, str):
                    at = datetime.fromisoformat(at.replace("Z", "+00:00"))
                if not isinstance(at, datetime):
                    continue
                at = _aware(at).replace(second=0, microsecond=0)
                kwh = Decimal(str(r.get("consumption_kwh") or 0))
                existing = (
                    await session.execute(
                        select(MeterReading).where(
                            MeterReading.meter_id == meter.id,
                            MeterReading.reading_at == at,
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    existing.consumption_kwh = kwh
                    existing.quality_flag = r.get("quality_flag") or existing.quality_flag
                else:
                    session.add(
                        MeterReading(
                            id=uuid4(),
                            organization_id=gap.organization_id or meter.organization_id,
                            meter_id=meter.id,
                            asset_id=meter.asset_id,
                            reading_at=at,
                            period_minutes=HH_MINUTES,
                            consumption_kwh=kwh,
                            source="dcc_retry",
                            quality_flag=r.get("quality_flag") or "ok",
                        )
                    )

            # Re-check gap window
            present = set(
                (
                    await session.execute(
                        select(MeterReading.reading_at).where(
                            MeterReading.meter_id == meter.id,
                            MeterReading.reading_at >= gap.gap_start,
                            MeterReading.reading_at < gap.gap_end,
                        )
                    )
                ).scalars().all()
            )
            still = find_half_hour_gaps(
                list(present),
                window_start=gap.gap_start,
                window_end=gap.gap_end,
            )
            if not still:
                gap.status = "resolved"
                gap.updated_at = now
                resolved += 1
                continue

        if gap.retry_count >= MAX_RETRIES and gap.status != "resolved":
            gap.status = "escalated"
            gap.escalated_at = now
            item = await enqueue_approval(
                session,
                source_feature="C",
                item_type="meter_reading_gap_escalated",
                summary=(
                    f"Meter {gap.meter_id} reading gap escalated after {MAX_RETRIES} DCC retries "
                    f"({gap.missing_periods} missing HH periods from {gap.gap_start})"
                ),
                severity="high",
                payload={
                    "gap_id": str(gap.id),
                    "meter_id": str(gap.meter_id),
                    "missing_periods": gap.missing_periods,
                    "retry_count": gap.retry_count,
                },
                organization_id=gap.organization_id,
                related_entity_type="meter_reading_gap",
                related_entity_id=gap.id,
            )
            escalated += 1
            log.info("energy.gap.escalated", gap_id=str(gap.id), queue_id=str(item.id))

    await session.commit()
    return {
        "ok": True,
        "retried": retried,
        "resolved": resolved,
        "escalated": escalated,
    }


async def pull_and_ingest_meter(
    session: AsyncSession,
    *,
    meter_id: UUID,
    window_start: datetime,
    window_end: datetime,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Pull HH readings from DCC/MPRN for a meter window and ingest."""
    from .dcc_client import pull_meter_window

    meter = await session.get(EnergyMeter, meter_id)
    if not meter:
        return {"ok": False, "error": "meter_not_found"}
    readings = await pull_meter_window(
        meter_id=meter.id,
        meter_type=meter.meter_type,
        mpan=meter.mpan,
        mprn=meter.mprn,
        dcc_device_id=meter.dcc_device_id,
        window_start=window_start,
        window_end=window_end,
    )
    if not readings:
        return {"ok": True, "meter_id": str(meter_id), "inserted": 0, "pulled": 0, "note": "no_readings"}
    return await ingest_readings(
        session,
        meter_id=meter_id,
        readings=readings,
        organization_id=organization_id or meter.organization_id,
        source="dcc",
        detect_gaps=True,
    )


async def list_meters(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    site_id: UUID | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    q = select(EnergyMeter).where(EnergyMeter.active.is_(True)).limit(limit)
    if organization_id:
        q = q.where(EnergyMeter.organization_id == organization_id)
    if site_id:
        q = q.where(EnergyMeter.site_id == site_id)
    rows = list((await session.execute(q)).scalars().all())
    return [meter_to_dict(r) for r in rows]


def parse_readings_csv(text: str) -> list[dict[str, Any]]:
    """
    Parse half-hourly meter CSV into ingest rows.

    Accepted headers (case-insensitive): reading_at|timestamp|datetime,
    consumption_kwh|kwh|value, optional mpan|mprn|meter_id, period_minutes.
    """
    import csv
    import io

    raw = text.lstrip("\ufeff").strip()
    if not raw:
        return []
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        return []
    fields = {((f or "").strip().lower()): (f or "").strip() for f in reader.fieldnames}

    def col(*names: str) -> str | None:
        for n in names:
            if n in fields:
                return fields[n]
        return None

    at_col = col("reading_at", "timestamp", "datetime", "time", "ts")
    kwh_col = col("consumption_kwh", "kwh", "value", "consumption")
    if not at_col or not kwh_col:
        raise ValueError("CSV must include reading_at/timestamp and consumption_kwh/kwh columns")

    mpan_col = col("mpan")
    mprn_col = col("mprn")
    meter_col = col("meter_id", "meter")
    period_col = col("period_minutes", "period")

    out: list[dict[str, Any]] = []
    for row in reader:
        at = (row.get(at_col) or "").strip()
        kwh = (row.get(kwh_col) or "").strip()
        if not at or not kwh:
            continue
        item: dict[str, Any] = {
            "reading_at": at,
            "consumption_kwh": kwh,
            "period_minutes": int((row.get(period_col) or HH_MINUTES) if period_col else HH_MINUTES),
        }
        if meter_col and (row.get(meter_col) or "").strip():
            item["meter_id"] = (row.get(meter_col) or "").strip()
        if mpan_col and (row.get(mpan_col) or "").strip():
            item["mpan"] = (row.get(mpan_col) or "").strip()
        if mprn_col and (row.get(mprn_col) or "").strip():
            item["mprn"] = (row.get(mprn_col) or "").strip()
        out.append(item)
    return out


async def ingest_readings_csv(
    session: AsyncSession,
    *,
    csv_text: str,
    meter_id: UUID | None = None,
    organization_id: UUID | None = None,
    source: str = "csv",
    detect_gaps: bool = True,
    meter_type: str = "electricity",
    tariff_gbp_per_kwh: float = 0.28,
) -> dict[str, Any]:
    """
    Ingest CSV readings. If meter_id is omitted, resolve/create from row mpan/mprn.
    Rows may target multiple meters when identifiers differ.
    """
    try:
        rows = parse_readings_csv(csv_text)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not rows:
        return {"ok": False, "error": "no_readings_in_csv"}

    # Group by resolved meter — look up each MPAN/MPRN once, not once per row.
    buckets: dict[UUID, list[dict[str, Any]]] = {}
    created_meters: list[str] = []
    resolved: dict[tuple[str | None, str | None, str | None], UUID | None] = {}

    async def resolve_meter(row: dict[str, Any]) -> UUID | None:
        nonlocal created_meters
        if meter_id:
            return meter_id
        mid = row.get("meter_id")
        if mid:
            try:
                return UUID(str(mid))
            except Exception:
                pass
        mpan = row.get("mpan")
        mprn = row.get("mprn")
        cache_key = (str(mid) if mid else None, mpan, mprn)
        if cache_key in resolved:
            return resolved[cache_key]
        existing: EnergyMeter | None = None
        if mpan:
            existing = (
                await session.execute(select(EnergyMeter).where(EnergyMeter.mpan == mpan).limit(1))
            ).scalar_one_or_none()
        if existing is None and mprn:
            existing = (
                await session.execute(select(EnergyMeter).where(EnergyMeter.mprn == mprn).limit(1))
            ).scalar_one_or_none()
        if existing:
            resolved[cache_key] = existing.id
            return existing.id
        if not mpan and not mprn:
            resolved[cache_key] = None
            return None
        created = await upsert_meter(
            session,
            {
                "organization_id": organization_id,
                "meter_type": "gas" if mprn and not mpan else meter_type,
                "mpan": mpan,
                "mprn": mprn,
                "tariff_gbp_per_kwh": tariff_gbp_per_kwh,
            },
        )
        mid_s = (created.get("meter") or {}).get("id")
        if mid_s:
            created_meters.append(str(mid_s))
            found = UUID(str(mid_s))
            resolved[cache_key] = found
            return found
        resolved[cache_key] = None
        return None

    for row in rows:
        mid = await resolve_meter(row)
        if mid is None:
            continue
        buckets.setdefault(mid, []).append(
            {
                "reading_at": row["reading_at"],
                "consumption_kwh": row["consumption_kwh"],
                "period_minutes": row.get("period_minutes") or HH_MINUTES,
            }
        )

    if not buckets:
        return {
            "ok": False,
            "error": "could_not_resolve_meter",
            "hint": "Pass meter_id or include mpan/mprn/meter_id columns in the CSV",
        }

    results: list[dict[str, Any]] = []
    total_inserted = 0
    all_gaps: list[dict[str, Any]] = []
    for mid, readings in buckets.items():
        res = await ingest_readings(
            session,
            meter_id=mid,
            readings=readings,
            organization_id=organization_id,
            source=source,
            detect_gaps=detect_gaps,
        )
        results.append(res)
        total_inserted += int(res.get("inserted") or 0)
        all_gaps.extend(res.get("gaps_flagged") or [])

    return {
        "ok": all(r.get("ok") for r in results),
        "meters": [str(m) for m in buckets.keys()],
        "created_meters": created_meters,
        "inserted": total_inserted,
        "gaps_flagged": all_gaps,
        "per_meter": results,
    }


async def list_readings(
    session: AsyncSession,
    *,
    meter_id: UUID,
    limit: int = 2000,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[dict[str, Any]]:
    # Latest `limit` points, then chronological — otherwise a CSV longer than
    # the limit hid every newly uploaded reading behind the oldest rows.
    q = select(MeterReading).where(MeterReading.meter_id == meter_id)
    if window_start:
        q = q.where(MeterReading.reading_at >= _aware(window_start))
    if window_end:
        q = q.where(MeterReading.reading_at <= _aware(window_end))
    q = q.order_by(MeterReading.reading_at.desc()).limit(limit)
    rows = list((await session.execute(q)).scalars().all())
    rows.reverse()
    return [
        {
            "id": str(r.id),
            "meter_id": str(r.meter_id),
            "reading_at": r.reading_at.isoformat() if r.reading_at else None,
            "period_minutes": r.period_minutes,
            "consumption_kwh": float(r.consumption_kwh) if r.consumption_kwh is not None else 0.0,
            "source": r.source,
            "quality_flag": r.quality_flag,
        }
        for r in rows
    ]


async def list_gaps(
    session: AsyncSession,
    *,
    meter_id: UUID | None = None,
    status: str | None = "open",
    limit: int = 100,
) -> list[dict[str, Any]]:
    q = select(MeterReadingGap).order_by(MeterReadingGap.gap_start.desc()).limit(limit)
    if meter_id:
        q = q.where(MeterReadingGap.meter_id == meter_id)
    if status:
        q = q.where(MeterReadingGap.status == status)
    rows = list((await session.execute(q)).scalars().all())
    return [
        {
            "id": str(r.id),
            "meter_id": str(r.meter_id),
            "gap_start": r.gap_start.isoformat() if r.gap_start else None,
            "gap_end": r.gap_end.isoformat() if r.gap_end else None,
            "missing_periods": r.missing_periods,
            "retry_count": r.retry_count,
            "status": r.status,
        }
        for r in rows
    ]
