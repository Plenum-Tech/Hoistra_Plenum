"""
Docker startup helper: align Azure/existing DB with Alembic without re-creating tables.

1. Always add missing work_orders columns (idempotent).
2. If plenum_cafm.work_orders exists but alembic_version is empty, stamp 004 then upgrade.
3. On duplicate-object errors during upgrade, stamp head and continue.
"""
from __future__ import annotations

import os
import subprocess
import sys

import psycopg2


def _sync_url() -> str:
    raw = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DB_URL", "").strip()
    if not raw:
        print("migrate_startup: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if raw.startswith(prefix):
            return "postgresql://" + raw[len(prefix) :]
    return raw


def _connect():
    return psycopg2.connect(_sync_url())


def _ensure_work_order_columns(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE plenum_cafm.work_orders "
            "ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(14, 2)"
        )
        cur.execute(
            "ALTER TABLE plenum_cafm.work_orders "
            "ADD COLUMN IF NOT EXISTS asset_category VARCHAR(100)"
        )
    conn.commit()
    print("migrate_startup: work_orders columns OK (estimated_cost, asset_category)")


def _ensure_approval_request_columns(conn) -> None:
    """005 may be stamped but not applied on pre-provisioned Azure schemas."""
    ddls = (
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS level INTEGER",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS step_order INTEGER",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS risk_score INTEGER",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS match_score INTEGER",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS suggestion_source VARCHAR(20)",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS unblocked_at TIMESTAMPTZ",
    )
    with conn.cursor() as cur:
        for ddl in ddls:
            cur.execute(ddl)
    conn.commit()
    print("migrate_startup: wo_approval_requests columns OK (level, step_order, ...)")


def _table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'plenum_cafm' AND table_name = %s
            """,
            (table,),
        )
        return cur.fetchone() is not None


def _alembic_revision(conn) -> str | None:
    """Alembic stores its version table in the public schema."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'alembic_version'
            """
        )
        if cur.fetchone() is None:
            return None
        cur.execute("SELECT version_num FROM public.alembic_version LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None


def _run_alembic(*args: str) -> int:
    root = os.path.dirname(os.path.dirname(__file__))
    proc = subprocess.run(["alembic", *args], cwd=root)
    return proc.returncode


def main() -> None:
    conn = _connect()
    try:
        _ensure_work_order_columns(conn)
        if _table_exists(conn, "wo_approval_requests"):
            _ensure_approval_request_columns(conn)

        if _table_exists(conn, "work_orders") and _alembic_revision(conn) is None:
            print("migrate_startup: existing work_orders — stamping alembic at 004")
            if _run_alembic("stamp", "004") != 0:
                sys.exit(1)

        code = _run_alembic("upgrade", "head")
        if code != 0:
            print(
                "migrate_startup: upgrade failed — stamping head (schema likely pre-provisioned)",
                file=sys.stderr,
            )
            if _run_alembic("stamp", "head") != 0:
                sys.exit(1)
    finally:
        conn.close()

    print("migrate_startup: complete")


if __name__ == "__main__":
    main()
