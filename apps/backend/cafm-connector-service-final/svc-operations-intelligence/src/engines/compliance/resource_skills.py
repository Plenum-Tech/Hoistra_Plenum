"""A3 — operative-level ResourceSkill certifications."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import ResourceSkill
from ...shared.vendor_identity import deleted_vendor_ids_sql, vendor_link_refusal
from ..compliance.lifecycle import compute_certificate_status


# Operative modules remain distinct even when CountryPack groups them
# (ASBESTOS_P402_P403_P404, PA1_PA2_PA6) per Word pack cards.
OPERATIVE_CODES = {
    "ACS_CARD",
    "SIA_INDIVIDUAL",
    "P402",
    "P403",
    "P404",
    "PA1",
    "PA2",
    "PA6",
    "ASBESTOS_P402_P403_P404",
    "PA1_PA2_PA6",
}


async def upsert_resource_skill(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    vendor_id = data.get("vendor_id")
    skill = data.get("skill_type_code")
    if not vendor_id or not skill:
        return {"ok": False, "error": "vendor_id and skill_type_code required"}
    # The column is uuid; a legacy or malformed id failed later, mid-write, with a 500.
    try:
        vendor_id = UUID(str(vendor_id).strip())
    except (TypeError, ValueError):
        return {"ok": False, "reason": "invalid_vendor_id",
                "error": "That vendor id cannot hold an operative skill."}
    # A deleted vendor takes no new skills (Hussain, 12 Oct 2026), and neither does one removed
    # since, another company's, or one whose status cannot be read — this links new data to
    # the vendor, and a vendor with no row used to read as "not deleted" (review, 12 Oct 2026).
    refused = await vendor_link_refusal(
        session, vendor_id, data.get("organization_id"),
        what="no operative skill was recorded. Add the supplier again if it is still used",
    )
    if refused:
        return refused

    expiry = data.get("expiry_date")
    if isinstance(expiry, str):
        expiry = date.fromisoformat(expiry[:10])
    life = compute_certificate_status(expiry)

    skill_id = data.get("id")
    row = await session.get(ResourceSkill, UUID(str(skill_id))) if skill_id else None
    if not row:
        # Match existing by vendor + skill + cert number when re-ingesting
        q = select(ResourceSkill).where(
            ResourceSkill.vendor_id == UUID(str(vendor_id)),
            ResourceSkill.skill_type_code == skill,
        )
        if data.get("certificate_number"):
            q = q.where(ResourceSkill.certificate_number == data["certificate_number"])
        row = (await session.execute(q.limit(1))).scalar_one_or_none()
    if not row:
        row = ResourceSkill(id=uuid4())
        session.add(row)

    row.organization_id = UUID(str(data["organization_id"])) if data.get("organization_id") else None
    row.vendor_id = UUID(str(vendor_id))
    row.resource_id = UUID(str(data["resource_id"])) if data.get("resource_id") else None
    row.operative_name = data.get("operative_name")
    row.skill_type_code = skill
    row.certificate_number = data.get("certificate_number")
    row.issue_date = (
        date.fromisoformat(str(data["issue_date"])[:10]) if data.get("issue_date") else None
    )
    row.expiry_date = expiry
    row.status = life.status
    row.days_to_expiry = life.days_to_expiry
    row.updated_at = datetime.now(timezone.utc)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return {"ok": True, "resource_skill": _to_dict(row)}


async def list_vendor_operative_skills(
    session: AsyncSession,
    vendor_id: UUID | None = None,
    *,
    limit: int = 200,
    organization_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Operative skills held against vendors. ``organization_id=None`` is unrestricted, so an
    internal caller and a superadmin keep the cross-company view."""
    # A retired vendor's skills are history, not operatives on offer (Hussain, 12 Oct 2026).
    q = (
        select(ResourceSkill)
        .where(_live_vendor_skill())
        .order_by(ResourceSkill.updated_at.desc())
        .limit(limit)
    )
    if vendor_id is not None:
        q = q.where(ResourceSkill.vendor_id == vendor_id)
    if organization_id is not None:
        # A skill row names a vendor, and the vendor names the company. There is no vendors
        # model in this service, and the two sides disagree on type — vendors.id is character
        # varying while vendor_id is uuid — so the comparison is made as text, the same way
        # scoping is written everywhere else here.
        q = q.where(
            sa_text(
                "resource_skills.vendor_id::text IN ("
                " SELECT v.id::text FROM plenum_cafm.vendors v"
                " WHERE v.organization_id::text = :skill_scope_org)"
            ).bindparams(skill_scope_org=str(organization_id))
        )
    rows = list((await session.execute(q)).scalars().all())
    return [_to_dict(r) for r in rows]


async def lapsed_operative_flags_for_vendors(
    session: AsyncSession,
    vendor_ids: list[UUID],
) -> dict[str, list[dict[str, Any]]]:
    if not vendor_ids:
        return {}
    # A retired vendor's operatives are not flagged (Hussain, 12 Oct 2026): nobody is
    # engaging them, and the flag would put the deleted vendor back on the scan report.
    rows = list(
        (
            await session.execute(
                select(ResourceSkill).where(
                    ResourceSkill.vendor_id.in_(vendor_ids),
                    ResourceSkill.status == "Lapsed",
                    _live_vendor_skill(),
                )
            )
        ).scalars().all()
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r.vendor_id), []).append(_to_dict(r))
    return out


def _live_vendor_skill() -> Any:
    """A skill held against a vendor that has not been deleted. Compared as text — vendors.id
    is character varying, resource_skills.vendor_id is uuid."""
    return sa_text(f"resource_skills.vendor_id::text NOT IN ({deleted_vendor_ids_sql()})")


def _to_dict(r: ResourceSkill) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "vendor_id": str(r.vendor_id),
        "operative_name": r.operative_name,
        "skill_type_code": r.skill_type_code,
        "certificate_number": r.certificate_number,
        "expiry_date": r.expiry_date.isoformat() if r.expiry_date else None,
        "status": r.status,
        "days_to_expiry": r.days_to_expiry,
    }
