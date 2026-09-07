"""End-to-end Fiix WorkOrder *write* test (fetch -> preprocess -> write -> verify).

Proves the full data-pull path lands real Fiix WorkOrder rows in a CAFM table —
the "after write / final result" the schema-mapping flow now auto-triggers.

SAFE: writes into a throwaway schema (fiix_wo_e2e_<pid>) cloned from
plenum_cafm.work_orders, verifies the row count, then DROPs the schema in a
finally block. Live plenum_cafm data is never touched.

Run inside the schema-mapper container:
    docker exec final-plenum-cafm-schema-mapper-app-1 \
        sh -lc 'cd /app && python tests/test_fiix_workorder_write_e2e.py'
"""
import asyncio
import os
import sys
from datetime import datetime
from uuid import uuid4

_SVC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _SVC_ROOT)
sys.path.insert(0, os.path.join(_SVC_ROOT, "..", "shared-lib"))

from sqlalchemy import text  # noqa: E402

from src.db import get_async_session_factory  # noqa: E402
from src.connectors.fiix_data_connector import FiixDataConnector  # noqa: E402
from src.graph.nodes.fiix_preprocess_node import _process_object, _table_clean  # noqa: E402
from src.graph.nodes.fiix_write_node import fiix_write_node  # noqa: E402
from src.models.migration import FiixIngestionJob  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


async def main():
    creds = [os.environ.get(k) for k in
             ("FIIX_SUBDOMAIN", "FIIX_APP_KEY", "FIIX_ACCESS_KEY", "FIIX_SECRET_KEY")]
    if not all(creds):
        print("SKIP - FIIX_* credentials not set")
        return
    sub, app, acc, sec = creds

    scratch = f"fiix_wo_e2e_{os.getpid()}"
    session_factory = get_async_session_factory()

    # ── 1. Pull + preprocess live WorkOrder rows ─────────────────────
    connector = FiixDataConnector(subdomain=sub, app_key=app, access_key=acc,
                                  secret_key=sec, timeout=30)
    raw, _ = connector.fetch_object_page("WorkOrder", start_index=0)
    check("WorkOrder rows pulled", len(raw) > 0)

    warnings: list[str] = []
    rows = _process_object("WorkOrder", raw, warnings)
    rows = _table_clean("work_orders", rows, warnings)
    check("rows survived preprocess/clean", len(rows) > 0)

    try:
        # ── 2. Build an isolated scratch schema with just work_orders ─
        async with session_factory() as s:
            await s.execute(text(f"CREATE SCHEMA IF NOT EXISTS {scratch}"))
            await s.execute(text(
                f"CREATE TABLE {scratch}.work_orders "
                f"(LIKE plenum_cafm.work_orders INCLUDING ALL)"
            ))
            await s.commit()

        # ── 3. Create the ingestion job row + run the real write node ─
        ingestion_id = uuid4()
        async with session_factory() as s:
            s.add(FiixIngestionJob(
                id=ingestion_id,
                organization_id=uuid4(),
                created_by="test_e2e",
                status="writing",
                progress_pct=66.0,
            ))
            await s.commit()

        async with session_factory() as write_session:
            state = {
                "ingestion_id": str(ingestion_id),
                "target_schema": scratch,
                "db_session": write_session,
                "preprocessed_tables": {"work_orders": rows},
                "status": "writing",
                "current_node": 3,
                "notes": [],
            }
            result = await fiix_write_node(state)

        check("write node completed", result.get("status") == "complete")
        written = result.get("total_records_written", 0)
        print(f"     total_records_written = {written}")
        print(f"     write_results = {result.get('write_results')}")
        check("WorkOrder rows written (>0)", written > 0)

        # ── 4. Verify the rows are actually in the table ──────────────
        # NB: this bare clone has only the canonical columns; the live schema-mapping
        # run additionally ADDs custom columns (wo_code, source_system, ...) via DDL.
        async with session_factory() as s:
            cnt = (await s.execute(
                text(f"SELECT count(*) FROM {scratch}.work_orders")
            )).scalar_one()
            sample = (await s.execute(text(
                f"SELECT id, location_id, description FROM {scratch}.work_orders "
                f"ORDER BY id LIMIT 1"
            ))).first()
        print(f"     row count in {scratch}.work_orders = {cnt}")
        print(f"     sample row (id, location_id, description) = {tuple(sample) if sample else None}")
        check("table actually contains rows", cnt >= written and cnt > 0)
        check("sample row has a Fiix integer id", sample is not None and isinstance(sample[0], int))

        print(f"\nALL TESTS PASSED - {written} WorkOrder rows written to {scratch}.work_orders")

    finally:
        # ── Always drop the scratch schema ────────────────────────────
        try:
            async with session_factory() as s:
                await s.execute(text(f"DROP SCHEMA IF EXISTS {scratch} CASCADE"))
                await s.commit()
            print(f"     cleaned up scratch schema {scratch}")
        except Exception as exc:
            print(f"     WARNING: failed to drop scratch schema {scratch}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
