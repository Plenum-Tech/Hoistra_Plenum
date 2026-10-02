"""Which open work orders cannot proceed because of the vendor - and why.

"Out of those, which can't we proceed with because vendor compliance or a contract renewal is
pending?" used to take the agent two reads it then joined by hand, and on 2 Oct 2026 it answered
"no work order is held for a contract renewal" without having read a contract. This is that join
as one deterministic read over the open work orders in scope:

    work order -> its vendor -> the vendor's block state, its lapsed / expiring accreditation
    certificates, its contracts (expired, ending soon, not active) and whether its SLA terms are
    confirmed.

Only VENDOR-scope certificates count as the vendor's accreditation: a statutory certificate on a
building or asset the vendor services (an FRA, a LOLER) is the building's compliance, not the vendor's.

A HARD blocker stops the job today: the vendor is blocked, an accreditation has lapsed, the
contract has expired or is not active. A SOFT one is a risk to call out: an accreditation expiring,
a contract ending within RENEWAL_DAYS with no renewal recorded, SLA terms still a draft. A work
order with neither is `clear`. Scoped to the caller's company and buildings; reads only.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from .value_ledger import _grab_rows

log = get_logger(__name__)

#: A contract ending within this many days with nothing renewed is "renewal pending".
RENEWAL_DAYS = 60
OPEN_EXCLUDED = ("completed", "closed", "cancelled", "complete", "done")
HARD = ("vendor_blocked", "accreditation_lapsed", "contract_expired", "contract_inactive")
LIMIT = 200


def _d(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def classify(row: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """The blockers on one work order, hard first. Pure: the same row always classifies the same."""
    t = today or date.today()
    out: list[dict[str, Any]] = []
    if (row.get("block_state") or "").lower() == "blocked":
        out.append({"kind": "vendor_blocked", "hard": True,
                    "detail": row.get("block_reason") or ("Vendor blocked: " + (row.get("blocked_accreditation_type") or "accreditation"))})
    for c in row.get("certs") or []:
        st = (c.get("status") or "").lower()
        exp = _d(c.get("expiry"))
        # "Lapsed" or past its expiry stops the job. The register also marks a certificate "Overdue"
        # ahead of its expiry (the renewal is overdue, the cover is not): that is a risk, not a stop.
        if st in ("lapsed", "expired") or (exp and exp < t):
            out.append({"kind": "accreditation_lapsed", "hard": True, "date": exp.isoformat() if exp else None,
                        "detail": f"{c.get('type') or 'Accreditation'} lapsed" + (f" on {exp.isoformat()}" if exp else "")})
        elif st in ("expiring soon", "overdue") or (exp and (exp - t).days <= RENEWAL_DAYS):
            out.append({"kind": "accreditation_expiring", "hard": False, "date": exp.isoformat() if exp else None,
                        "detail": f"{c.get('type') or 'Accreditation'} expires" + (f" on {exp.isoformat()}" if exp else " soon")})
    for k in row.get("contracts") or []:
        end = _d(k.get("end"))
        st = (k.get("status") or "").lower()
        name = k.get("name") or "Contract"
        if end and end < t:
            out.append({"kind": "contract_expired", "hard": True, "date": end.isoformat(),
                        "detail": f"{name} ended {end.isoformat()}; no renewal recorded"})
        elif st and st not in ("active", "confirmed", "signed", "live", "current"):
            out.append({"kind": "contract_inactive", "hard": True, "date": end.isoformat() if end else None,
                        "detail": f"{name} is {st}"})
        elif end and (end - t).days <= RENEWAL_DAYS:
            out.append({"kind": "contract_renewal_due", "hard": False, "date": end.isoformat(),
                        "detail": f"{name} ends {end.isoformat()} ({(end - t).days} days); renewal not recorded"})
    for p in row.get("unconfirmed_sla") or []:
        out.append({"kind": "contract_terms_unconfirmed", "hard": False,
                    "detail": f"SLA terms for {p.get('ref') or 'the contract'} are {p.get('status') or 'unconfirmed'}"})
    # One line per kind, hard first, so a vendor with three lapsed certificates reads as one blocker
    # with the first detail rather than three rows.
    seen: dict[str, dict[str, Any]] = {}
    for b in out:
        seen.setdefault(b["kind"], b)
    return sorted(seen.values(), key=lambda b: (not b["hard"], b["kind"]))


async def read_blockers(session: AsyncSession, *, organization_id: UUID | None,
                        building_ids: tuple[UUID, ...] | None = None, building_id: UUID | None = None,
                        period_from: date | None = None, period_to: date | None = None,
                        wo_codes: list[str] | None = None, today: date | None = None) -> dict[str, Any]:
    t = today or date.today()
    where = ["lower(coalesce(w.status, '')) <> ALL(:wb_closed)"]
    from datetime import timedelta

    p: dict[str, Any] = {"wb_closed": list(OPEN_EXCLUDED), "wb_horizon": t + timedelta(days=RENEWAL_DAYS)}
    if organization_id:
        where.append("(w.organization_id = CAST(:wb_org AS uuid) OR b.organization_id = CAST(:wb_org AS uuid))")
        p["wb_org"] = str(organization_id)
    if building_ids is not None:
        if not building_ids:
            where.append("FALSE")
        else:
            where.append("w.building_id = ANY(CAST(:wb_bids AS uuid[]))")
            p["wb_bids"] = [str(x) for x in building_ids]
    if building_id:
        where.append("w.building_id = CAST(:wb_bid AS uuid)")
        p["wb_bid"] = str(building_id)
    if period_from:
        where.append("coalesce(w.raised_at, w.reported_at, w.created_at) >= :wb_from")
        p["wb_from"] = datetime(period_from.year, period_from.month, period_from.day)
    if period_to:
        where.append("coalesce(w.raised_at, w.reported_at, w.created_at) < :wb_to")
        p["wb_to"] = datetime(period_to.year, period_to.month, period_to.day)
    if wo_codes:
        where.append("w.wo_code = ANY(:wb_codes)")
        p["wb_codes"] = [str(c) for c in wo_codes][:LIMIT]
    rows = await _grab_rows(session, f"""
        WITH wo AS (
            SELECT w.id, w.wo_code, w.title, w.status, w.priority, w.sla_due_at, w.asset_name, w.asset_code,
                   b.name AS building_name, b.building_code,
                   COALESCE(w.vendor_id, w.assigned_vendor) AS vid, w.vendor_name AS wo_vendor_name
              FROM plenum_cafm.work_orders w
              LEFT JOIN plenum_cafm.buildings b ON b.building_id = w.building_id
             WHERE {" AND ".join(where)}
             ORDER BY w.sla_due_at NULLS LAST, w.wo_code
             LIMIT {LIMIT})
        SELECT wo.wo_code, wo.title, wo.status, wo.priority, wo.sla_due_at, wo.asset_name, wo.asset_code,
               wo.building_name, wo.building_code,
               COALESCE(v.vendor_name, wo.wo_vendor_name) AS vendor, v.block_state, v.block_reason,
               v.blocked_accreditation_type,
               (SELECT json_agg(json_build_object('type', coalesce(c.cert_type, c.certificate_type_code), 'status', c.status,
                                                   'expiry', c.expiry_date))
                  FROM plenum_cafm.compliance_certificates c
                 WHERE c.vendor_id = wo.vid
                   AND lower(coalesce(c.cert_scope, 'vendor')) = 'vendor'
                   AND (lower(coalesce(c.status, '')) IN ('lapsed', 'overdue', 'expired', 'expiring soon')
                        OR (c.expiry_date IS NOT NULL AND c.expiry_date < CAST(:wb_horizon AS date)))) AS certs,
               (SELECT json_agg(json_build_object('name', k.contract_name, 'end', k.contract_end, 'status', k.status))
                  FROM plenum_cafm.vendor_contracts k
                 WHERE k.vendor_id = wo.vid
                   AND (k.contract_end IS NULL OR k.contract_end < CAST(:wb_horizon AS date)
                        OR lower(coalesce(k.status, 'active')) NOT IN ('active', 'confirmed', 'signed', 'live', 'current'))) AS contracts,
               (SELECT json_agg(json_build_object('ref', s.contract_ref, 'status', s.status))
                  FROM plenum_cafm.contract_sla_parameters s
                 WHERE s.vendor_id = wo.vid AND lower(coalesce(s.status, '')) <> 'confirmed'
                   AND NOT EXISTS (SELECT 1 FROM plenum_cafm.contract_sla_parameters s2
                                    WHERE s2.vendor_id = wo.vid AND lower(coalesce(s2.status, '')) = 'confirmed')) AS unconfirmed_sla
          FROM wo LEFT JOIN plenum_cafm.vendors v ON v.id = wo.vid""", p)
    if isinstance(rows, dict):
        return {"ok": False, "error": rows.get("error")}
    import json

    blocked, at_risk, clear = [], [], []
    for r in rows:
        row = dict(r)
        for k in ("certs", "contracts", "unconfirmed_sla"):
            v = row.get(k)
            row[k] = json.loads(v) if isinstance(v, str) else (v or [])
        blockers = classify(row, t)
        item = {"wo_code": row["wo_code"], "title": row["title"], "status": row["status"], "priority": row["priority"],
                "sla_due_at": row["sla_due_at"].isoformat() if isinstance(row["sla_due_at"], datetime) else row["sla_due_at"],
                "asset": row.get("asset_name") or row.get("asset_code"), "building": row.get("building_name"),
                "building_code": row.get("building_code"), "vendor": row.get("vendor"), "blockers": blockers}
        if any(b["hard"] for b in blockers):
            blocked.append(item)
        elif blockers:
            at_risk.append(item)
        else:
            clear.append({k: item[k] for k in ("wo_code", "title", "status", "vendor")})
    return {
        "ok": True, "as_of": t.isoformat(), "renewal_window_days": RENEWAL_DAYS,
        "open_work_orders": len(rows), "blocked": blocked, "at_risk": at_risk, "clear": clear,
        "summary": {"blocked": len(blocked), "at_risk": len(at_risk), "clear": len(clear)},
        "answer_rules": ANSWER_RULES,
    }


ANSWER_RULES = """Answer from this read only:
- `blocked` are the work orders that cannot proceed today - a table | Work order | Asset | Vendor | Blocker |
  Priority | Due |, the blocker being each `hard` entry's detail. Say the count in one line first.
- `at_risk` can proceed today but carry a vendor risk (accreditation expiring, contract ending within the
  renewal window with no renewal recorded, SLA terms unconfirmed): list them separately and say so.
- `clear` have no vendor blocker: say how many, do not list them unless asked.
- Never call a renewal "pending" or "not pending" beyond what `contracts` shows; when a vendor has no
  contract on file say "no contract on file".
- Then "What needs to happen": renew or reassign, per blocked vendor.
"""
