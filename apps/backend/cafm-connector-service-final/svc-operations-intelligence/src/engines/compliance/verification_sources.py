"""Compliance Certification Center — verification sources config (§8.8)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models import ComplianceVerificationSource, CountryCertificatePack
from .country_pack import load_pack_json

log = get_logger(__name__)

# The seed SQL lives at the SERVICE root (…/svc-operations-intelligence/migrations/),
# i.e. parents[3] of this file (…/src/engines/compliance/verification_sources.py) — not
# parents[2] (…/src). Resolve robustly so a missing dir never silently leaves the
# compliance_verification_sources table empty (which strands every verify at "not_configured").
def _find_sources_sql() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "migrations" / "compliance_verification_sources.sql"
        if candidate.exists():
            return candidate
    return here.parents[3] / "migrations" / "compliance_verification_sources.sql"


SOURCES_SQL = _find_sources_sql()

# Per-country migrations, applied in order after the UK base seed:
#   _country.sql adds country_code + the composite PK, then one file per country.
# Kept as separate files so each country's registers can be reviewed and updated alone.
# Runs BEFORE the UK base seed: adds country_code + the composite PK that the base
# seed's ON CONFLICT target relies on. Guarded, so it is a no-op on a fresh database.
SCHEMA_SQL_FILE = "compliance_verification_sources_country.sql"

# One seed file per country, applied after the UK base seed. Kept separate so each
# country's registers can be reviewed and updated on its own.
COUNTRY_SQL_FILES = (
    "compliance_verification_sources_us.sql",
    "compliance_verification_sources_uae.sql",
)

# Pack codes → CCC §1 canonical codes (also stored as code_aliases in DB)
CODE_ALIASES: dict[str, str] = {
    "FIRE_ALARM_SERVICE": "FIRE_ALARM_SVC",
    "SPRINKLER": "SPRINKLER_TEST",
    "CP17": "GAS_CP17",
    "GAS_SAFE_CP17": "GAS_CP17",
    "BOILER_SERVICE": "BOILER_SVC",
    "L8_RISK": "LEGIONELLA_RA",
    "LEGIONELLA": "LEGIONELLA_RA",
    "COLD_WATER_TANK": "CWST_INSPECTION",
    "LOLER": "LOLER_LIFT",
    "FGAS": "FGAS_LEAK",
    "GAS_SAFE": "GASSAFE_COMPANY",
    "ACS_CARD": "ACS_GAS_CARD",
    "NICEIC": "NICEIC_CONTRACTOR",
    "NAPIT": "NAPIT_REG",
    "BAFE": "BAFE_SP203",
    "BAFE_SP203_1": "BAFE_SP203",
    "NSI": "NSI_GOLD_FIRE",
    "LEIA": "LEIA_MEMBER",
    "HSE_ASBESTOS_LICENCE": "ASBESTOS_LICENCE",
    "P402": "BOHS_P40x",
    "P403": "BOHS_P40x",
    "P404": "BOHS_P40x",
    "LCA": "LCA_REG",
    "REFCOM": "REFCOM_COMPANY",
    "CHAS": "CHAS_SSIP",
    "SSIP": "CHAS_SSIP",
    "CHAS_SSIP": "CHAS_SSIP",
    "SIA": "SIA_LICENCE",
    "SIA_INDIVIDUAL": "SIA_LICENCE",
    "BPCA": "BPCA_MEMBER",
    "PA1": "PESTICIDE_PAx",
    "PA2": "PESTICIDE_PAx",
    "PA6": "PESTICIDE_PAx",
    # Pack codes whose verify-code alias was missing, so §8 verification looked up the pack
    # code itself and found no source row. The single-door classifier now stores the pack
    # code (it used to store the verify code, which never joined country_certificate_pack),
    # so these four types had no verification channel until the alias existed.
    "PA1_PA2_PA6": "PESTICIDE_PAx",
    "ASBESTOS_P402_P403_P404": "BOHS_P40x",
    "BTEC_LEGIONELLA": "LEGIONELLA_COMP",
    "NSI_GOLD_SECURITY": "NSI_GOLD_SEC",
    # UK EL/PL liability-insurance variants → the two FCA-verified canonical codes, so
    # EVERY liability-insurance certificate routes to the FCA register check centrally
    # (one rule, not per-code seed rows). get_pack_type uses the raw code, so extraction
    # of PUBLIC_LIABILITY/etc. still resolves its own pack — only §8 verification canonicalizes.
    "PUBLIC_LIABILITY": "CONTRACTOR_PL",
    "CONTRACTOR_LIABILITY": "CONTRACTOR_PL",
    "CONTRACTOR_PL_INSURANCE": "CONTRACTOR_PL",
    "PL_INSURANCE": "CONTRACTOR_PL",
    "CONTRACTOR_EL_INSURANCE": "CONTRACTOR_EL",
    "EMPLOYERS_LIABILITY": "EL_INSURANCE",
}


DEFAULT_COUNTRY = "UK"


def normalize_country(country_code: str | None) -> str:
    """Normalise a country to the codes used by the packs (UK / US / UAE)."""
    c = str(country_code or "").strip().upper()
    if not c:
        return DEFAULT_COUNTRY
    if c in {"GB", "GBR", "UNITED KINGDOM"}:
        return "UK"
    if c in {"USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
        return "US"
    if c in {"ARE", "UNITED ARAB EMIRATES"}:
        return "UAE"
    return c


def canonicalize_type_code(code: str | None, country_code: str | None = None) -> str | None:
    """Canonicalise a certificate type code.

    CODE_ALIASES maps onto UK scheme codes (e.g. PUBLIC_LIABILITY -> CONTRACTOR_PL, which
    verifies against the UK FCA register), so it must ONLY be applied for UK. A UAE
    PUBLIC_LIABILITY certificate keeps its own code and resolves to the CBUAE row instead.
    """
    if not code:
        return None
    c = str(code).strip().upper().replace(" ", "_").replace("-", "_")
    if normalize_country(country_code) != "UK":
        return c
    return CODE_ALIASES.get(c, c)


def source_to_dict(row: ComplianceVerificationSource) -> dict[str, Any]:
    return {
        "certificate_type_code": row.certificate_type_code,
        "country_code": getattr(row, "country_code", DEFAULT_COUNTRY) or DEFAULT_COUNTRY,
        "cert_scope": row.cert_scope,
        "verification_group": row.verification_group,
        "channel": row.channel,
        "register": row.register,
        "source_url": row.source_url,
        "api_available": row.api_available,
        "refresh_cadence": row.refresh_cadence,
        "notes": row.notes,
        "code_aliases": row.code_aliases or [],
    }


async def seed_verification_sources(session: AsyncSession) -> dict[str, Any]:
    """Apply migration SQL (idempotent upsert).

    Runs via ``exec_driver_sql`` (raw DBAPI) rather than ``text()`` because the seed uses
    PostgreSQL ``::jsonb`` casts — SQLAlchemy's ``text()`` would misread ``:jsonb`` as a
    bind parameter and raise. Guarded so a seed failure never crashes app startup.
    """
    if not SOURCES_SQL.exists():
        return {"ok": False, "error": f"missing {SOURCES_SQL.name}"}
    sql = SOURCES_SQL.read_text(encoding="utf-8")
    try:
        # Split into single statements and run each via raw DBAPI: asyncpg can't run
        # multiple commands in one prepared statement, and text() would misparse the
        # ``::jsonb`` casts as bind params. Mirrors db.apply_sql_migrations.
        from ...db import _split_sql, engine

        async def _apply(path, label):
            """Apply one migration file; never let a failure crash startup."""
            if not path.exists():
                log.warning("verification_sources.sql_missing", file=label)
                return
            try:
                async with engine.begin() as conn:
                    for stmt in _split_sql(path.read_text(encoding="utf-8")):
                        await conn.exec_driver_sql(stmt)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "verification_sources.sql_failed", file=label, error=str(exc)[:300]
                )

        # 1) schema upgrade (country_code + composite PK) — must precede the base seed
        await _apply(SOURCES_SQL.parent / SCHEMA_SQL_FILE, SCHEMA_SQL_FILE)
        # 2) UK base seed
        async with engine.begin() as conn:
            for stmt in _split_sql(sql):
                await conn.exec_driver_sql(stmt)
        # 3) per-country seeds — independent, so one failure never blocks the others
        for fname in COUNTRY_SQL_FILES:
            await _apply(SOURCES_SQL.parent / fname, fname)
    except Exception as exc:  # noqa: BLE001 — seeding must never crash startup
        log.warning("verification_sources.seed_failed", error=str(exc)[:300])
        return {"ok": False, "error": str(exc)[:300]}
    count = (
        await session.execute(select(ComplianceVerificationSource))
    ).scalars().all()
    return {"ok": True, "count": len(count)}


async def list_verification_sources(
    session: AsyncSession,
    *,
    cert_scope: str | None = None,
    channel: str | None = None,
) -> list[dict[str, Any]]:
    q = select(ComplianceVerificationSource).order_by(
        ComplianceVerificationSource.cert_scope,
        ComplianceVerificationSource.certificate_type_code,
    )
    if cert_scope:
        q = q.where(ComplianceVerificationSource.cert_scope == cert_scope.lower())
    if channel:
        q = q.where(ComplianceVerificationSource.channel == channel)
    rows = list((await session.execute(q)).scalars().all())
    if not rows:
        # Auto-seed once if empty
        await seed_verification_sources(session)
        rows = list((await session.execute(q)).scalars().all())
    return [source_to_dict(r) for r in rows]


async def get_verification_source(
    session: AsyncSession,
    certificate_type_code: str | None,
    country_code: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the verification source for a type code WITHIN its own country.

    Registers are national. A US or UAE certificate must never resolve to a UK register
    (before country scoping, UAE PUBLIC_LIABILITY canonicalised to CONTRACTOR_PL and was
    "verified" against the UK FCA register). So the lookup is scoped to the certificate
    country and there is deliberately NO cross-country fallback: an unmapped type returns
    None, which surfaces honestly as status "not_configured" plus a Verify-now link.
    """
    country = normalize_country(country_code)
    code = canonicalize_type_code(certificate_type_code, country)
    if not code:
        return None

    row = await session.get(ComplianceVerificationSource, (code, country))
    if row:
        return source_to_dict(row)

    # Alias / case-insensitive search, scoped to this country only.
    q = select(ComplianceVerificationSource).where(
        ComplianceVerificationSource.country_code == country
    )
    rows = list((await session.execute(q)).scalars().all())
    if not rows:
        # Table (or this country's rows) not seeded yet — seed once, then retry.
        await seed_verification_sources(session)
        rows = list((await session.execute(q)).scalars().all())
    for r in rows:
        aliases = [str(a).upper() for a in (r.code_aliases or [])]
        # canonicalize_type_code upper-cases, but a few seeded codes carry mixed case
        # (e.g. "PESTICIDE_PAx"), so compare case-insensitively.
        if code in aliases or code == (r.certificate_type_code or "").upper():
            return source_to_dict(r)

    # UK-only alias fallback (CODE_ALIASES is a UK scheme map).
    if country == "UK":
        mapped = CODE_ALIASES.get(code)
        if mapped and mapped != code:
            row = await session.get(ComplianceVerificationSource, (mapped, country))
            if row:
                return source_to_dict(row)
    return None


async def list_catalogue(session: AsyncSession) -> dict[str, Any]:
    """54-card catalogue: pack types joined with verification source channel."""
    sources = {
        s["certificate_type_code"]: s
        for s in await list_verification_sources(session)
    }
    # Index by (country, code) and (country, alias) — the same code means a different
    # authority per country, so a UAE ISO_9001 must not pick up the UK UKAS row.
    by_alias: dict[tuple[str, str], dict[str, Any]] = {}
    for s in sources.values():
        ctry = normalize_country(s.get("country_code"))
        by_alias[(ctry, str(s["certificate_type_code"]).upper())] = s
        for a in s.get("code_aliases") or []:
            by_alias[(ctry, str(a).upper())] = s

    pack_rows = list(
        (
            await session.execute(
                select(CountryCertificatePack)
                .where(CountryCertificatePack.is_active.is_(True))
                .order_by(
                    CountryCertificatePack.certificate_scope,
                    CountryCertificatePack.certificate_type_code,
                )
            )
        ).scalars().all()
    )

    cards: list[dict[str, Any]] = []
    if pack_rows:
        for p in pack_rows:
            p_country = normalize_country(getattr(p, "country_code", None))
            canon = (
                canonicalize_type_code(p.certificate_type_code, p_country)
                or p.certificate_type_code
            )
            src = by_alias.get((p_country, str(canon).upper())) or by_alias.get(
                (p_country, (p.certificate_type_code or "").upper())
            )
            cards.append(
                {
                    "certificate_type_code": p.certificate_type_code,
                    "country_code": p_country,
                    "canonical_code": canon,
                    "certificate_type_name": p.certificate_type_name,
                    "cert_scope": (p.certificate_scope or "").lower(),
                    "trade_category": p.trade_category,
                    "issuing_body": p.issuing_body,
                    "verification_url": p.verification_url or (src or {}).get("source_url"),
                    "channel": (src or {}).get("channel") or "website",
                    "api_available": (src or {}).get("api_available") or "no",
                    "verification_group": (src or {}).get("verification_group"),
                    "register": (src or {}).get("register"),
                }
            )
    else:
        # Pack not seeded — fall back to sources table + JSON pack names
        pack = load_pack_json()
        names = {
            t.get("certificate_type_code"): t
            for t in (pack.get("certificate_types") or [])
            if t.get("certificate_type_code")
        }
        for code, src in sources.items():
            meta = names.get(code) or names.get(CODE_ALIASES.get(code, code)) or {}
            cards.append(
                {
                    "certificate_type_code": code,
                    "canonical_code": code,
                    "certificate_type_name": meta.get("certificate_type_name") or code,
                    "cert_scope": src["cert_scope"],
                    "trade_category": meta.get("trade_category") or src["verification_group"],
                    "issuing_body": meta.get("issuing_body") or src.get("register"),
                    "verification_url": src.get("source_url"),
                    "channel": src["channel"],
                    "api_available": src["api_available"],
                    "verification_group": src["verification_group"],
                    "register": src.get("register"),
                }
            )

    building = [c for c in cards if c["cert_scope"] == "building"]
    vendor = [c for c in cards if c["cert_scope"] == "vendor"]
    return {
        "ok": True,
        "count": len(cards),
        "building_count": len(building),
        "vendor_count": len(vendor),
        "cards": cards,
    }
