"""Reconcile what the migration leaves behind, so Feature B can read its own data.

The migration lands rows but does not finish two jobs that scoring depends on.

Vendor linkage. Scoring and PPM completion both filter by vendor_id, and the migration
writes it as NULL — so a vendor's own work orders are invisible to their scorecard. On
ppm_visits the vendor's name survives on the row and can be resolved directly. On
work_orders it does not: the destination table has no vendor_name column, so the name is
dropped at write time and there is nothing left to resolve from. That linkage has to be
supplied by the caller, which still holds the uploaded file.

Duplicates. The migration's write is not idempotent — the confirm-write step can fire
several times for one run, and each pass inserts a full copy. Assets survive it because
they have a real primary key; work orders and PPM visits use serial ids, so 390 rows
became 1,950. Scoring counts rows, so duplicates inflate every component silently.

Both are repaired here rather than in the shared mapper: this runs after a Feature B
upload, is scoped to the codes that upload carried, and cannot affect another tenant's
migration. The mapper's write should still be made idempotent — this is the safety net,
not the fix.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

# (table, code column) pairs that a Feature B upload writes to.
_TABLES = (("work_orders", "wo_code"), ("ppm_visits", "ppm_code"))


async def _dedupe(session: AsyncSession, table: str, code_col: str, codes: list[str]) -> int:
    """Keep one row per code — the newest, which is the most completely written.

    Newest rather than oldest on purpose: when a run fails partway and is retried, the
    earlier copies are the truncated ones. Keeping the lowest id preserves exactly the
    rows with NULL columns.
    """
    if not codes:
        return 0
    result = await session.execute(
        text(
            f"""
            DELETE FROM plenum_cafm.{table} a
            USING plenum_cafm.{table} b
            WHERE a.{code_col} = ANY(:codes)
              AND a.{code_col} = b.{code_col}
              AND a.id < b.id
            """  # noqa: S608 — table and column come from the fixed _TABLES tuple
        ),
        {"codes": codes},
    )
    return result.rowcount or 0


async def reconcile_feature_b_migration(
    session: AsyncSession,
    *,
    vendor_links: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Deduplicate and vendor-link the rows a Feature B migration just wrote.

    ``vendor_links`` maps table -> {code: vendor_name}, supplied by the caller from the
    uploaded file for tables that do not keep the vendor's name. Tables that do keep it
    (ppm_visits) are resolved from the row itself and need no input.
    """
    links = vendor_links or {}
    out: dict[str, Any] = {"ok": True, "tables": {}}

    for table, code_col in _TABLES:
        codes = sorted((links.get(table) or {}).keys())
        stats: dict[str, Any] = {}
        try:
            if not codes:
                # Nothing named for this table by the caller; fall back to whatever the
                # row itself carries, which is the ppm_visits case.
                res = await session.execute(
                    text(
                        f"SELECT DISTINCT {code_col} FROM plenum_cafm.{table} "  # noqa: S608
                        f"WHERE vendor_id IS NULL AND {code_col} IS NOT NULL LIMIT 5000"
                    )
                )
                codes = [r[0] for r in res.all()]

            stats["duplicates_removed"] = await _dedupe(session, table, code_col, codes)

            # Resolve by name held on the row where the column exists.
            has_name = (
                await session.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema='plenum_cafm' AND table_name=:t "
                        "AND column_name='vendor_name'"
                    ),
                    {"t": table},
                )
            ).first() is not None

            linked = 0
            if has_name:
                res = await session.execute(
                    text(
                        f"""
                        UPDATE plenum_cafm.{table} t
                           SET vendor_id = CAST(v.id AS uuid)
                          FROM plenum_cafm.vendors v
                         WHERE t.vendor_id IS NULL
                           AND t.vendor_name IS NOT NULL
                           AND lower(btrim(t.vendor_name, ' .,')) = lower(btrim(v.vendor_name, ' .,'))
                           -- vendors.id is VARCHAR; a row whose id is not a UUID would
                           -- fail the cast and take the whole statement with it.
                           AND v.id ~ '^[0-9a-fA-F-]{{36}}$'
                           AND t.{code_col} = ANY(:codes)
                        """  # noqa: S608
                    ),
                    {"codes": codes},
                )
                linked += res.rowcount or 0

            # Resolve from the caller's map for tables that dropped the name.
            for code, vendor_name in (links.get(table) or {}).items():
                if not vendor_name:
                    continue
                res = await session.execute(
                    text(
                        f"""
                        UPDATE plenum_cafm.{table} t
                           SET vendor_id = CAST(v.id AS uuid)
                          FROM plenum_cafm.vendors v
                         WHERE t.vendor_id IS NULL
                           AND t.{code_col} = :code
                           AND lower(btrim(v.vendor_name, ' .,')) = lower(btrim(:name, ' .,'))
                           AND v.id ~ '^[0-9a-fA-F-]{{36}}$'
                        """  # noqa: S608
                    ),
                    {"code": code, "name": vendor_name},
                )
                linked += res.rowcount or 0

            stats["vendor_linked"] = linked
            remaining = (
                await session.execute(
                    text(
                        f"SELECT count(*) FROM plenum_cafm.{table} "  # noqa: S608
                        f"WHERE {code_col} = ANY(:codes) AND vendor_id IS NULL"
                    ),
                    {"codes": codes},
                )
            ).scalar()
            stats["still_unlinked"] = int(remaining or 0)
        except Exception as exc:  # noqa: BLE001
            # One table failing must not cost the other its reconciliation, and must not
            # leave the caller's transaction poisoned.
            await session.rollback()
            log.warning(
                "post_migration.reconcile_failed", table=table, error=str(exc)[:300]
            )
            stats["error"] = str(exc)[:200]
            out["ok"] = False
        out["tables"][table] = stats

    await session.commit()
    log.info("post_migration.reconciled", **{k: v for k, v in out["tables"].items()})
    return out
