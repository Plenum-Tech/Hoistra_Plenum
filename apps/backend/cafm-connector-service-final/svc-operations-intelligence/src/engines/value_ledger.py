"""The Home page's money cards — the Platform value ledger and the P&L — read from the store.

The value card states its own rule, and this module enforces it in SQL rather than in a
constant: a line exists only where an engine recorded a priced row (detected), and a
saved figure only where a person's action is recorded on that same row. Nothing is
claimed for detection alone. A module whose engines record no priced value yet —
maintenance and compliance today — reports its activity and says why it shows no figure,
instead of showing one.

The P&L is the same discipline from the other side: actual spend is what the store can
price (billed invoice lines, metered kWh at each meter's stored tariff), and the budget
column is null on every head for as long as no budget ledger exists to read. The card's
"no budget ledger is connected yet" note is a fact this module keeps true, not a label.

Reads only. Every source is read inside its own savepoint, so a deployment missing one
of the tables degrades that line to "not counted" with the reason — never a failed read.
What "saved" means per module:

    energy    an anomaly priced at the meter's tariff whose status reached resolved or
              closed; superseded and dismissed firings are duplicates or judged wrong
              and are never value, detected or saved. Counted per METER — each meter's
              largest priced finding — because the rules overlap and a per-firing sum
              claims the same excess kWh several times over
    vendors   a flagged invoice line's delta the PM rejected — money held, which is
              contractually fixed; a challenge still in flight is detected only
    assets    replace-minus-repair on a recommendation that left "open" without being
              dismissed — an estimate, and the note says so
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from .auth import access

log = get_logger(__name__)

#: Column spellings that might say a work order was reactive rather than planned.
_WO_TYPE_COLS = ("wo_type", "maintenance_type", "work_type", "order_type")
#: How a reactive work order spells itself across source systems.
_REACTIVE_RE = "(reactive|unplanned|corrective|breakdown|emergency)"

_MODULE_NAMES = {
    "energy": "Energy", "vendors": "Vendors", "maintenance": "Maintenance",
    "compliance": "Compliance", "assets": "Assets",
}
_HEAD_NAMES = {
    "maintenance": "Maintenance", "energy": "Energy",
    "compliance": "Compliance", "unplanned": "Unplanned failure",
}


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return round(float(v), 2)
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, (datetime, date)) else (str(v) if v else None)


async def _grab(session: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    """One aggregate row as a plain dict, or {"error": why} — never an exception."""
    try:
        async with session.begin_nested():
            row = (await session.execute(text(sql), params)).mappings().first()
        return dict(row) if row is not None else {"error": "the query returned no row"}
    except Exception as exc:  # noqa: BLE001 — a missing table is a fact to report, not a failure
        log.warning("value.source_unreadable", error=str(exc)[:200])
        return {"error": str(exc)[:200]}


async def _grab_rows(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("value.source_unreadable", error=str(exc)[:200])
        return {"error": str(exc)[:200]}


async def _wo_type_column(session: AsyncSession) -> str | None:
    """The work_orders column that could say "reactive", on this deployment — if any."""
    try:
        async with session.begin_nested():
            cols = (await session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'plenum_cafm' AND table_name = 'work_orders'"
            ))).scalars().all()
    except Exception:  # noqa: BLE001
        return None
    have = {str(c) for c in cols}
    return next((c for c in _WO_TYPE_COLS if c in have), None)


async def read_value_summary(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    building_ids: tuple[UUID, ...] | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    """Every read the two cards need, one source at a time, then the pure shaping."""
    now = datetime.now(timezone.utc)
    yr = int(year or now.year)
    base: dict[str, Any] = {
        "y0": datetime(yr, 1, 1, tzinfo=timezone.utc),
        "y1": datetime(yr + 1, 1, 1, tzinfo=timezone.utc),
        "y0d": date(yr, 1, 1), "y1d": date(yr + 1, 1, 1),
    }
    org = ""
    if organization_id is not None:
        org = " AND organization_id = CAST(:org AS uuid)"
        base["org"] = str(organization_id)
    bpred, bparams = access.building_predicate(building_ids)
    apred, aparams = access.building_predicate(building_ids, "a.building_id", prefix="ascope")
    vpred, vparams = access.vendor_predicate(building_ids)
    mpred, mparams = access.building_predicate(building_ids, "m.building_id", prefix="mscope")

    raw: dict[str, Any] = {}

    # Detected is per METER, not per firing. The detection rules overlap: one meter's
    # out-of-hours waste fires nonocc_spike, weekend_spike and baseline_drift alike, and
    # each firing annualises largely the same excess kWh — summed per firing, the store
    # once claimed 120% of ALL metered consumption as excess, which is not a number.
    # Each meter therefore contributes its largest priced finding (a firing with no
    # meter stands alone), and saved is capped the same way on the resolved/closed
    # subset, so saved <= detected holds per meter by construction.
    #
    # And each SUPPLY once, not each meter. A sub-meter decomposes its building's incoming
    # supply: the floor meters split it by floor, the asset meters split it again by plant
    # (a chiller's kWh is also Basement kWh). Harbour Point, 25 Sep 2026: 2 supply meters
    # plus 34 sub-meters derived from them as fixed shares, and the card summed all 36 —
    # £375k "detected" where the supply meters' own findings came to £127k, because every
    # floor echoed the same main-meter anomaly in proportion. So per building and fuel the
    # figure is the LARGEST of three readings of the same kWh — the supply meters, the floor
    # decomposition, the asset decomposition — never their sum. A sub-meter-only finding
    # still counts whenever it outweighs what the supply meter saw.
    raw["anomalies"] = await _grab(session, f"""
        WITH firings AS (
            SELECT id, meter_id, financial_gbp, status
              FROM plenum_cafm.energy_anomalies
             WHERE status NOT IN ('superseded','dismissed')
               AND detected_at >= :y0 AND detected_at < :y1{org}{bpred}
        ),
        per_meter AS (
            SELECT COALESCE(meter_id::text, id::text) AS grp,
                   max(financial_gbp) AS mx,
                   max(financial_gbp) FILTER (WHERE status IN ('resolved','closed')) AS mx_saved
              FROM firings
             WHERE financial_gbp IS NOT NULL
             GROUP BY 1
        ),
        placed AS (
            SELECT p.mx, p.mx_saved,
                   COALESCE(m.building_id::text, 'solo:' || p.grp) AS bkey,
                   COALESCE(m.meter_type, 'unknown') AS fuel,
                   CASE WHEN m.id IS NULL OR m.is_sub_meter IS NOT TRUE THEN 'supply'
                        WHEN m.asset_id IS NOT NULL THEN 'asset'
                        ELSE 'floor' END AS role
              FROM per_meter p
              LEFT JOIN plenum_cafm.energy_meters m ON m.id::text = p.grp
        ),
        per_role AS (
            SELECT bkey, fuel, role, sum(mx) AS d, sum(COALESCE(mx_saved, 0)) AS s
              FROM placed GROUP BY 1, 2, 3
        ),
        per_supply AS (
            SELECT bkey, fuel, max(d) AS d, max(s) AS s FROM per_role GROUP BY 1, 2
        )
        SELECT (SELECT count(*) FROM firings) AS firings,
               (SELECT count(*) FROM firings WHERE financial_gbp IS NULL) AS unpriced,
               (SELECT count(*) FROM firings WHERE status IN ('resolved','closed')) AS actioned,
               (SELECT count(*) FROM per_meter) AS meters,
               (SELECT count(*) FROM placed WHERE role <> 'supply') AS sub_meters,
               COALESCE((SELECT sum(mx) FROM per_meter), 0) AS meter_sum,
               COALESCE((SELECT sum(d) FROM per_supply), 0) AS detected,
               COALESCE((SELECT sum(s) FROM per_supply), 0) AS saved
    """, {**base, **bparams})

    # One line per meter — its largest priced finding — so the drill-down's lines are
    # the very lines the module figure adds up. The buildings table spells its name
    # column differently across deployments (name here, building_name elsewhere), so the
    # row is read as jsonb and whichever spelling exists wins — a COALESCE over a column
    # that does not exist would fail the whole read at parse.
    org_a = org.replace("organization_id", "a.organization_id")
    # The drill-down lists the lines the module figure counts — and only those. With the
    # per-supply rule above, a building's figure comes from ONE reading of its supply per
    # fuel (the supply meters, or the floors, or the assets); listing every meter's top
    # finding put the same kWh on the card twice again, one supply line plus four floor
    # echoes of it. So each meter's largest finding is placed as above, the winning reading
    # per building and fuel is found the way per_supply finds it, and only its lines show.
    raw["anomaly_items"] = await _grab_rows(session, f"""
        WITH per_meter_top AS (
            SELECT DISTINCT ON (COALESCE(a.meter_id::text, a.id::text))
                   COALESCE(a.meter_id::text, a.id::text) AS grp,
                   a.anomaly_type AS what, a.building_id,
                   a.financial_gbp AS amount, a.status, a.detected_at AS at
              FROM plenum_cafm.energy_anomalies a
             WHERE a.status NOT IN ('superseded','dismissed') AND a.financial_gbp IS NOT NULL
               AND a.detected_at >= :y0 AND a.detected_at < :y1{org_a}{apred}
             ORDER BY COALESCE(a.meter_id::text, a.id::text), a.financial_gbp DESC
        ),
        placed AS (
            SELECT t.*,
                   COALESCE(m.building_id::text, 'solo:' || t.grp) AS bkey,
                   COALESCE(m.meter_type, 'unknown') AS fuel,
                   CASE WHEN m.id IS NULL OR m.is_sub_meter IS NOT TRUE THEN 'supply'
                        WHEN m.asset_id IS NOT NULL THEN 'asset'
                        ELSE 'floor' END AS role
              FROM per_meter_top t
              LEFT JOIN plenum_cafm.energy_meters m ON m.id::text = t.grp
        ),
        per_role AS (
            SELECT bkey, fuel, role, sum(amount) AS d FROM placed GROUP BY 1, 2, 3
        ),
        winner AS (
            SELECT DISTINCT ON (bkey, fuel) bkey, fuel, role
              FROM per_role ORDER BY bkey, fuel, d DESC
        )
        SELECT p.what,
               COALESCE(to_jsonb(b) ->> 'building_name', to_jsonb(b) ->> 'name',
                        to_jsonb(b) ->> 'building_code') AS building,
               p.amount, p.status, p.at
          FROM placed p
          JOIN winner w ON w.bkey = p.bkey AND w.fuel = p.fuel AND w.role = p.role
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = p.building_id
         ORDER BY p.amount DESC
         LIMIT 5
    """, {**base, **aparams})

    raw["invoice_lines"] = await _grab(session, f"""
        SELECT count(*) AS flagged,
               COALESCE(sum(delta_gbp), 0) AS detected,
               count(*) FILTER (WHERE pm_decision = 'reject') AS rejected,
               COALESCE(sum(delta_gbp) FILTER (WHERE pm_decision = 'reject'), 0) AS saved_rejected,
               count(*) FILTER (WHERE pm_decision = 'challenge') AS challenged,
               COALESCE(sum(delta_gbp) FILTER (WHERE pm_decision = 'challenge'), 0) AS challenged_delta
          FROM plenum_cafm.invoice_lines
         WHERE match_status = 'flagged' AND delta_gbp IS NOT NULL
           AND created_at >= :y0 AND created_at < :y1{org}{vpred}
    """, {**base, **vparams})

    raw["invoice_items"] = await _grab_rows(session, f"""
        SELECT COALESCE(description, invoice_ref, wo_code) AS what,
               delta_gbp AS amount, pm_decision, created_at AS at
          FROM plenum_cafm.invoice_lines
         WHERE match_status = 'flagged' AND delta_gbp IS NOT NULL
           AND created_at >= :y0 AND created_at < :y1{org}{vpred}
         ORDER BY delta_gbp DESC
         LIMIT 5
    """, {**base, **vparams})

    raw["variance"] = await _grab(session, f"""
        SELECT count(*) AS alerts, COALESCE(sum(total_delta), 0) AS detected
          FROM plenum_cafm.cost_variance_alerts
         WHERE score_month >= :y0d AND score_month < :y1d{org}{vpred}
    """, {**base, **vparams})

    # A flagged invoice line and a cost-variance alert can both describe one overrun on the
    # same vendor's work in the same month — the line prices what was billed over the rate,
    # the alert what the job cost over its estimate, and an over-rate bill raises both. They
    # are added for the figure, so the part they share is taken off once: per vendor and
    # month, the smaller of the two. Where only one exists, it counts in full.
    raw["vendor_overlap"] = await _grab(session, f"""
        WITH inv AS (
            SELECT vendor_id::text AS v, date_trunc('month', created_at)::date AS mth,
                   sum(delta_gbp) AS d
              FROM plenum_cafm.invoice_lines
             WHERE match_status = 'flagged' AND delta_gbp IS NOT NULL
               AND created_at >= :y0 AND created_at < :y1{org}{vpred}
             GROUP BY 1, 2
        ),
        var AS (
            SELECT vendor_id::text AS v, date_trunc('month', score_month)::date AS mth,
                   sum(total_delta) AS d
              FROM plenum_cafm.cost_variance_alerts
             WHERE score_month >= :y0d AND score_month < :y1d{org}{vpred}
             GROUP BY 1, 2
        )
        SELECT COALESCE(sum(least(inv.d, var.d)), 0) AS overlap
          FROM inv JOIN var ON inv.v = var.v AND inv.mth = var.mth
    """, {**base, **vparams})

    raw["recommendations"] = await _grab(session, f"""
        SELECT count(*) AS recs,
               COALESCE(sum(replace_cost_gbp), 0) AS at_risk,
               count(*) FILTER (WHERE status NOT IN ('open','dismissed')) AS actioned,
               COALESCE(sum(greatest(replace_cost_gbp - COALESCE(repair_cost_gbp, 0), 0))
                        FILTER (WHERE status NOT IN ('open','dismissed')), 0) AS preserved
          FROM plenum_cafm.energy_recommendations
         WHERE replace_cost_gbp IS NOT NULL
           AND created_at >= :y0 AND created_at < :y1{org}{bpred}
    """, {**base, **bparams})

    raw["rec_items"] = await _grab_rows(session, f"""
        SELECT summary AS what, replace_cost_gbp AS at_risk,
               greatest(replace_cost_gbp - COALESCE(repair_cost_gbp, 0), 0) AS preserved,
               status, created_at AS at
          FROM plenum_cafm.energy_recommendations
         WHERE replace_cost_gbp IS NOT NULL
           AND created_at >= :y0 AND created_at < :y1{org}{bpred}
         ORDER BY replace_cost_gbp DESC
         LIMIT 5
    """, {**base, **bparams})

    # The queue has no building column. For a building-restricted reader the items are
    # narrowed through the certificate each one is about; an item about no certificate is
    # not attributable to their buildings and is not counted for them.
    qpred, qparams = "", {}
    if building_ids is not None:
        cpred, cparams = access.building_predicate(building_ids, "c.building_id", prefix="qscope")
        qpred = (" AND related_entity_type = 'compliance_certificate'"
                 " AND related_entity_id IN (SELECT c.id FROM plenum_cafm.compliance_certificates c"
                 " WHERE TRUE" + cpred + ")")
        qparams = cparams
    raw["approvals"] = await _grab(session, f"""
        SELECT count(*) AS decided,
               count(*) FILTER (WHERE status = 'approved') AS approved
          FROM plenum_cafm.approvals_queue_items
         WHERE source_feature = 'A' AND decided_at IS NOT NULL
           AND decided_at >= :y0 AND decided_at < :y1{org}{qpred}
    """, {**base, **qparams})

    # The certificate store spells its tenant column both ways across migrations.
    cert_org = ""
    if organization_id is not None:
        cert_org = (" AND (organization_id = CAST(:org AS uuid)"
                    " OR org_id = CAST(:org AS uuid))")
    raw["certificates"] = await _grab(session, f"""
        SELECT count(*) AS issued
          FROM plenum_cafm.compliance_certificates
         WHERE issue_date >= :y0d AND issue_date < :y1d{cert_org}{bpred}
    """, {**base, **bparams})

    raw["work_orders"] = await _grab(session, f"""
        SELECT count(*) AS closed
          FROM plenum_cafm.work_orders
         WHERE COALESCE(closed_at, completed_at::timestamptz) >= :y0
           AND COALESCE(closed_at, completed_at::timestamptz) < :y1{org}{bpred}
    """, {**base, **bparams})

    raw["pnl_maintenance"] = await _grab(session, f"""
        SELECT COALESCE(sum(line_total), 0) AS actual, count(*) AS lines
          FROM plenum_cafm.invoice_lines
         WHERE line_total IS NOT NULL
           AND created_at >= :y0 AND created_at < :y1{org}{vpred}
    """, {**base, **vparams})

    org_m = org.replace("organization_id", "m.organization_id")
    raw["pnl_energy"] = await _grab(session, f"""
        SELECT COALESCE(sum(r.consumption_kwh * m.tariff_gbp_per_kwh), 0) AS actual,
               count(DISTINCT m.id) AS meters
          FROM plenum_cafm.meter_readings r
          JOIN plenum_cafm.energy_meters m ON m.id = r.meter_id
         WHERE r.reading_at >= :y0 AND r.reading_at < :y1
           -- Supply meters only. A sub-meter's kWh is already inside its supply meter's
           -- reading; adding it bills the same energy twice (Harbour Point: £466k shown,
           -- £329k actually drawn through its two supplies in 2026).
           AND m.is_sub_meter IS NOT TRUE{org_m}{mpred}
    """, {**base, **mparams})

    # Reactive spend needs a type column on work_orders, and not every source system
    # provides one — introspected, and the head says so when it cannot split.
    type_col = await _wo_type_column(session)
    if type_col is None:
        raw["pnl_unplanned"] = {"actual": None, "lines": 0, "type_column": None}
    else:
        org_l = org.replace("organization_id", "l.organization_id")
        vpred_l, vparams_l = access.vendor_predicate(building_ids, "l.vendor_id", prefix="lscope")
        got = await _grab(session, f"""
            SELECT COALESCE(sum(l.line_total), 0) AS actual, count(*) AS lines
              FROM plenum_cafm.invoice_lines l
              JOIN plenum_cafm.work_orders w ON CAST(w.id AS text) = CAST(l.work_order_id AS text)
             WHERE l.line_total IS NOT NULL AND w.{type_col} ~* :reactive
               AND l.created_at >= :y0 AND l.created_at < :y1{org_l}{vpred_l}
        """, {**base, **vparams_l, "reactive": _REACTIVE_RE})
        got["type_column"] = None if "error" in got else type_col
        raw["pnl_unplanned"] = got

    return shape_value_summary(raw, year=yr, now=now, restricted=building_ids is not None)


# ── the shaping — pure, so the tests need no database ────────────────────


def _err(src: Any) -> str | None:
    return src.get("error") if isinstance(src, dict) else None


def shape_value_summary(
    raw: dict[str, Any], *, year: int, now: datetime, restricted: bool,
) -> dict[str, Any]:
    modules = [
        _energy_module(raw.get("anomalies") or {"error": "not read"},
                       raw.get("anomaly_items") or []),
        _vendors_module(raw.get("invoice_lines") or {"error": "not read"},
                        raw.get("variance") or {"error": "not read"},
                        raw.get("invoice_items") or [],
                        raw.get("vendor_overlap") or {}),
        _maintenance_module(raw.get("work_orders") or {"error": "not read"}),
        _compliance_module(raw.get("approvals") or {"error": "not read"},
                           raw.get("certificates") or {"error": "not read"}),
        _assets_module(raw.get("recommendations") or {"error": "not read"},
                       raw.get("rec_items") or []),
    ]
    counted = [m for m in modules if m["counted"]]
    return {
        "ok": True,
        "year": year,
        "as_of": now.isoformat(),
        "currency": "GBP",
        "scoped_to_buildings": restricted,
        "ledger": {
            "modules": modules,
            "counted_modules": len(counted),
            "total_detected": round(sum(m["detected"] or 0 for m in counted), 2) if counted else None,
            "total_saved": round(sum(m["saved"] or 0 for m in counted), 2) if counted else None,
            "note": ("A line exists only where an engine recorded a priced row and a decision "
                     "is recorded against it; a module with nothing priced says so."
                     + (" Narrowed to your allocated buildings." if restricted else "")),
        },
        "pnl": _pnl(raw),
    }


def _energy_module(agg: dict[str, Any], items: Any) -> dict[str, Any]:
    err = _err(agg)
    if err:
        return _not_counted("energy", f"the anomaly store could not be read — {err}")
    saved_st = ("resolved", "closed")
    rows = items if isinstance(items, list) else []
    return {
        "key": "energy", "name": _MODULE_NAMES["energy"], "counted": True,
        "detected": _num(agg.get("detected")) or 0.0,
        "saved": _num(agg.get("saved")) or 0.0,
        "items": [{
            "what": (str(r.get("what") or "").replace("_", " ").capitalize()
                     + (f" · {r['building']}" if r.get("building") else "")),
            "action": ("Resolved" if r.get("status") in saved_st
                       else str(r.get("status") or "open").capitalize()),
            "detected": _num(r.get("amount")),
            "saved": _num(r.get("amount")) if r.get("status") in saved_st else None,
            "basis": "measured · priced at the meter's tariff",
            "at": _iso(r.get("at")),
        } for r in rows],
        "note": (f"{int(agg.get('firings') or 0)} anomalies this year on "
                 f"{int(agg.get('meters') or 0)} meters, "
                 f"{int(agg.get('unpriced') or 0)} of them unpriced and carrying no figure; "
                 f"{int(agg.get('actioned') or 0)} resolved or closed. The rules overlap, "
                 "so detected counts each meter's largest priced finding, not the sum "
                 "of firings"
                 + (f"; and each supply once — {int(agg.get('sub_meters') or 0)} sub-meters "
                    "split the same kWh by floor and by plant, so per building and fuel the "
                    "largest of those readings counts, not their sum"
                    if int(agg.get("sub_meters") or 0) else "")
                 + "."),
    }


def _vendors_module(lines: dict[str, Any], variance: dict[str, Any], items: Any,
                    overlap: dict[str, Any] | None = None) -> dict[str, Any]:
    lerr, verr = _err(lines), _err(variance)
    if lerr and verr:
        return _not_counted(
            "vendors", f"neither invoice lines nor variance alerts could be read — {lerr}")
    detected = 0.0
    notes: list[str] = []
    if lerr:
        notes.append(f"invoice lines could not be read — {lerr}")
        saved = 0.0
    else:
        detected += _num(lines.get("detected")) or 0.0
        saved = _num(lines.get("saved_rejected")) or 0.0
        notes.append(
            f"{int(lines.get('flagged') or 0)} flagged invoice lines; "
            f"{int(lines.get('rejected') or 0)} rejected (held), "
            f"{int(lines.get('challenged') or 0)} under challenge — a challenge in flight "
            "is detected, not saved.")
    if verr:
        notes.append(f"variance alerts could not be read — {verr}")
    else:
        detected += _num(variance.get("detected")) or 0.0
        notes.append(f"{int(variance.get('alerts') or 0)} systematic overrun alerts, "
                     "detection only — an acknowledgement recovers nothing.")
        # Only when both halves were read can they overlap.
        shared = 0.0 if lerr or _err(overlap) else (_num((overlap or {}).get("overlap")) or 0.0)
        if shared:
            detected -= shared
            notes.append(f"£{shared:,.0f} described by both an invoice flag and a variance "
                         "alert for the same vendor and month is counted once.")
    rows = items if isinstance(items, list) else []
    return {
        "key": "vendors", "name": _MODULE_NAMES["vendors"], "counted": True,
        "detected": round(detected, 2), "saved": round(saved, 2),
        "items": [{
            "what": str(r.get("what") or "Flagged line"),
            "action": {"reject": "Rejected — held", "challenge": "Under challenge",
                       "approve": "Approved to pay"}.get(str(r.get("pm_decision") or ""),
                                                         "Awaiting decision"),
            "detected": _num(r.get("amount")),
            "saved": _num(r.get("amount")) if r.get("pm_decision") == "reject" else None,
            "basis": "invoice line outside the contract's rate schedule",
            "at": _iso(r.get("at")),
        } for r in rows],
        "note": " ".join(notes),
    }


def _maintenance_module(wo: dict[str, Any]) -> dict[str, Any]:
    err = _err(wo)
    activity = (f"{int(wo.get('closed') or 0)} work orders closed this year. " if not err
                else "")
    m = _not_counted(
        "maintenance",
        activity + "The work-order chain records no priced value yet — warranty "
        "recoveries, averted reactive cost and deferral models live in no table, "
        "so nothing is claimed.")
    return m


def _compliance_module(approvals: dict[str, Any], certs: dict[str, Any]) -> dict[str, Any]:
    aerr, cerr = _err(approvals), _err(certs)
    bits: list[str] = []
    if not aerr:
        bits.append(f"{int(approvals.get('decided') or 0)} compliance approvals decided "
                    f"({int(approvals.get('approved') or 0)} approved)")
    if not cerr:
        bits.append(f"{int(certs.get('issued') or 0)} certificates issued this year")
    activity = ("; ".join(bits) + ". ") if bits else ""
    return _not_counted(
        "compliance",
        activity + "Exposure at lapse is not priced anywhere in the store, so no figure "
        "is claimed for the renewals.")


def _assets_module(recs: dict[str, Any], items: Any) -> dict[str, Any]:
    err = _err(recs)
    if err:
        return _not_counted("assets", f"the recommendation store could not be read — {err}")
    rows = items if isinstance(items, list) else []
    return {
        "key": "assets", "name": _MODULE_NAMES["assets"], "counted": True,
        "detected": _num(recs.get("at_risk")) or 0.0,
        "saved": _num(recs.get("preserved")) or 0.0,
        "items": [{
            "what": str(r.get("what") or "Recommendation"),
            "action": ("Actioned" if str(r.get("status")) not in ("open", "dismissed")
                       else str(r.get("status") or "open").capitalize()),
            "detected": _num(r.get("at_risk")),
            "saved": (_num(r.get("preserved"))
                      if str(r.get("status")) not in ("open", "dismissed") else None),
            "basis": "estimated · replacement cost less repair cost",
            "at": _iso(r.get("at")),
        } for r in rows],
        "note": (f"{int(recs.get('recs') or 0)} priced recommendations, "
                 f"{int(recs.get('actioned') or 0)} actioned. Value preserved is "
                 "replacement cost less repair cost on the actioned ones — estimated, "
                 "not measured."),
    }


def _not_counted(key: str, note: str) -> dict[str, Any]:
    return {"key": key, "name": _MODULE_NAMES[key], "counted": False,
            "detected": None, "saved": None, "items": [], "note": note}


def _pnl(raw: dict[str, Any]) -> dict[str, Any]:
    maint = raw.get("pnl_maintenance") or {"error": "not read"}
    energy = raw.get("pnl_energy") or {"error": "not read"}
    unpl = raw.get("pnl_unplanned") or {"error": "not read"}

    def _head(key: str, actual: float | None, basis: str) -> dict[str, Any]:
        return {"key": key, "name": _HEAD_NAMES[key], "budget": None,
                "actual": actual, "basis": basis}

    merr = _err(maint)
    maintenance = _head(
        "maintenance",
        None if merr else _num(maint.get("actual")),
        f"invoice lines could not be read — {merr}" if merr else
        f"{int(maint.get('lines') or 0)} invoice lines billed against work orders this year")

    eerr = _err(energy)
    energy_head = _head(
        "energy",
        None if eerr else _num(energy.get("actual")),
        f"meter readings could not be read — {eerr}" if eerr else
        f"metered consumption on the supply meters, priced at each meter's stored tariff "
        f"({int(energy.get('meters') or 0)} meters)")

    compliance = _head(
        "compliance", None,
        "no billed compliance cost lands in the store yet — bookings and renewals "
        "carry no invoice here")

    uerr = _err(unpl)
    if uerr:
        unplanned = _head("unplanned", None, f"could not be read — {uerr}")
    elif unpl.get("type_column") is None:
        unplanned = _head(
            "unplanned", None,
            "work_orders carries no type column on this deployment, so reactive spend "
            "cannot be split out of the billed lines")
    else:
        unplanned = _head(
            "unplanned", _num(unpl.get("actual")),
            f"invoice lines on work orders whose {unpl['type_column']} reads reactive "
            f"({int(unpl.get('lines') or 0)} lines)")

    return {
        "budget_connected": False,
        "saved": None,
        "heads": [maintenance, energy_head, compliance, unplanned],
        "note": ("No budget ledger is connected — budgets are null and no saving against "
                 "budget is claimed. Actuals are read from the store."),
    }
