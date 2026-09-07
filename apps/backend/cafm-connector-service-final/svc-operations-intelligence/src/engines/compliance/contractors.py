"""A2 — recommend contractors filtered by required accreditation."""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...shared.vendor_identity import find_vendor_id
from ...core.logging import get_logger

log = get_logger(__name__)


async def recommend_contractors(
    session: AsyncSession,
    *,
    required_accreditation: str | None,
    organization_id: UUID | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Approved vendors filtered by accreditation currency.
    Excludes vendors with block_state = Blocked for that accreditation type.
    """
    if not required_accreditation:
        return []

    sql = """
        SELECT v.id, v.vendor_name, v.vendor_code, v.block_state,
               v.blocked_accreditation_type,
               cc.certificate_type_code, cc.expiry_date, cc.status, cc.days_to_expiry,
               (
                 SELECT vc.email FROM plenum_cafm.vendor_contacts vc
                 WHERE vc.vendor_id = v.id
                   AND vc.email IS NOT NULL AND TRIM(vc.email) <> ''
                 ORDER BY vc.is_primary DESC NULLS LAST
                 LIMIT 1
               ) AS contact_email
        FROM plenum_cafm.vendors v
        JOIN plenum_cafm.compliance_certificates cc
          ON cc.vendor_id = v.id
         AND cc.cert_scope = 'Vendor'
         AND (
               cc.certificate_type_code ILIKE :acc
            OR cc.cert_type ILIKE :acc
            OR CAST(cc.raw_metadata AS text) ILIKE :acc_like
         )
        WHERE COALESCE(v.block_state, 'Clear') <> 'Blocked'
          AND (cc.days_to_expiry IS NULL OR cc.days_to_expiry > 0)
          AND (cc.status IS NULL OR cc.status <> 'Lapsed')
        ORDER BY cc.days_to_expiry DESC NULLS LAST
        LIMIT :lim
    """
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(sql),
                    {
                        "acc": required_accreditation,
                        "acc_like": f"%{required_accreditation}%",
                        "lim": limit,
                    },
                )
            ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        # Fallback without vendor_contacts join if table/column missing
        log.warning("contractors.recommend_with_email_failed", error=str(exc))
        try:
            async with session.begin_nested():
                rows = (
                    await session.execute(
                        text(
                            """
                            SELECT v.id, v.vendor_name, v.vendor_code, v.block_state,
                                   v.blocked_accreditation_type,
                                   cc.certificate_type_code, cc.expiry_date,
                                   cc.status, cc.days_to_expiry,
                                   NULL::text AS contact_email
                            FROM plenum_cafm.vendors v
                            JOIN plenum_cafm.compliance_certificates cc
                              ON cc.vendor_id = v.id AND cc.cert_scope = 'Vendor'
                             AND (
                                   cc.certificate_type_code ILIKE :acc
                                OR cc.cert_type ILIKE :acc
                             )
                            WHERE COALESCE(v.block_state, 'Clear') <> 'Blocked'
                              AND (cc.days_to_expiry IS NULL OR cc.days_to_expiry > 0)
                            ORDER BY cc.days_to_expiry DESC NULLS LAST
                            LIMIT :lim
                            """
                        ),
                        {
                            "acc": required_accreditation,
                            "lim": limit,
                        },
                    )
                ).mappings().all()
        except Exception as exc2:  # noqa: BLE001
            log.warning("contractors.recommend_failed", error=str(exc2))
            return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        vid = str(r["id"])
        if vid in seen:
            continue
        seen.add(vid)
        out.append(
            {
                "vendor_id": vid,
                "vendor_name": r["vendor_name"],
                "vendor_code": r.get("vendor_code"),
                "accreditation": r.get("certificate_type_code"),
                "expiry_date": str(r["expiry_date"]) if r.get("expiry_date") else None,
                "status": r.get("status"),
                "email": r.get("contact_email"),
                "contact_email": r.get("contact_email"),
            }
        )
    return out


async def resolve_or_create_vendor(
    session: AsyncSession,
    *,
    company_name: str | None,
    organization_id: UUID | None = None,
    create_if_missing: bool = True,
) -> UUID | None:
    """
    Match Vendor by name (ilike) for cert FK linking.
    Optionally create a stub vendor (needs organization_id) so risk scan can run.
    """
    name = (company_name or "").strip()
    if not name or len(name) < 2:
        return None
    name = re.sub(r"\s+", " ", name)
    try:
        # Exact, then normalised ("Gough & Kelly Limited" is "Gough and Kelly Ltd."), both
        # ordered so a name that matches several rows always returns the same one.
        matched = await find_vendor_id(session, name)
        if matched:
            return UUID(str(matched))
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM plenum_cafm.vendors
                        WHERE vendor_name ILIKE :like
                        ORDER BY created_at NULLS LAST, id
                        LIMIT 1
                        """
                    ),
                    {"like": f"%{name[:40]}%"},
                )
            ).mappings().first()
            if row:
                return UUID(str(row["id"]))
            if not create_if_missing or not organization_id:
                return None
            new_id = uuid4()
            # organization_id is a legacy INTEGER column; a UUID/str org that doesn't fit
            # becomes NULL (the column is nullable) so the insert never fails on a type
            # mismatch. Combined with dropping the non-existent updated_at column above,
            # this makes vendor auto-create robust on the legacy vendors schema.
            try:
                org_val: int | None = (
                    int(organization_id) if organization_id is not None else None
                )
            except (TypeError, ValueError):
                org_val = None
            await session.execute(
                text(
                    """
                    INSERT INTO plenum_cafm.vendors
                      (id, organization_id, vendor_name, status, created_at)
                    VALUES
                      (:id, :org, :name, 'active', now())
                    """
                ),
                {"id": str(new_id), "org": org_val, "name": name[:255]},
            )
            # Best-effort block_state column (added in Phase 2 migration)
            try:
                await session.execute(
                    text(
                        """
                        UPDATE plenum_cafm.vendors
                        SET block_state = 'Clear'
                        WHERE id = :id AND block_state IS NULL
                        """
                    ),
                    {"id": str(new_id)},
                )
            except Exception:  # noqa: BLE001
                pass
            return new_id
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "contractors.resolve_vendor_failed",
            error=str(exc)[:200],
            name=name[:80],
        )
        return None


async def resolve_asset_display(
    session: AsyncSession,
    asset_id: UUID | None,
) -> dict[str, str | None]:
    """Resolve human asset_code (never raw UUID in emails when code exists)."""
    if not asset_id:
        return {"asset_code": None, "asset_reference": None}
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT asset_code, asset_name
                        FROM plenum_cafm.assets WHERE id = :id LIMIT 1
                        """
                    ),
                    {"id": str(asset_id)},
                )
            ).mappings().first()
            if row:
                code = row.get("asset_code") or row.get("asset_name")
                return {
                    "asset_code": code,
                    "asset_reference": code or str(asset_id),
                }
    except Exception:  # noqa: BLE001
        pass
    return {"asset_code": None, "asset_reference": str(asset_id)}


async def resolve_site_display(
    session: AsyncSession,
    site_id: UUID | None,
) -> dict[str, str | None]:
    """Best-effort building name + address for alert emails."""
    if not site_id:
        return {"building_name": "Building", "address": None}
    # Try sites then locations (SAVEPOINT so a missing table does not abort the request txn)
    for table, name_col, addr_cols in (
        ("sites", "site_name", ("address", "address_line1", "city")),
        ("locations", "location_name", ("address", "city")),
    ):
        try:
            async with session.begin_nested():
                cols = ", ".join([name_col, *addr_cols])
                row = (
                    await session.execute(
                        text(
                            f"SELECT {cols} FROM plenum_cafm.{table} WHERE id = :id LIMIT 1"
                        ),
                        {"id": str(site_id)},
                    )
                ).mappings().first()
                if row:
                    name = row.get(name_col) or "Building"
                    parts = [str(row.get(c)) for c in addr_cols if row.get(c)]
                    return {
                        "building_name": name,
                        "address": ", ".join(parts) if parts else None,
                    }
        except Exception:  # noqa: BLE001
            continue
    return {"building_name": str(site_id), "address": None}


async def resolve_site_by_name(session: AsyncSession, name: str | None) -> UUID | None:
    """Match a building certificate to an existing site by name, UUID keys only.

    Kept for callers that can only store a UUID. It delegates to
    ``site_links.resolve_site_link`` rather than carrying a second copy of the matching
    rules: the copy that lived here named a fixed set of columns and returned a value only
    when the site key parsed as a UUID, so against this deployment's ``plenum_cafm.sites``
    (keyed on ``site_id VARCHAR(50)``) it returned None every time and no building
    certificate was ever linked to its site.

    Prefer ``resolve_site_link``, which also resolves varchar-keyed sites.
    """
    from .site_links import resolve_site_link

    link = await resolve_site_link(session, name=name)
    return link.site_uuid if link else None
