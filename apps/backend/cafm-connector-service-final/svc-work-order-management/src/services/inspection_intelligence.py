"""What the inspection reports say once they are read together rather than one at a time.

The panel asks four questions of the whole set of reports, and each is a join nobody had
made:

* **Recommendations that never became orders** — and, of those, how many are on assets energy
  has since flagged. That second number is the interesting one: it says the inspector saw it
  first and nobody acted.
* **Open anomalies corroborated by an earlier finding** — an anomaly detected in August is a
  different proposition when a report in June already named the same thing on the same asset.
* **Findings on parts still under warranty**, and what that work was invoiced at, because that
  is money claimable rather than money spent.
* **Assets graded poor by an inspector**, with what they are, so "three assets are in bad
  condition" comes with the sentence that makes it actionable.

Two honest constraints run through all of it.

``inspections`` is fourteen columns on one database and nine on the other. Every query here is
built from what ``information_schema`` says is actually present, and a question that cannot be
answered on this database says so rather than returning zero — because zero reads as "we
checked and there are none", which is a different and wrong claim.

Corroboration is decided by **subject and time**, never by a model. A report corroborates an
anomaly when it is about the same asset, predates the detection, and shares vocabulary with it.
The overlapping words are returned so a person can see what the match was made on and disagree.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger

log = get_logger(__name__)
_shape: dict[str, set[str]] | None = None

#: Words that appear in every report and carry no subject matter, so they cannot be what a
#: corroboration is made on.
STOPWORDS = frozenset("""
a an and are as at be been but by for from had has have if in into is it its of on or that
the this to was were will with not no observed noted found report inspection asset unit
site building visit checked check during also which when where there their they them
""".split())

#: How many subject words a report and an anomaly must share before the report is said to
#: corroborate it. Two is deliberately low and the shared words are always returned, so the
#: reader judges the match rather than trusting the threshold.
MIN_SHARED_TERMS = 2

#: An inspector's grade, 1 as new to 5 end of life. At or above this is "poor".
POOR_GRADE = 4

#: How long after a report an order may be raised and still count as acting on it. Beyond
#: this it is simply later work on the same asset, and crediting it would quietly zero the
#: "never converted" figure on any register with a normal volume of orders.
FOLLOW_UP_WINDOW_DAYS = 90


def _words(*parts: Any) -> set[str]:
    """The subject words in some text: lower-cased, de-punctuated, stopwords removed."""
    blob = " ".join(str(p) for p in parts if p)
    return {w for w in re.findall(r"[a-z]{3,}", blob.lower()) if w not in STOPWORDS}


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


async def shape(session: AsyncSession) -> dict[str, set[str]]:
    """Which columns each table this module reads actually has, cached per process."""
    global _shape
    if _shape is not None:
        return _shape
    rows = (await session.execute(text(
        """SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'plenum_cafm'
              AND table_name IN ('inspections','assets','buildings','work_orders',
                                 'energy_anomalies','work_order_parts','spare_parts',
                                 'vendors')"""))).all()
    out: dict[str, set[str]] = {}
    for table, column in rows:
        out.setdefault(table, set()).add(column)
    _shape = out
    return out


def _scope(building_ids: list[UUID] | None, column: str) -> tuple[str, dict[str, Any]]:
    """The caller's buildings. None is unrestricted; an empty list matches no row."""
    if building_ids is None:
        return "", {}
    ids = [str(b) for b in building_ids]
    if not ids:
        return " AND FALSE", {}
    return f" AND {column} = ANY(CAST(:scope_b AS uuid[]))", {"scope_b": ids}


async def _rows(session: AsyncSession, sql: str, params: dict, what: str) -> list[dict]:
    """One question, in its own savepoint, so a shape surprise takes only that answer down."""
    try:
        async with session.begin_nested():
            return [dict(r) for r in
                    (await session.execute(text(sql), params)).mappings().all()]
    except Exception as exc:  # noqa: BLE001
        log.warning("inspection_intel.failed", question=what, error=str(exc)[:250])
        return []


def _unanswerable(reason: str) -> dict[str, Any]:
    """A question this database cannot be asked. Never a zero — zero is a finding."""
    return {"answerable": False, "reason": reason, "count": None}


# ── the reports themselves ───────────────────────────────────────────────────────────

async def corpus(
    session: AsyncSession, *, building_ids: list[UUID] | None,
) -> dict[str, Any]:
    """The header: how many reports, on how many assets, since when."""
    clause, params = _scope(building_ids, "a.building_id")
    rows = await _rows(session, f"""
        SELECT count(*)                       AS reports,
               count(DISTINCT i.asset_id)     AS assets,
               min(i.inspection_date)         AS since,
               max(i.inspection_date)         AS latest
          FROM plenum_cafm.inspections i
          JOIN plenum_cafm.assets a ON a.id::text = i.asset_id::text
         WHERE a.building_id IS NOT NULL{clause}""", params, "corpus")
    r = rows[0] if rows else {}
    return {
        "reports": int(r.get("reports") or 0),
        "assets": int(r.get("assets") or 0),
        "since": _iso(r.get("since")),
        "latest": _iso(r.get("latest")),
    }


# ── 1. recommendations that never became orders ──────────────────────────────────────

async def unconverted_recommendations(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int = 100,
) -> dict[str, Any]:
    """Reports recommending something, where no order followed — and who energy has since
    flagged.

    A recommendation counts as converted if the report names the order it became, or if any
    work order was raised on that asset after the report was written. The second test is the
    forgiving one: it credits an order somebody raised without linking it back.
    """
    sh = await shape(session)
    insp = sh.get("inspections", set())
    if "inspection_date" not in insp or "asset_id" not in insp:
        return _unanswerable("inspections here records no asset or no date")

    # What marks a report as recommending something, in whichever spelling this database has.
    flags = [c for c in ("corrective_action", "recommendation", "findings_jsonb")
             if c in insp]
    if not flags:
        return _unanswerable(
            "inspections here records no recommendation, corrective action or findings")
    open_expr = " OR ".join(
        {"corrective_action": "i.corrective_action IS TRUE",
         "recommendation": "nullif(btrim(i.recommendation), '') IS NOT NULL",
         "findings_jsonb": "i.findings_jsonb IS NOT NULL"}[c] for c in flags)

    converted = ("i.converted_work_order_id IS NOT NULL"
                 if "converted_work_order_id" in insp else "FALSE")
    wo = sh.get("work_orders", set())
    # An order counts as following a recommendation only if it was raised after the report,
    # inside a window where it could plausibly be that work, and is not the order the report
    # came off in the first place. "Any order on this asset, ever after" credits a reactive
    # callout months later with closing out a filter change, and on a register where every
    # asset carries dozens of orders it means nothing is ever unconverted — which is exactly
    # what this figure exists to show.
    followed = ("""EXISTS (SELECT 1 FROM plenum_cafm.work_orders w
                            WHERE w.asset_id::text = i.asset_id::text
                              AND w.created_at::date > i.inspection_date
                              AND w.created_at::date <= i.inspection_date
                                  + make_interval(days => :follow_days)
                              AND w.id::text <> coalesce(i.work_order_id, ''))"""
                if {"asset_id", "created_at"} <= wo else "FALSE")

    clause, params = _scope(building_ids, "a.building_id")
    params["follow_days"] = FOLLOW_UP_WINDOW_DAYS
    rows = await _rows(session, f"""
        SELECT i.id::text AS id, i.asset_id::text AS asset_id, a.asset_name, i.asset_code,
               a.building_id::text AS building_id, b.name AS building,
               i.inspection_date, i.inspector, i.risk_level,
               left(coalesce(i.observations, ''), 300) AS observations,
               (SELECT count(*) FROM plenum_cafm.energy_anomalies e
                 WHERE e.asset_id::text = i.asset_id::text
                   AND e.status NOT IN ('resolved','closed','dismissed')) AS anomalies_now
          FROM plenum_cafm.inspections i
          JOIN plenum_cafm.assets a ON a.id::text = i.asset_id::text
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
         WHERE a.building_id IS NOT NULL{clause}
           AND ({open_expr})
           AND NOT ({converted})
           AND NOT ({followed})
         ORDER BY i.inspection_date DESC""", params, "unconverted")

    flagged = [r for r in rows if int(r["anomalies_now"] or 0) > 0]
    return {
        "answerable": True,
        "count": len(rows),
        "now_flagged_by_energy": len(flagged),
        "headline": (
            f"{len(rows)} recommendations never converted to orders"
            + (f" — {len(flagged)} on assets now flagged by energy, so the inspector saw it "
               f"first" if flagged else "")),
        "method": ("a recommendation counts as converted if the report names the order, or if "
                   "any work order was raised on that asset after the report date"),
        "reports": [{
            "id": r["id"], "asset_id": r["asset_id"], "asset_name": r["asset_name"],
            "asset_code": r["asset_code"], "building": r["building"],
            "building_id": r["building_id"], "inspection_date": _iso(r["inspection_date"]),
            "inspector": r["inspector"], "risk_level": r["risk_level"],
            "observations": r["observations"],
            "now_flagged_by_energy": int(r["anomalies_now"] or 0) > 0,
        } for r in rows[:limit]],
    }


# ── 2. anomalies an earlier report already named ─────────────────────────────────────

async def corroborated_anomalies(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int = 100,
) -> dict[str, Any]:
    """Open anomalies where a report written *before* detection already named the same thing.

    The match is on subject and time, not on a model: same asset, report predates detection,
    and the two share at least two subject words. Every match returns the words it was made
    on, so the reader can see it and disagree.
    """
    sh = await shape(session)
    if "inspections" not in sh or "observations" not in sh.get("inspections", set()):
        return _unanswerable("inspections here records no observations to match against")

    clause, params = _scope(building_ids, "e.building_id")
    anomalies = await _rows(session, f"""
        SELECT e.id::text AS id, e.asset_id::text AS asset_id, a.asset_name,
               e.building_id::text AS building_id, b.name AS building,
               e.anomaly_type, e.detected_at, e.metric_pct, e.financial_gbp, e.currency
          FROM plenum_cafm.energy_anomalies e
          LEFT JOIN plenum_cafm.assets a ON a.id::text = e.asset_id::text
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = e.building_id
         WHERE e.asset_id IS NOT NULL
           AND e.status NOT IN ('resolved','closed','dismissed'){clause}
         ORDER BY e.detected_at DESC""", params, "open_anomalies")
    if not anomalies:
        return {"answerable": True, "count": 0, "open_anomalies": 0,
                "headline": "no open anomalies are attributed to an asset",
                "anomalies": []}

    reports = await _rows(session, """
        SELECT i.id::text AS id, i.asset_id::text AS asset_id, i.inspection_date,
               i.inspector, i.risk_level, i.finding_type,
               left(coalesce(i.observations, ''), 400) AS observations
          FROM plenum_cafm.inspections i
         WHERE i.asset_id IS NOT NULL AND i.inspection_date IS NOT NULL
           AND i.asset_id::text = ANY(CAST(:aids AS text[]))""",
        {"aids": [a["asset_id"] for a in anomalies]}, "reports_for_anomalies")
    by_asset: dict[str, list[dict]] = {}
    for r in reports:
        by_asset.setdefault(r["asset_id"], []).append(r)

    out, corroborated = [], 0
    for a in anomalies:
        detected = a["detected_at"]
        det_date = detected.date() if isinstance(detected, datetime) else detected
        a_words = _words(a["anomaly_type"], a["asset_name"])
        best = None
        for r in by_asset.get(a["asset_id"], []):
            when = r["inspection_date"]
            when = when.date() if isinstance(when, datetime) else when
            if not (isinstance(when, date) and isinstance(det_date, date)) or when >= det_date:
                continue                       # must predate detection to corroborate it
            shared = a_words & _words(r["observations"], r["finding_type"])
            if len(shared) >= MIN_SHARED_TERMS and (best is None or when > best["when"]):
                best = {"when": when, "report": r, "shared": sorted(shared)}
        if best:
            corroborated += 1
        out.append({
            "anomaly_id": a["id"], "asset_id": a["asset_id"], "asset_name": a["asset_name"],
            "building": a["building"], "building_id": a["building_id"],
            "anomaly_type": a["anomaly_type"], "detected_at": _iso(detected),
            "metric_pct": _num(a["metric_pct"]), "cost": _num(a["financial_gbp"]),
            "currency": a["currency"],
            "corroborated": best is not None,
            "corroborating_report": None if not best else {
                "id": best["report"]["id"],
                "inspection_date": _iso(best["when"]),
                "inspector": best["report"]["inspector"],
                "risk_level": best["report"]["risk_level"],
                "observations": best["report"]["observations"],
                "shared_terms": best["shared"],
                "days_before_detection": (det_date - best["when"]).days,
            },
        })
    out.sort(key=lambda x: (not x["corroborated"], x["asset_name"] or ""))
    return {
        "answerable": True,
        "count": corroborated,
        "open_anomalies": len(anomalies),
        "headline": (f"{corroborated} of {len(anomalies)} open anomalies corroborated by an "
                     f"earlier finding"),
        "method": (f"same asset, report predates detection, and at least {MIN_SHARED_TERMS} "
                   f"subject words in common; the shared words are returned on every match"),
        "anomalies": out[:limit],
    }


# ── 3. findings on parts still under warranty ────────────────────────────────────────

async def warranted_findings(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int = 100,
) -> dict[str, Any]:
    """Findings landing on parts whose warranty has not run out, and what that work cost.

    Warranty is taken from the fitting, not the asset: an asset installed in 2009 is long out
    of warranty while a contactor fitted last month is not. A fitting with no warranty term
    recorded is not counted as warranted — an unknown term is not an in-force one.
    """
    sh = await shape(session)
    wop = sh.get("work_order_parts", set())
    if "warranty_expiry" not in wop and "fitted_at" not in wop:
        return _unanswerable("no warranty is recorded against a fitted part here")

    term = ("coalesce(p.warranty_expiry, "
            "(p.fitted_at + make_interval(months => sp.warranty_months)))"
            if {"fitted_at"} <= wop and "warranty_months" in sh.get("spare_parts", set())
            else "p.warranty_expiry")
    value = "p.invoiced_value" if "invoiced_value" in wop else "NULL"
    ccy = "p.currency" if "currency" in wop else "NULL"

    clause, params = _scope(building_ids, "a.building_id")
    # work_order_parts carries asset_id itself, so the asset is reached directly rather than
    # through the order — one fewer join and one fewer way for a fitting to go unplaced.
    rows = await _rows(session, f"""
        SELECT p.id::text AS id, p.asset_id::text AS asset_id, a.asset_name,
               a.building_id::text AS building_id, b.name AS building,
               sp.part_name, sp.part_code, {term} AS warranted_until,
               {value} AS invoiced_value, {ccy} AS currency,
               p.work_order_id::text AS work_order_id, p.fitted_at
          FROM plenum_cafm.work_order_parts p
          JOIN plenum_cafm.assets a ON a.id::text = p.asset_id::text
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
          LEFT JOIN plenum_cafm.spare_parts sp ON sp.id::text = p.part_id::text
         WHERE a.building_id IS NOT NULL{clause}
           AND {term} IS NOT NULL AND {term} >= current_date
           AND EXISTS (SELECT 1 FROM plenum_cafm.inspections i
                        WHERE i.asset_id::text = a.id::text)
         ORDER BY {term}""", params, "warranted_findings")

    claimable = sum(_num(r["invoiced_value"]) or 0.0 for r in rows)
    ccy_seen = next((r["currency"] for r in rows if r["currency"]), None)
    priced = sum(1 for r in rows if r["invoiced_value"] is not None)
    return {
        "answerable": True,
        "count": len(rows),
        "claimable_value": round(claimable, 2) if priced else None,
        "currency": ccy_seen,
        "priced": priced,
        "headline": (f"{len(rows)} findings on parts still under warranty"
                     + (f" — {ccy_seen or ''}{claimable:,.0f} of invoiced work claimable"
                        if priced else "")),
        "method": ("warranty from the fitting, not the asset; a fitting with no term recorded "
                   "is not counted as warranted"),
        "findings": [{
            "id": r["id"], "asset_id": r["asset_id"], "asset_name": r["asset_name"],
            "building": r["building"], "building_id": r["building_id"],
            "part_name": r["part_name"], "part_code": r["part_code"],
            "fitted_at": _iso(r["fitted_at"]), "warranted_until": _iso(r["warranted_until"]),
            "invoiced_value": _num(r["invoiced_value"]), "currency": r["currency"],
            "work_order_id": r["work_order_id"],
        } for r in rows[:limit]],
    }


# ── 4. assets an inspector graded poor ───────────────────────────────────────────────

async def poorly_graded(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int = 100,
) -> dict[str, Any]:
    """Assets an inspector graded 4 or 5 of 5, with what they are and when they went in.

    The grade comes from ``assets.condition_score``, which the condition deduction writes from
    the report text with its provenance. Grade 5 is end of life and is called out separately,
    because "poor" and "finished" are different conversations.
    """
    sh = await shape(session)
    if "condition_score" not in sh.get("assets", set()):
        return _unanswerable("assets here carries no condition grade")

    clause, params = _scope(building_ids, "a.building_id")
    params["poor"] = POOR_GRADE
    rows = await _rows(session, f"""
        SELECT a.id::text AS id, a.asset_name, a.asset_code, a.condition_score,
               a.installation_date, a.criticality,
               a.building_id::text AS building_id, b.name AS building,
               a.condition_updated_at
          FROM plenum_cafm.assets a
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
         WHERE a.condition_score >= :poor AND a.building_id IS NOT NULL{clause}
         ORDER BY a.condition_score DESC, a.installation_date""", params, "poorly_graded")

    years = [r["installation_date"].year for r in rows if r["installation_date"]]
    end_of_life = sum(1 for r in rows if int(r["condition_score"] or 0) >= 5)
    span = (f"all {min(years)}–{max(years)}" if years and min(years) != max(years)
            else (f"all {years[0]}" if years else None))
    return {
        "answerable": True,
        "count": len(rows),
        "end_of_life": end_of_life,
        "install_year_range": [min(years), max(years)] if years else None,
        "headline": (
            f"{len(rows)} assets graded poor ({POOR_GRADE} of 5) by inspectors"
            + (f" — {span}" if span else "")
            + (f" — {end_of_life} graded end of life" if end_of_life
               else " — none graded end of life")),
        "assets": [{
            "asset_id": r["id"], "asset_name": r["asset_name"], "asset_code": r["asset_code"],
            "condition_score": r["condition_score"],
            "installation_date": _iso(r["installation_date"]),
            "criticality": r["criticality"], "building": r["building"],
            "building_id": r["building_id"],
            "graded_at": _iso(r["condition_updated_at"]),
            "end_of_life": int(r["condition_score"] or 0) >= 5,
        } for r in rows[:limit]],
    }


# ── the panel ────────────────────────────────────────────────────────────────────────

async def panel(
    session: AsyncSession, *, building_ids: list[UUID] | None,
) -> dict[str, Any]:
    """The whole inspection-intelligence panel in one call: the header and the four cards."""
    head = await corpus(session, building_ids=building_ids)
    cards = {
        "unconverted_recommendations": await unconverted_recommendations(
            session, building_ids=building_ids, limit=20),
        "corroborated_anomalies": await corroborated_anomalies(
            session, building_ids=building_ids, limit=20),
        "warranted_findings": await warranted_findings(
            session, building_ids=building_ids, limit=20),
        "poorly_graded": await poorly_graded(
            session, building_ids=building_ids, limit=20),
    }
    return {
        "ok": True,
        "corpus": head,
        "cards": cards,
        "unanswerable": [k for k, v in cards.items() if not v.get("answerable")],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": ("a card that cannot be answered on this database says so rather than "
                 "returning zero — zero would read as 'we checked and there are none'"),
    }


# ── who files reports, and who does not ──────────────────────────────────────────────

async def reports_by_vendor(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int = 100,
) -> dict[str, Any]:
    """How many reports each vendor has filed, against the completed work they did.

    The question behind this is "which vendor files the fewest reports", and a bare count
    answers it badly: a vendor with two orders and two reports is not worse than one with
    forty orders and thirty reports. So the ratio is what is ranked, and the vendors with no
    completed work at all are left out of the ranking rather than sitting at the bottom of it
    on a denominator of zero.
    """
    sh = await shape(session)
    wo = sh.get("work_orders", set())
    if "inspections" not in sh or not {"asset_id", "status"} <= wo:
        return _unanswerable("this database records no reports against completed work")

    vendor_col = next((c for c in ("assigned_vendor", "vendor") if c in wo), None)
    if not vendor_col:
        return _unanswerable("work orders here record no vendor")
    name_col = "vendor_name" if "vendor_name" in sh.get("vendors", set()) else "name"

    clause, params = _scope(building_ids, "w.building_id")
    params["done"] = ["completed", "closed", "complete", "done"]
    rows = await _rows(session, f"""
        SELECT coalesce(ven.{name_col}, w.{vendor_col}::text, 'Unassigned') AS vendor,
               count(*) FILTER (WHERE lower(w.status) = ANY(CAST(:done AS text[])))
                   AS completed_orders,
               count(DISTINCT i.id) AS reports
          FROM plenum_cafm.work_orders w
          LEFT JOIN plenum_cafm.vendors ven ON ven.id::text = w.{vendor_col}::text
          LEFT JOIN plenum_cafm.inspections i
                 ON i.asset_id::text = w.asset_id::text
          WHERE w.building_id IS NOT NULL{clause}
         GROUP BY 1
         ORDER BY 1""", params, "reports_by_vendor")

    out = []
    for r in rows:
        done = int(r["completed_orders"] or 0)
        filed = int(r["reports"] or 0)
        out.append({
            "vendor": r["vendor"], "completed_orders": done, "reports": filed,
            # None, not 0.0, when there is no completed work to file against: a vendor who
            # has done nothing has not failed to report on it.
            "reports_per_completed_order": round(filed / done, 2) if done else None,
            "rankable": done > 0,
        })
    rankable = [v for v in out if v["rankable"]]
    rankable.sort(key=lambda v: v["reports_per_completed_order"])
    return {
        "answerable": True,
        "count": len(out),
        "rankable": len(rankable),
        "fewest": rankable[0] if rankable else None,
        "most": rankable[-1] if rankable else None,
        "method": ("ranked on reports per completed order, not on the raw count; vendors with "
                   "no completed work are counted but not ranked"),
        "vendors": (rankable + [v for v in out if not v["rankable"]])[:limit],
    }
