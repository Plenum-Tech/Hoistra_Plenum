"""Feature B2 — the work orders behind a vendor's score (the Vendors page Evidence tab).

The scoring engine writes one `vendor_wo_scores` row per work order it scored, and the
monthly card is built from those rows — but nothing read them back, so the Evidence tab
could only say "the engine holds the jobs and no read lists them". This is that read.

A score row stores the verdicts (response met, completion met, first fix, recall) and not
the timestamps they were judged from, so the hours are read back off the work order and the
targets off the vendor's confirmed contract. Every lookup that enriches a row is best-effort
and in its own savepoint: a tenant whose assets or buildings table has a different shape
still gets its score rows, with the unresolved names left null rather than the list failing.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.contract_performance import ContractSlaParameters, VendorWoScore
from .scoring import (
    _iso,
    _parse_dt,
    _work_order_columns,
    hours_between,
    sla_target_hours,
)

log = get_logger(__name__)


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _utc_dt(v: Any) -> datetime | None:
    """A work-order timestamp as an aware datetime.

    to_jsonb keeps each column's own type, so a `timestamp` column arrives with no offset
    beside a `timestamptz` one that has it, and subtracting the two raises TypeError. The
    scoring fetch casts every one to timestamptz, which reads a naive value as UTC on these
    servers — the same here, so the hours shown are the hours that were scored.
    """
    # A varchar timestamp column can hold anything a migration wrote ("01/09/2026"). One
    # such value must blank that field, not fail the whole list.
    try:
        d = _parse_dt(v)
    except (ValueError, TypeError):
        return None
    if d is not None and d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _pick(j: dict[str, Any] | None, *keys: str) -> Any:
    if not j:
        return None
    for k in keys:
        v = j.get(k)
        if v not in (None, ""):
            return v
    return None


def evidence_row(
    score: dict[str, Any],
    *,
    work_order: dict[str, Any] | None,
    asset: dict[str, Any] | None,
    building: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    """One scored work order, with what it was measured against. Pure — no database.

    `score` is a vendor_wo_scores row as a dict; `work_order`, `asset` and `building` are
    the matching rows as JSON objects (any shape), each None when it could not be found.
    Hours are null when either timestamp is missing, and a target is null when the contract
    does not state one for that priority — never a guessed default.
    """
    wo = work_order or {}
    comps = score.get("component_scores") or {}
    priority = _pick(wo, "priority", "wo_priority")
    reported = _utc_dt(_pick(wo, "reported_at", "raised_date", "created_at", "created_date"))
    attended = _utc_dt(_pick(wo, "attended_at", "responded_at", "response_at"))
    completed = _utc_dt(_pick(wo, "completed_at", "completion_date"))
    p = params or {}
    confirmed = bool(p.get("_contract_confirmed"))

    def hrs(v: float | None) -> float | None:
        return round(v, 2) if v is not None else None

    return {
        "id": str(score["id"]),
        "vendor_id": str(score["vendor_id"]) if score.get("vendor_id") else None,
        "wo_code": score.get("wo_code"),
        "score_month": _iso(score.get("score_month")),
        "priority": priority,
        "asset_id": str(score["asset_id"]) if score.get("asset_id") else None,
        "asset_name": _pick(asset, "asset_name", "name", "asset_code"),
        "building_id": _pick(wo, "building_id") or _pick(asset, "building_id"),
        "building_name": _pick(building, "name", "building_name", "building_code"),
        "criticality": comps.get("criticality"),
        "criticality_weight": comps.get("criticality_weight"),
        "reported_at": _iso(reported),
        "attended_at": _iso(attended),
        "completed_at": _iso(completed),
        "sla_response_met": score.get("sla_response_met"),
        "sla_completion_met": score.get("sla_completion_met"),
        "response_hours": hrs(hours_between(reported, attended)),
        "completion_hours": hrs(hours_between(reported, completed)),
        # Only a confirmed contract's hours are a target. Defaults are a platform number the
        # vendor never agreed to, and scoring refuses to run on them for the same reason.
        "response_target_hours": sla_target_hours(p, priority, "response") if confirmed else None,
        "completion_target_hours": sla_target_hours(p, priority, "completion") if confirmed else None,
        "contract_parameters_id": p.get("_contract_parameters_id") if confirmed else None,
        "first_fix": score.get("first_fix"),
        "recall": score.get("recall"),
        "accreditation_current": score.get("accreditation_current"),
        "component_scores": comps,
        "overall_score": _f(score.get("overall_score")),
        "capped_by_block": bool(score.get("capped_by_block")),
        "cost_actual": _f(score.get("cost_actual")),
        "cost_estimated": _f(score.get("cost_estimated")),
        "cost_variance_pct": _f(score.get("cost_variance_pct")),
    }


async def _confirmed_params_read_only(session: AsyncSession, vendor_id: Any) -> dict[str, Any]:
    """The vendor's governing confirmed contract, chosen as scoring chooses it.

    Not scoring's `_load_confirmed_params`: that one enqueues an approval when two confirmed
    contracts share a signed date, and a GET must not write. {} when none is confirmed.
    """
    from sqlalchemy import nulls_last

    from .parameters import params_to_dict

    async with session.begin_nested():
        row = await _confirmed_row(session, vendor_id)
    if row is None:
        return {}
    out = params_to_dict(row)
    out["_contract_confirmed"] = True
    out["_contract_parameters_id"] = str(row.id)
    return out


async def _confirmed_row(session: AsyncSession, vendor_id: Any) -> Any:
    from sqlalchemy import nulls_last

    return (
        await session.execute(
            select(ContractSlaParameters)
            .where(
                ContractSlaParameters.status == "confirmed",
                ContractSlaParameters.vendor_id == vendor_id,
            )
            .order_by(
                nulls_last(ContractSlaParameters.signed_date.desc()),
                ContractSlaParameters.confirmed_at.desc(),
            )
            .limit(1)
        )
    ).scalars().first()


async def _json_rows(
    session: AsyncSession, sql: str, ids: list[str], label: str,
    params: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """{key: [rows-as-json]} for a fixed statement over an expanding id list, {} on failure.

    A list per key: wo_code is not unique across a portfolio, and the caller picks the row
    that belongs to the scored vendor rather than whichever came back last."""
    if not ids:
        return {}
    try:
        async with session.begin_nested():
            stmt = text(sql).bindparams(bindparam("ids", expanding=True))
            rows = (await session.execute(stmt, {"ids": ids, **(params or {})})).mappings().all()
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            if r["k"] is not None:
                out.setdefault(str(r["k"]), []).append(dict(r["j"] or {}))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("wo_evidence.lookup_failed", table=label, error=str(exc)[:200])
        return {}


def pick_work_order(candidates: list[dict[str, Any]] | None, vendor_id: Any) -> dict[str, Any] | None:
    """The work order behind a score row, among same-coded rows. Pure.

    Prefers the row assigned to the scored vendor; with none matching, a single candidate
    is used and several are refused — guessing between two companies' WO-1001 would show
    one company another's timestamps."""
    rows = candidates or []
    if not rows:
        return None
    vid = str(vendor_id) if vendor_id else None
    if vid:
        for r in rows:
            if vid in (str(r.get("vendor_id") or ""), str(r.get("assigned_vendor") or "")):
                return r
    return rows[0] if len(rows) == 1 else None


async def list_wo_evidence(
    session: AsyncSession,
    *,
    vendor_id: UUID | None = None,
    score_month: date | None = None,
    organization_id: UUID | None = None,
    limit: int = 200,
    building_ids: tuple[UUID, ...] | None = None,
    latest_only: bool = False,
) -> list[dict[str, Any]]:
    """The scored work orders, newest month first, each with its hours and targets.

    `latest_only` keeps each vendor's newest scored month and nothing older — what the
    Vendors page shows beside each card. Without it a portfolio-wide read under one limit
    let a busy vendor's history crowd out another vendor's current month."""
    q = (
        select(VendorWoScore)
        .order_by(VendorWoScore.score_month.desc(), VendorWoScore.wo_code)
        .limit(max(1, min(int(limit or 200), 1000)))
    )
    if building_ids is not None:
        from ..auth import access as _access

        q = _access.orm_where(q, *_access.vendor_predicate(building_ids, "vendor_id"))
    if vendor_id:
        q = q.where(VendorWoScore.vendor_id == vendor_id)
    if score_month:
        q = q.where(VendorWoScore.score_month == score_month)
    if organization_id:
        q = q.where(VendorWoScore.organization_id == organization_id)
    if latest_only and not score_month:
        from sqlalchemy import func, tuple_

        from ...models.contract_performance import VendorMonthlyScorecard

        # The month of each vendor's newest SCORECARD — the card the page shows beside this
        # list — not the newest month anything was scored. Scoring can run for a month whose
        # card has not been cut yet; keyed on the scores, the list then held only that month
        # and the card on screen found none of its own rows.
        from sqlalchemy import or_

        carded = (select(VendorMonthlyScorecard.vendor_id, func.max(VendorMonthlyScorecard.score_month))
                  .group_by(VendorMonthlyScorecard.vendor_id))
        if organization_id:
            carded = carded.where(VendorMonthlyScorecard.organization_id == organization_id)
        # A vendor scored but never carded has no card month; its newest scored month stands in.
        card_vendors = select(VendorMonthlyScorecard.vendor_id)
        uncarded = (select(VendorWoScore.vendor_id, func.max(VendorWoScore.score_month))
                    .where(VendorWoScore.vendor_id.not_in(card_vendors))
                    .group_by(VendorWoScore.vendor_id))
        pair = tuple_(VendorWoScore.vendor_id, VendorWoScore.score_month)
        q = q.where(or_(pair.in_(carded), pair.in_(uncarded)))
    scores = [
        {c.name: getattr(r, c.key) for c in VendorWoScore.__table__.columns}
        for r in (await session.execute(q)).scalars().all()
    ]
    if not scores:
        return []

    # Work orders by wo_code — the handle every score row keeps (work_order_id is null
    # wherever work_orders.id is an integer). to_jsonb keeps this independent of which
    # optional columns a tenant's work_orders table has.
    cols = await _work_order_columns(session)
    codes = sorted({str(s["wo_code"]) for s in scores if s.get("wo_code")})
    wos: dict[str, list[dict[str, Any]]] = {}
    if "wo_code" in cols:
        # Narrowed to the company the score rows belong to, with the same null-company rule
        # the scoring fetch applies (_build_udr_wo_query) — a work order the scorer could
        # read is one this can read, and no other.
        orgs = sorted({str(s["organization_id"]) for s in scores if s.get("organization_id")})
        org_sql = ""
        if orgs and "organization_id" in cols:
            org_sql = (" AND (wo.organization_id IS NULL OR wo.organization_id::text = ANY(CAST(:orgs AS text[])))")
        wos = await _json_rows(
            session,
            "SELECT wo.wo_code::text AS k, to_jsonb(wo) AS j "
            "FROM plenum_cafm.work_orders wo WHERE wo.wo_code::text IN :ids" + org_sql,
            codes, "work_orders", {"orgs": orgs} if org_sql else None,
        )

    asset_ids = sorted({str(s["asset_id"]) for s in scores if s.get("asset_id")})
    assets = await _json_rows(
        session,
        "SELECT a.id::text AS k, to_jsonb(a) AS j FROM plenum_cafm.assets a WHERE a.id::text IN :ids",
        asset_ids, "assets",
    )

    first = lambda m, k: (m.get(k) or [None])[0]  # noqa: E731 — ids below are unique keys
    wo_for = {str(sc["id"]): pick_work_order(wos.get(str(sc.get("wo_code"))), sc.get("vendor_id")) for sc in scores}

    building_keys: set[str] = set()
    for sc in scores:
        b = _pick(wo_for[str(sc["id"])], "building_id") or _pick(
            first(assets, str(sc.get("asset_id"))), "building_id"
        )
        if b:
            building_keys.add(str(b))
    buildings = await _json_rows(
        session,
        "SELECT b.building_id::text AS k, to_jsonb(b) AS j "
        "FROM plenum_cafm.buildings b WHERE b.building_id::text IN :ids",
        sorted(building_keys), "buildings",
    )

    # One confirmed contract per vendor; loaded once each, not per row.
    params_by_vendor: dict[str, dict[str, Any]] = {}
    for vid in {s["vendor_id"] for s in scores if s.get("vendor_id")}:
        try:
            params_by_vendor[str(vid)] = await _confirmed_params_read_only(session, vid)
        except Exception as exc:  # noqa: BLE001
            log.warning("wo_evidence.params_failed", vendor_id=str(vid), error=str(exc)[:200])

    out: list[dict[str, Any]] = []
    for sc in scores:
        wo = wo_for[str(sc["id"])]
        asset = first(assets, str(sc.get("asset_id")))
        bkey = _pick(wo, "building_id") or _pick(asset, "building_id")
        out.append(
            evidence_row(
                sc,
                work_order=wo,
                asset=asset,
                building=first(buildings, str(bkey)) if bkey else None,
                params=params_by_vendor.get(str(sc.get("vendor_id"))),
            )
        )
    return out
