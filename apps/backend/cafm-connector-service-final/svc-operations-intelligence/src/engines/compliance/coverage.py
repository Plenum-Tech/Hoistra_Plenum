"""Per-building / per-vendor CountryPack coverage %."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import certificates as cert_svc
from .country_pack import list_pack_types


# What the `required` denominator counts. Today there is exactly one basis: every Building
# type in the country pack. No data anywhere records which types a PARTICULAR building is
# obliged to hold (building_country_packs stores only a version, and pack types carry no
# applicability), so coverage is "how many of the pack's types this building has on record"
# — not a compliance score. A building with one certificate reads 3.7% against the 27-type
# UK pack even when the other 26 types could never apply to it. Callers must say which
# basis they are showing; the field exists per row as well as per response so a row stays
# self-describing once a per-site basis is added.
REQUIRED_BASIS_COUNTRY_PACK = "country_pack"
REQUIRED_BASIS_NOTE = (
    "`required` is every Building type in the country pack. Applicability per building is "
    "not recorded, so this measures pack completeness, not compliance — no building is "
    "expected to hold every type."
)


def _as_uuid_str(value: Any) -> str | None:
    """The key as a UUID string, or None when the sites table keys on a varchar."""
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        return None


def _pct(on_record: int, required: int) -> float:
    if required <= 0:
        return 100.0
    return round(100.0 * on_record / required, 1)


async def _site_labels(session: AsyncSession, site_keys: list[str]) -> dict[str, str]:
    """Display names for the site keys certificates actually carry.

    Delegates to ``site_links.site_labels``, which names only columns the live table has.
    The query this replaced selected a ``code`` column ``plenum_cafm.sites`` does not have
    and matched on ``id`` alone, so it raised on every call, was swallowed by the except,
    and returned no labels at all — each per-site row was titled with a raw key.
    """
    from .site_links import site_labels

    return await site_labels(session, site_keys)


async def _vendor_rows(session: AsyncSession, organization_id: UUID | None) -> list[dict[str, Any]]:
    """Vendors coverage can say something about: those holding a certificate, or blocked.

    This used to take the first 500 vendors by name and let the caller filter afterwards
    for "vendors with certificates on file". With 2,073 vendors on the register the cut
    landed in the C's, so a vendor whose name sorted later was dropped before that filter
    ever saw it — 6 of the 13 vendors that actually hold a certificate were missing from
    coverage entirely, and a newly registered one could never appear.

    Selecting on the condition itself is both correct and cheaper: the set that qualifies
    is small, and the limit now bounds a list that is already the right one rather than
    truncating the register alphabetically.
    """
    sql = """
        SELECT v.id::text AS id, v.vendor_name, v.vendor_code,
               coalesce(v.block_state, 'Clear') AS block_state,
               v.blocked_accreditation_type, v.block_reason
        FROM plenum_cafm.vendors v
        WHERE (
            EXISTS (
                SELECT 1 FROM plenum_cafm.compliance_certificates c
                WHERE c.cert_scope = 'Vendor'
                  AND c.vendor_id IS NOT NULL
                  AND c.vendor_id::text = v.id::text
            )
            OR lower(coalesce(v.block_state, '')) = 'blocked'
        )
    """
    params: dict[str, Any] = {}
    if organization_id:
        sql += " AND v.organization_id = :oid"
        params["oid"] = str(organization_id)
    sql += " ORDER BY v.vendor_name NULLS LAST LIMIT 500"
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


async def building_coverage(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    country_code: str = "UK",
) -> dict[str, Any]:
    """Coverage % per building against the Building CountryPack.

    Grouped by the building the certificate belongs to — the linked site when there is one,
    and otherwise the building the document names. Only certificates that name no building
    at all fall into a portfolio bucket.

    This used to key solely on ``site_id``, a UUID column that no certificate could populate
    (``plenum_cafm.sites`` keys on ``site_id VARCHAR(50)`` here), so every building
    certificate in the register landed in one "Portfolio (no site linked)" row: a single
    coverage figure spanning unrelated buildings, gaps that belonged to no building in
    particular, and a dashboard whose per-building rings never found a coverage row to join.
    A building that is not registered as a site is still a building with obligations, so it
    gets its own bucket and its coverage is reported next to the linked ones, marked
    ``linked: false`` so the two are never confused.
    """
    from .site_links import coverage_bucket

    pack = await list_pack_types(session, country_code=country_code, scope="Building")
    required_codes = [
        str(p.certificate_type_code)
        for p in pack
        if getattr(p, "certificate_type_code", None)
    ]
    required_set = set(required_codes)

    # Sites with an activated pack — they belong in coverage at 0% even with no certificate
    # on file, because a pack activated on a site is an obligation the site has.
    try:
        async with session.begin_nested():
            pack_sites = (
                await session.execute(
                    text(
                        """
                        SELECT site_id::text AS site_id, pack_version, country_code
                        FROM plenum_cafm.building_country_packs
                        """
                    )
                )
            ).mappings().all()
        pack_by_site = {str(r["site_id"]): dict(r) for r in pack_sites}
    except Exception:  # noqa: BLE001
        pack_by_site = {}

    certs = await cert_svc.list_certificates(
        session,
        cert_scope="Building",
        organization_id=organization_id,
        limit=1000,
    )

    buckets: dict[str, dict[str, Any]] = {}

    def _bucket(key: str, *, label: str, linked: bool, site_key: str | None) -> dict[str, Any]:
        b = buckets.get(key)
        if b is None:
            b = {
                "label": label,
                "linked": linked,
                "site_key": site_key,
                "rows": [],
            }
            buckets[key] = b
        elif label and not b["label"]:
            b["label"] = label
        return b

    for site_key in pack_by_site:
        _bucket(f"site:{site_key}", label="", linked=True, site_key=site_key)

    for c in certs:
        key, label, linked = coverage_bucket(c)
        site_key = key.split(":", 1)[1] if linked else None
        b = _bucket(
            key,
            label=label or str(c.get("site_label") or ""),
            linked=linked,
            site_key=site_key,
        )
        b["rows"].append(c)

    labels = await _site_labels(
        session, [str(b["site_key"]) for b in buckets.values() if b["site_key"]]
    )

    buildings: list[dict[str, Any]] = []
    for key, b in buckets.items():
        rows = b["rows"]
        site_key = b["site_key"]
        present = {
            str(r.get("certificate_type_code") or r.get("cert_type") or "").strip()
            for r in rows
            if (r.get("certificate_type_code") or r.get("cert_type"))
        }
        present &= required_set if required_set else present
        gaps = sorted(required_set - present) if required_set else []
        on_record = len(required_set & present) if required_set else len(present)
        current = sum(1 for r in rows if str(r.get("status") or "").lower() == "current")
        lapsed = sum(1 for r in rows if str(r.get("status") or "").lower() == "lapsed")
        site_uuid = _as_uuid_str(site_key)
        pack_row = pack_by_site.get(str(site_key)) if site_key else None
        buildings.append(
            {
                # site_id stays the UUID-shaped key the API has always returned; site_ref
                # carries a varchar site key, and both are None for an unlinked building.
                "site_id": site_uuid,
                "site_ref": None if site_uuid else site_key,
                "site_name": (
                    (labels.get(str(site_key)) if site_key else None)
                    or b["label"]
                    or (str(site_key) if site_key else "Portfolio (no building on certificate)")
                ),
                # Whether this building is a row in plenum_cafm.sites or only a name on a
                # document. Per-site reporting means something different for each.
                "linked": bool(b["linked"]),
                "pack_version": (pack_row or {}).get("pack_version"),
                "country_code": (pack_row or {}).get("country_code") or country_code,
                "required": len(required_set),
                "required_basis": REQUIRED_BASIS_COUNTRY_PACK,
                "on_record": on_record,
                "gaps": gaps,
                "coverage_pct": _pct(on_record, len(required_set)),
                "certificates_total": len(rows),
                "compliant": current,
                "non_compliant": lapsed,
            }
        )

    buildings.sort(
        key=lambda b: (
            b["site_name"] == "Portfolio (no building on certificate)",
            -b["certificates_total"],
            b["site_name"] or "",
        )
    )

    avg = (
        round(sum(b["coverage_pct"] for b in buildings) / len(buildings), 1)
        if buildings
        else 0.0
    )
    return {
        "ok": True,
        "scope": "Building",
        "country_code": country_code,
        "required_types": len(required_set),
        "required_basis": REQUIRED_BASIS_COUNTRY_PACK,
        "required_basis_note": REQUIRED_BASIS_NOTE,
        "average_coverage_pct": avg,
        "buildings_linked_to_site": sum(1 for b in buildings if b["linked"]),
        "buildings_unlinked": sum(1 for b in buildings if not b["linked"]),
        "buildings": buildings,
    }


async def vendor_coverage(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    country_code: str = "UK",
    trade_category: str | None = None,
) -> dict[str, Any]:
    """Coverage % per vendor against Vendor CountryPack types (optionally by trade)."""
    pack = await list_pack_types(session, country_code=country_code, scope="Vendor")
    if trade_category:
        trade_l = trade_category.strip().lower()
        pack = [
            p
            for p in pack
            if str(getattr(p, "trade_category", "") or "").strip().lower() == trade_l
        ]
    required_codes = [
        str(p.certificate_type_code)
        for p in pack
        if getattr(p, "certificate_type_code", None)
    ]
    # Full vendor pack is large (27 types) — for coverage without trade filter,
    # score against types the vendor actually holds ∪ high-signal core types.
    required_set = set(required_codes)

    vendors = await _vendor_rows(session, organization_id)
    certs = await cert_svc.list_certificates(
        session,
        cert_scope="Vendor",
        organization_id=organization_id,
        limit=1000,
    )
    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for c in certs:
        vid = str(c.get("vendor_id") or "").strip()
        if not vid:
            # Try metadata name bucket later
            continue
        by_vendor.setdefault(vid, []).append(c)

    out: list[dict[str, Any]] = []
    for v in vendors:
        vid = str(v["id"])
        rows = by_vendor.get(vid, [])
        present = {
            str(r.get("certificate_type_code") or r.get("cert_type") or "").strip()
            for r in rows
            if (r.get("certificate_type_code") or r.get("cert_type"))
        }
        # Without trade filter: coverage = held current-ish types / held+typical insurance cores
        if trade_category:
            denom = required_set
            on_record = len(present & denom)
            gaps = sorted(denom - present)
            required_n = len(denom)
        else:
            # Trade-agnostic: % of vendor's own accreditations that are Current (currency),
            # plus gap count vs pack types they partially cover is less meaningful —
            # report held vs pack intersection and currency.
            relevant = present & required_set if required_set else present
            on_record = len(relevant)
            # Gaps = pack types in same trades as certificates they hold
            trades_held = {
                str(r.get("trade_category") or "").strip().lower()
                for r in rows
                if r.get("trade_category")
            }
            trade_required = {
                str(p.certificate_type_code)
                for p in pack
                if str(getattr(p, "trade_category", "") or "").strip().lower() in trades_held
            } if trades_held else set()
            gaps = sorted(trade_required - present) if trade_required else []
            required_n = len(trade_required) if trade_required else max(on_record, 1)
            if trade_required:
                on_record = len(present & trade_required)
            else:
                required_n = max(len(present), 1)
                on_record = len(present)

        current = sum(1 for r in rows if str(r.get("status") or "").lower() == "current")
        lapsed = sum(1 for r in rows if str(r.get("status") or "").lower() == "lapsed")
        out.append(
            {
                "vendor_id": vid,
                "vendor_name": v.get("vendor_name") or v.get("vendor_code") or vid,
                "block_state": v.get("block_state") or "Clear",
                "blocked_accreditation_type": v.get("blocked_accreditation_type"),
                "required": required_n,
                "on_record": on_record,
                "gaps": gaps[:30],
                "coverage_pct": _pct(on_record, required_n),
                "certificates_total": len(rows),
                "compliant": current,
                "non_compliant": lapsed,
                "cleared": str(v.get("block_state") or "Clear").lower() != "blocked"
                and lapsed == 0
                and on_record > 0,
            }
        )

    # Prefer vendors with certificates on file; still include blocked empties
    out = [r for r in out if r["certificates_total"] > 0 or str(r["block_state"]).lower() == "blocked"]
    out.sort(key=lambda r: (-r["certificates_total"], r["coverage_pct"], r["vendor_name"] or ""))
    avg = round(sum(r["coverage_pct"] for r in out) / len(out), 1) if out else 0.0
    return {
        "ok": True,
        "scope": "Vendor",
        "country_code": country_code,
        "trade_category": trade_category,
        "average_coverage_pct": avg,
        "vendors": out,
    }
