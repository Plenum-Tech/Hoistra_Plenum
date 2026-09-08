"""Per-building rollups computed from the building graph.

The Buildings screen's Floors, Floor area and use split used to be columns somebody typed
on the building row. They are now derived from what is actually on record:

    Floors      count of plenum_cafm.floors for the building
    Floor area  sum of plenum_cafm.spaces.area_sqm
    Use split   spaces grouped by space_type, weighted by area

so the screen cannot disagree with the graph beneath it. A building with no floor or space
rows falls back to the recorded column, and the row says which of the two it used
(``floors_source`` / ``gfa_source`` / ``use_mix_source``) — a surveyed figure and a counted
one are different claims and are never silently mixed.

Key columns are introspected rather than assumed: this deployment keys on VARCHAR business
keys (``building_id`` = "B-001") while udr_phase1_schema.sql declares the same tables with a
UUID ``id``. Both shapes are read without configuration, the same approach
``compliance/site_links.py`` uses for sites.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

# table -> key columns in preference order. The first one the live table actually has wins.
_KEYS: dict[str, tuple[str, ...]] = {
    "buildings": ("building_id", "id"),
    "floors": ("floor_id", "id"),
    "spaces": ("space_id", "id"),
    "assets": ("asset_id", "id"),
    "equipment": ("equipment_id", "id"),
    "meters": ("meter_id", "id"),
    "work_orders": ("work_order_id", "id"),
    "documents": ("document_id", "id"),
    "compliance_certificates": ("certificate_id", "id"),
    "contracts": ("contract_id", "id"),
    "invoices": ("invoice_id", "id"),
}

_SHAPE_CACHE: dict[str, dict[str, Any]] | None = None


async def graph_shape(session: AsyncSession, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Which of the graph's tables exist, and what each one's key column is called.

    Cached per process — a deployment property, not a request one. A table that is absent
    simply reports ``exists: False`` and every rollup that needs it contributes zero, so a
    partially migrated database still renders a table instead of raising.
    """
    global _SHAPE_CACHE
    if _SHAPE_CACHE is not None and not refresh:
        return _SHAPE_CACHE
    shape: dict[str, dict[str, Any]] = {t: {"exists": False, "key": None, "columns": set()} for t in _KEYS}
    try:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm'
                    """
                )
            )
        ).all()
    except Exception as exc:  # noqa: BLE001 — introspection must never break a caller
        log.warning("building_graph.shape_failed", error=str(exc)[:200])
        return shape
    cols: dict[str, set[str]] = {}
    for table, column in rows:
        cols.setdefault(str(table), set()).add(str(column))
    for table, candidates in _KEYS.items():
        have = cols.get(table)
        if not have:
            continue
        key = next((c for c in candidates if c in have), None)
        shape[table] = {"exists": bool(key), "key": key, "columns": have}
    _SHAPE_CACHE = shape
    return shape


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ── pure shaping ──────────────────────────────────────────────────────────────────────


def use_mix_from_area(by_type: dict[str, float]) -> list[dict[str, Any]]:
    """{"Commercial": 380.0, "Retail": 33.0} → [{"use": "Commercial", "pct": 92.0}, …].

    Largest share first, percentages of the total area, rounded to one decimal. Types with
    no area contribute nothing — a space with a type but no measurement cannot be given a
    share of the floor plate without inventing one.
    """
    total = sum(v for v in by_type.values() if v and v > 0)
    if total <= 0:
        return []
    out = [
        {"use": str(k), "pct": round(100.0 * v / total, 1)}
        for k, v in by_type.items()
        if v and v > 0
    ]
    out.sort(key=lambda d: (-d["pct"], d["use"]))
    return out


def dominant_use(mix: list[dict[str, Any]], *, mixed_below_pct: float = 80.0) -> str | None:
    """The use to print above the split. A building whose largest use is under
    ``mixed_below_pct`` of its area is "Mixed" — that is what mixed use means, and calling
    a 46% residential tower "Residential" would overstate it."""
    if not mix:
        return None
    top = mix[0]
    if len(mix) == 1 or top["pct"] >= mixed_below_pct:
        return str(top["use"])
    return "Mixed"


def merge_figure(counted: Any, recorded: Any, *, counted_source: str, recorded_source: str) -> tuple[Any, str | None]:
    """Prefer what the graph counted; fall back to what was recorded. Returns (value, source)."""
    if counted is not None and (not isinstance(counted, (int, float)) or counted > 0):
        return counted, counted_source
    if recorded is not None:
        return recorded, recorded_source
    return None, None


# ── DB rollups ────────────────────────────────────────────────────────────────────────


async def _scalar_counts(session: AsyncSession, sql: str) -> dict[str, int]:
    """Run a `SELECT <building key>, count(*)` and return it as a dict, or {} on failure."""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql))).all()
        return {str(k): int(n) for k, n in rows if k is not None}
    except Exception as exc:  # noqa: BLE001
        log.warning("building_graph.count_failed", error=str(exc)[:200], sql=sql[:120])
        return {}


async def spaces_rollup(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Per building: total area, space count, and area by space_type.

    A space reaches its building directly (``spaces.building_id``) or through its floor,
    so both are tried and the direct link wins.
    """
    shape = await graph_shape(session)
    if not shape["spaces"]["exists"]:
        return {}
    floor_key = shape["floors"]["key"] if shape["floors"]["exists"] else None
    join = (
        f"LEFT JOIN plenum_cafm.floors f ON f.{floor_key}::text = s.floor_id::text"
        if floor_key
        else ""
    )
    bid = "COALESCE(s.building_id::text, f.building_id::text)" if floor_key else "s.building_id::text"
    sql = f"""
        SELECT {bid} AS bid,
               COALESCE(NULLIF(TRIM(s.space_type), ''), 'Unclassified') AS use_type,
               SUM(COALESCE(s.area_sqm, 0)) AS area,
               COUNT(*) AS n
        FROM plenum_cafm.spaces s
        {join}
        GROUP BY 1, 2
    """
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql))).all()
    except Exception as exc:  # noqa: BLE001
        log.warning("building_graph.spaces_rollup_failed", error=str(exc)[:200])
        return {}
    out: dict[str, dict[str, Any]] = {}
    for bid_v, use_type, area, n in rows:
        if bid_v is None:
            continue
        b = out.setdefault(str(bid_v), {"area": 0.0, "spaces": 0, "by_type": {}})
        a = _num(area) or 0.0
        b["area"] += a
        b["spaces"] += int(n or 0)
        if a > 0:
            b["by_type"][str(use_type)] = b["by_type"].get(str(use_type), 0.0) + a
    return out


async def child_counts(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Every relation in the graph, counted per building.

    Counts follow the FK the graph names. Where a table reaches the building through a
    parent (equipment through its asset, certificates through their document), the join is
    made explicitly so the number means what the graph says it means.
    """
    shape = await graph_shape(session)
    result: dict[str, dict[str, int]] = {}

    def add(relation: str, counts: dict[str, int]) -> None:
        for bid, n in counts.items():
            result.setdefault(bid, {})[relation] = n

    if shape["floors"]["exists"]:
        add("floors", await _scalar_counts(
            session,
            "SELECT building_id::text, count(*) FROM plenum_cafm.floors GROUP BY 1",
        ))
    if shape["assets"]["exists"]:
        add("assets", await _scalar_counts(
            session,
            "SELECT building_id::text, count(*) FROM plenum_cafm.assets GROUP BY 1",
        ))
        akey = shape["assets"]["key"]
        if shape["equipment"]["exists"]:
            add("equipment", await _scalar_counts(
                session,
                f"""SELECT a.building_id::text, count(*) FROM plenum_cafm.equipment e
                    JOIN plenum_cafm.assets a ON a.{akey}::text = e.asset_id::text
                    GROUP BY 1""",
            ))
        if shape["meters"]["exists"]:
            add("meters", await _scalar_counts(
                session,
                f"""SELECT COALESCE(m.building_id::text, a.building_id::text), count(*)
                    FROM plenum_cafm.meters m
                    LEFT JOIN plenum_cafm.assets a ON a.{akey}::text = m.asset_id::text
                    GROUP BY 1""",
            ))
    if shape["work_orders"]["exists"]:
        add("work_orders", await _scalar_counts(
            session,
            "SELECT building_id::text, count(*) FROM plenum_cafm.work_orders GROUP BY 1",
        ))
    if shape["documents"]["exists"]:
        add("documents", await _scalar_counts(
            session,
            "SELECT building_id::text, count(*) FROM plenum_cafm.documents GROUP BY 1",
        ))
        dkey = shape["documents"]["key"]
        if shape["compliance_certificates"]["exists"]:
            add("certificates", await _scalar_counts(
                session,
                f"""SELECT COALESCE(c.building_id::text, d.building_id::text), count(*)
                    FROM plenum_cafm.compliance_certificates c
                    LEFT JOIN plenum_cafm.documents d
                           ON d.{dkey}::text = COALESCE(c.document_id::text, c.source_document_id::text)
                    GROUP BY 1""",
            ))
    if shape["contracts"]["exists"]:
        add("contracts", await _scalar_counts(
            session,
            "SELECT building_id::text, count(*) FROM plenum_cafm.contracts GROUP BY 1",
        ))
        ckey = shape["contracts"]["key"]
        if shape["invoices"]["exists"]:
            add("invoices", await _scalar_counts(
                session,
                f"""SELECT COALESCE(i.building_id::text, c.building_id::text), count(*)
                    FROM plenum_cafm.invoices i
                    LEFT JOIN plenum_cafm.contracts c ON c.{ckey}::text = i.contract_id::text
                    GROUP BY 1""",
            ))
    return result


async def load_buildings(session: AsyncSession, *, limit: int = 1000) -> list[dict[str, Any]]:
    """Every building row, keyed on whatever key column the table actually has."""
    shape = await graph_shape(session)
    if not shape["buildings"]["exists"]:
        return []
    key = shape["buildings"]["key"]
    have = shape["buildings"]["columns"]
    wanted = [
        "site_id", "building_code", "name", "building_name", "country", "country_code",
        "state", "city", "postcode", "use_type", "status", "floors_recorded",
        "gfa_sqm_recorded", "eui_kwh_per_m2", "benchmark_kwh_per_m2", "benchmark_standard",
        "benchmark_standing", "benchmark_standing_note", "metering_route",
        "metering_granularity", "hoist_score",
    ]
    cols = [f"{key}::text AS building_id"] + [f"{c}::text AS {c}" for c in wanted if c in have]
    order = "name" if "name" in have else ("building_name" if "building_name" in have else key)
    sql = (
        f"SELECT {', '.join(cols)} FROM plenum_cafm.buildings "
        f"ORDER BY {order} NULLS LAST LIMIT {int(limit)}"
    )
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql))).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("building_graph.load_buildings_failed", error=str(exc)[:200])
        return []
    return [{k: (v.strip() if isinstance(v, str) else v) for k, v in dict(r).items()} for r in rows]


async def rollups(session: AsyncSession, *, limit: int = 1000) -> dict[str, dict[str, Any]]:
    """building_id → {floors, spaces, gfa_sqm, use_mix, counts{}} computed from the graph."""
    shape = await graph_shape(session)
    counts = await child_counts(session)
    sp = await spaces_rollup(session)
    out: dict[str, dict[str, Any]] = {}
    for bid in set(counts) | set(sp):
        c = counts.get(bid, {})
        s = sp.get(bid, {})
        mix = use_mix_from_area(s.get("by_type", {}))
        out[bid] = {
            "floors": c.get("floors"),
            "spaces": s.get("spaces"),
            "gfa_sqm": round(s["area"], 2) if s.get("area") else None,
            "use_mix": mix,
            "dominant_use": dominant_use(mix),
            "counts": c,
        }
    out["_shape"] = {t: v["key"] for t, v in shape.items() if v["exists"]}
    return out
