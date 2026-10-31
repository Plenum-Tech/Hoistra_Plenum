"""What is driving spend on a building, and which plant is driving the overrun.

The graph now carries the chain this needs end to end:

    buildings ──< assets ──< work_orders ──< invoice_lines

An asset is placed when it is ingested. A work order records the asset it is against and
inherits that asset's building. An invoice line names the work order it bills. So the
question "which asset is costing us the most on Bishopsgate Tower, and where are we over
contract" is one join away — where before the chain broke at every step.

**Billed and over-contract are different numbers and are never added together.**
``billed`` is what the vendor invoiced. ``over_contract`` is ``delta_gbp``: the part of that
which exceeds what the contract said the work should cost, as the invoice verifier computed
it. A building can be the largest spender and have no overrun at all — that is a busy
building, not a problem — and a small asset with a large delta is the one worth opening.

Two disclosures travel with every figure, because the alternative is a number that reads as
complete when it is not:

* ``unattributed`` — lines billed against this building whose work order names no asset.
  They are real spend and they are counted in the building total, but they cannot be blamed
  on a piece of plant, so they sit outside the per-asset ranking rather than being quietly
  dropped or spread across it.
* ``lines_without_delta`` — lines the verifier never scored against a contract. Their
  overrun is unknown, not zero, and a rank built on the other lines says so.

Reads only. Introspects rather than assuming, like everything else on the graph: a
deployment part-way through the migration returns what it can and states what is missing.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from .building_rollup import graph_shape

log = get_logger(__name__)

#: Column spellings that might hold an asset's human reference.
_ASSET_CODE_COLS = ("asset_code", "code", "asset_tag", "tag")
_ASSET_NAME_COLS = ("asset_name", "name", "description")
#: How a work order points at its asset.
_WO_ASSET_COLS = ("asset_id", "asset_code", "asset")
_WO_CODE_COLS = ("wo_code", "work_order_code", "code", "reference")


def _num(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _pick(cols: set[str], candidates: tuple[str, ...]) -> str | None:
    return next((c for c in candidates if c in cols), None)


async def chain_shape(session: AsyncSession) -> dict[str, Any]:
    """Which parts of buildings→assets→work_orders→invoice_lines this database can join.

    Returned to the caller rather than raised on, so a part-migrated deployment gets a
    partial answer that names its own gaps instead of an error.
    """
    shape = await graph_shape(session)
    assets, wo = shape["assets"], shape["work_orders"]

    a_cols = assets["columns"] if assets["exists"] else set()
    w_cols = wo["columns"] if wo["exists"] else set()

    inv_cols: set[str] = set()
    try:
        rows = (
            await session.execute(
                text(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'plenum_cafm' AND table_name = 'invoice_lines'"""
                )
            )
        ).all()
        inv_cols = {str(r[0]) for r in rows}
    except Exception as exc:  # noqa: BLE001
        log.warning("cost_drivers.introspect_failed", error=str(exc)[:200])

    wo_asset = _pick(w_cols, _WO_ASSET_COLS)
    out = {
        "assets": {
            "exists": assets["exists"],
            "key": assets["key"],
            "code": _pick(a_cols, _ASSET_CODE_COLS),
            "name": _pick(a_cols, _ASSET_NAME_COLS),
            "has_building": "building_id" in a_cols,
        },
        "work_orders": {
            "exists": wo["exists"],
            "key": wo["key"],
            "code": _pick(w_cols, _WO_CODE_COLS),
            "asset": wo_asset,
            # asset_id holds the asset's key; asset_code holds its reference. The join
            # differs and guessing wrong silently matches nothing.
            "asset_is_key": wo_asset in {"asset_id", "asset"},
            "has_building": "building_id" in w_cols,
            "title": _pick(w_cols, ("title", "description", "summary")),
            "status": _pick(w_cols, ("status", "state")),
        },
        "invoice_lines": {
            "exists": bool(inv_cols),
            "code": _pick(inv_cols, ("wo_code",)),
            "has_delta": "delta_gbp" in inv_cols,
            "has_total": "line_total" in inv_cols,
        },
    }
    missing: list[str] = []
    if not out["assets"]["has_building"]:
        missing.append("assets.building_id — run the building graph migration")
    if not out["work_orders"]["has_building"]:
        missing.append("work_orders.building_id — run the building graph migration")
    if not out["work_orders"]["asset"]:
        missing.append("work_orders has no asset column — spend cannot be attributed to plant")
    if not out["invoice_lines"]["exists"]:
        missing.append("invoice_lines is absent — no billed figures to read")
    elif not out["invoice_lines"]["code"]:
        missing.append("invoice_lines.wo_code — lines cannot be tied to a work order")
    out["missing"] = missing
    out["can_attribute"] = not missing
    return out


async def building_cost_drivers(
    session: AsyncSession,
    building_id: str,
    *,
    limit: int = 25,
) -> dict[str, Any]:
    """Spend on one building, ranked by how far over contract each asset has run.

    Ranked on ``over_contract`` rather than ``billed`` deliberately: the largest spender is
    usually the largest asset, which tells you nothing you did not know. The gap between
    what was billed and what the contract said is the part somebody can act on.
    """
    shape = await chain_shape(session)
    a, w, il = shape["assets"], shape["work_orders"], shape["invoice_lines"]

    out: dict[str, Any] = {
        "ok": True,
        "building_id": building_id,
        "assets": [],
        "totals": {"billed": 0.0, "over_contract": 0.0, "lines": 0, "work_orders": 0},
        "unattributed": {"billed": 0.0, "over_contract": 0.0, "lines": 0},
        "lines_without_delta": 0,
        "missing": shape["missing"],
    }
    if not shape["can_attribute"]:
        out["ok"] = False
        return out

    # The work order's asset column either holds the asset's key or its code.
    join_on = f"a.{a['key']}::text = w.{w['asset']}::text" if w["asset_is_key"] \
        else f"a.{a['code']}::text = w.{w['asset']}::text"
    delta = "l.delta_gbp" if il["has_delta"] else "NULL::numeric"
    total = "l.line_total" if il["has_total"] else "NULL::numeric"

    sql = f"""
        SELECT
            a.{a['key']}::text                        AS asset_id,
            {"a." + a['code'] + "::text" if a['code'] else "NULL"}  AS asset_code,
            {"a." + a['name'] + "::text" if a['name'] else "NULL"}  AS asset_name,
            count(DISTINCT w.{w['key']}::text)        AS work_orders,
            count(l.id)                               AS lines,
            coalesce(sum({total}), 0)                 AS billed,
            coalesce(sum({delta}), 0)                 AS over_contract,
            count(*) FILTER (WHERE {delta} IS NULL)   AS lines_without_delta,
            count(*) FILTER (WHERE l.match_status = 'flagged') AS flagged_lines
        FROM plenum_cafm.work_orders w
        JOIN plenum_cafm.assets a ON {join_on}
        LEFT JOIN plenum_cafm.invoice_lines l
               ON upper(l.{il['code']}::text) = upper(w.{w['code']}::text)
        WHERE w.building_id::text = :bid
        GROUP BY 1, 2, 3
        ORDER BY over_contract DESC, billed DESC
        LIMIT :lim
    """
    try:
        rows = (
            await session.execute(text(sql), {"bid": str(building_id), "lim": int(limit)})
        ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("cost_drivers.query_failed", error=str(exc)[:250])
        return {**out, "ok": False, "missing": out["missing"] + [str(exc)[:200]]}

    for r in rows:
        out["assets"].append({
            "asset_id": r["asset_id"],
            "asset_code": r["asset_code"],
            "asset_name": r["asset_name"],
            "work_orders": int(r["work_orders"] or 0),
            "lines": int(r["lines"] or 0),
            "billed": round(_num(r["billed"]), 2),
            "over_contract": round(_num(r["over_contract"]), 2),
            "flagged_lines": int(r["flagged_lines"] or 0),
            "lines_without_delta": int(r["lines_without_delta"] or 0),
        })

    # Spend on this building whose work order names no asset. Real money, and it belongs in
    # the building total — but it cannot be blamed on a piece of plant, so it is reported
    # beside the ranking rather than inside it.
    try:
        u = (
            await session.execute(
                text(
                    f"""SELECT coalesce(sum({total}), 0) AS billed,
                               coalesce(sum({delta}), 0) AS over_contract,
                               count(l.id)               AS lines
                        FROM plenum_cafm.work_orders w
                        JOIN plenum_cafm.invoice_lines l
                          ON upper(l.{il['code']}::text) = upper(w.{w['code']}::text)
                        WHERE w.building_id::text = :bid
                          AND w.{w['asset']} IS NULL"""
                ),
                {"bid": str(building_id)},
            )
        ).mappings().first()
        if u:
            out["unattributed"] = {
                "billed": round(_num(u["billed"]), 2),
                "over_contract": round(_num(u["over_contract"]), 2),
                "lines": int(u["lines"] or 0),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("cost_drivers.unattributed_failed", error=str(exc)[:200])

    out["totals"] = {
        "billed": round(
            sum(x["billed"] for x in out["assets"]) + out["unattributed"]["billed"], 2
        ),
        "over_contract": round(
            sum(x["over_contract"] for x in out["assets"])
            + out["unattributed"]["over_contract"], 2
        ),
        "lines": sum(x["lines"] for x in out["assets"]) + out["unattributed"]["lines"],
        "work_orders": sum(x["work_orders"] for x in out["assets"]),
    }
    out["lines_without_delta"] = sum(x["lines_without_delta"] for x in out["assets"])
    out["worst"] = out["assets"][0] if out["assets"] else None
    return out


async def asset_work_history(
    session: AsyncSession, asset_id: str, *, limit: int = 100
) -> dict[str, Any]:
    """Every work order recorded against one asset, with what each was billed.

    The reason work orders record their asset: an asset's history is only readable if the
    work done on it says which plant it was done on.
    """
    shape = await chain_shape(session)
    a, w, il = shape["assets"], shape["work_orders"], shape["invoice_lines"]
    if not (a["exists"] and w["exists"] and w["asset"]):
        return {"ok": False, "asset_id": asset_id, "work_orders": [],
                "missing": shape["missing"]}

    join_val = "a." + (a["key"] if w["asset_is_key"] else (a["code"] or a["key"]))
    delta = "l.delta_gbp" if il["has_delta"] else "NULL::numeric"
    total = "l.line_total" if il["has_total"] else "NULL::numeric"
    lines_join = (
        f"""LEFT JOIN plenum_cafm.invoice_lines l
                   ON upper(l.{il['code']}::text) = upper(w.{w['code']}::text)"""
        if il["exists"] and il["code"] else
        "LEFT JOIN (SELECT NULL::uuid AS id, NULL::text AS wo_code) l ON FALSE"
    )
    sql = f"""
        SELECT w.{w['key']}::text                       AS work_order_id,
               w.{w['code']}::text                      AS wo_code,
               {("w." + w["title"] + "::text") if w["title"] else "NULL"} AS title,
               {("w." + w["status"] + "::text") if w["status"] else "NULL"} AS status,
               coalesce(sum({total}), 0)                AS billed,
               coalesce(sum({delta}), 0)                AS over_contract,
               count(l.id)                              AS lines
        FROM plenum_cafm.work_orders w
        JOIN plenum_cafm.assets a ON {join_val}::text = w.{w['asset']}::text
        {lines_join}
        WHERE a.{a['key']}::text = :aid
        GROUP BY 1, 2, 3, 4
        ORDER BY over_contract DESC
        LIMIT :lim
    """
    try:
        rows = (
            await session.execute(text(sql), {"aid": str(asset_id), "lim": int(limit)})
        ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("cost_drivers.history_failed", error=str(exc)[:250])
        return {"ok": False, "asset_id": asset_id, "work_orders": [],
                "missing": [str(exc)[:200]]}

    history = [{
        "work_order_id": r["work_order_id"],
        "wo_code": r["wo_code"],
        "title": r["title"],
        "status": r["status"],
        "billed": round(_num(r["billed"]), 2),
        "over_contract": round(_num(r["over_contract"]), 2),
        "lines": int(r["lines"] or 0),
    } for r in rows]
    return {
        "ok": True,
        "asset_id": asset_id,
        "count": len(history),
        "billed": round(sum(h["billed"] for h in history), 2),
        "over_contract": round(sum(h["over_contract"] for h in history), 2),
        "work_orders": history,
    }
