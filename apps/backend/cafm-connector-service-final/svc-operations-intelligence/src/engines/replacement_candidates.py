"""What should be bought again - parts to reorder and assets at the end of their life - in one read.

"Which assets are required for repurchase?" (3 Oct 2026) has two honest readings and the data
holds no register for either: `purchase_orders`, `asset_condition_verdicts` and
`energy_recommendations` are empty. The agent took the empty energy table for "the authoritative
replacement register" and never read the signals the Assets page and the cost-saving read use.
This is those signals, joined, as the product defines them:

    parts_to_reorder            spare_parts at or below reorder_level (company-wide: parts carry no
                                building), stock-outs first, with the cost to restock
    end_of_life                 assets with condition_score >= EOL_GRADE (the Assets page's
                                "remediate or replace" grade), with design life used and open jobs
    repeat_failures             assets with REPEAT_JOBS or more reactive jobs in the period and the
                                reactive cost against replacement value (>= REPLACE_RATIO is a
                                replace-or-fix-the-cause case) - the cost-saving read's rule
    inspection_recommendations  inspections whose recommendation says replace, and whether an order
                                ever followed
    replacement_work_orders     work orders whose title says replacement / replaced
    purchase_orders             what is actually on order (usually nothing - and the answer says so)

Nothing here is a decision: a grade-4 chiller is a candidate, not a purchase. Scoped to the
caller's company and buildings; reads only, each section in its own savepoint.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from .value_ledger import _grab, _grab_rows, _num, _wo_type_column
from .value_savings import _DONE, _REACTIVE_RE, _bscope

log = get_logger(__name__)

#: Condition grade at which the Assets page opens a replacement case (1 new ... 5 end of life).
EOL_GRADE = 4
#: Reactive jobs in the period past which an asset is a repeat failure.
REPEAT_JOBS = 3
#: Reactive cost / replacement value past which the repeat failure is a replace-or-fix case.
REPLACE_RATIO = 0.5
#: Replacement questions look back a year by default: a month hides a slow failure.
DEFAULT_DAYS = 365
REPLACE_RE = r"(replace|replacement|end of life|beyond economic repair|write[- ]?off|condemn)"
#: An inspector's "Replace the belts" counts; "replacements claimable" under a Monitor verdict does not.
INSPECTION_RE = r"(\mreplace\M|end of life|beyond economic repair|write[- ]?off|condemn)"
LIMIT = 60
#: Repeat failures are many on a busy site: the answer wants the costliest, not all of them.
REPEAT_LIMIT = 15


def design_life_used_pct(installed: date | None, life_years: Any, today: date) -> float | None:
    """Straight-line share of the design life consumed, 0-200 (past 100 is beyond design life)."""
    life = _num(life_years)
    if not installed or not life or life <= 0:
        return None
    age = (today - installed).days / 365.25
    return round(min(max(age / life, 0.0), 2.0) * 100, 1)


def classify_repeat(jobs: int, cost: float | None, replacement_value: float | None) -> dict[str, Any]:
    """Pure: the repeat-failure verdict the cost-saving read states, so both reads agree."""
    ratio = round(cost / replacement_value, 2) if cost and replacement_value else None
    return {"cost_to_replacement_ratio": ratio,
            "verdict": "replace or fix the cause" if ratio is not None and ratio >= REPLACE_RATIO
            else ("repeat failure" if jobs >= REPEAT_JOBS else "watch")}


async def read_candidates(session: AsyncSession, *, organization_id: UUID | None,
                          building_ids: tuple[UUID, ...] | None = None, building_id: UUID | None = None,
                          period_from: date | None = None, period_to: date | None = None,
                          today: date | None = None) -> dict[str, Any]:
    t = today or date.today()
    start = period_from or (t - timedelta(days=DEFAULT_DAYS))
    end = period_to or (t + timedelta(days=1))
    org = str(organization_id) if organization_id else None
    scope_ids = (building_id,) if building_id is not None else building_ids
    a_scope, a_p = _bscope(scope_ids, "a.building_id")
    w_scope, w_p = _bscope(scope_ids, "w.building_id")
    org_a = " AND a.organization_id::text = :o" if org else ""
    org_w = " AND w.organization_id::text = :o" if org else ""
    org_p = " AND p.organization_id::text = :o" if org else ""
    org_i = " AND i.organization_id::text = :o" if org else ""
    base: dict[str, Any] = {"o": org, "a": start, "b": end}
    out: dict[str, Any] = {"ok": True, "as_of": t.isoformat(), "period_from": start.isoformat(),
                           "period_to": (end - timedelta(days=1)).isoformat(), "eol_grade": EOL_GRADE,
                           "building_scoped": scope_ids is not None, "currency": "GBP"}

    # Parts: the only literal "buy again" signal the data holds. Parts carry no building.
    rows = await _grab_rows(session, f"""
        SELECT p.part_code, p.part_name, p.stock_quantity, p.reorder_level, p.max_quantity, p.unit_price,
               coalesce(p.supplier, p.vendor_name) AS supplier
          FROM plenum_cafm.spare_parts p
         WHERE p.reorder_level IS NOT NULL AND coalesce(p.stock_quantity, 0) <= p.reorder_level{org_p}
         ORDER BY coalesce(p.stock_quantity, 0) = 0 DESC, (p.reorder_level - coalesce(p.stock_quantity, 0)) DESC
         LIMIT {LIMIT}""", base)
    out["parts_to_reorder"] = rows if isinstance(rows, dict) else [{
        "part_code": r["part_code"], "part": r["part_name"], "stock": int(r["stock_quantity"] or 0),
        "reorder_level": int(r["reorder_level"]), "stock_out": int(r["stock_quantity"] or 0) == 0,
        "shortfall_to_max": max(int(r["max_quantity"] or r["reorder_level"]) - int(r["stock_quantity"] or 0), 0),
        "unit_price": _num(r["unit_price"]),
        "cost_to_restock": round(max(int(r["max_quantity"] or r["reorder_level"]) - int(r["stock_quantity"] or 0), 0)
                                 * float(r["unit_price"]), 2) if r["unit_price"] is not None else None,
        "supplier": r["supplier"]} for r in rows]

    # Assets at the grade the Assets page calls end of life.
    rows = await _grab_rows(session, f"""
        SELECT a.asset_code, a.asset_name, a.condition_score, a.condition_updated_at, a.replacement_value,
               a.installation_date, a.design_life_years, a.criticality, a.site_name AS building, a.building_code,
               (SELECT count(*) FROM plenum_cafm.work_orders o
                 WHERE o.asset_id::text = a.id::text AND lower(coalesce(o.status, '')) NOT IN {_DONE}) AS open_jobs,
               (SELECT i.recommendation FROM plenum_cafm.inspections i
                 WHERE i.asset_id::text = a.id::text AND coalesce(i.recommendation, '') <> ''
                 ORDER BY i.inspection_date DESC NULLS LAST LIMIT 1) AS latest_recommendation
          FROM plenum_cafm.assets a
         WHERE a.condition_score >= :g{org_a}{a_scope}
         ORDER BY a.condition_score DESC, a.replacement_value DESC NULLS LAST LIMIT {LIMIT}""",
        {**base, **a_p, "g": EOL_GRADE})
    out["end_of_life"] = rows if isinstance(rows, dict) else [{
        "asset": r["asset_code"], "name": r["asset_name"], "condition_grade": int(r["condition_score"]),
        "graded_on": r["condition_updated_at"].date().isoformat() if r["condition_updated_at"] else None,
        "replacement_value": _num(r["replacement_value"]),
        "design_life_used_pct": design_life_used_pct(r["installation_date"], r["design_life_years"], t),
        "installed": r["installation_date"].isoformat() if r["installation_date"] else None,
        "criticality": r["criticality"], "building": r["building"] or r["building_code"],
        "open_work_orders": int(r["open_jobs"] or 0), "latest_recommendation": (r["latest_recommendation"] or "")[:240] or None}
        for r in rows]

    # Repeat failures in the period - the cost-saving read's rule, so the two reads agree.
    type_col = await _wo_type_column(session)
    reactive = f"lower(coalesce(w.{type_col}, '')) ~ '{_REACTIVE_RE}'" if type_col else "FALSE"
    rows = await _grab_rows(session, f"""
        SELECT a.asset_code, a.asset_name, a.replacement_value, a.condition_score, count(*) AS jobs,
               sum(coalesce(w.actual_cost, w.estimated_cost, 0)) AS cost,
               string_agg(DISTINCT coalesce(v.vendor_name, w.vendor_name), ', ') AS vendors
          FROM plenum_cafm.work_orders w
          JOIN plenum_cafm.assets a ON a.id::text = w.asset_id::text
          LEFT JOIN plenum_cafm.vendors v ON v.id::text = w.vendor_id::text
         WHERE {reactive}{org_w}{w_scope}
           AND coalesce(w.reported_at, w.raised_at, w.created_at) >= CAST(:a AS date)
           AND coalesce(w.reported_at, w.raised_at, w.created_at) < CAST(:b AS date)
         GROUP BY a.id, a.asset_code, a.asset_name, a.replacement_value, a.condition_score
        HAVING count(*) >= :n
         ORDER BY sum(coalesce(w.actual_cost, w.estimated_cost, 0)) DESC LIMIT {REPEAT_LIMIT}""",
        {**base, **w_p, "n": REPEAT_JOBS})
    out["repeat_failures"] = rows if isinstance(rows, dict) else [{
        "asset": r["asset_code"], "name": r["asset_name"], "reactive_jobs": int(r["jobs"]),
        "reactive_cost": _num(r["cost"]), "replacement_value": _num(r["replacement_value"]),
        "condition_grade": r["condition_score"], "vendors": r["vendors"],
        **classify_repeat(int(r["jobs"]), _num(r["cost"]), _num(r["replacement_value"]))} for r in rows]

    # What the inspectors wrote, and whether anyone acted on it.
    rows = await _grab_rows(session, f"""
        SELECT i.inspection_date, i.inspector, i.risk_level, i.recommendation, i.converted_work_order_id, i.work_order_id,
               a.asset_code, a.asset_name
          FROM plenum_cafm.inspections i
          LEFT JOIN plenum_cafm.assets a ON a.id::text = i.asset_id::text
         WHERE coalesce(i.recommendation, '') ~* '{INSPECTION_RE}'{org_i}{a_scope}
         ORDER BY i.inspection_date DESC NULLS LAST LIMIT {LIMIT}""", {**base, **a_p})
    out["inspection_recommendations"] = rows if isinstance(rows, dict) else [{
        "asset": r["asset_code"], "name": r["asset_name"],
        "inspected": r["inspection_date"].isoformat() if r["inspection_date"] else None,
        "inspector": r["inspector"], "risk": r["risk_level"], "recommendation": (r["recommendation"] or "")[:300],
        "actioned": bool(r["converted_work_order_id"] or r["work_order_id"])} for r in rows]

    # Jobs that were replacements - evidence of what has already been bought.
    rows = await _grab_rows(session, f"""
        SELECT w.wo_code, w.title, w.status, coalesce(w.actual_cost, w.estimated_cost) AS cost,
               coalesce(w.completed_at, w.closed_at, w.raised_at, w.reported_at, w.created_at) AS when_at,
               coalesce(a.asset_code, w.asset_code) AS asset
          FROM plenum_cafm.work_orders w
          LEFT JOIN plenum_cafm.assets a ON a.id::text = w.asset_id::text
         WHERE coalesce(w.title, '') ~* '{REPLACE_RE}'{org_w}{w_scope}
         ORDER BY when_at DESC NULLS LAST LIMIT {LIMIT}""", {**base, **w_p})
    out["replacement_work_orders"] = rows if isinstance(rows, dict) else [{
        "wo_code": r["wo_code"], "title": (r["title"] or "")[:160], "status": r["status"], "cost": _num(r["cost"]),
        "when": r["when_at"].date().isoformat() if r["when_at"] else None, "asset": r["asset"]} for r in rows]

    # Purchase orders: the register the question assumes. Say what it holds, even when nothing.
    po = await _grab(session, f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE lower(coalesce(status, '')) NOT IN ('received', 'closed', 'cancelled', 'complete', 'completed')) AS open
          FROM plenum_cafm.purchase_orders{' WHERE organization_id::text = :o' if org else ''}""", base)
    out["purchase_orders"] = po if "error" in po else {"total": int(po.get("total") or 0), "open": int(po.get("open") or 0)}

    def _n(k: str) -> int:
        v = out.get(k)
        return len(v) if isinstance(v, list) else 0

    out["summary"] = {"parts_to_reorder": _n("parts_to_reorder"),
                      "stock_outs": sum(1 for p in out["parts_to_reorder"] if p.get("stock_out")) if isinstance(out["parts_to_reorder"], list) else 0,
                      "end_of_life": _n("end_of_life"),
                      "replace_or_fix": sum(1 for r in out["repeat_failures"] if r.get("verdict") == "replace or fix the cause") if isinstance(out["repeat_failures"], list) else 0,
                      "repeat_failures": _n("repeat_failures"), "inspection_recommendations": _n("inspection_recommendations"),
                      "replacement_work_orders": _n("replacement_work_orders"),
                      "purchase_orders_open": (out["purchase_orders"] or {}).get("open", 0)}
    out["answer_rules"] = ANSWER_RULES
    return out


ANSWER_RULES = """Answer from this read only. "Repurchase / replace / reorder" has two readings - give both, parts first:
- `parts_to_reorder`: a table | Part | Stock | Reorder level | Shortfall | Cost to restock | Supplier |; stock-outs
  (stock 0) named first. Parts are company stock, not per building: say so when a building was asked.
- `end_of_life`: assets at condition grade >= eol_grade (the Assets page's "remediate or replace" grade):
  | Asset | Grade | Replacement value | Design life used | Open jobs | Latest recommendation |.
- `repeat_failures` (the costliest, up to 15) with verdict "replace or fix the cause": name the asset, jobs,
  reactive cost and the ratio. A component the inspector said to replace is a part, not the asset: say which.
- `inspection_recommendations` not actioned: list them; actioned ones in one line.
- `purchase_orders`: state what is on order. When `open` is 0 say "no purchase order is on file" - never
  call another table a repurchase register.
- These are candidates for a decision, not purchases: say so once. No forecasts, no invented costs.
"""
