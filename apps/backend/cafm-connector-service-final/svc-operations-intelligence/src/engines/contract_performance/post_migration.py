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
from ...shared.vendor_identity import live_vendor_sql

log = get_logger(__name__)

# (table, code column) pairs that a Feature B upload writes to.
_TABLES = (("work_orders", "wo_code"), ("ppm_visits", "ppm_code"))


async def _dedupe(
    session: AsyncSession, table: str, code_col: str, codes: list[str], org: str
) -> int:
    """Keep one row per code — the newest, which is the most completely written.

    Newest rather than oldest on purpose: when a run fails partway and is retried, the
    earlier copies are the truncated ones. Keeping the lowest id preserves exactly the
    rows with NULL columns.

    Both sides in the caller's company (review, 12 Oct 2026): a work order is a duplicate only
    of its own company's row. Unscoped, company B's WO-0001 was deleted whenever company A
    also had a WO-0001 with a larger id.
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
              AND a.organization_id::text = :org
              AND b.organization_id::text = :org
              AND a.id < b.id
            """  # noqa: S608 — table and column come from the fixed _TABLES tuple
        ),
        {"codes": codes, "org": org},
    )
    return result.rowcount or 0


async def _columns(session: AsyncSession, table: str) -> set[str]:
    res = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='plenum_cafm' AND table_name=:t"
        ),
        {"t": table},
    )
    return {str(r[0]) for r in res.all()}


async def _reconcile_table(
    session: AsyncSession,
    table: str,
    code_col: str,
    named: dict[str, str],
    org: str,
    vendor_scope: str,
) -> dict[str, Any]:
    cols = await _columns(session, table)
    # A table without its code column (ppm_visits has no ppm_code on either shape) or without
    # a company column cannot be reconciled in one company — skipped, not failed (review,
    # 12 Oct 2026: its error used to roll back the other table's links).
    missing = [c for c in (code_col, "vendor_id", "organization_id") if c not in cols]
    if missing:
        return {"skipped": f"no {', '.join(missing)} column"}

    stats: dict[str, Any] = {}
    codes = sorted(named.keys())
    if not codes:
        # Nothing named for this table by the caller; fall back to whatever the
        # row itself carries, which is the ppm_visits case.
        res = await session.execute(
            text(
                f"SELECT DISTINCT t.{code_col} FROM plenum_cafm.{table} t "  # noqa: S608
                f"WHERE t.vendor_id IS NULL AND t.{code_col} IS NOT NULL "
                f"AND t.organization_id::text = :org LIMIT 5000"
            ),
            {"org": org},
        )
        codes = [r[0] for r in res.all()]

    stats["duplicates_removed"] = await _dedupe(session, table, code_col, codes, org)

    linked = 0
    # Resolve by name held on the row where the column exists.
    if "vendor_name" in cols:
        res = await session.execute(
            text(
                f"""
                UPDATE plenum_cafm.{table} t
                   SET vendor_id = CAST(v.id AS uuid)
                  FROM plenum_cafm.vendors v
                 WHERE t.vendor_id IS NULL
                   AND t.vendor_name IS NOT NULL
                   AND t.organization_id::text = :org
                   AND lower(btrim(t.vendor_name, ' .,')) = lower(btrim(v.vendor_name, ' .,'))
                   -- vendors.id is VARCHAR on production and uuid on hoistra_test: read as
                   -- text so the test is legal on both, and a non-UUID id cannot fail the cast.
                   AND v.id::text ~ '^[0-9a-fA-F-]{{36}}$'
                   {vendor_scope}
                   AND t.{code_col} = ANY(:codes)
                """  # noqa: S608
            ),
            {"codes": codes, "org": org},
        )
        linked += res.rowcount or 0

    # Resolve from the caller's map for tables that dropped the name.
    for code, vendor_name in named.items():
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
                   AND t.organization_id::text = :org
                   AND lower(btrim(v.vendor_name, ' .,')) = lower(btrim(:name, ' .,'))
                   AND v.id::text ~ '^[0-9a-fA-F-]{{36}}$'
                   {vendor_scope}
                """  # noqa: S608
            ),
            {"code": code, "name": vendor_name, "org": org},
        )
        linked += res.rowcount or 0

    stats["vendor_linked"] = linked
    remaining = (
        await session.execute(
            text(
                f"SELECT count(*) FROM plenum_cafm.{table} t "  # noqa: S608
                f"WHERE t.{code_col} = ANY(:codes) AND t.vendor_id IS NULL "
                f"AND t.organization_id::text = :org"
            ),
            {"codes": codes, "org": org},
        )
    ).scalar()
    stats["still_unlinked"] = int(remaining or 0)
    return stats


async def reconcile_feature_b_migration(
    session: AsyncSession,
    *,
    vendor_links: dict[str, dict[str, str]] | None = None,
    organization_id: Any = None,
) -> dict[str, Any]:
    """Deduplicate and vendor-link the rows a Feature B migration just wrote.

    ``vendor_links`` maps table -> {code: vendor_name}, supplied by the caller from the
    uploaded file for tables that do not keep the vendor's name. Tables that do keep it
    (ppm_visits) are resolved from the row itself and need no input.

    A name is matched only to a vendor that is not deleted, and only to the caller's company's
    vendors. The match used to reach every tenant's register, and a deleted vendor (whose name
    the row still carries) was re-linked to its old work, which put it back into scoring
    (Hussain, 12 Oct 2026). The rows themselves are the company's too — the codes picked, both
    sides of the de-duplication and every update — and a call naming no company does nothing
    (review, 12 Oct 2026): unscoped, it deleted and relinked other tenants' work orders.
    """
    org = str(organization_id).strip() if organization_id is not None else ""
    if not org:
        return {"ok": False, "reason": "no_company",
                "error": "No company was named, so nothing was reconciled."}
    links = vendor_links or {}
    out: dict[str, Any] = {"ok": True, "tables": {}}
    vendor_scope = f"AND {live_vendor_sql('v')} AND v.organization_id::text = :org"

    for table, code_col in _TABLES:
        try:
            # Each table in its own savepoint: one failing must not cost the other its
            # reconciliation. A session.rollback() here undid the tables before it (review,
            # 12 Oct 2026: work_orders' links vanished when ppm_visits failed after them).
            async with session.begin_nested():
                stats = await _reconcile_table(
                    session, table, code_col, dict(links.get(table) or {}), org, vendor_scope)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "post_migration.reconcile_failed", table=table, error=str(exc)[:300]
            )
            stats = {"error": str(exc)[:200]}
            out["ok"] = False
        out["tables"][table] = stats

    await session.commit()
    log.info("post_migration.reconciled", **{k: v for k, v in out["tables"].items()})
    return out
