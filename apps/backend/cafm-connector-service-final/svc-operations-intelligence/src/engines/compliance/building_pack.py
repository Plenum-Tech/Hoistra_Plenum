"""A4 — building country-pack activation + pack version notify."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import BuildingCountryPack, CountryCertificatePack
from ...shared.approvals import enqueue_approval


async def activate_building_pack(
    session: AsyncSession,
    *,
    site_id: UUID,
    country_code: str = "UK",
    pack_version: str | None = None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """UK building activation applies pack automatically — zero manual cert type config."""
    version = pack_version
    if not version:
        row = (
            await session.execute(
                select(CountryCertificatePack.pack_version)
                .where(
                    CountryCertificatePack.country_code == country_code,
                    CountryCertificatePack.is_active.is_(True),
                )
                .order_by(CountryCertificatePack.pack_version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        version = row or "1.1"

    existing = (
        await session.execute(
            select(BuildingCountryPack).where(BuildingCountryPack.site_id == site_id)
        )
    ).scalar_one_or_none()

    if existing:
        existing.country_code = country_code
        existing.pack_version = version
        existing.activated_at = datetime.now(timezone.utc)
        existing.organization_id = organization_id or existing.organization_id
    else:
        session.add(
            BuildingCountryPack(
                id=uuid4(),
                organization_id=organization_id,
                site_id=site_id,
                country_code=country_code,
                pack_version=version,
            )
        )
    await session.commit()
    return {
        "ok": True,
        "site_id": str(site_id),
        "country_code": country_code,
        "pack_version": version,
        "message": "Country pack applied — certificate types available with zero manual config.",
    }


async def notify_pack_version_change(
    session: AsyncSession,
    *,
    country_code: str,
    new_version: str,
    change_notes: str | None = None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """A4 — pack version bump flags PMs via Approvals queue; buildings pick up on next scan."""
    buildings = list(
        (
            await session.execute(
                select(BuildingCountryPack).where(
                    BuildingCountryPack.country_code == country_code
                )
            )
        ).scalars().all()
    )
    updated = 0
    for b in buildings:
        if b.pack_version != new_version:
            b.pack_version = new_version
            updated += 1

    item = await enqueue_approval(
        session,
        source_feature="A",
        item_type="pack_version_change",
        summary=f"Country pack {country_code} updated to v{new_version} — {updated} buildings",
        severity="Info",
        organization_id=organization_id,
        payload={
            "country_code": country_code,
            "new_version": new_version,
            "change_notes": change_notes,
            "buildings_updated": updated,
            "site_ids": [str(b.site_id) for b in buildings],
        },
    )
    await session.commit()
    return {
        "ok": True,
        "buildings_updated": updated,
        "queue_item_id": str(item.id),
    }
