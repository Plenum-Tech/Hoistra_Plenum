"""The condition engine: two signals, a stated rule, a band, and the sentence that explains it.

The Assets page infers condition from energy before a fault shows. It reads two signals that
both already exist — whether an asset's section is running over *its own* reference, and
whether an anomaly is attributed to the asset — and bands every asset from them:

* **Threat** — the section is over reference **and** an anomaly is attributed to this asset.
  Both signals agree, so the asset is named rather than the zone.
* **Watch** — one signal alone. Either the section is over but nothing is attributed to this
  asset, which means it shares the load and an inspection settles whether it contributes; or
  an anomaly attributed to it has persisted past the threshold while its section is fine.
* **In control** — neither. Counted separately are the assets that do carry an anomaly which
  has not persisted long enough to count, because "nothing found" and "found something too
  small to act on" are different answers and the second one is the interesting one.

Two design decisions worth stating.

**The thresholds are data, not constants.** They are two steppers on the page, and a band
decided under one pair means nothing once somebody moves them — so a verdict records the
thresholds it was reached under, and so does every run.

**Reads are computed live; runs are recorded.** The summary and the per-building rollup assess
from current signals every time, so the page is never showing a stale band. The run table
exists for the "last run" stamp and so a band can be compared with what it was last week — not
as the source the page reads from. A scan that has never run therefore does not mean an empty
page; it means no history yet.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import asset_intelligence as ai

log = get_logger(__name__)

#: What the page ships with, and what an organisation that has never touched the steppers
#: is scanned against. Held here as well as in the table's DEFAULT so the engine can answer
#: without a row existing.
DEFAULT_SECTION_OVER_PCT = 10.0
DEFAULT_ANOMALY_WEEKS = 3.0

#: The rule that produced a band, named in every verdict it writes.
METHOD = "rule:section-over-reference+anomaly-attributed/v1"

BAND_THREAT = "threat"
BAND_WATCH = "watch"
BAND_IN_CONTROL = "in_control"

#: Machine-readable reasons. The page renders its own words; these are what it renders from.
R_SECTION_OVER = "section_over_reference"
R_ANOMALY_HERE = "anomaly_attributed"
R_ANOMALY_PERSISTENT = "anomaly_persistent"
R_SHARES_LOAD = "shares_section_load"
R_ANOMALY_UNDER = "anomaly_under_threshold"


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


# ── the rule ─────────────────────────────────────────────────────────────────────────

async def rules(session: AsyncSession, *, organization_id: UUID | None) -> dict[str, Any]:
    """The thresholds this organisation bands against, defaulted if never set."""
    out = {
        "section_over_reference_pct": DEFAULT_SECTION_OVER_PCT,
        "anomaly_persistent_weeks": DEFAULT_ANOMALY_WEEKS,
        "is_default": True,
        "updated_at": None,
    }
    if organization_id is None:
        return out
    try:
        async with session.begin_nested():
            row = (await session.execute(text("""
                SELECT section_over_reference_pct, anomaly_persistent_weeks, updated_at
                  FROM plenum_cafm.asset_condition_rules
                 WHERE organization_id = :org"""), {"org": str(organization_id)})).mappings().first()
    except Exception as exc:  # noqa: BLE001
        log.warning("condition.rules_failed", error=str(exc)[:200])
        return out
    if row:
        out.update({
            "section_over_reference_pct": _num(row["section_over_reference_pct"]),
            "anomaly_persistent_weeks": _num(row["anomaly_persistent_weeks"]),
            "is_default": False,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        })
    return out


async def set_rules(
    session: AsyncSession, *, organization_id: UUID, section_over_reference_pct: float,
    anomaly_persistent_weeks: float, updated_by: UUID | None = None,
) -> dict[str, Any]:
    """Move the steppers. Refuses values that would make the rule meaningless."""
    if not 0 <= section_over_reference_pct <= 500:
        return {"ok": False, "error": "section_over_reference_pct must be between 0 and 500"}
    if not 0 <= anomaly_persistent_weeks <= 520:
        return {"ok": False, "error": "anomaly_persistent_weeks must be between 0 and 520"}
    try:
        async with session.begin_nested():
            await session.execute(text("""
                INSERT INTO plenum_cafm.asset_condition_rules
                    (organization_id, section_over_reference_pct, anomaly_persistent_weeks,
                     updated_at, updated_by)
                VALUES (:org, :pct, :wks, now(), :by)
                ON CONFLICT (organization_id) DO UPDATE
                   SET section_over_reference_pct = EXCLUDED.section_over_reference_pct,
                       anomaly_persistent_weeks = EXCLUDED.anomaly_persistent_weeks,
                       updated_at = now(), updated_by = EXCLUDED.updated_by"""),
                {"org": str(organization_id), "pct": section_over_reference_pct,
                 "wks": anomaly_persistent_weeks,
                 "by": str(updated_by) if updated_by else None})
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("condition.set_rules_failed", error=str(exc)[:300])
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, **await rules(session, organization_id=organization_id)}


# ── the banding ──────────────────────────────────────────────────────────────────────

def classify(
    *, section_deviation_pct: float | None, anomalies_open: int, anomaly_weeks: float | None,
    section_over_pct: float, persistent_weeks: float,
) -> dict[str, Any]:
    """Band one asset from its two signals, and say why in the page's own terms.

    Pure: no session, no clock. Everything it decides from is an argument, which is what makes
    the rule testable and what lets the same function serve a live read and a stored scan.
    """
    dev = _num(section_deviation_pct)
    section_over = dev is not None and dev > section_over_pct
    has_anomaly = anomalies_open > 0
    weeks = _num(anomaly_weeks)
    persistent = has_anomaly and weeks is not None and weeks >= persistent_weeks

    reasons: list[str] = []
    if section_over:
        reasons.append(R_SECTION_OVER)
    if has_anomaly:
        reasons.append(R_ANOMALY_HERE)
    if persistent:
        reasons.append(R_ANOMALY_PERSISTENT)

    if section_over and has_anomaly:
        band = BAND_THREAT
        explanation = (
            "Section over reference and an anomaly attributed to this asset. Both signals "
            "agree; a work order is justified without waiting for a fault.")
    elif section_over:
        band = BAND_WATCH
        reasons.append(R_SHARES_LOAD)
        explanation = (
            "The section is over reference but no anomaly is attributed to this asset. It "
            "shares the load; an inspection settles whether it contributes.")
    elif persistent:
        band = BAND_WATCH
        explanation = (
            f"An anomaly attributed to this asset has been open {weeks:g} weeks, past the "
            f"{persistent_weeks:g}-week threshold, while its section is within reference.")
    elif has_anomaly:
        band = BAND_IN_CONTROL
        reasons.append(R_ANOMALY_UNDER)
        explanation = (
            f"An anomaly is attributed to this asset but has not persisted past "
            f"{persistent_weeks:g} weeks, and its section is within reference.")
    else:
        band = BAND_IN_CONTROL
        explanation = (
            "Its section is within reference and no anomaly is attributed to it."
            if dev is not None else
            "No anomaly is attributed to it. Its section is not metered, so the section "
            "signal could not be read.")
    return {
        "band": band,
        "reasons": reasons,
        "explanation": explanation,
        "section_deviation_pct": dev,
        "section_over_reference": section_over,
        "anomalies_open": anomalies_open,
        "anomaly_weeks": weeks,
        "anomaly_persistent": persistent,
        "section_measured": dev is not None,
    }


# ── the signals ──────────────────────────────────────────────────────────────────────

async def _signals(
    session: AsyncSession, *, building_ids: list[UUID] | None,
) -> list[dict[str, Any]]:
    """Every asset in scope with its section's deviation and its own open anomalies.

    The section deviation is not recomputed here. It comes from ``asset_intelligence.sections``
    — the same function that fills the section headers on the page — because the moment the
    asset row and the section header compute the same figure two different ways, they disagree
    and neither can be trusted. There is one definition of "over reference" and this reads it.
    """
    sect = await ai.sections(session, building_ids=building_ids)
    by_section: dict[str, dict[str, Any]] = {
        x["section_id"]: x for x in (sect.get("sections") or []) if x.get("section_id")}

    clause, params = _scope(building_ids, "a.building_id")
    sql = f"""
        SELECT a.id::text AS asset_id, a.asset_name, a.asset_code, a.criticality,
               a.building_id::text AS building_id, b.name AS building,
               a.section_id::text AS section_id,
               v.vendor_name AS vendor,
               (SELECT count(*) FROM plenum_cafm.energy_anomalies e
                 WHERE e.asset_id::text = a.id::text
                   AND e.status NOT IN ('resolved','closed','dismissed')) AS anomalies_open,
               (SELECT max(EXTRACT(EPOCH FROM (now() - e.detected_at)) / 604800.0)
                  FROM plenum_cafm.energy_anomalies e
                 WHERE e.asset_id::text = a.id::text
                   AND e.status NOT IN ('resolved','closed','dismissed')) AS weeks,
               (SELECT sum(e.financial_gbp) FROM plenum_cafm.energy_anomalies e
                 WHERE e.asset_id::text = a.id::text
                   AND e.status NOT IN ('resolved','closed','dismissed')) AS anomaly_cost,
               (SELECT min(e.currency) FROM plenum_cafm.energy_anomalies e
                 WHERE e.asset_id::text = a.id::text
                   AND e.status NOT IN ('resolved','closed','dismissed')) AS currency
          FROM plenum_cafm.assets a
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
          LEFT JOIN plenum_cafm.vendors v ON v.id::text = a.vendor_id::text
         WHERE a.building_id IS NOT NULL{clause}
         ORDER BY a.asset_name"""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("condition.signals_failed", error=str(exc)[:300])
        return []

    out = []
    for r in rows:
        sec = by_section.get(r["section_id"]) or {}
        out.append({
            **dict(r),
            "deviation_pct": sec.get("deviation_pct"),
            "eui": sec.get("eui_kwh_per_m2"),
            "reference_eui_kwh_m2": sec.get("reference_eui_kwh_m2"),
            "section_name": sec.get("name"),
        })
    return out


async def assess(
    session: AsyncSession, *, building_ids: list[UUID] | None,
    organization_id: UUID | None,
) -> dict[str, Any]:
    """Every asset in scope, banded. Computed now; nothing written."""
    rule = await rules(session, organization_id=organization_id)
    pct = rule["section_over_reference_pct"]
    wks = rule["anomaly_persistent_weeks"]
    rows = await _signals(session, building_ids=building_ids)

    out = []
    for r in rows:
        verdict = classify(
            section_deviation_pct=r["deviation_pct"], anomalies_open=int(r["anomalies_open"] or 0),
            anomaly_weeks=r["weeks"], section_over_pct=pct, persistent_weeks=wks)
        out.append({
            "asset_id": r["asset_id"], "asset_name": r["asset_name"],
            "asset_code": r["asset_code"], "criticality": r["criticality"],
            "building_id": r["building_id"], "building": r["building"],
            "section_id": r["section_id"], "section": r["section_name"],
            "vendor": r["vendor"],
            "section_eui": _num(r["eui"]), "section_reference": _num(r["reference_eui_kwh_m2"]),
            "anomaly_annual_cost": _num(r["anomaly_cost"]), "currency": r["currency"],
            **verdict,
        })
    order = {BAND_THREAT: 0, BAND_WATCH: 1, BAND_IN_CONTROL: 2}
    out.sort(key=lambda a: (order[a["band"]], -(a["section_deviation_pct"] or 0),
                            a["asset_name"] or ""))
    return {"rules": rule, "method": METHOD, "assets": out}


# ── what the page reads ──────────────────────────────────────────────────────────────

def _tally(assets: list[dict[str, Any]]) -> dict[str, Any]:
    """The four KPI cards, with the sub-counts each one prints underneath."""
    threat = [a for a in assets if a["band"] == BAND_THREAT]
    watch = [a for a in assets if a["band"] == BAND_WATCH]
    control = [a for a in assets if a["band"] == BAND_IN_CONTROL]
    return {
        "assets": len(assets),
        "threat": len(threat),
        "watch": len(watch),
        "in_control": len(control),
        # "6 shared section · 1 persistent anomaly"
        "watch_shares_section": sum(1 for a in watch if R_SHARES_LOAD in a["reasons"]),
        "watch_persistent_anomaly": sum(
            1 for a in watch if R_ANOMALY_PERSISTENT in a["reasons"]),
        # "3 with an anomaly under threshold"
        "in_control_anomaly_under_threshold": sum(
            1 for a in control if R_ANOMALY_UNDER in a["reasons"]),
        # An asset whose section has no sub-meter was banded on one signal, not two. Said
        # out loud, because a page that calls it "in control" is overstating what was checked.
        "section_not_measured": sum(1 for a in assets if not a["section_measured"]),
    }


async def summary(
    session: AsyncSession, *, building_ids: list[UUID] | None, organization_id: UUID | None,
) -> dict[str, Any]:
    """The KPI cards, the per-building rollup, and when the scan last ran."""
    a = await assess(session, building_ids=building_ids, organization_id=organization_id)
    assets = a["assets"]

    buildings: dict[str, dict[str, Any]] = {}
    for x in assets:
        b = buildings.setdefault(x["building_id"] or "unplaced", {
            "building_id": x["building_id"], "building": x["building"],
            "threat": 0, "watch": 0, "in_control": 0, "assets": 0,
            "worst_deviation_pct": None})
        b["assets"] += 1
        b[x["band"]] += 1
        d = x["section_deviation_pct"]
        if d is not None and (b["worst_deviation_pct"] is None or d > b["worst_deviation_pct"]):
            b["worst_deviation_pct"] = round(d, 1)
    ranked = sorted(buildings.values(),
                    key=lambda b: (-(b["worst_deviation_pct"] if b["worst_deviation_pct"]
                                     is not None else -9999), b["building"] or ""))

    sections: dict[str, dict[str, Any]] = {}
    for x in assets:
        if not x["section_id"]:
            continue
        s = sections.setdefault(x["section_id"], {
            "section_id": x["section_id"], "section": x["section"],
            "building_id": x["building_id"], "building": x["building"],
            "eui_kwh_per_m2": x["section_eui"], "reference_eui_kwh_m2": x["section_reference"],
            "deviation_pct": round(x["section_deviation_pct"], 1)
            if x["section_deviation_pct"] is not None else None,
            "over_reference": x["section_over_reference"],
            "threat": 0, "watch": 0, "in_control": 0, "assets": 0})
        s["assets"] += 1
        s[x["band"]] += 1

    return {
        "ok": True,
        "rules": a["rules"],
        "method": a["method"],
        "summary": _tally(assets),
        "buildings": ranked,
        "sections": sorted(sections.values(),
                           key=lambda s: -(s["deviation_pct"] or -9999)),
        "last_run": await last_run(session, organization_id=organization_id),
        "note": (
            "bands are computed from current signals on every read, so they are never stale; "
            "the last run is when a scan was last recorded, not when these were decided"
        ),
    }


async def last_run(
    session: AsyncSession, *, organization_id: UUID | None,
) -> dict[str, Any] | None:
    """When the scan last ran, and what it found. Null before the first scan."""
    where = "WHERE organization_id = :org" if organization_id else ""
    params = {"org": str(organization_id)} if organization_id else {}
    try:
        async with session.begin_nested():
            row = (await session.execute(text(f"""
                SELECT run_id::text, started_at, finished_at, assets_scanned, threat, watch,
                       in_control, buildings, scope, section_over_reference_pct,
                       anomaly_persistent_weeks, error
                  FROM plenum_cafm.asset_condition_runs
                  {where}
                 ORDER BY started_at DESC LIMIT 1"""), params)).mappings().first()
    except Exception as exc:  # noqa: BLE001
        log.warning("condition.last_run_failed", error=str(exc)[:200])
        return None
    if not row:
        return None
    out = dict(row)
    for k in ("started_at", "finished_at"):
        out[k] = out[k].isoformat() if out[k] else None
    return out


# ── the scan ─────────────────────────────────────────────────────────────────────────

async def scan(
    session: AsyncSession, *, building_ids: list[UUID] | None, organization_id: UUID | None,
) -> dict[str, Any]:
    """Band every asset in scope and record the run and its verdicts.

    The reads do not depend on this having happened — they assess live. What a scan adds is a
    timestamp somebody can point at and a row per asset that can be compared with next week's.
    """
    started = datetime.now(timezone.utc)
    a = await assess(session, building_ids=building_ids, organization_id=organization_id)
    assets, rule = a["assets"], a["rules"]
    tally = _tally(assets)
    run_id = uuid4()
    scope = "portfolio" if building_ids is None else f"{len(building_ids)} building(s)"

    try:
        async with session.begin_nested():
            await session.execute(text("""
                INSERT INTO plenum_cafm.asset_condition_runs
                    (run_id, organization_id, started_at, finished_at, assets_scanned,
                     threat, watch, in_control, buildings,
                     section_over_reference_pct, anomaly_persistent_weeks, scope)
                VALUES (:rid, :org, :started, now(), :n, :t, :w, :c, :b, :pct, :wks, :scope)"""),
                {"rid": str(run_id), "org": str(organization_id) if organization_id else None,
                 "started": started, "n": tally["assets"], "t": tally["threat"],
                 "w": tally["watch"], "c": tally["in_control"],
                 "b": len({x["building_id"] for x in assets}),
                 "pct": rule["section_over_reference_pct"],
                 "wks": rule["anomaly_persistent_weeks"], "scope": scope})

            for x in assets:
                await session.execute(text("""
                    INSERT INTO plenum_cafm.asset_condition_verdicts
                        (run_id, organization_id, asset_id, building_id, section_id, band,
                         reasons, section_deviation_pct, section_over_reference,
                         anomalies_open, anomaly_weeks, anomaly_persistent,
                         anomaly_annual_cost, currency,
                         section_over_reference_pct, anomaly_persistent_weeks)
                    VALUES (:rid, :org, :aid, CAST(:bid AS uuid), CAST(:sid AS uuid), :band,
                            CAST(:reasons AS jsonb), :dev, :over, :n, :wk, :pers,
                            :cost, :ccy, :pct, :wks)"""),
                    {"rid": str(run_id),
                     "org": str(organization_id) if organization_id else None,
                     "aid": x["asset_id"], "bid": x["building_id"], "sid": x["section_id"],
                     "band": x["band"], "reasons": json.dumps(x["reasons"]),
                     "dev": x["section_deviation_pct"], "over": x["section_over_reference"],
                     "n": x["anomalies_open"], "wk": x["anomaly_weeks"],
                     "pers": x["anomaly_persistent"], "cost": x["anomaly_annual_cost"],
                     "ccy": x["currency"], "pct": rule["section_over_reference_pct"],
                     "wks": rule["anomaly_persistent_weeks"]})
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("condition.scan_write_failed", error=str(exc)[:300])
        return {"ok": False, "error": str(exc)[:200], "summary": tally,
                "note": "the scan computed but could not be recorded"}

    log.info("condition.scan", run_id=str(run_id), **{k: v for k, v in tally.items()
                                                      if isinstance(v, int)})
    return {"ok": True, "run_id": str(run_id), "rules": rule, "method": METHOD,
            "summary": tally, "scope": scope,
            "started_at": started.isoformat()}
