"""Cost savings - where a company or one building can save money, and what it takes, in one read.

The chat had no way to answer "where can we save money" as a whole: the energy engine priced its
anomalies, contract performance flagged invoice lines, the value ledger summed both for the Home
card - and nothing put them beside the work orders that would capture them (1 Oct 2026). This
read does, for a period and optionally one building:

    ledger           the value ledger's modules: detected vs saved (engines/value_ledger.py)
    open_jobs        open work orders whose asset carries priced energy waste, or whose job is a
                     predictive / energy fix - with the cost to act and the money at stake
    repeat_failures  assets with three or more reactive jobs in the period: their reactive cost
                     against the asset's replacement value, and any open job on them
    overcharges      invoice lines flagged against contract rates on the period's jobs
    spend            reactive vs planned work-order cost in the period
    warnings         figures that look wrong before anyone quotes them

The ledger's discipline holds: a figure is "detected" until a decision is recorded against it,
never "saved"; an energy finding is counted once per asset (its largest), because the rules
overlap; nothing here is a forecast. Reads only - every section in its own savepoint.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from . import value_ledger as ledger_svc
from .value_ledger import _grab, _grab_rows, _num, _wo_type_column

log = get_logger(__name__)

_DONE = "('completed','complete','closed','cancelled','canceled','rejected','void','done','resolved')"
_REACTIVE_RE = "(reactive|unplanned|corrective|breakdown|emergency)"
#: Words in a job that say it removes waste or a coming failure - money even when unpriced.
_SAVING_JOB_RE = ("(predictive|bearing|vibration|motor current|setback|night set|schedule|out.of.hours|"
                  "fighting|simultaneous|stuck|actuator|valve|leak|drift|efficien|burner|flue|lighting scene|"
                  "time ?clock|override|setpoint|set point)")
#: Call-outs on one asset in one month past which the records themselves should be checked.
CALLOUT_CHECK = 30
LIMIT = 15


def period_bounds(period: str | None, today: date | None = None) -> tuple[date, date, str]:
    """(start, end exclusive, label). last_month is the default - a month is how FMs review cost."""
    t = today or date.today()
    m0 = date(t.year, t.month, 1)
    p = (period or "last_month").strip().lower().replace(" ", "_").replace("-", "_")
    if p == "this_month":
        return m0, t + timedelta(days=1), m0.strftime("%B %Y") + " to date"
    if p in ("this_year", "year_to_date", "ytd"):
        return date(t.year, 1, 1), t + timedelta(days=1), str(t.year) + " to date"
    if p in ("last_90_days", "quarter", "last_quarter"):
        return t - timedelta(days=90), t + timedelta(days=1), "the last 90 days"
    start = (m0 - timedelta(days=1)).replace(day=1)
    return start, m0, start.strftime("%B %Y")


def _bscope(building_ids: tuple[UUID, ...] | None, col: str) -> tuple[str, dict[str, Any]]:
    if building_ids is None:
        return "", {}
    if not building_ids:
        return " AND FALSE", {}
    return f" AND {col} = ANY(CAST(:sv_b AS uuid[]))", {"sv_b": [str(b) for b in building_ids]}


async def read_savings(session: AsyncSession, *, organization_id: UUID | None,
                       building_ids: tuple[UUID, ...] | None = None, period: str | None = None,
                       today: date | None = None) -> dict[str, Any]:
    start, end, label = period_bounds(period, today)
    org = str(organization_id) if organization_id else None
    base = {"o": org, "a": start, "b": end}
    w_scope, w_p = _bscope(building_ids, "w.building_id")
    e_scope, e_p = _bscope(building_ids, "e.building_id")
    org_w = " AND w.organization_id::text = :o" if org else ""
    org_e = " AND e.organization_id::text = :o" if org else ""
    type_col = await _wo_type_column(session)
    reactive = (f"lower(coalesce(w.{type_col}, '')) ~ '{_REACTIVE_RE}'" if type_col else "FALSE")
    jobtype = f"w.{type_col}" if type_col else "NULL"
    out: dict[str, Any] = {"ok": True, "period": label, "from": start.isoformat(), "to": (end - timedelta(days=1)).isoformat(),
                           "currency": "GBP", "building_scoped": building_ids is not None}

    # The ledger (calendar year) - detected vs saved per module, as the Home card states it.
    try:
        led = await ledger_svc.read_value_summary(session, organization_id=organization_id,
                                                  building_ids=building_ids, year=start.year)
        lg = led.get("ledger") or {}
        out["ledger"] = {"year": start.year, "total_detected": lg.get("total_detected"), "total_saved": lg.get("total_saved"),
                         "modules": [{"module": m.get("name"), "detected": m.get("detected"), "saved": m.get("saved"),
                                      "note": m.get("note"),
                                      "top": [{"what": i.get("what"), "detected": i.get("detected"), "action": i.get("action")}
                                              for i in (m.get("items") or [])[:3]]}
                                     for m in lg.get("modules") or []]}
    except Exception as exc:  # noqa: BLE001 - the ledger is one section; the rest still answers
        log.warning("savings.ledger_unreadable", error=str(exc)[:200])
        out["ledger"] = {"error": str(exc)[:200]}

    # Open jobs with money at stake: the asset's largest open priced energy finding (counted once).
    rows = await _grab_rows(session, f"""
        WITH waste AS (
            SELECT e.asset_id::text AS aid, max(e.financial_gbp) AS gbp_yr, count(*) AS findings,
                   (array_agg(e.anomaly_type ORDER BY e.financial_gbp DESC NULLS LAST))[1] AS top_type,
                   bool_or(e.financial_gbp IS NULL) AS unpriced_too
              FROM plenum_cafm.energy_anomalies e
             WHERE lower(coalesce(e.status, 'open')) = 'open' AND e.asset_id IS NOT NULL{org_e}{e_scope}
             GROUP BY 1)
        SELECT w.wo_code, coalesce(w.title, w.issue_description, w.task_description, '') AS title, w.status,
               {jobtype} AS job_type, w.priority, coalesce(w.estimated_cost, 0) AS est_cost,
               a.asset_code, a.asset_name, a.replacement_value, a.criticality,
               v.vendor_name AS vendor, k.gbp_yr, k.findings, k.top_type, k.unpriced_too
          FROM plenum_cafm.work_orders w
          LEFT JOIN plenum_cafm.assets a ON a.id::text = w.asset_id::text
          LEFT JOIN plenum_cafm.vendors v ON v.id::text = w.vendor_id::text
          LEFT JOIN waste k ON k.aid = w.asset_id::text
         WHERE lower(coalesce(w.status, '')) NOT IN {_DONE}{org_w}{w_scope}
           AND (k.aid IS NOT NULL OR lower(coalesce({jobtype}, '')) = 'predictive'
                OR lower(coalesce(w.title, '') || ' ' || coalesce(w.issue_description, '')) ~ '{_SAVING_JOB_RE}')
         ORDER BY k.gbp_yr DESC NULLS LAST, a.replacement_value DESC NULLS LAST
         LIMIT {LIMIT}""", {**base, **w_p, **e_p})
    out["open_jobs"] = rows if isinstance(rows, dict) else [{
        "wo_code": r["wo_code"], "what": r["title"][:160], "status": r["status"], "type": r["job_type"],
        "vendor": r["vendor"], "asset": r["asset_code"] or r["asset_name"], "cost_to_act": _num(r["est_cost"]),
        "energy_waste_gbp_per_year": _num(r["gbp_yr"]), "energy_finding": r["top_type"],
        "unpriced_energy_finding": bool(r["unpriced_too"]) and r["gbp_yr"] is None,
        "asset_replacement_value": _num(r["replacement_value"]), "criticality": r["criticality"]} for r in rows]

    # Repeat failures in the period, against what the asset would cost to replace.
    rows = await _grab_rows(session, f"""
        SELECT a.asset_code, a.asset_name, a.replacement_value, count(*) AS jobs,
               sum(coalesce(w.actual_cost, w.estimated_cost, 0)) AS cost,
               string_agg(DISTINCT v.vendor_name, ', ') AS vendors,
               (SELECT string_agg(o.wo_code, ', ') FROM plenum_cafm.work_orders o
                 WHERE o.asset_id::text = a.id::text AND lower(coalesce(o.status, '')) NOT IN {_DONE}) AS open_wos
          FROM plenum_cafm.work_orders w
          JOIN plenum_cafm.assets a ON a.id::text = w.asset_id::text
          LEFT JOIN plenum_cafm.vendors v ON v.id::text = w.vendor_id::text
         WHERE {reactive}{org_w}{w_scope}
           AND coalesce(w.reported_at, w.raised_at, w.created_at) >= CAST(:a AS date)
           AND coalesce(w.reported_at, w.raised_at, w.created_at) < CAST(:b AS date)
         GROUP BY a.id, a.asset_code, a.asset_name, a.replacement_value
        HAVING count(*) >= 3
         ORDER BY sum(coalesce(w.actual_cost, w.estimated_cost, 0)) DESC LIMIT {LIMIT}""", {**base, **w_p})
    months = max((end - start).days / 30.4, 1.0)
    out["repeat_failures"] = rows if isinstance(rows, dict) else [{
        "asset": r["asset_code"] or r["asset_name"], "name": r["asset_name"], "reactive_jobs": int(r["jobs"]),
        "reactive_cost": _num(r["cost"]), "replacement_value": _num(r["replacement_value"]),
        "cost_to_replacement_ratio": round(float(r["cost"]) / float(r["replacement_value"]), 2)
        if r["replacement_value"] and r["cost"] else None,
        "jobs_per_month": round(int(r["jobs"]) / months, 1), "vendors": r["vendors"], "open_work_orders": r["open_wos"]}
        for r in rows]

    # Overcharges: invoice lines flagged against contract rates on the period's jobs.
    rows = await _grab_rows(session, f"""
        SELECT l.wo_code, l.description, l.match_status, l.delta_gbp, v.vendor_name AS vendor
          FROM plenum_cafm.invoice_lines l
          JOIN plenum_cafm.work_orders w ON w.id::text = l.work_order_id::text
          LEFT JOIN plenum_cafm.vendors v ON v.id::text = w.vendor_id::text
         WHERE coalesce(l.delta_gbp, 0) > 0 AND lower(coalesce(l.match_status, '')) NOT IN ('matched', 'accepted', 'approved'){org_w}{w_scope}
           AND coalesce(w.reported_at, w.raised_at, w.created_at) >= CAST(:a AS date)
           AND coalesce(w.reported_at, w.raised_at, w.created_at) < CAST(:b AS date)
         ORDER BY l.delta_gbp DESC""", {**base, **w_p})
    if isinstance(rows, dict):
        out["overcharges"] = rows
    else:
        by_vendor: dict[str, float] = {}
        for r in rows:
            by_vendor[r["vendor"] or "unknown"] = round(by_vendor.get(r["vendor"] or "unknown", 0) + float(r["delta_gbp"]), 2)
        out["overcharges"] = {"lines": len(rows), "total": _num(sum(float(r["delta_gbp"]) for r in rows)),
                              "by_vendor": dict(sorted(by_vendor.items(), key=lambda x: -x[1])),
                              "top": [{"wo_code": r["wo_code"], "line": (r["description"] or "")[:120], "vendor": r["vendor"],
                                       "over_contract": _num(r["delta_gbp"]), "status": r["match_status"]} for r in rows[:LIMIT]]}

    # Spend in the period: reactive vs the rest.
    sp = await _grab(session, f"""
        SELECT count(*) AS jobs, count(*) FILTER (WHERE {reactive}) AS reactive_jobs,
               sum(coalesce(w.actual_cost, w.estimated_cost, 0)) AS cost,
               sum(coalesce(w.actual_cost, w.estimated_cost, 0)) FILTER (WHERE {reactive}) AS reactive_cost
          FROM plenum_cafm.work_orders w
         WHERE coalesce(w.reported_at, w.raised_at, w.created_at) >= CAST(:a AS date)
           AND coalesce(w.reported_at, w.raised_at, w.created_at) < CAST(:b AS date){org_w}{w_scope}""", {**base, **w_p})
    if "error" in sp:
        out["spend"] = sp
    else:
        cost, rc = float(sp.get("cost") or 0), float(sp.get("reactive_cost") or 0)
        out["spend"] = {"jobs": int(sp.get("jobs") or 0), "reactive_jobs": int(sp.get("reactive_jobs") or 0),
                        "cost": _num(cost), "reactive_cost": _num(rc),
                        "reactive_share_pct": round(100 * rc / cost, 1) if cost else None,
                        "basis": "work-order actual cost, else its estimate" + ("" if type_col else
                                 "; no column says reactive on this deployment, so reactive is not split out")}
    out["warnings"] = warnings(out)
    return out


def warnings(out: dict[str, Any]) -> list[str]:
    """Figures to check before anyone quotes them."""
    w: list[str] = []
    led = out.get("ledger") or {}
    for m in led.get("modules") or []:
        if m.get("detected") and not m.get("saved"):
            w.append(f"{m['module']}: £{m['detected']:,.0f} detected and nothing saved yet - no decision is recorded "
                     "against these findings, so none of it is recovered.")
        top = (m.get("top") or [{}])[0]
        if m.get("detected") and top.get("detected") and top["detected"] > 0.6 * m["detected"] and len(m.get("top") or []) > 1:
            w.append(f"{m['module']}: one finding ({top.get('what')}, £{top['detected']:,.0f}) is "
                     f"{100 * top['detected'] / m['detected']:.0f}% of the module's £{m['detected']:,.0f} - check its "
                     "baseline and tariff before quoting the total.")
    jobs = out.get("open_jobs") if isinstance(out.get("open_jobs"), list) else []
    priced = [j for j in jobs if j.get("energy_waste_gbp_per_year")]
    total = sum(j["energy_waste_gbp_per_year"] for j in priced)
    for j in priced:
        if total and j["energy_waste_gbp_per_year"] > 0.6 * total and len(priced) > 1:
            w.append(f"{j['asset']}: one finding is {100 * j['energy_waste_gbp_per_year'] / total:.0f}% of the priced energy "
                     "waste - check its baseline and tariff before quoting it.")
    for r in out.get("repeat_failures") if isinstance(out.get("repeat_failures"), list) else []:
        if r["jobs_per_month"] >= CALLOUT_CHECK:
            w.append(f"{r['asset']}: {r['reactive_jobs']} reactive jobs in the period ({r['jobs_per_month']}/month) - "
                     "confirm these are separate visits, not duplicate records, before acting on the totals.")
    return w
