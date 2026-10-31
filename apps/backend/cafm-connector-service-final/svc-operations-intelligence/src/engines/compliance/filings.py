"""B8 — filings: LL84 benchmarking, the BCA Benchmarking Report, Green Mark.

Three obligations that are discharged by FILING something rather than by holding a
certificate that expires. New York's Local Law 84 wants the building's energy and water
benchmarked in Portfolio Manager and submitted to the city by 1 May each year. Singapore's
BCA wants the annual Building Energy Benchmarking Report submitted (the rating it produces
is voluntary; the submission is not). Green Mark is a voluntary certification, awarded at a
level and valid for three years.

Until now there was nowhere to record "we filed this", so the tile that says "Filed" could
only ever be a placeholder. This is the record: one row per building, scheme and compliance
year, and the position — filed / due / overdue / lapsed — derived from it and the calendar.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.compliance import RegulatoryFiling
from ...shared.approvals import write_audit

#: scheme → (label, country, due month/day for the compliance year, validity years if a
#: certification). period_year is the year the filing REPORTS ON; LL84 for 2025 is due
#: 1 May 2026, so due = (year + 1, month, day).
SCHEMES: dict[str, dict[str, Any]] = {
    "LL84": {"label": "LL84 benchmarking", "country": "US", "due": (5, 1), "due_year_offset": 1,
             "kind": "filing", "authority": "NYC Department of Buildings",
             "note": "annual submission via ENERGY STAR Portfolio Manager"},
    "BCA_BENCHMARKING": {"label": "BCA energy submission", "country": "SG", "due": (9, 30),
                         "due_year_offset": 1, "kind": "filing",
                         "authority": "Building and Construction Authority",
                         "note": "mandatory annual Building Energy Benchmarking Report"},
    "GREEN_MARK": {"label": "Green Mark", "country": "SG", "due": None, "due_year_offset": 0,
                   "kind": "certification", "validity_years": 3,
                   "levels": ("Certified", "Gold", "GoldPlus", "Platinum"),
                   "authority": "Building and Construction Authority",
                   "note": "voluntary certification, valid three years"},
}
FILED_STATUSES = frozenset({"filed", "submitted", "accepted", "certified"})
STATUSES = FILED_STATUSES | {"due", "overdue", "lapsed"}


class FilingError(ValueError):
    def __init__(self, reason: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.reason, self.message, self.http_status = reason, message, http_status


def due_date_for(scheme: str, period_year: int) -> date | None:
    spec = SCHEMES.get(scheme)
    if not spec or not spec.get("due"):
        return None
    month, day = spec["due"]
    return date(period_year + spec["due_year_offset"], month, day)


def _row(f: RegulatoryFiling) -> dict[str, Any]:
    return {
        "id": str(f.id), "building_id": str(f.building_id), "scheme": f.scheme,
        "scheme_label": SCHEMES.get(f.scheme, {}).get("label", f.scheme),
        "period_year": f.period_year, "status": f.status,
        "filed_at": f.filed_at.isoformat() if f.filed_at else None,
        "due_date": f.due_date.isoformat() if f.due_date else None,
        "reference": f.reference, "certification_level": f.certification_level,
        "valid_until": f.valid_until.isoformat() if f.valid_until else None,
        "submitted_by": f.submitted_by,
        "evidence_document_id": str(f.evidence_document_id) if f.evidence_document_id else None,
        "detail": f.detail_json or {},
    }


async def record_filing(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    building_id: UUID,
    scheme: str,
    period_year: int,
    status: str = "filed",
    filed_at: date | None = None,
    reference: str | None = None,
    certification_level: str | None = None,
    valid_until: date | None = None,
    submitted_by: str | None = None,
    evidence_document_id: UUID | None = None,
    detail: dict[str, Any] | None = None,
    actor: str = "system",
    created_by: UUID | None = None,
) -> dict[str, Any]:
    """Record (or update) the filing for one building, scheme and year. Idempotent on that
    triple: filing the same year twice updates the record rather than duplicating it."""
    scheme = scheme.upper().strip()
    spec = SCHEMES.get(scheme)
    if not spec:
        raise FilingError("unknown_scheme", f"Unknown scheme {scheme!r}. One of {sorted(SCHEMES)}.")
    if status not in STATUSES:
        raise FilingError("bad_status", f"status must be one of {sorted(STATUSES)}.")
    if spec["kind"] == "certification":
        if certification_level and certification_level not in spec["levels"]:
            raise FilingError("bad_level", f"{spec['label']} level must be one of {spec['levels']}.")
        if status in FILED_STATUSES and not valid_until:
            valid_until = (filed_at or date.today()).replace(year=(filed_at or date.today()).year + spec["validity_years"])
    if status in FILED_STATUSES and filed_at is None:
        filed_at = date.today()

    existing = (await session.execute(
        select(RegulatoryFiling).where(
            RegulatoryFiling.building_id == building_id,
            RegulatoryFiling.scheme == scheme,
            RegulatoryFiling.period_year == period_year,
        )
    )).scalar_one_or_none()
    row = existing or RegulatoryFiling(id=uuid4(), building_id=building_id, scheme=scheme,
                                       period_year=period_year, created_by=created_by)
    row.organization_id = organization_id or row.organization_id
    row.status = status
    row.filed_at = filed_at
    row.due_date = due_date_for(scheme, period_year)
    row.reference = reference or row.reference
    row.certification_level = certification_level or row.certification_level
    row.valid_until = valid_until or row.valid_until
    row.submitted_by = submitted_by or row.submitted_by
    row.evidence_document_id = evidence_document_id or row.evidence_document_id
    row.detail_json = {**(row.detail_json or {}), **(detail or {})}
    if existing is None:
        session.add(row)
    await session.flush()
    await write_audit(
        session, actor=actor, action_type=f"compliance.filing.{'updated' if existing else 'recorded'}",
        source_feature="A", organization_id=organization_id,
        input_payload={"building_id": str(building_id), "scheme": scheme, "period_year": period_year},
        output_payload={"id": str(row.id), "status": status},
    )
    await session.commit()
    return {"ok": True, "created": existing is None, "filing": _row(row)}


async def list_filings(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    building_ids: Iterable[UUID] | None = None,
    scheme: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    q = select(RegulatoryFiling).order_by(
        RegulatoryFiling.building_id, RegulatoryFiling.scheme, RegulatoryFiling.period_year.desc()
    ).limit(limit)
    if organization_id:
        q = q.where(RegulatoryFiling.organization_id == organization_id)
    if building_ids is not None:
        q = q.where(RegulatoryFiling.building_id.in_(list(building_ids) or [uuid4()]))
    if scheme:
        q = q.where(RegulatoryFiling.scheme == scheme.upper())
    return [_row(f) for f in (await session.execute(q)).scalars().all()]


def position_for(
    scheme: str, filings: list[dict[str, Any]], *, today: date | None = None
) -> dict[str, Any]:
    """Where one building stands on one scheme, from its filings and today's date.

    A filing scheme: the latest compliance year that is due by today must be on file —
    filed → "filed"; not filed and past due → "overdue"; not filed and not yet due → "due".
    A certification: the latest award that is still valid → "certified" with its level;
    an expired one → "lapsed"; none → "none".
    """
    today = today or date.today()
    spec = SCHEMES[scheme]
    mine = sorted((f for f in filings if f["scheme"] == scheme), key=lambda f: -f["period_year"])
    if spec["kind"] == "certification":
        valid = [f for f in mine if f["status"] in FILED_STATUSES
                 and (not f["valid_until"] or date.fromisoformat(f["valid_until"]) >= today)]
        if valid:
            f = valid[0]
            return {"scheme": scheme, "status": "certified", "level": f["certification_level"],
                    "valid_until": f["valid_until"], "filing": f}
        lapsed = [f for f in mine if f["status"] in FILED_STATUSES]
        if lapsed:
            return {"scheme": scheme, "status": "lapsed", "level": lapsed[0]["certification_level"],
                    "valid_until": lapsed[0]["valid_until"], "filing": lapsed[0]}
        return {"scheme": scheme, "status": "none", "level": None, "valid_until": None, "filing": None}

    # the most recent compliance year whose due date has passed is the one that must be filed
    year = today.year - spec["due_year_offset"]
    due = due_date_for(scheme, year)
    if due and due > today:
        year -= 1
        due = due_date_for(scheme, year)
    on_file = next((f for f in mine if f["period_year"] == year and f["status"] in FILED_STATUSES), None)
    if on_file:
        status = "filed"
    elif due and today > due:
        status = "overdue"
    else:
        status = "due"
    next_year = year + 1
    return {
        "scheme": scheme, "status": status, "period_year": year,
        "due_date": due.isoformat() if due else None, "filing": on_file,
        "next_due": (due_date_for(scheme, next_year) or today).isoformat(),
        "days_overdue": (today - due).days if status == "overdue" and due else 0,
    }


async def filing_positions(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    building_ids: Iterable[UUID],
    country_code: str,
    today: date | None = None,
) -> dict[str, Any]:
    """The filing tiles for one country across the caller's buildings."""
    today = today or date.today()
    ids = list(building_ids)
    schemes = [s for s, spec in SCHEMES.items() if spec["country"] == country_code.upper()]
    rows = await list_filings(session, organization_id=organization_id, building_ids=ids)
    by_building: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_building.setdefault(r["building_id"], []).append(r)
    out: dict[str, Any] = {"ok": True, "country_code": country_code.upper(), "schemes": {}, "tiles": []}
    for scheme in schemes:
        spec = SCHEMES[scheme]
        positions = {b: position_for(scheme, by_building.get(str(b), []), today=today) for b in ids}
        counts: dict[str, int] = {}
        for p in positions.values():
            counts[p["status"]] = counts.get(p["status"], 0) + 1
        out["schemes"][scheme] = {"label": spec["label"], "kind": spec["kind"], "counts": counts,
                                  "buildings": {str(b): p for b, p in positions.items()}}
        n = len(ids)
        if spec["kind"] == "certification":
            certified = counts.get("certified", 0)
            levels = sorted({p["level"] for p in positions.values() if p["status"] == "certified" and p["level"]})
            out["tiles"].append({
                "l": spec["label"], "v": (", ".join(levels) if levels else "None"),
                "s": (f"{certified} of {n} certified" if certified else "voluntary · not certified"),
                "tone": "ok" if certified else "dormant", "basis": "certificate",
            })
        else:
            filed = counts.get("filed", 0)
            overdue = counts.get("overdue", 0)
            out["tiles"].append({
                "l": spec["label"], "v": "Filed" if filed == n and n else ("Overdue" if overdue else "Due"),
                "s": (f"annual submission on record · {filed} of {n}" if filed == n and n
                      else f"{overdue} overdue · {filed} of {n} filed" if overdue
                      else f"{filed} of {n} filed · next due {min((p['next_due'] for p in positions.values()), default='')}"),
                "tone": "ok" if filed == n and n else ("risk" if overdue else "warn"), "basis": "filing",
            })
    return out
