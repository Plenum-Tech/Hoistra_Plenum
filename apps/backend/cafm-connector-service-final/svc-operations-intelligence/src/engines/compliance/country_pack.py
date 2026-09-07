"""A4 — Country Certificate Pack loader / query / versioning."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models import BuildingCountryPack, CountryCertificatePack
from .lifecycle import DEFAULT_THRESHOLDS

log = get_logger(__name__)

_REFERENCE_DIR = Path(__file__).resolve().parents[2] / "reference"
_PACK_PATH = _REFERENCE_DIR / "uk_compliance_pack_v1_1.json"
_UAE_PACK_PATH = _REFERENCE_DIR / "uae_compliance_pack_v1.json"
_US_PACK_PATH = _REFERENCE_DIR / "us_compliance_pack_v1.json"

# country_code -> reference JSON path (drives startup auto-seed + admin re-seed).
PACK_PATHS: dict[str, Path] = {
    "UK": _PACK_PATH,
    "UAE": _UAE_PACK_PATH,
    "US": _US_PACK_PATH,
}


def load_pack_json(path: Path | None = None) -> dict[str, Any]:
    p = path or _PACK_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def default_alert_thresholds() -> dict[str, Any]:
    """PRD ladder thresholds — used when pack JSON omits per-type overrides."""
    return deepcopy(DEFAULT_THRESHOLDS)


def effective_alert_thresholds(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw:
        return {**DEFAULT_THRESHOLDS, **raw}
    return default_alert_thresholds()


async def seed_uk_pack(session: AsyncSession, *, force: bool = False) -> dict[str, Any]:
    """Load UK Compliance Certification Pack v1.1 into country_certificate_packs."""
    return await seed_pack_from_path(session, _PACK_PATH, force=force)


async def seed_uae_pack(session: AsyncSession, *, force: bool = False) -> dict[str, Any]:
    """Load the UAE statutory FM compliance pack into country_certificate_packs."""
    return await seed_pack_from_path(session, _UAE_PACK_PATH, force=force)


async def seed_us_pack(session: AsyncSession, *, force: bool = False) -> dict[str, Any]:
    """Load the US statutory FM compliance pack into country_certificate_packs."""
    return await seed_pack_from_path(session, _US_PACK_PATH, force=force)


async def seed_all_packs(session: AsyncSession, *, force: bool = False) -> dict[str, Any]:
    """Seed every bundled country pack (UK + UAE + US). Idempotent unless force=True."""
    results: dict[str, Any] = {}
    for country, path in PACK_PATHS.items():
        try:
            results[country] = await seed_pack_from_path(session, path, force=force)
        except FileNotFoundError:
            results[country] = {"seeded": False, "reason": "reference_file_missing"}
    return {"entity": "CountryPack", "packs": results}


async def seed_pack_from_path(
    session: AsyncSession, path: Path, *, force: bool = False
) -> dict[str, Any]:
    """Load one Country Certificate Pack JSON (any country) into country_certificate_packs.

    Idempotent: if the country+version already exists and ``force`` is False, it backfills
    missing thresholds and keeps issuing bodies / Verify-now URLs in sync with the JSON.
    """
    pack = load_pack_json(path)
    country = pack["country_code"]
    version = pack["pack_version"]
    defaults = default_alert_thresholds()

    existing = (
        await session.execute(
            select(CountryCertificatePack.id).where(
                CountryCertificatePack.country_code == country,
                CountryCertificatePack.pack_version == version,
            ).limit(1)
        )
    ).scalar_one_or_none()

    if existing and not force:
        rows = list(
            (
                await session.execute(
                    select(CountryCertificatePack).where(
                        CountryCertificatePack.country_code == country,
                        CountryCertificatePack.pack_version == version,
                    )
                )
            ).scalars().all()
        )
        by_code = {
            item["certificate_type_code"]: item for item in pack.get("certificate_types") or []
        }
        backfilled = 0
        verify_synced = 0
        for row in rows:
            if not row.alert_thresholds:
                row.alert_thresholds = defaults
                backfilled += 1
            src = by_code.get(row.certificate_type_code) or {}
            # Keep Verify-now URLs / issuing bodies in sync with pack JSON
            changed = False
            if src.get("verification_url") and row.verification_url != src.get(
                "verification_url"
            ):
                row.verification_url = src.get("verification_url")
                changed = True
            if src.get("issuing_body") and row.issuing_body != src.get("issuing_body"):
                row.issuing_body = src.get("issuing_body")
                changed = True
            if changed:
                verify_synced += 1
        if backfilled or verify_synced:
            await session.commit()
        from .verification_sources import seed_verification_sources

        sources = await seed_verification_sources(session)
        return {
            "seeded": False,
            "reason": "already_present",
            "country_code": country,
            "pack_version": version,
            "types": len(rows),
            "thresholds_backfilled": backfilled,
            "verification_urls_synced": verify_synced,
            "type_counts": _count_scopes(rows),
            "entity": "CountryPack",
            "verification_sources": sources,
            "note": (
                "CountryPack is the aggregate of country_certificate_packs rows "
                f"(one row per certificate type). {country} pack v{version} = "
                f"{len(rows)} types."
            ),
        }

    if force:
        rows = (
            await session.execute(
                select(CountryCertificatePack).where(
                    CountryCertificatePack.country_code == country,
                    CountryCertificatePack.pack_version == version,
                )
            )
        ).scalars().all()
        for row in rows:
            await session.delete(row)
        await session.flush()

    inserted = 0
    for item in pack["certificate_types"]:
        session.add(
            CountryCertificatePack(
                pack_id=item["pack_id"],
                country_code=item["country_code"],
                pack_version=item["pack_version"],
                certificate_type_code=item["certificate_type_code"],
                certificate_type_name=item["certificate_type_name"],
                certificate_scope=item["certificate_scope"],
                trade_category=item.get("trade_category"),
                regulation_reference=item.get("regulation_reference"),
                regulation_url=item.get("regulation_url"),
                frequency_months=item.get("frequency_months"),
                issuing_body=item.get("issuing_body"),
                required_contractor_accreditation=item.get(
                    "required_contractor_accreditation"
                ),
                verification_url=item.get("verification_url"),
                key_fields_schema=item.get("key_fields_schema") or {},
                alert_thresholds=item.get("alert_thresholds") or defaults,
                is_active=True,
            )
        )
        inserted += 1

    await session.commit()
    log.info("country_pack.seeded", country=country, version=version, types=inserted)
    counts = pack.get("type_counts") or {
        "building": sum(
            1 for i in pack["certificate_types"] if i["certificate_scope"] == "Building"
        ),
        "vendor": sum(
            1 for i in pack["certificate_types"] if i["certificate_scope"] == "Vendor"
        ),
        "total": inserted,
    }
    from .verification_sources import seed_verification_sources

    sources = await seed_verification_sources(session)
    return {
        "seeded": True,
        "country_code": country,
        "pack_version": version,
        "types": inserted,
        "type_counts": counts,
        "entity": "CountryPack",
        "alert_thresholds_default": defaults,
        "verification_sources": sources,
    }


def _count_scopes(rows: list[CountryCertificatePack]) -> dict[str, int]:
    building = sum(1 for r in rows if r.certificate_scope == "Building")
    vendor = sum(1 for r in rows if r.certificate_scope == "Vendor")
    return {"building": building, "vendor": vendor, "total": len(rows)}


async def list_pack_types(
    session: AsyncSession,
    *,
    country_code: str = "UK",
    scope: str | None = None,
    pack_version: str | None = None,
) -> list[CountryCertificatePack]:
    q = select(CountryCertificatePack).where(
        CountryCertificatePack.country_code == country_code,
        CountryCertificatePack.is_active.is_(True),
    )
    if scope:
        q = q.where(CountryCertificatePack.certificate_scope == scope)
    if pack_version:
        q = q.where(CountryCertificatePack.pack_version == pack_version)
    q = q.order_by(
        CountryCertificatePack.certificate_scope,
        CountryCertificatePack.trade_category,
        CountryCertificatePack.certificate_type_name,
    )
    return list((await session.execute(q)).scalars().all())


async def get_pack_type(
    session: AsyncSession,
    certificate_type_code: str,
    *,
    country_code: str = "UK",
    pack_version: str | None = None,
) -> CountryCertificatePack | None:
    q = select(CountryCertificatePack).where(
        CountryCertificatePack.country_code == country_code,
        CountryCertificatePack.certificate_type_code == certificate_type_code,
        CountryCertificatePack.is_active.is_(True),
    )
    if pack_version:
        q = q.where(CountryCertificatePack.pack_version == pack_version)
    else:
        q = q.order_by(CountryCertificatePack.pack_version.desc())
    return (await session.execute(q.limit(1))).scalar_one_or_none()


async def resolve_site_pack_context(
    session: AsyncSession,
    site_id: UUID | None,
    *,
    fallback_country: str = "UK",
) -> dict[str, Any]:
    """
    Multi-country portfolios: building → BuildingCountryPack assignment.
    Returns country_code + pack_version used for thresholds / type lookup on scan.
    """
    if not site_id:
        return {
            "country_code": fallback_country,
            "pack_version": None,
            "source": "default",
        }
    row = (
        await session.execute(
            select(BuildingCountryPack).where(BuildingCountryPack.site_id == site_id)
        )
    ).scalar_one_or_none()
    if not row:
        return {
            "country_code": fallback_country,
            "pack_version": None,
            "source": "default",
            "site_id": str(site_id),
        }
    return {
        "country_code": row.country_code or fallback_country,
        "pack_version": row.pack_version,
        "source": "building_country_pack",
        "site_id": str(site_id),
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
    }


async def get_country_pack_entity(
    session: AsyncSession,
    *,
    country_code: str = "UK",
    pack_version: str | None = None,
) -> dict[str, Any]:
    """
    A4 CountryPack entity view — aggregate over per-type rows.

    Storage is normalised (one DB row per certificate type). This response is the
    CountryPack contract: pack_id, country_code, version, type_counts, types[].
    """
    rows = await list_pack_types(
        session, country_code=country_code, pack_version=pack_version
    )
    if not rows and pack_version is None:
        latest = (
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
        if latest:
            rows = await list_pack_types(
                session, country_code=country_code, pack_version=latest
            )

    if not rows:
        return {
            "ok": False,
            "entity": "CountryPack",
            "error": f"No pack types for {country_code}",
            "country_code": country_code,
        }

    head = rows[0]
    counts = _count_scopes(rows)
    return {
        "ok": True,
        "entity": "CountryPack",
        "pack_id": head.pack_id,
        "country_code": head.country_code,
        "pack_version": head.pack_version,
        "type_counts": counts,
        "storage": "country_certificate_packs",
        "note": (
            "CountryPack entity = aggregate of per-type rows. "
            "UK pack v1.1 ships 54 types (27 Building + 27 Vendor) from "
            "UK_Compliance_Certification_Pack_v1.1.docx."
        ),
        "types": [pack_to_dict(r) for r in rows],
    }


async def load_country_pack_from_json_admin(
    session: AsyncSession,
    pack_payload: dict[str, Any],
) -> dict[str, Any]:
    """A4 — new country packs are configuration, not code."""
    country = pack_payload["country_code"]
    version = pack_payload["pack_version"]
    pack_id = pack_payload.get("pack_id") or f"{country}-COMPLIANCE-v{version}"
    defaults = default_alert_thresholds()
    inserted = 0
    for item in pack_payload["certificate_types"]:
        session.add(
            CountryCertificatePack(
                pack_id=pack_id,
                country_code=country,
                pack_version=version,
                certificate_type_code=item["certificate_type_code"],
                certificate_type_name=item["certificate_type_name"],
                certificate_scope=item["certificate_scope"],
                trade_category=item.get("trade_category"),
                regulation_reference=item.get("regulation_reference"),
                regulation_url=item.get("regulation_url"),
                frequency_months=item.get("frequency_months"),
                issuing_body=item.get("issuing_body"),
                required_contractor_accreditation=item.get(
                    "required_contractor_accreditation"
                ),
                verification_url=item.get("verification_url"),
                key_fields_schema=item.get("key_fields_schema") or {},
                alert_thresholds=item.get("alert_thresholds") or defaults,
                is_active=True,
            )
        )
        inserted += 1
    await session.commit()
    return {
        "loaded": True,
        "entity": "CountryPack",
        "country_code": country,
        "pack_version": version,
        "pack_id": pack_id,
        "types": inserted,
        "alert_thresholds_default_applied": True,
    }


def pack_to_dict(row: CountryCertificatePack) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "pack_id": row.pack_id,
        "country_code": row.country_code,
        "pack_version": row.pack_version,
        "certificate_type_code": row.certificate_type_code,
        "certificate_type_name": row.certificate_type_name,
        "certificate_scope": row.certificate_scope,
        "trade_category": row.trade_category,
        "regulation_reference": row.regulation_reference,
        "regulation_url": row.regulation_url,
        "frequency_months": row.frequency_months,
        "issuing_body": row.issuing_body,
        "required_contractor_accreditation": row.required_contractor_accreditation,
        "verification_url": row.verification_url,
        "key_fields_schema": row.key_fields_schema,
        "alert_thresholds": row.alert_thresholds or default_alert_thresholds(),
        "is_active": row.is_active,
    }


_THRESHOLD_KEYS = (
    "current_gt",
    "expiring_soon_min",
    "expiring_soon_max",
    "due_for_renewal_min",
    "due_for_renewal_max",
    "overdue_min",
    "overdue_max",
    "critical_min",
    "critical_max",
    "lapsed_lte",  # legacy alias → critical_max
)


async def update_pack_type_thresholds(
    session: AsyncSession,
    *,
    certificate_type_code: str,
    alert_thresholds: dict[str, Any],
    country_code: str = "UK",
    pack_version: str | None = None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """
    PM admin: update A1 alert ladder thresholds for one certificate type in the
    active Country Pack. Does not rewrite the seed JSON — DB pack row is SoR
    after admin edit until next force re-seed.
    """
    from datetime import datetime, timezone

    from ...shared.approvals import write_audit

    row = await get_pack_type(
        session,
        certificate_type_code,
        country_code=country_code,
        pack_version=pack_version,
    )
    if not row:
        return {
            "ok": False,
            "error": f"No pack type {certificate_type_code} for {country_code}",
        }

    merged = effective_alert_thresholds(row.alert_thresholds)
    before = dict(merged)
    for key in _THRESHOLD_KEYS:
        if key not in alert_thresholds:
            continue
        try:
            merged[key] = int(alert_thresholds[key])
        except (TypeError, ValueError):
            return {"ok": False, "error": f"alert_thresholds.{key} must be an integer"}

    # Map legacy lapsed_lte edits onto critical_max
    if "lapsed_lte" in alert_thresholds and "critical_max" not in alert_thresholds:
        merged["critical_max"] = int(merged["lapsed_lte"])
    merged["lapsed_lte"] = int(merged.get("critical_max", merged.get("lapsed_lte", 7)))
    if "critical_min" not in merged:
        merged["critical_min"] = 0

    # Basic band sanity (PRD ladder should not invert)
    if not (
        int(merged["critical_min"])
        <= int(merged["critical_max"])
        < int(merged["overdue_min"])
        <= int(merged["overdue_max"])
        < int(merged["due_for_renewal_min"])
        <= int(merged["due_for_renewal_max"])
        < int(merged["expiring_soon_min"])
        <= int(merged["expiring_soon_max"])
        <= int(merged["current_gt"])
    ):
        return {
            "ok": False,
            "error": (
                "Invalid ladder: need Critical ≤ Overdue ≤ Due for Renewal "
                "< Expiring Soon ≤ Current"
            ),
        }

    row.alert_thresholds = merged
    row.updated_at = datetime.now(timezone.utc)
    await write_audit(
        session,
        actor="user:pm_admin",
        action_type="country_pack.thresholds.update",
        source_feature="A",
        organization_id=organization_id,
        input_payload={"before": before, "certificate_type_code": certificate_type_code},
        output_payload={"after": merged, "certificate_type_code": certificate_type_code},
        detail={"country_code": country_code, "pack_version": row.pack_version},
    )
    await session.commit()
    await session.refresh(row)
    return {
        "ok": True,
        "message": f"Thresholds updated for {certificate_type_code}",
        "type": pack_to_dict(row),
    }
