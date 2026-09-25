"""Sections, asset value at risk, and a failure assessment that says what it is.

Four blocks on the Assets page were fixture data because nothing behind them existed: the
EUI-against-reference tree over a building's sections, the value at risk, the vendor who holds
an asset, and the instrumented-asset panel with its bands and failure figure. The tables now
exist (``asset_intelligence_tables.sql``); this computes the answers from them.

Three things worth stating plainly, because they are the difference between a number and a
number somebody can act on.

**A section is judged against its own reference, not the building's.** A server room measured
against an office benchmark reads as a catastrophe and a car park reads as a triumph, and
neither tells anyone anything. ``building_sections.reference_eui_kwh_m2`` is per section for
that reason, and the deviation returned is against that.

**Value at risk is arithmetic, and the arithmetic is returned with it.** An asset loses value
on a straight line across its design life. Running above its reference ages it faster —
``wear_coefficient`` says how much faster — so the gap between the straight line and the worn
line is what the deviation is costing. Every input is in the response so the figure can be
checked rather than believed.

**The failure assessment is a rule, not a trained model.** It carries no accuracy, precision
or recall, because none has been measured against labelled outcomes and quoting such numbers
without a fitted model is inventing evidence. What it returns is a probability built from
signals that are genuinely on record — condition grade, open anomalies, age against design
life, readings outside their band — with those signals listed beside it, and ``method`` naming
the rule. A person can disagree with it on the evidence shown.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

#: The rule that produces a failure probability, named in every row it writes.
METHOD = "rule:condition+anomaly+age+band/v1"

#: How much each signal can contribute. They sum to 1.0 and the result is capped there.
WEIGHT_CONDITION = 0.35
WEIGHT_ANOMALY = 0.25
WEIGHT_AGE = 0.25
WEIGHT_BAND = 0.15


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _scope(building_ids: list[UUID] | None, column: str) -> tuple[str, dict[str, Any]]:
    """The caller's buildings. None is unrestricted; an empty list matches no row."""
    if building_ids is None:
        return "", {}
    ids = [str(b) for b in building_ids]
    if not ids:
        return " AND FALSE", {}
    return f" AND {column} = ANY(CAST(:scope_b AS uuid[]))", {"scope_b": ids}


# ── 6. sections ──────────────────────────────────────────────────────────────────────

async def sections(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int = 500,
) -> dict[str, Any]:
    """Every section in scope with its measured intensity against its own reference.

    The measured figure comes from the sub-meters attached to the section, summed across them
    — a floor with an electricity and a gas sub-meter is one floor, not two rows — and
    annualised over the time the meters actually cover, the way the building's EUI is: 90
    days of readings on a floor are a quarter of a year, not a quarter of the intensity. A
    section with no meter, no area or under a month of coverage returns a null intensity
    rather than a zero: not measured and measured-at-zero are different facts.
    """
    clause, params = _scope(building_ids, "s.building_id")
    params["lim"] = int(limit)
    sql = f"""
        SELECT s.section_id::text        AS section_id,
               s.building_id::text       AS building_id,
               b.name                    AS building,
               s.name                    AS name,
               s.section_type            AS section_type,
               s.gross_area_m2           AS area_m2,
               s.reference_eui_kwh_m2    AS reference_eui,
               s.reference_source        AS reference_source,
               f.name                    AS floor,
               m.id::text                AS meter_id,
               lower(coalesce(m.meter_type, 'electricity')) AS fuel,
               (SELECT sum(r.consumption_kwh) FROM plenum_cafm.meter_readings r
                 WHERE r.meter_id = m.id
                   AND r.reading_at >= now() - interval '365 days') AS kwh_year,
               (SELECT sum(coalesce(r.period_minutes, 30)) FROM plenum_cafm.meter_readings r
                 WHERE r.meter_id = m.id
                   AND r.reading_at >= now() - interval '365 days') AS covered_minutes
          FROM plenum_cafm.building_sections s
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = s.building_id
          LEFT JOIN plenum_cafm.floors f ON f.floor_id = s.floor_id
          LEFT JOIN plenum_cafm.energy_meters m
                 ON m.section_id = s.section_id AND m.active
         WHERE s.building_id IS NOT NULL{clause}
         ORDER BY b.name, s.name, m.meter_type
         LIMIT :lim"""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_intel.sections_failed", error=str(exc)[:300])
        return {"ok": False, "sections": [], "error": str(exc)[:200]}

    return {"ok": True, **summarise_sections([dict(r) for r in rows])}


#: Metered minutes in a month, the same figure the EUI window annualises by.
_MINUTES_PER_MONTH = 365.25 * 24 * 60 / 12


def summarise_sections(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure: one meter-row per (section, meter) → one entry per section.

    kWh add across the section's meters; coverage is the longest-covered meter's, because two
    meters reading the same quarter cover one quarter between them. Intensity is annualised
    from that coverage and is None below a month of it.
    """
    by_section: dict[str, dict[str, Any]] = {}
    for r in rows:
        sid = r["section_id"]
        sec = by_section.get(sid)
        if sec is None:
            sec = by_section[sid] = {
                "section_id": sid, "building_id": r["building_id"], "building": r["building"],
                "name": r["name"], "section_type": r["section_type"], "floor": r.get("floor"),
                "area_m2": _num(r["area_m2"]), "reference_eui_kwh_m2": _num(r["reference_eui"]),
                "reference_source": r["reference_source"],
                "meter_id": None, "meters": 0, "fuels": [], "kwh_measured": 0.0,
                "_minutes": 0.0,
            }
        if not r.get("meter_id"):
            continue
        sec["meters"] += 1
        sec["meter_id"] = sec["meter_id"] or r["meter_id"]
        fuel = r.get("fuel") or "electricity"
        if fuel not in sec["fuels"]:
            sec["fuels"].append(fuel)
        sec["kwh_measured"] += _num(r.get("kwh_year")) or 0.0
        sec["_minutes"] = max(sec["_minutes"], _num(r.get("covered_minutes")) or 0.0)

    out = []
    for sec in by_section.values():
        months = sec.pop("_minutes") / _MINUTES_PER_MONTH
        area, ref = sec["area_m2"], sec["reference_eui_kwh_m2"]
        kwh = sec["kwh_measured"]
        annual = kwh * 12.0 / months if kwh and months >= 1.0 else None
        eui = round(annual / area, 1) if annual is not None and area else None
        dev = round((eui - ref) / ref * 100, 1) if eui is not None and ref else None
        sec.update({
            "fuel": sec["fuels"][0] if sec["fuels"] else None,
            "months_measured": round(months, 1) if months else 0.0,
            "kwh_measured": round(kwh, 1),
            "kwh_annualised": round(annual, 1) if annual is not None else None,
            "eui_kwh_per_m2": eui,
            "deviation_pct": dev,
            "over_reference": bool(dev is not None and dev > 0),
            # Said out loud rather than implied by a zero.
            "measured": eui is not None,
        })
        out.append(sec)
    over = [s for s in out if s["over_reference"]]
    return {
        "count": len(out),
        "summary": {
            "sections": len(out),
            "measured": sum(1 for s in out if s["measured"]),
            "over_reference": len(over),
            "worst_deviation_pct": max((s["deviation_pct"] for s in over), default=None),
        },
        "sections": out,
    }


# ── 7. value at risk ─────────────────────────────────────────────────────────────────

def value_at_risk(
    *, replacement_value: float | None, design_life_years: float | None,
    installation_date: Any, wear_coefficient: float | None, deviation_pct: float | None,
    today: date | None = None,
) -> dict[str, Any]:
    """What running above reference is costing this asset, and the arithmetic behind it.

    Straight line: an asset worth ``replacement_value`` new is worth nothing after
    ``design_life_years``. Running ``deviation_pct`` above its reference ages it faster by
    ``wear_coefficient``, so the worn line reaches nothing sooner. The gap between the two
    lines today is the loss attributable to the deviation.

    Returns every input beside the answer. A figure like this is only worth having if the
    person reading it can see what it was made from.
    """
    today = today or date.today()
    rv = _num(replacement_value)
    life = _num(design_life_years)
    wear = _num(wear_coefficient)
    dev = _num(deviation_pct)

    installed = installation_date
    if isinstance(installed, datetime):
        installed = installed.date()
    age = None
    if isinstance(installed, date):
        age = round((today - installed).days / 365.25, 2)

    out: dict[str, Any] = {
        "replacement_value": rv, "design_life_years": life, "age_years": age,
        "wear_coefficient": wear, "deviation_pct": dev,
        "straight_line_value": None, "adjusted_value": None, "value_at_risk": None,
        "design_life_used_pct": None, "remaining_life_months": None,
        "basis": None,
    }
    if rv is None or not life or age is None:
        out["basis"] = "not computable: needs replacement value, design life and an install date"
        return out

    used = min(age / life, 1.0)
    straight = rv * max(0.0, 1.0 - used)
    out["straight_line_value"] = round(straight, 2)
    out["design_life_used_pct"] = round(used * 100, 1)

    # Effective age: running above reference ages it faster, by wear x deviation.
    factor = 1.0
    if wear and dev and dev > 0:
        factor = 1.0 + (wear * dev / 100.0)
    worn_used = min(age * factor / life, 1.0)
    adjusted = rv * max(0.0, 1.0 - worn_used)
    out["adjusted_value"] = round(adjusted, 2)
    out["value_at_risk"] = round(straight - adjusted, 2)
    out["remaining_life_months"] = round(max(0.0, life - age * factor) * 12, 1)
    out["basis"] = (
        f"straight line over {life:g} years from a replacement value of {rv:g}; "
        f"effective age {round(age * factor, 2):g} years after a wear factor of {factor:.3g}"
    )
    return out


# ── 9. bands and the failure assessment ──────────────────────────────────────────────

async def bands_for(session: AsyncSession, *, asset_id: str, category: str | None) -> dict[str, dict]:
    """The band limits that apply to one asset, most specific first."""
    sql = """
        SELECT reading_type, unit, lo, hi, note,
               CASE WHEN asset_id IS NOT NULL THEN 3
                    WHEN asset_category IS NOT NULL THEN 2 ELSE 1 END AS specificity
          FROM plenum_cafm.asset_reading_bands
         WHERE asset_id::text = :aid
            OR (asset_id IS NULL AND asset_category IS NOT NULL AND asset_category = :cat)
            OR (asset_id IS NULL AND asset_category IS NULL)
         ORDER BY reading_type, specificity DESC"""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), {"aid": str(asset_id), "cat": category})).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_intel.bands_failed", error=str(exc)[:200])
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["reading_type"], {
            "lo": _num(r["lo"]), "hi": _num(r["hi"]), "unit": r["unit"], "note": r["note"],
        })
    return out


def assess_failure(
    *, condition_score: int | None, open_anomalies: int, anomaly_weeks: float | None,
    design_life_used_pct: float | None, readings_out_of_band: int, readings_total: int,
) -> dict[str, Any]:
    """A failure probability from signals that are on record, with those signals returned.

    Not a fitted model. There is no accuracy, precision or recall here because nothing has
    been measured against labelled outcomes, and quoting those numbers without a model is
    inventing evidence. Each signal contributes at most its own weight and the total is
    capped at one.
    """
    drivers: list[dict[str, Any]] = []
    score = 0.0

    if condition_score is not None:
        # 1 as new, 5 end of life. 3 and above starts contributing.
        part = max(0.0, (condition_score - 2) / 3.0) * WEIGHT_CONDITION
        score += part
        drivers.append({"signal": "condition grade", "value": condition_score,
                        "contribution": round(part, 4),
                        "note": "inspector grade, 1 as new to 5 end of life"})

    if open_anomalies:
        persistence = min((anomaly_weeks or 1.0) / 4.0, 1.0)
        part = min(open_anomalies / 2.0, 1.0) * persistence * WEIGHT_ANOMALY
        score += part
        drivers.append({"signal": "open energy anomalies", "value": open_anomalies,
                        "weeks_persistent": anomaly_weeks, "contribution": round(part, 4),
                        "note": "a finding that persists counts for more than a new one"})

    if design_life_used_pct is not None:
        part = min(max(design_life_used_pct - 60.0, 0.0) / 40.0, 1.0) * WEIGHT_AGE
        score += part
        drivers.append({"signal": "design life used", "value": design_life_used_pct,
                        "contribution": round(part, 4),
                        "note": "starts contributing past 60 per cent of design life"})

    if readings_total:
        part = (readings_out_of_band / readings_total) * WEIGHT_BAND
        score += part
        drivers.append({"signal": "readings outside band",
                        "value": f"{readings_out_of_band} of {readings_total}",
                        "contribution": round(part, 4)})

    return {
        "probability": round(min(score, 1.0), 4),
        "method": METHOD,
        "is_fitted_model": False,
        "accuracy": None, "precision": None, "recall": None,
        "why_no_metrics": (
            "no model has been fitted against labelled failures, so accuracy, precision and "
            "recall would be invented rather than measured"
        ),
        "drivers": drivers,
    }


# ── the asset, assembled ─────────────────────────────────────────────────────────────

async def asset_detail(
    session: AsyncSession, *, asset_id: str, building_ids: list[UUID] | None,
) -> dict[str, Any]:
    """One asset with everything the drawer shows: section, vendor, value, readings, risk.

    An asset on a building the caller is not allocated to comes back as not found rather
    than as data, and the two are reported the same way so the response does not confirm
    that somebody else's asset exists.
    """
    clause, params = _scope(building_ids, "a.building_id")
    params["aid"] = str(asset_id)
    sql = f"""
        SELECT a.id::text AS id, a.asset_name, a.asset_code, a.building_id::text AS building_id,
               b.name AS building, a.installation_date, a.warranty_expiry, a.status,
               a.criticality, a.health_score, a.condition_score, a.condition_updated_at,
               a.replacement_value, a.replacement_currency, a.design_life_years,
               a.wear_coefficient, a.vendor_id::text AS vendor_id,
               v.vendor_name AS vendor, v.vendor_code,
               s.section_id::text AS section_id, s.name AS section,
               s.reference_eui_kwh_m2 AS section_reference
          FROM plenum_cafm.assets a
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
          LEFT JOIN plenum_cafm.vendors v ON v.id::text = a.vendor_id::text
          LEFT JOIN plenum_cafm.building_sections s ON s.section_id = a.section_id
         WHERE a.id::text = :aid{clause}
         LIMIT 1"""
    try:
        async with session.begin_nested():
            row = (await session.execute(text(sql), params)).mappings().first()
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_intel.asset_failed", error=str(exc)[:300])
        return {"ok": False, "reason": "query_failed", "error": str(exc)[:200]}
    if row is None:
        return {"ok": False, "reason": "not_found_or_not_in_scope", "asset_id": str(asset_id)}

    anom = await _asset_anomaly(session, asset_id)
    bands = await bands_for(session, asset_id=str(asset_id), category=row["criticality"])
    readings = await _asset_readings(session, asset_id, bands)

    var = value_at_risk(
        replacement_value=row["replacement_value"], design_life_years=row["design_life_years"],
        installation_date=row["installation_date"], wear_coefficient=row["wear_coefficient"],
        deviation_pct=anom["worst_pct"],
    )
    graded = [r for r in readings if r["state"] != "unknown"]
    risk = assess_failure(
        condition_score=row["condition_score"], open_anomalies=anom["open"],
        anomaly_weeks=anom["weeks"], design_life_used_pct=var["design_life_used_pct"],
        readings_out_of_band=sum(1 for r in graded if r["state"] == "out_of_band"),
        readings_total=len(graded),
    )
    risk["remaining_life_months"] = var["remaining_life_months"]
    risk["design_life_used_pct"] = var["design_life_used_pct"]

    return {
        "ok": True,
        "asset": {
            "id": row["id"], "asset_name": row["asset_name"], "asset_code": row["asset_code"],
            "building_id": row["building_id"], "building": row["building"],
            "status": row["status"], "criticality": row["criticality"],
            "health_score": row["health_score"], "condition_score": row["condition_score"],
            "condition_updated_at": _iso(row["condition_updated_at"]),
            "installation_date": _iso(row["installation_date"]),
            "warranty_expiry": _iso(row["warranty_expiry"]),
            "section_id": row["section_id"], "section": row["section"],
            "section_reference_eui": _num(row["section_reference"]),
            "vendor_id": row["vendor_id"], "vendor": row["vendor"],
            "vendor_code": row["vendor_code"],
        },
        "anomaly": anom,
        "value": var,
        "readings": readings,
        "failure_assessment": risk,
    }


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


async def _asset_anomaly(session: AsyncSession, asset_id: str) -> dict[str, Any]:
    """The open findings attributed to this asset, and how long the worst has run."""
    blank = {"open": 0, "weeks": None, "worst_pct": None, "annual_cost": None, "currency": None}
    try:
        async with session.begin_nested():
            a = (await session.execute(text("""
                SELECT count(*) AS n, max(metric_pct) AS worst,
                       sum(financial_gbp) AS cost, min(currency) AS ccy,
                       max(EXTRACT(EPOCH FROM (now() - detected_at)) / 604800.0) AS weeks
                  FROM plenum_cafm.energy_anomalies
                 WHERE asset_id::text = :aid
                   AND status NOT IN ('resolved','closed','dismissed')"""),
                {"aid": str(asset_id)})).mappings().first()
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_intel.anomaly_failed", error=str(exc)[:200])
        return blank
    if not a:
        return blank
    weeks = _num(a["weeks"])
    return {"open": int(a["n"] or 0), "weeks": round(weeks, 1) if weeks else None,
            "worst_pct": _num(a["worst"]), "annual_cost": _num(a["cost"]), "currency": a["ccy"]}


async def _asset_readings(
    session: AsyncSession, asset_id: str, bands: dict[str, dict],
) -> list[dict[str, Any]]:
    """The latest reading of each type, said to be in band, out of band, or ungraded.

    A reading with no band is ``unknown`` rather than in band. Nothing is graded against a
    limit that was never set.
    """
    try:
        async with session.begin_nested():
            rr = (await session.execute(text("""
                SELECT DISTINCT ON (reading_type) reading_type, value, unit, recorded_at
                  FROM plenum_cafm.asset_readings
                 WHERE asset_id::text = :aid
                 ORDER BY reading_type, recorded_at DESC"""),
                {"aid": str(asset_id)})).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_intel.readings_failed", error=str(exc)[:200])
        return []
    out = []
    for r in rr:
        band = bands.get(r["reading_type"], {})
        val, lo, hi = _num(r["value"]), band.get("lo"), band.get("hi")
        state = "unknown"
        if val is not None and (lo is not None or hi is not None):
            outside = (lo is not None and val < lo) or (hi is not None and val > hi)
            state = "out_of_band" if outside else "in_band"
        out.append({
            "reading_type": r["reading_type"], "value": val,
            "unit": r["unit"] or band.get("unit"), "recorded_at": _iso(r["recorded_at"]),
            "band_lo": lo, "band_hi": hi, "state": state, "note": band.get("note"),
        })
    return out


async def portfolio_value_at_risk(
    session: AsyncSession, *, building_ids: list[UUID] | None,
) -> dict[str, Any]:
    """The headline figure, and only the assets that actually contribute to it.

    An asset missing any of the three inputs is reported as not computable rather than
    quietly counted as worth nothing, because a total that silently includes zeros for
    unpriced assets is a smaller number that looks like a real one.
    """
    clause, params = _scope(building_ids, "a.building_id")
    sql = f"""
        SELECT a.id::text AS id, a.asset_name, a.building_id::text AS building_id,
               a.installation_date, a.replacement_value, a.design_life_years,
               a.wear_coefficient, a.replacement_currency,
               (SELECT max(metric_pct) FROM plenum_cafm.energy_anomalies e
                 WHERE e.asset_id::text = a.id::text
                   AND e.status NOT IN ('resolved','closed','dismissed')) AS worst_pct
          FROM plenum_cafm.assets a
         WHERE a.building_id IS NOT NULL{clause}"""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_intel.portfolio_failed", error=str(exc)[:300])
        return {"ok": False, "error": str(exc)[:200]}

    total, computable, not_computable = 0.0, 0, 0
    contributing: list[dict[str, Any]] = []
    currency = None
    for r in rows:
        v = value_at_risk(
            replacement_value=r["replacement_value"], design_life_years=r["design_life_years"],
            installation_date=r["installation_date"], wear_coefficient=r["wear_coefficient"],
            deviation_pct=_num(r["worst_pct"]),
        )
        if v["value_at_risk"] is None:
            not_computable += 1
            continue
        computable += 1
        currency = currency or r["replacement_currency"]
        if v["value_at_risk"] > 0:
            total += v["value_at_risk"]
            contributing.append({
                "asset_id": r["id"], "asset_name": r["asset_name"],
                "building_id": r["building_id"], "value_at_risk": v["value_at_risk"],
                "deviation_pct": v["deviation_pct"],
                "design_life_used_pct": v["design_life_used_pct"],
            })
    contributing.sort(key=lambda a: a["value_at_risk"], reverse=True)
    return {
        "ok": True,
        "value_at_risk": round(total, 2),
        "currency": currency,
        "assets_counted": computable,
        "assets_not_computable": not_computable,
        "assets_contributing": len(contributing),
        "note": (
            "counts only assets carrying a replacement value, a design life and an install "
            "date; the rest are reported as not computable rather than treated as worthless"
        ),
        "assets": contributing[:100],
    }


# ── what has been done to this asset, and what was recommended ───────────────────────

async def asset_notes(
    session: AsyncSession, *, asset_id: str, building_ids: list[UUID] | None,
    limit: int = 20,
) -> dict[str, Any]:
    """The work orders on one asset with the report each produced, and what is still owed.

    This is the block under an asset on the Assets page: an order, its date and vendor, the
    grade the inspector gave, what they wrote, and — the part that matters — whether the
    recommendation they left ever became anything. A recommendation with no order after it is
    the reason the block is worth showing at all.

    Warranty is answered from two places and they are different claims. The asset itself may
    still be under warranty, and a *part fitted to it* may be under its own term long after
    the asset's has run out — a contactor fitted last month on a chiller installed in 2009.
    Both are returned, labelled, and neither is inferred from the other.
    """
    clause, params = _scope(building_ids, "a.building_id")
    params["aid"] = str(asset_id)

    try:
        async with session.begin_nested():
            owns = (await session.execute(text(f"""
                SELECT a.id::text AS id, a.asset_name, a.warranty_expiry,
                       a.building_id::text AS building_id
                  FROM plenum_cafm.assets a
                 WHERE a.id::text = :aid{clause} LIMIT 1"""), params)).mappings().first()
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_intel.notes_scope_failed", error=str(exc)[:200])
        return {"ok": False, "reason": "query_failed"}
    if owns is None:
        return {"ok": False, "reason": "not_found_or_not_in_scope", "asset_id": str(asset_id)}

    orders = await _notes_rows(session, """
        SELECT w.id::text AS work_order_id,
               coalesce(w.wo_code, w.id::text) AS wo_code,
               w.status, w.created_at, w.closed_at, w.completed_at,
               coalesce(v.vendor_name, w.assigned_vendor::text) AS vendor,
               w.title, w.issue_description
          FROM plenum_cafm.work_orders w
          LEFT JOIN plenum_cafm.vendors v ON v.id::text = w.assigned_vendor::text
         WHERE w.asset_id::text = :aid
         ORDER BY coalesce(w.completed_at, w.closed_at, w.created_at) DESC NULLS LAST
         LIMIT :lim""", {"aid": str(asset_id), "lim": int(limit)}, "work_orders")

    reports = await _notes_rows(session, """
        SELECT i.id::text AS id, i.inspection_date, i.inspector, i.risk_level,
               i.finding_type, i.observations, i.recommendation,
               i.corrective_action, i.converted_work_order_id, i.work_order_id
          FROM plenum_cafm.inspections i
         WHERE i.asset_id::text = :aid
         ORDER BY i.inspection_date DESC NULLS LAST
         LIMIT :lim""", {"aid": str(asset_id), "lim": int(limit)}, "inspections")

    by_wo: dict[str, list[dict[str, Any]]] = {}
    loose: list[dict[str, Any]] = []
    for r in reports:
        key = str(r.get("work_order_id") or "")
        (by_wo.setdefault(key, []) if key else loose).append(r)

    def recommendation_of(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        for r in rows:
            text_ = (r.get("recommendation") or "").strip()
            if not text_ and not r.get("corrective_action"):
                continue
            done = r.get("converted_work_order_id") is not None
            return {
                "text": text_ or "corrective action required",
                # Open means nobody raised anything off it. That is the whole point of the
                # line: "recommendation open" is a thing somebody still owes.
                "state": "done" if done else "open",
                "became_work_order": r.get("converted_work_order_id"),
                "risk_level": r.get("risk_level"),
            }
        return None

    out_orders = []
    for w in orders:
        attached = by_wo.get(w["work_order_id"], []) + by_wo.get(w["wo_code"], [])
        when = w.get("completed_at") or w.get("closed_at") or w.get("created_at")
        out_orders.append({
            "work_order_id": w["work_order_id"], "wo_code": w["wo_code"],
            "date": _iso(when), "status": w.get("status"), "vendor": w.get("vendor"),
            "title": w.get("title") or w.get("issue_description"),
            "grade": next((r.get("risk_level") for r in attached if r.get("risk_level")), None),
            "notes": [r.get("observations") for r in attached if r.get("observations")],
            "recommendation": recommendation_of(attached),
            "report_on_file": bool(attached),
        })

    # Reports that name no order are still reports, and one carrying an open recommendation is
    # the most interesting row on the block — it is work nobody has raised.
    out_reports = [{
        "id": r["id"], "date": _iso(r.get("inspection_date")), "inspector": r.get("inspector"),
        "grade": r.get("risk_level"), "finding_type": r.get("finding_type"),
        "observations": r.get("observations"),
        "recommendation": recommendation_of([r]),
        "work_order": None,
    } for r in loose]

    warranty = await _warranty_for(session, asset_id, owns.get("warranty_expiry"))
    open_recs = sum(
        1 for x in out_orders + out_reports
        if (x.get("recommendation") or {}).get("state") == "open")

    return {
        "ok": True,
        "asset_id": owns["id"], "asset_name": owns["asset_name"],
        "work_orders": out_orders,
        "reports_without_an_order": out_reports,
        "recommendations_open": open_recs,
        "warranty": warranty,
        "note": ("a recommendation is open until an order is raised off it; asset warranty "
                 "and part warranty are separate claims and neither is inferred from the other"),
    }


async def _notes_rows(
    session: AsyncSession, sql: str, params: dict[str, Any], what: str,
) -> list[dict[str, Any]]:
    """One source in its own savepoint: a table shaped differently here must cost this block
    that source, not the whole asset."""
    try:
        async with session.begin_nested():
            return [dict(r) for r in
                    (await session.execute(text(sql), params)).mappings().all()]
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_intel.notes_failed", source=what, error=str(exc)[:220])
        return []


async def _warranty_for(
    session: AsyncSession, asset_id: str, asset_expiry: Any,
) -> dict[str, Any]:
    """What is still under warranty on this asset — the asset itself, and any part fitted.

    A part fitted last month can be under its own term long after the asset's has run out,
    which is exactly the case worth surfacing: the plant is out of warranty and the component
    that failed is not.
    """
    today = date.today()
    asset_when = asset_expiry.date() if isinstance(asset_expiry, datetime) else asset_expiry
    out: dict[str, Any] = {
        "asset": {
            "expires": _iso(asset_when),
            "in_warranty": bool(isinstance(asset_when, date) and asset_when >= today),
        },
        "parts": [],
        "claimable": False,
    }
    rows = await _notes_rows(session, """
        SELECT p.id::text AS id, sp.part_name, sp.part_code, p.fitted_at,
               coalesce(p.warranty_expiry,
                        (p.fitted_at + make_interval(months => sp.warranty_months))) AS expires,
               p.invoiced_value, p.currency
          FROM plenum_cafm.work_order_parts p
          LEFT JOIN plenum_cafm.spare_parts sp ON sp.id::text = p.part_id::text
         WHERE p.asset_id::text = :aid
         ORDER BY expires DESC NULLS LAST""", {"aid": str(asset_id)}, "warranty_parts")
    for r in rows:
        when = r.get("expires")
        when = when.date() if isinstance(when, datetime) else when
        live = isinstance(when, date) and when >= today
        out["parts"].append({
            "part_name": r.get("part_name"), "part_code": r.get("part_code"),
            "fitted_at": _iso(r.get("fitted_at")), "expires": _iso(when),
            "in_warranty": live,
            "invoiced_value": _num(r.get("invoiced_value")), "currency": r.get("currency"),
            # Claimable means the part is inside its term, so work on it should not be paid
            # for twice. It is not a claim, it is a prompt to check one.
            "claimable": live,
        })
    out["claimable"] = out["asset"]["in_warranty"] or any(p["claimable"] for p in out["parts"])
    return out
