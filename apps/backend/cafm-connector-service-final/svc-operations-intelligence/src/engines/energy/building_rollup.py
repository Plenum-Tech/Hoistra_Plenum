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
    "portfolios": ("portfolio_id", "id"),
    "locations": ("location_id", "id"),
    "regulation_packs": ("pack_id", "id"),
    "sites": ("site_id", "id"),
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


#: Square feet in one square metre. The canonical schema records gross_area_sqft; every
#: benchmark is kWh/m², so the two are never compared without this.
SQFT_PER_SQM = 10.763910416709722


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
    # The canonical column is gross_area_sqft; area_sqm is the older spelling. Everything
    # downstream is metric — every benchmark on the platform is kWh/m² — so a square-foot
    # column is converted here rather than travelling on mislabelled as metres.
    sqft = "gross_area_sqft" in shape["spaces"]["columns"]
    area_col = "gross_area_sqft" if sqft else "area_sqm"
    sql = f"""
        SELECT {bid} AS bid,
               COALESCE(NULLIF(TRIM(s.space_type), ''), 'Unclassified') AS use_type,
               SUM(COALESCE(s.{area_col}, 0)) AS area,
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
        b = out.setdefault(str(bid_v), {"area": 0.0, "spaces": 0, "by_type": {}, "area_unit": "m2"})
        a = _num(area) or 0.0
        if sqft:
            a = a / SQFT_PER_SQM
        b["area"] += a
        b["spaces"] += int(n or 0)
        if a > 0:
            b["by_type"][str(use_type)] = b["by_type"].get(str(use_type), 0.0) + a
    return out


def invoices_count_sql(shape: dict[str, Any]) -> str | None:
    """SQL to count invoices per building, or None where they cannot reach one.

    Three shapes are possible and all three occur:

      building_id on the view    count it directly — this is what the canonical schema
                                 gives, since the view resolves the building through the
                                 document the invoice was read from
      contract_id only           reach the building through the contract
      both                       prefer the direct link, fall back to the contract
      neither                    an invoice here cannot be tied to a building at all, and
                                 saying so by returning None is the honest answer: the
                                 count then reads "?" rather than "0"

    Pure string-building over introspected column names, so it can be tested without a
    database — which is the point, because the version that assumed contract_id could only
    have been caught by running it against a schema that lacks one.
    """
    info = shape.get("invoices") or {}
    if not info.get("exists"):
        return None
    cols: set[str] = info.get("columns") or set()
    direct = "building_id" in cols
    via_contract = (
        "contract_id" in cols
        and (shape.get("contracts") or {}).get("exists")
        and "building_id" in ((shape.get("contracts") or {}).get("columns") or set())
    )
    if direct and via_contract:
        ckey = shape["contracts"]["key"]
        return (f"SELECT COALESCE(i.building_id::text, c.building_id::text), count(*)\n"
                f"  FROM plenum_cafm.invoices i\n"
                f"  LEFT JOIN plenum_cafm.contracts c "
                f"ON c.{ckey}::text = i.contract_id::text\n"
                f" GROUP BY 1")
    if direct:
        return ("SELECT i.building_id::text, count(*)\n"
                "  FROM plenum_cafm.invoices i\n"
                " GROUP BY 1")
    if via_contract:
        ckey = shape["contracts"]["key"]
        return (f"SELECT c.building_id::text, count(*)\n"
                f"  FROM plenum_cafm.invoices i\n"
                f"  JOIN plenum_cafm.contracts c "
                f"ON c.{ckey}::text = i.contract_id::text\n"
                f" GROUP BY 1")
    return None


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
        if shape["equipment"]["exists"] and "asset_id" in shape["equipment"]["columns"]:
            add("equipment", await _scalar_counts(
                session,
                f"""SELECT a.building_id::text, count(*) FROM plenum_cafm.equipment e
                    JOIN plenum_cafm.assets a ON a.{akey}::text = e.asset_id::text
                    GROUP BY 1""",
            ))
        if shape["meters"]["exists"] and "asset_id" in shape["meters"]["columns"]:
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
        # Of those, the ones we actually hold. The card said "8 documents" for a building
        # holding none of them, which is the count equivalent of a working download icon on
        # a row with no file.
        if "blob_url" in shape["documents"]["columns"]:
            # FILTER, not WHERE. A WHERE would drop a building whose documents are all
            # unheld out of the result entirely, and an absent count is how a caller says
            # "nobody counted this" — so the one building that most needs to say "none
            # held" would be the one saying nothing.
            add("documents_held", await _scalar_counts(
                session,
                """SELECT building_id::text,
                          count(*) FILTER (WHERE NULLIF(blob_url::text, '') IS NOT NULL)
                     FROM plenum_cafm.documents GROUP BY 1""",
            ))
        dkey = shape["documents"]["key"]
        if shape["compliance_certificates"]["exists"] and (
            {"document_id", "source_document_id"}
            & shape["compliance_certificates"]["columns"]
        ):
            add("certificates", await _scalar_counts(
                session,
                f"""SELECT COALESCE(c.building_id::text, d.building_id::text), count(*)
                    FROM plenum_cafm.compliance_certificates c
                    LEFT JOIN plenum_cafm.documents d
                           ON d.{dkey}::text = COALESCE(c.document_id::text, c.source_document_id::text)
                    GROUP BY 1""",
            ))
            # Certificates with no document behind them at all. These reach a building only
            # by their own building_id — there is no document to reach it through, which is
            # the whole point of counting them.
            add("certificates_unevidenced", await _scalar_counts(
                session,
                f"""SELECT COALESCE(c.building_id::text, d.building_id::text),
                           count(*) FILTER (WHERE COALESCE(c.document_id,
                                                           c.source_document_id) IS NULL)
                      FROM plenum_cafm.compliance_certificates c
                      LEFT JOIN plenum_cafm.documents d
                             ON d.{dkey}::text = COALESCE(c.document_id::text,
                                                          c.source_document_id::text)
                     GROUP BY 1""",
            ))
    if shape["contracts"]["exists"]:
        add("contracts", await _scalar_counts(
            session,
            "SELECT building_id::text, count(*) FROM plenum_cafm.contracts GROUP BY 1",
        ))
    inv_sql = invoices_count_sql(shape)
    if inv_sql:
        add("invoices", await _scalar_counts(session, inv_sql))
    return result


async def load_buildings(session: AsyncSession, *, limit: int = 1000) -> list[dict[str, Any]]:
    """Every building row, keyed on whatever key column the table actually has."""
    shape = await graph_shape(session)
    if not shape["buildings"]["exists"]:
        return []
    key = shape["buildings"]["key"]
    have = shape["buildings"]["columns"]
    # Canonical columns first, older spellings kept so a part-migrated database still reads.
    wanted = [
        "site_id", "location_id", "building_code", "name", "building_name", "primary_use",
        "floors", "gross_area_sqft", "eui_kwh_m2", "hoist_score", "status",
        "country", "country_code", "state", "city", "postcode", "use_type",
        "floors_recorded", "gfa_sqm_recorded", "eui_kwh_per_m2", "benchmark_kwh_per_m2",
        "benchmark_standard", "benchmark_standing", "benchmark_standing_note",
        "metering_route", "metering_granularity",
        # Read so the row can hand a client something to send back on a patch. Without it
        # expected_updated_at is unusable: nobody can quote a value the API never returns.
        "updated_at", "created_at",
    ]
    cols = [f"b.{key}::text AS building_id"] + [f"b.{c}::text AS {c}" for c in wanted if c in have]

    # A building is scored against the pack its LOCATION points at — the standard is a
    # property of where the building is, not a column copied onto every row.
    joins = ""
    if shape["locations"]["exists"] and "location_id" in have:
        lkey = shape["locations"]["key"]
        cols += ["l.country_code::text AS loc_country_code", "l.region::text AS loc_region"]
        joins += f" LEFT JOIN plenum_cafm.locations l ON l.{lkey}::text = b.location_id::text"
        if shape["regulation_packs"]["exists"]:
            pkey = shape["regulation_packs"]["key"]
            cols += [
                "p.standard::text AS pack_standard",
                "p.benchmark_source::text AS pack_benchmark_source",
                "p.standing::text AS pack_standing",
                "p.standing_note::text AS pack_standing_note",
            ]
            joins += f" LEFT JOIN plenum_cafm.regulation_packs p ON p.{pkey}::text = l.pack_id::text"

    # Sites carry the portfolio and the address the building inherits for display.
    if shape["sites"]["exists"] and "site_id" in have:
        skey = shape["sites"]["key"]
        _sn = [f"s.{c}" for c in ("name", "site_name") if c in shape["sites"]["columns"]]
        site_name = f"COALESCE({', '.join(_sn)})" if _sn else "NULL"
        cols += [f"{site_name}::text AS site_name"]
        # The site's address, as a fallback for display only. A building whose location_id
        # is not set still sits somewhere, and showing no country at all because the graph
        # link is missing is worse than showing the estate's. It does NOT stand in for the
        # regulation pack — that comes from the location or not at all, because scoring a
        # building against a standard inferred from its estate's address is a guess with a
        # legal claim attached.
        # metering_route / metering_granularity are site columns and only ever existed
        # there — `wanted` above asks plenum_cafm.buildings for them, which has neither, so
        # they were silently dropped and every building reported "no meters on record".
        # Not a display nicety: record completeness counts metering, so nine buildings with
        # a half-hourly data collector on file were each capped at 78%.
        for c in ("country_code", "region", "city", "postcode",
                  "metering_route", "metering_granularity"):
            if c in shape["sites"]["columns"]:
                cols += [f"s.{c}::text AS site_{c}"]
        joins += f" LEFT JOIN plenum_cafm.sites s ON s.{skey}::text = b.site_id::text"

    order = "b.name" if "name" in have else f"b.{key}"
    sql = (
        f"SELECT {', '.join(cols)} FROM plenum_cafm.buildings b{joins} "
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
            "area_unit": "m2",
            "use_mix": mix,
            "dominant_use": dominant_use(mix),
            "counts": c,
        }
    out["_shape"] = {t: v["key"] for t, v in shape.items() if v["exists"]}
    return out
