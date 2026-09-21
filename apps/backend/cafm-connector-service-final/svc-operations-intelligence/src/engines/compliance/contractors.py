"""A2 — recommend contractors filtered by required accreditation."""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...shared.vendor_identity import _org_clause, find_vendor_id
from ...core.logging import get_logger

log = get_logger(__name__)


def _as_linkable_uuid(vendor_id: Any, *, name: str) -> UUID | None:
    """The matched vendor's id, if a uuid column can actually hold it.

    ``plenum_cafm.vendors.id`` is character varying, and 1,025 of the 2,073 rows carry
    legacy ids — "V-01", "VEN-CLIMATE-001". Every column that points at a vendor
    (compliance_certificates.vendor_id, contract_sla_parameters.vendor_id) is uuid, and
    there is no foreign key between them, so nothing has ever forced the two into
    agreement.

    This used to be a bare ``UUID(str(matched))`` inside the function's blanket
    ``except Exception``. A contract naming "Apex Lifts" therefore FOUND Apex Lifts, threw
    the answer away as if the lookup had failed, and saved with no vendor — reported to the
    reader as "Ingestion complete". The distinction matters because the two cases need
    different fixes: a genuine lookup failure is a bug here, an unlinkable legacy id is a
    schema migration that only a DBA can run.

    Returns None either way — a legacy id genuinely cannot go in a uuid column — but says
    which one happened, under its own event name.
    """
    try:
        return UUID(str(vendor_id))
    except (TypeError, ValueError):
        log.warning(
            "contractors.vendor_legacy_id_unlinkable",
            vendor_id=str(vendor_id)[:40],
            name=name[:80],
            detail=(
                "vendor exists but its id is not a uuid, and every column that links to a "
                "vendor is; the row needs migrating before it can be attached"
            ),
        )
        return None

#: None until probed. vendor_contacts.is_primary is present in one deployment and absent in
#: the other, and the two databases disagree on more than this one column.
_IS_PRIMARY: bool | None = None


async def _has_is_primary(session: AsyncSession) -> bool:
    """Whether vendor_contacts carries is_primary here. Read once per process."""
    global _IS_PRIMARY
    if _IS_PRIMARY is None:
        try:
            async with session.begin_nested():
                found = (
                    await session.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_schema = 'plenum_cafm' "
                            "AND table_name = 'vendor_contacts' "
                            "AND column_name = 'is_primary' LIMIT 1"
                        )
                    )
                ).scalar()
            _IS_PRIMARY = bool(found)
        except Exception as exc:  # noqa: BLE001 — an unreadable catalogue costs the
            # preference, never the recommendation.
            log.warning("contractors.is_primary_probe_failed", error=str(exc)[:160])
            _IS_PRIMARY = False
    return _IS_PRIMARY


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

    # Casts on both sides of every vendor join. plenum_cafm.vendors.id is character varying
    # and compliance_certificates.vendor_id / vendor_contacts.vendor_id are uuid, so the
    # uncast comparison raised "operator does not exist: uuid = character varying" — and both
    # the primary query and its fallback caught it and returned [], so this function answered
    # "no approved contractor found" for every accreditation, for every company, silently.
    #
    # The company filter is applied here rather than left to the caller. All three call sites
    # already passed organization_id; the parameter was accepted and never referenced, so a
    # recommendation could name another company's vendor.
    # vendor_contacts.is_primary exists in one deployment's schema and not the other, and
    # naming a column that is not there fails the whole statement at parse time — which is how
    # this query came to fall back on every call. Probed once, like the meter register.
    contact_order = "vc.is_primary DESC NULLS LAST, " if await _has_is_primary(session) else ""

    org_filter = " AND v.organization_id::text = :org" if organization_id is not None else ""
    params: dict[str, Any] = {
        "acc": required_accreditation,
        "acc_like": f"%{required_accreditation}%",
        "lim": limit,
    }
    if organization_id is not None:
        params["org"] = str(organization_id)

    sql = f"""
        SELECT v.id, v.vendor_name, v.vendor_code, v.block_state,
               v.blocked_accreditation_type,
               cc.certificate_type_code, cc.expiry_date, cc.status, cc.days_to_expiry,
               (
                 SELECT vc.email FROM plenum_cafm.vendor_contacts vc
                 WHERE vc.vendor_id::text = v.id::text
                   AND vc.email IS NOT NULL AND TRIM(vc.email) <> ''
                 ORDER BY {contact_order}vc.id
                 LIMIT 1
               ) AS contact_email
        FROM plenum_cafm.vendors v
        JOIN plenum_cafm.compliance_certificates cc
          ON cc.vendor_id::text = v.id::text
         AND cc.cert_scope = 'Vendor'
         AND (
               cc.certificate_type_code ILIKE :acc
            OR cc.cert_type ILIKE :acc
            OR CAST(cc.raw_metadata AS text) ILIKE :acc_like
         )
        WHERE COALESCE(v.block_state, 'Clear') <> 'Blocked'
          AND (cc.days_to_expiry IS NULL OR cc.days_to_expiry > 0)
          AND (cc.status IS NULL OR cc.status <> 'Lapsed')
          {org_filter}
        ORDER BY cc.days_to_expiry DESC NULLS LAST
        LIMIT :lim
    """
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(text(sql), params)
            ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        # Fallback without vendor_contacts join if table/column missing
        log.warning("contractors.recommend_with_email_failed", error=str(exc))
        try:
            async with session.begin_nested():
                rows = (
                    await session.execute(
                        text(
                            f"""
                            SELECT v.id, v.vendor_name, v.vendor_code, v.block_state,
                                   v.blocked_accreditation_type,
                                   cc.certificate_type_code, cc.expiry_date,
                                   cc.status, cc.days_to_expiry,
                                   NULL::text AS contact_email
                            FROM plenum_cafm.vendors v
                            JOIN plenum_cafm.compliance_certificates cc
                              ON cc.vendor_id::text = v.id::text AND cc.cert_scope = 'Vendor'
                             AND (
                                   cc.certificate_type_code ILIKE :acc
                                OR cc.cert_type ILIKE :acc
                             )
                            WHERE COALESCE(v.block_state, 'Clear') <> 'Blocked'
                              AND (cc.days_to_expiry IS NULL OR cc.days_to_expiry > 0)
                              {org_filter}
                            ORDER BY cc.days_to_expiry DESC NULLS LAST
                            LIMIT :lim
                            """
                        ),
                        # The fallback drops the vendor_contacts join, not the company filter.
                        {k: v for k, v in params.items() if k != "acc_like"},
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
        # ordered so a name that matches several rows always returns the same one, and both
        # scoped to the caller's own company — the register holds every tenant's suppliers.
        matched = await find_vendor_id(session, name, organization_id=organization_id)
        if matched is not None:
            return _as_linkable_uuid(matched, name=name)
        like_binds = {"like": f"%{name[:40]}%"}
        like_org = _org_clause(organization_id, like_binds)
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        f"""
                        SELECT id FROM plenum_cafm.vendors
                        WHERE vendor_name ILIKE :like
                          {like_org}
                        ORDER BY created_at NULLS LAST, id
                        LIMIT 1
                        """
                    ),
                    like_binds,
                )
            ).mappings().first()
            if row:
                return _as_linkable_uuid(row["id"], name=name)
            if not create_if_missing or not organization_id:
                return None
            new_id = uuid4()
            # vendors.organization_id is a uuid column — as is every one of the 120
            # organization_id columns in plenum_cafm, and organizations.id itself. There is no
            # legacy integer column here and never was on this database. A comment claiming
            # otherwise had this line cast the company id to an int before the INSERT:
            # int(UUID('00000000-…-000000000001')) is 1, which a uuid column rejects every
            # time. The failure was swallowed below into a warning and a None, so contracts
            # were written with no vendor and certificates went unlinked for three weeks
            # while every upload reported "complete". The id goes through as itself.
            await session.execute(
                text(
                    """
                    INSERT INTO plenum_cafm.vendors
                      (id, organization_id, vendor_name, status, created_at)
                    VALUES
                      (:id, :org, :name, 'active', now())
                    """
                ),
                {"id": str(new_id), "org": str(organization_id), "name": name[:255]},
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
