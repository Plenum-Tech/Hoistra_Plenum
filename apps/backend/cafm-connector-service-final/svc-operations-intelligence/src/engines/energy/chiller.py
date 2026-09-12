"""B9 — chiller kW/RT against design (UAE).

In a Gulf building the chiller plant is half the electricity bill, and the one number that
says whether it is healthy is kW per refrigeration ton: electrical power in over cooling
delivered. A chiller sold at 0.68 kW/RT that is running at 0.81 is spending 19% more for the
same cooling — and nothing here compared the two, because there was no table for the design
figure and none for the readings.

Now there are both. ``chiller_design_specs`` holds what the machine was sold as;
``chiller_performance_readings`` holds BMS or sub-meter samples (kW in; RT out, or thermal kW
which is converted; ambient). The assessment is detectors.detect_chiller_efficiency — the
rule card's "kW per RT more than 15% above design at matched ambient" — and a breach is
raised as an energy anomaly on the ASSET, priced at the meter tariff, into the same approval
queue as every other anomaly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import ChillerDesignSpec, ChillerPerformanceReading, EnergyAnomaly
from ...shared.approvals import enqueue_approval, write_audit
from . import detectors
from .anomalies import _SETTLED_STATUSES

log = get_logger(__name__)

KW_PER_RT = 3.517          # 1 refrigeration ton = 3.517 kW of cooling
DEFAULT_TARIFF = 0.28      # used only when no meter on the building carries one
DEFAULT_WINDOW_DAYS = 14


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _rt(rt: float | None, thermal_kw: float | None) -> float | None:
    if rt is not None:
        return float(rt)
    if thermal_kw is not None:
        return float(thermal_kw) / KW_PER_RT
    return None


async def upsert_design_spec(
    session: AsyncSession,
    *,
    asset_id: UUID,
    design_kw_per_rt: float,
    building_id: UUID | None = None,
    organization_id: UUID | None = None,
    design_capacity_rt: float | None = None,
    design_ambient_c: float | None = None,
    design_chw_supply_c: float | None = None,
    source: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    if design_kw_per_rt <= 0:
        return {"ok": False, "error": "design_kw_per_rt_must_be_positive"}
    row = await session.get(ChillerDesignSpec, asset_id)
    created = row is None
    if row is None:
        row = ChillerDesignSpec(asset_id=asset_id)
        session.add(row)
    if building_id is None:
        building_id = (await session.execute(
            text("SELECT building_id FROM plenum_cafm.assets WHERE id = CAST(:a AS uuid)"),
            {"a": str(asset_id)})).scalar()
    row.building_id = building_id or row.building_id
    row.organization_id = organization_id or row.organization_id
    row.design_kw_per_rt = Decimal(str(design_kw_per_rt))
    row.design_capacity_rt = Decimal(str(design_capacity_rt)) if design_capacity_rt is not None else row.design_capacity_rt
    row.design_ambient_c = Decimal(str(design_ambient_c)) if design_ambient_c is not None else row.design_ambient_c
    row.design_chw_supply_c = Decimal(str(design_chw_supply_c)) if design_chw_supply_c is not None else row.design_chw_supply_c
    row.source = source or row.source
    row.notes = notes or row.notes
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True, "created": created, "asset_id": str(asset_id),
            "building_id": str(row.building_id) if row.building_id else None,
            "design_kw_per_rt": float(row.design_kw_per_rt),
            "design_capacity_rt": float(row.design_capacity_rt) if row.design_capacity_rt is not None else None,
            "design_ambient_c": float(row.design_ambient_c) if row.design_ambient_c is not None else None}


async def ingest_readings(
    session: AsyncSession,
    *,
    asset_id: UUID,
    readings: list[dict[str, Any]],
    building_id: UUID | None = None,
    organization_id: UUID | None = None,
    source: str = "bms",
) -> dict[str, Any]:
    """Store samples. Each: reading_at, kw_input, and cooling_load_rt or cooling_load_kw;
    ambient_c / chw_supply_c / chw_return_c optional. Rows that cannot make a kW/RT figure
    (no cooling load) are kept — they may be useful later — but counted separately."""
    if building_id is None:
        building_id = (await session.execute(
            text("SELECT building_id FROM plenum_cafm.assets WHERE id = CAST(:a AS uuid)"),
            {"a": str(asset_id)})).scalar()
    stored = unusable = rejected = 0
    for r in readings:
        try:
            at = r["reading_at"]
            at = _aware(datetime.fromisoformat(at) if isinstance(at, str) else at)
            kw = float(r["kw_input"])
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        rt = r.get("cooling_load_rt")
        tkw = r.get("cooling_load_kw")
        if rt is None and tkw is None:
            unusable += 1
        session.add(ChillerPerformanceReading(
            id=uuid4(), organization_id=organization_id, asset_id=asset_id, building_id=building_id,
            reading_at=at, kw_input=Decimal(str(kw)),
            cooling_load_rt=Decimal(str(rt)) if rt is not None else None,
            cooling_load_kw=Decimal(str(tkw)) if tkw is not None else None,
            ambient_c=Decimal(str(r["ambient_c"])) if r.get("ambient_c") is not None else None,
            chw_supply_c=Decimal(str(r["chw_supply_c"])) if r.get("chw_supply_c") is not None else None,
            chw_return_c=Decimal(str(r["chw_return_c"])) if r.get("chw_return_c") is not None else None,
            source=source,
        ))
        stored += 1
    await session.commit()
    return {"ok": True, "asset_id": str(asset_id), "stored": stored,
            "without_cooling_load": unusable, "rejected": rejected}


async def _tariff_for(session: AsyncSession, building_id: UUID | None) -> float:
    if not building_id:
        return DEFAULT_TARIFF
    v = (await session.execute(text("""
        SELECT tariff_gbp_per_kwh FROM plenum_cafm.energy_meters
         WHERE active AND site_id = CAST(:b AS uuid) ORDER BY is_sub_meter, created_at LIMIT 1
    """), {"b": str(building_id)})).scalar()
    return float(v) if v else DEFAULT_TARIFF


async def assess(
    session: AsyncSession,
    *,
    asset_id: UUID,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """kW/RT over the window against the design figure. Read-only: the position, whether or
    not it breaches, so a dashboard can show 0.70 against 0.68 as well as 0.81."""
    spec = await session.get(ChillerDesignSpec, asset_id)
    if not spec:
        return {"ok": False, "error": "design_spec_required", "asset_id": str(asset_id),
                "hint": "POST /api/energy/chillers/{asset_id}/design with design_kw_per_rt first."}
    latest = (await session.execute(
        select(ChillerPerformanceReading.reading_at).where(ChillerPerformanceReading.asset_id == asset_id)
        .order_by(ChillerPerformanceReading.reading_at.desc()).limit(1))).scalar()
    if latest is None:
        return {"ok": False, "error": "no_readings", "asset_id": str(asset_id)}
    since = _aware(latest) - timedelta(days=window_days)
    rows = (await session.execute(
        select(ChillerPerformanceReading).where(
            ChillerPerformanceReading.asset_id == asset_id,
            ChillerPerformanceReading.reading_at >= since,
        ).order_by(ChillerPerformanceReading.reading_at))).scalars().all()
    samples = []
    for r in rows:
        rt = _rt(float(r.cooling_load_rt) if r.cooling_load_rt is not None else None,
                 float(r.cooling_load_kw) if r.cooling_load_kw is not None else None)
        if rt is None:
            continue
        samples.append((_aware(r.reading_at), float(r.kw_input), rt,
                        float(r.ambient_c) if r.ambient_c is not None else None))
    if len(samples) < 4:
        return {"ok": False, "error": "too_few_samples", "samples": len(samples), "asset_id": str(asset_id)}

    design = float(spec.design_kw_per_rt)
    cap = float(spec.design_capacity_rt) if spec.design_capacity_rt is not None else None
    amb = float(spec.design_ambient_c) if spec.design_ambient_c is not None else None
    tariff = await _tariff_for(session, spec.building_id)
    hit = detectors.detect_chiller_efficiency(
        samples, tariff=tariff, design_kw_per_rt=design, design_capacity_rt=cap, design_ambient_c=amb)
    # the position even when the rule does not fire
    kept = [s for s in samples
            if (cap is None or s[2] >= detectors.COP_MIN_LOAD_FRACTION * cap)
            and (amb is None or s[3] is None or abs(s[3] - amb) <= detectors.COP_AMBIENT_BAND_C)]
    total_kw = sum(s[1] for s in kept)
    total_rt = sum(s[2] for s in kept)
    actual = total_kw / total_rt if total_rt > 0 else None
    return {
        "ok": True,
        "asset_id": str(asset_id),
        "building_id": str(spec.building_id) if spec.building_id else None,
        "design_kw_per_rt": design,
        "actual_kw_per_rt": round(actual, 4) if actual else None,
        "deviation_pct": round(100.0 * (actual / design - 1.0), 1) if actual else None,
        "breach": hit is not None,
        "threshold_pct": detectors.COP_EXCESS_PCT - 100.0,
        "samples_used": len(kept),
        "samples_total": len(samples),
        "window_days": window_days,
        "window_end": _aware(latest).isoformat(),
        "ambient_matched": amb is not None,
        "tariff_used": tariff,
        "anomaly": hit,
    }


async def scan(
    session: AsyncSession,
    *,
    asset_id: UUID,
    organization_id: UUID | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Assess, and raise the breach as an energy anomaly on the asset (once per live event)."""
    out = await assess(session, asset_id=asset_id, window_days=window_days)
    if not out.get("ok"):
        return out
    hit = out.get("anomaly")
    if not hit:
        return {**out, "raised": None}
    spec = await session.get(ChillerDesignSpec, asset_id)
    org = organization_id or (spec.organization_id if spec else None)
    existing = (await session.execute(
        select(EnergyAnomaly).where(
            EnergyAnomaly.asset_id == asset_id,
            EnergyAnomaly.anomaly_type == hit["anomaly_type"],
            EnergyAnomaly.status.notin_(_SETTLED_STATUSES),
        ).order_by(EnergyAnomaly.detected_at.desc()).limit(1))).scalars().first()
    if existing is not None:
        existing.window_end = datetime.now(timezone.utc)
        existing.metric_pct = Decimal(str(hit["metric_pct"]))
        existing.detail_json = hit.get("detail") or {}
        await session.commit()
        return {**out, "raised": {"id": str(existing.id), "already_open": True}}
    row = EnergyAnomaly(
        id=uuid4(), organization_id=org, site_id=spec.building_id if spec else None,
        meter_id=None, asset_id=asset_id, anomaly_type=hit["anomaly_type"],
        window_end=datetime.now(timezone.utc), metric_pct=Decimal(str(hit["metric_pct"])),
        excess_kwh=Decimal(str(hit["excess_kwh"])),
        annualised_excess_kwh=Decimal(str(hit["annualised_excess_kwh"])),
        financial_gbp=Decimal(str(hit["financial_gbp"])), tariff_used=Decimal(str(out["tariff_used"])),
        detail_json=hit.get("detail") or {}, status="open",
    )
    session.add(row)
    await session.flush()
    item = await enqueue_approval(
        session, source_feature="C", item_type="energy_anomaly_chiller_efficiency",
        summary=(f"Chiller {asset_id} running at {hit['detail']['actual_kw_per_rt']} kW/RT against "
                 f"{out['design_kw_per_rt']} design ({hit['metric_pct']}%) · est £{hit['financial_gbp']}/yr "
                 f"excess. Actions: Acknowledge / Monitor / Mark expected. No WO created."),
        severity="high" if hit["financial_gbp"] >= 500 else "medium",
        payload={"anomaly_id": str(row.id), "anomaly_type": hit["anomaly_type"], "asset_id": str(asset_id),
                 "financial_gbp": hit["financial_gbp"], "actions": ["acknowledge", "monitor", "mark_expected"],
                 "creates_work_order": False},
        organization_id=org, related_entity_type="energy_anomaly", related_entity_id=row.id,
    )
    row.queue_item_id = item.id
    await write_audit(session, actor="system", action_type="energy.chiller.scan", source_feature="C",
                      organization_id=org, detail={"asset_id": str(asset_id), "metric_pct": hit["metric_pct"]})
    await session.commit()
    return {**out, "raised": {"id": str(row.id), "queue_item_id": str(item.id), "already_open": False}}


async def scan_all(
    session: AsyncSession, *, organization_id: UUID | None = None, building_ids: list[UUID] | None = None
) -> dict[str, Any]:
    q = select(ChillerDesignSpec.asset_id)
    if organization_id:
        q = q.where(ChillerDesignSpec.organization_id == organization_id)
    if building_ids is not None:
        q = q.where(ChillerDesignSpec.building_id.in_(building_ids or [uuid4()]))
    ids = [r[0] for r in (await session.execute(q)).all()]
    results = []
    for aid in ids:
        try:
            r = await scan(session, asset_id=aid, organization_id=organization_id)
            results.append({"asset_id": str(aid), "ok": r.get("ok"), "breach": r.get("breach"),
                            "actual_kw_per_rt": r.get("actual_kw_per_rt"), "error": r.get("error")})
        except Exception as exc:  # noqa: BLE001
            log.warning("energy.chiller.scan_failed", asset_id=str(aid), error=str(exc)[:200])
            results.append({"asset_id": str(aid), "ok": False, "error": str(exc)[:200]})
    return {"ok": True, "chillers": len(ids), "results": results}


async def plant_position(
    session: AsyncSession, *, building_ids: list[UUID]
) -> dict[str, Any]:
    """The AE tile: the worst chiller on the caller's buildings, against its design."""
    if not building_ids:
        return {"ok": True, "chillers": 0, "tile": None}
    ids = [r[0] for r in (await session.execute(
        select(ChillerDesignSpec.asset_id).where(ChillerDesignSpec.building_id.in_(building_ids)))).all()]
    positions = []
    for aid in ids:
        a = await assess(session, asset_id=aid)
        if a.get("ok"):
            positions.append(a)
    if not positions:
        return {"ok": True, "chillers": len(ids), "assessed": 0, "tile": {
            "l": "Chiller plant kW/RT", "v": "—",
            "s": ("no chiller design figures on file" if not ids else "no readings in the window"),
            "tone": "dormant", "basis": "consumption"}}
    worst = max(positions, key=lambda p: p["deviation_pct"] or -999)
    names = (await session.execute(text(
        "SELECT id::text, asset_code, asset_name FROM plenum_cafm.assets WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [p["asset_id"] for p in positions]})).mappings().all()
    label = {n["id"]: (n["asset_code"] or n["asset_name"]) for n in names}
    months = max(1, round(worst["window_days"] / 30))
    return {"ok": True, "chillers": len(ids), "assessed": len(positions), "worst": worst, "positions": positions,
            "tile": {"l": "Chiller plant kW/RT", "v": f"{worst['actual_kw_per_rt']:.2f}",
                     "s": f"design {worst['design_kw_per_rt']:.2f} · {label.get(worst['asset_id'], 'chiller')}"
                          + (" drifting" if worst["breach"] else " within 15%"),
                     "tone": "risk" if worst["breach"] else ("warn" if (worst["deviation_pct"] or 0) > 5 else "ok"),
                     "basis": "consumption", "months": months}}
