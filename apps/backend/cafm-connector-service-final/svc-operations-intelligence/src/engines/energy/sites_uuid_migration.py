"""Repoint plenum_cafm.sites onto a uuid key, in two phases, dry run by default.

Why this exists: nine tables already declare ``site_id uuid`` — compliance_certificates,
building_energy_profiles, eui_snapshots, energy_meters, building_country_packs and the rest
— while ``sites.site_id`` is VARCHAR(50). None of them can join. That is the bug
``compliance/coverage.py`` documents at length: every building certificate fell into one
"Portfolio (no site linked)" bucket because the join could never succeed.

The mapping is DETERMINISTIC — ``uuid5(namespace, site_id)`` — so the same site key yields
the same uuid in dev, staging and production, the migration is re-runnable without creating
a second identity for a site, and a value can be verified by recomputing it rather than
looking it up.

Two phases, because a primary key under live data is not changed in one step:

* **expand** — additive and reversible. Creates ``site_id_map``, adds ``sites.site_uuid``
  and a ``site_uuid`` shadow column to every table that references a site by its legacy key.
  Both keys work; nothing is dropped; readers continue to use the varchar key.
* **contract** — the cutover. Renames ``site_id`` to ``legacy_site_id``, promotes
  ``site_uuid`` to ``site_id``, and repoints the referencing tables. Requires expand to have
  run, and an explicit confirmation: it rewrites a primary key.

Neither phase deletes a row. ``legacy_site_id`` is kept after cutover so the change can be
read backwards and, if necessary, reversed.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

#: Fixed namespace for Hoistra site identifiers. Never change it — every uuid already
#: derived from a site key depends on it, and a new namespace would give every site a
#: second identity.
SITE_NAMESPACE = uuid.UUID("7f9c1a52-3f7d-5b6e-9a10-4c2b8d0e6f31")

MAP_TABLE = "plenum_cafm.site_id_map"

#: Columns that hold a site's LEGACY key and must be translated. vendors.site_id is
#: deliberately absent: it is an integer and does not reference plenum_cafm.sites.
_LEGACY_REFS: tuple[tuple[str, str], ...] = (
    ("assets", "site_id"),
    ("buildings", "site_id"),
)

#: Columns already declared uuid, waiting for a sites key they can actually join to.
_UUID_REFS: tuple[tuple[str, str], ...] = (
    ("compliance_certificates", "site_id"),
    ("building_country_packs", "site_id"),
    ("building_energy_profiles", "site_id"),
    ("eui_snapshots", "site_id"),
    ("energy_meters", "site_id"),
    ("energy_anomalies", "site_id"),
    ("energy_monthly_reports", "site_id"),
    ("energy_recommendations", "site_id"),
    ("site_occupancy_logs", "site_id"),
)

_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    """Validate an identifier before it is interpolated into SQL (repo convention)."""
    if not _SAFE_IDENT.match(name or ""):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


# ── pure ──────────────────────────────────────────────────────────────────────────────


def derive_site_uuid(site_id: Any) -> str | None:
    """The uuid a legacy site key maps to. Deterministic, so it can be recomputed anywhere."""
    s = str(site_id or "").strip()
    if not s:
        return None
    return str(uuid.uuid5(SITE_NAMESPACE, s))


def is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value).strip())
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def classify_values(values: list[Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Split a column's values into what the migration can and cannot translate.

    ``remappable`` holds a legacy key present in sites. ``already_uuid`` is a value that is
    already the derived uuid — a re-run, not an error. ``orphan`` is a non-null value that
    is neither: it points at a site that does not exist, and the migration will not invent
    one for it.
    """
    derived = set(mapping.values())
    out = {"total": 0, "null": 0, "remappable": 0, "already_uuid": 0, "orphans": [], "orphan_count": 0}
    for v in values:
        out["total"] += 1
        s = str(v).strip() if v is not None else ""
        if not s:
            out["null"] += 1
        elif s in mapping:
            out["remappable"] += 1
        elif s in derived:
            out["already_uuid"] += 1
        else:
            out["orphan_count"] += 1
            if len(out["orphans"]) < 20:
                out["orphans"].append(s)
    return out


# ── DB helpers ────────────────────────────────────────────────────────────────────────


async def _columns(session: AsyncSession) -> dict[str, dict[str, str]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns WHERE table_schema = 'plenum_cafm'
                """
            )
        )
    ).all()
    out: dict[str, dict[str, str]] = {}
    for t, c, d in rows:
        out.setdefault(str(t), {})[str(c)] = str(d)
    return out


async def _site_mapping(session: AsyncSession) -> dict[str, str]:
    """legacy site_id → derived uuid, for every row in sites."""
    try:
        rows = (
            await session.execute(text("SELECT site_id::text FROM plenum_cafm.sites"))
        ).all()
    except Exception as exc:  # noqa: BLE001
        log.warning("sites_uuid.read_sites_failed", error=str(exc)[:200])
        return {}
    out: dict[str, str] = {}
    for (sid,) in rows:
        s = str(sid or "").strip()
        u = derive_site_uuid(s)
        if s and u:
            out[s] = u
    return out


async def plan_migration(session: AsyncSession, *, orphan_samples: int = 20) -> dict[str, Any]:
    """What the migration would do. Reads only."""
    cols = await _columns(session)
    sites_cols = cols.get("sites", {})
    if not sites_cols:
        return {"ok": False, "error": "plenum_cafm.sites does not exist"}

    key_type = sites_cols.get("site_id")
    already_uuid = key_type == "uuid"
    mapping = await _site_mapping(session)

    # A collision would mean two different site keys deriving the same uuid — impossible for
    # uuid5 unless the source keys are identical, but asserted rather than assumed.
    collisions: dict[str, list[str]] = {}
    seen: dict[str, str] = {}
    for legacy, u in mapping.items():
        if u in seen:
            collisions.setdefault(u, [seen[u]]).append(legacy)
        seen[u] = legacy

    refs: list[dict[str, Any]] = []
    for table, column in _LEGACY_REFS + _UUID_REFS:
        t_cols = cols.get(table)
        if not t_cols or column not in t_cols:
            refs.append({"table": table, "column": column, "present": False})
            continue
        dtype = t_cols[column]
        try:
            async with session.begin_nested():
                vals = [
                    r[0]
                    for r in (
                        await session.execute(
                            text(
                                f"SELECT {_ident(column)}::text FROM plenum_cafm.{_ident(table)}"
                            )
                        )
                    ).all()
                ]
        except Exception as exc:  # noqa: BLE001
            refs.append({"table": table, "column": column, "present": True,
                         "error": str(exc)[:200]})
            continue
        summary = classify_values(vals, mapping)
        summary["orphans"] = summary["orphans"][:orphan_samples]
        refs.append({
            "table": table, "column": column, "present": True, "data_type": dtype,
            "kind": "legacy_key" if (table, column) in _LEGACY_REFS else "awaiting_uuid_key",
            **summary,
        })

    map_exists = "site_id_map" in cols
    expand_done = "site_uuid" in sites_cols
    return {
        "ok": True,
        "sites_rows": len(mapping),
        "sites_key_type": key_type,
        "already_uuid_keyed": already_uuid,
        "expand_applied": expand_done,
        "map_table_exists": map_exists,
        "collisions": collisions,
        "namespace": str(SITE_NAMESPACE),
        "sample_mapping": [
            {"legacy_site_id": k, "site_uuid": v} for k, v in list(mapping.items())[:10]
        ],
        "references": refs,
        "totals": {
            "columns_to_translate": sum(1 for r in refs if r.get("present") and r.get("remappable")),
            "rows_to_translate": sum(r.get("remappable", 0) for r in refs),
            "orphan_rows": sum(r.get("orphan_count", 0) for r in refs),
        },
        "next": (
            "already uuid-keyed — nothing to do" if already_uuid
            else "run phase=expand (additive, reversible)" if not expand_done
            else "run phase=contract with confirm=true to swap the primary key"
        ),
    }


async def apply_expand(session: AsyncSession) -> dict[str, Any]:
    """Additive phase. Creates the map, adds site_uuid everywhere, drops nothing."""
    cols = await _columns(session)
    if not cols.get("sites"):
        return {"ok": False, "error": "plenum_cafm.sites does not exist"}
    if cols["sites"].get("site_id") == "uuid":
        return {"ok": True, "skipped": "sites is already uuid-keyed"}

    mapping = await _site_mapping(session)
    steps: list[str] = []

    async def run(sql: str, label: str, params: dict | None = None) -> bool:
        try:
            async with session.begin_nested():
                await session.execute(text(sql), params or {})
            steps.append(label)
            return True
        except Exception as exc:  # noqa: BLE001
            steps.append(f"{label} FAILED: {str(exc)[:160]}")
            log.warning("sites_uuid.expand_step_failed", step=label, error=str(exc)[:200])
            return False

    await run(
        f"""CREATE TABLE IF NOT EXISTS {MAP_TABLE} (
                legacy_site_id VARCHAR(50) PRIMARY KEY,
                site_uuid      UUID NOT NULL UNIQUE,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now())""",
        "create site_id_map",
    )
    await run(
        "ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS site_uuid UUID",
        "add sites.site_uuid",
    )

    for legacy, u in mapping.items():
        await run(
            f"INSERT INTO {MAP_TABLE} (legacy_site_id, site_uuid) VALUES (:l, CAST(:u AS UUID)) "
            f"ON CONFLICT (legacy_site_id) DO NOTHING",
            f"map {legacy}",
            {"l": legacy, "u": u},
        )
    await run(
        f"""UPDATE plenum_cafm.sites s SET site_uuid = m.site_uuid
            FROM {MAP_TABLE} m WHERE m.legacy_site_id = s.site_id::text
              AND s.site_uuid IS DISTINCT FROM m.site_uuid""",
        "populate sites.site_uuid",
    )
    await run(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_sites_site_uuid ON plenum_cafm.sites (site_uuid)",
        "unique index on sites.site_uuid",
    )

    # Shadow column on every table holding a legacy key, populated through the map.
    for table, column in _LEGACY_REFS:
        if column not in cols.get(table, {}):
            continue
        t, c = _ident(table), _ident(column)
        await run(
            f"ALTER TABLE plenum_cafm.{t} ADD COLUMN IF NOT EXISTS site_uuid UUID",
            f"add {table}.site_uuid",
        )
        await run(
            f"""UPDATE plenum_cafm.{t} x SET site_uuid = m.site_uuid
                FROM {MAP_TABLE} m WHERE m.legacy_site_id = x.{c}::text
                  AND x.site_uuid IS DISTINCT FROM m.site_uuid""",
            f"populate {table}.site_uuid",
        )

    # Tables already typed uuid can be filled where they carry a derivable legacy value.
    await session.commit()
    return {
        "ok": True,
        "phase": "expand",
        "sites_mapped": len(mapping),
        "steps": steps,
        "failed_steps": [s for s in steps if "FAILED" in s],
        "next": "verify, then run phase=contract with confirm=true",
    }


async def apply_contract(session: AsyncSession, *, confirm: bool = False) -> dict[str, Any]:
    """Cutover. Swaps the primary key. Requires expand and an explicit confirmation."""
    if not confirm:
        return {"ok": False, "error": "contract rewrites a primary key — pass confirm=true"}
    cols = await _columns(session)
    sites_cols = cols.get("sites", {})
    if not sites_cols:
        return {"ok": False, "error": "plenum_cafm.sites does not exist"}
    if sites_cols.get("site_id") == "uuid":
        return {"ok": True, "skipped": "sites is already uuid-keyed"}
    if "site_uuid" not in sites_cols:
        return {"ok": False, "error": "run phase=expand first — sites.site_uuid is missing"}

    unmapped = (
        await session.execute(
            text("SELECT count(*) FROM plenum_cafm.sites WHERE site_uuid IS NULL")
        )
    ).scalar_one()
    if unmapped:
        return {"ok": False, "error": f"{unmapped} sites have no site_uuid — re-run expand"}

    steps: list[str] = []

    async def run(sql: str, label: str) -> None:
        try:
            async with session.begin_nested():
                await session.execute(text(sql))
            steps.append(label)
        except Exception as exc:  # noqa: BLE001
            steps.append(f"{label} FAILED: {str(exc)[:160]}")
            log.warning("sites_uuid.contract_step_failed", step=label, error=str(exc)[:200])

    # Referencing tables first, so no window exists where sites is uuid and they are not.
    for table, column in _LEGACY_REFS:
        if "site_uuid" not in cols.get(table, {}):
            continue
        t, c = _ident(table), _ident(column)
        await run(
            f"ALTER TABLE plenum_cafm.{t} RENAME COLUMN {c} TO legacy_site_id",
            f"{table}.{column} -> legacy_site_id",
        )
        await run(
            f"ALTER TABLE plenum_cafm.{t} RENAME COLUMN site_uuid TO {c}",
            f"{table}.site_uuid -> {column}",
        )

    # The key itself. The old value is kept, never dropped, so this reads backwards.
    await run(
        "ALTER TABLE plenum_cafm.sites DROP CONSTRAINT IF EXISTS sites_pkey",
        "drop old primary key",
    )
    await run(
        "ALTER TABLE plenum_cafm.sites RENAME COLUMN site_id TO legacy_site_id",
        "sites.site_id -> legacy_site_id",
    )
    await run("ALTER TABLE plenum_cafm.sites RENAME COLUMN site_uuid TO site_id", "sites.site_uuid -> site_id")
    await run("ALTER TABLE plenum_cafm.sites ALTER COLUMN site_id SET NOT NULL", "site_id NOT NULL")
    await run("ALTER TABLE plenum_cafm.sites ADD PRIMARY KEY (site_id)", "new primary key on site_id")
    await run(
        "CREATE INDEX IF NOT EXISTS ix_sites_legacy_site_id ON plenum_cafm.sites (legacy_site_id)",
        "index legacy_site_id",
    )
    await session.commit()

    failed = [s for s in steps if "FAILED" in s]
    return {
        "ok": not failed,
        "phase": "contract",
        "steps": steps,
        "failed_steps": failed,
        "note": "legacy_site_id is retained on every table — the change reads backwards.",
    }
