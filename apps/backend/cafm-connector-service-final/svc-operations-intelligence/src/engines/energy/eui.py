"""Feature C — EUI vs CIBSE TM46 + tariff financial deviation."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import (
    BuildingEnergyProfile,
    EnergyMeter,
    EuiSnapshot,
    MeterReading,
)
from ...shared.approvals import write_audit

log = get_logger(__name__)

_TM46_PATH = Path(__file__).resolve().parents[2] / "reference" / "cibse_tm46_uk.json"
_TM46_CACHE: dict[str, Any] | None = None


def load_tm46() -> dict[str, Any]:
    global _TM46_CACHE
    if _TM46_CACHE is None:
        _TM46_CACHE = json.loads(_TM46_PATH.read_text(encoding="utf-8"))
    return _TM46_CACHE


def tm46_benchmark(building_type: str, meter_type: str) -> float | None:
    pack = load_tm46()
    bt = (pack.get("building_types") or {}).get((building_type or "office").lower())
    if not bt:
        return None
    if meter_type == "gas":
        return float(bt.get("gas") or 0) or None
    if meter_type == "combined":
        return float(bt.get("electricity") or 0) + float(bt.get("gas") or 0)
    return float(bt.get("electricity") or 0) or None


def compute_eui(
    *,
    total_kwh: float,
    gia_m2: float,
    period_start: date,
    period_end: date,
    annual_benchmark_kwh_m2: float | None,
    tariff_gbp_per_kwh: float,
) -> dict[str, Any]:
    """
    EUI = total_kwh ÷ GIA ÷ period, annualised for TM46 compare.
    Period factor: days/365 for annualisation of period EUI.
    """
    if gia_m2 <= 0:
        return {"ok": False, "error": "gia_m2_must_be_positive"}
    days = max(1, (period_end - period_start).days + 1)
    period_eui = total_kwh / gia_m2  # kWh/m² for the period
    annualised_eui = period_eui * (365.0 / days)

    deviation_pct = None
    excess_kwh = None
    financial = None
    if annual_benchmark_kwh_m2 and annual_benchmark_kwh_m2 > 0:
        # Compare annualised EUI to TM46 annual benchmark
        deviation_pct = round(
            100.0 * (annualised_eui - annual_benchmark_kwh_m2) / annual_benchmark_kwh_m2, 2
        )
        # Excess for the period relative to pro-rata benchmark
        period_benchmark_kwh = annual_benchmark_kwh_m2 * gia_m2 * (days / 365.0)
        excess_kwh = round(max(0.0, total_kwh - period_benchmark_kwh), 4)
        financial = round(excess_kwh * tariff_gbp_per_kwh, 2)

    return {
        "ok": True,
        "total_kwh": round(total_kwh, 4),
        "gia_m2": gia_m2,
        "period_days": days,
        "eui_kwh_per_m2_period": round(period_eui, 6),
        "eui_kwh_per_m2_annualised": round(annualised_eui, 6),
        "benchmark_kwh_per_m2": annual_benchmark_kwh_m2,
        "deviation_pct": deviation_pct,
        "excess_kwh": excess_kwh,
        "financial_gbp": financial,
        "tariff_gbp_per_kwh": tariff_gbp_per_kwh,
    }


async def upsert_building_profile(
    session: AsyncSession,
    *,
    site_id: UUID,
    gia_m2: float,
    building_type: str = "office",
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    bt = (building_type or "office").lower()
    elec = tm46_benchmark(bt, "electricity")
    gas = tm46_benchmark(bt, "gas")
    q = select(BuildingEnergyProfile).where(BuildingEnergyProfile.site_id == site_id)
    row = (await session.execute(q)).scalar_one_or_none()
    if row is None:
        row = BuildingEnergyProfile(
            id=uuid4(),
            organization_id=organization_id,
            site_id=site_id,
            building_type=bt,
            gia_m2=Decimal(str(gia_m2)),
            tm46_electricity_benchmark=Decimal(str(elec)) if elec else None,
            tm46_gas_benchmark=Decimal(str(gas)) if gas else None,
        )
        session.add(row)
    else:
        row.gia_m2 = Decimal(str(gia_m2))
        row.building_type = bt
        row.tm46_electricity_benchmark = Decimal(str(elec)) if elec else None
        row.tm46_gas_benchmark = Decimal(str(gas)) if gas else None
        row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {
        "ok": True,
        "profile": {
            "id": str(row.id),
            "site_id": str(site_id),
            "building_type": bt,
            "gia_m2": float(row.gia_m2),
            "tm46_electricity_benchmark": elec,
            "tm46_gas_benchmark": gas,
        },
    }


async def compute_site_eui(
    session: AsyncSession,
    *,
    site_id: UUID,
    period_start: date,
    period_end: date,
    meter_type: str = "electricity",
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    profile = (
        await session.execute(
            select(BuildingEnergyProfile).where(BuildingEnergyProfile.site_id == site_id)
        )
    ).scalar_one_or_none()
    if not profile:
        return {"ok": False, "error": "building_energy_profile_required"}

    meters_q = select(EnergyMeter).where(
        EnergyMeter.site_id == site_id,
        EnergyMeter.active.is_(True),
    )
    if meter_type != "combined":
        meters_q = meters_q.where(EnergyMeter.meter_type == meter_type)
    meters = list((await session.execute(meters_q)).scalars().all())
    if not meters:
        return {"ok": False, "error": "no_meters_for_site"}

    meter_ids = [m.id for m in meters]
    start_dt = datetime(period_start.year, period_start.month, period_start.day, tzinfo=timezone.utc)
    end_dt = datetime(
        period_end.year, period_end.month, period_end.day, 23, 59, 59, tzinfo=timezone.utc
    )
    total = (
        await session.execute(
            select(func.coalesce(func.sum(MeterReading.consumption_kwh), 0)).where(
                MeterReading.meter_id.in_(meter_ids),
                MeterReading.reading_at >= start_dt,
                MeterReading.reading_at <= end_dt,
            )
        )
    ).scalar_one()
    total_kwh = float(total or 0)
    tariff = float(meters[0].tariff_gbp_per_kwh or 0.28)

    if meter_type == "gas":
        bench = float(profile.tm46_gas_benchmark) if profile.tm46_gas_benchmark else tm46_benchmark(
            profile.building_type, "gas"
        )
    elif meter_type == "combined":
        e = float(profile.tm46_electricity_benchmark or 0)
        g = float(profile.tm46_gas_benchmark or 0)
        bench = (e + g) or tm46_benchmark(profile.building_type, "combined")
    else:
        bench = (
            float(profile.tm46_electricity_benchmark)
            if profile.tm46_electricity_benchmark
            else tm46_benchmark(profile.building_type, "electricity")
        )

    result = compute_eui(
        total_kwh=total_kwh,
        gia_m2=float(profile.gia_m2),
        period_start=period_start,
        period_end=period_end,
        annual_benchmark_kwh_m2=bench,
        tariff_gbp_per_kwh=tariff,
    )
    if not result.get("ok"):
        return result

    snap = EuiSnapshot(
        id=uuid4(),
        organization_id=organization_id or profile.organization_id,
        site_id=site_id,
        period_start=period_start,
        period_end=period_end,
        meter_type=meter_type,
        total_kwh=Decimal(str(result["total_kwh"])),
        gia_m2=profile.gia_m2,
        eui_kwh_per_m2=Decimal(str(result["eui_kwh_per_m2_annualised"])),
        benchmark_kwh_per_m2=Decimal(str(bench)) if bench else None,
        deviation_pct=Decimal(str(result["deviation_pct"])) if result.get("deviation_pct") is not None else None,
        excess_kwh=Decimal(str(result["excess_kwh"])) if result.get("excess_kwh") is not None else None,
        financial_gbp=Decimal(str(result["financial_gbp"])) if result.get("financial_gbp") is not None else None,
        tariff_used=Decimal(str(tariff)),
    )
    session.add(snap)
    await write_audit(
        session,
        actor="system",
        action_type="energy.eui.compute",
        source_feature="C",
        organization_id=organization_id or profile.organization_id,
        output_payload={"site_id": str(site_id), "eui": result.get("eui_kwh_per_m2_annualised")},
    )
    await session.commit()
    result["snapshot_id"] = str(snap.id)
    result["site_id"] = str(site_id)
    result["building_type"] = profile.building_type
    result["meter_type"] = meter_type
    return result
