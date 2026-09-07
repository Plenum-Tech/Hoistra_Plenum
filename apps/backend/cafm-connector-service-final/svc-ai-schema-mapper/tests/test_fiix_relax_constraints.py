"""Verify constraint relaxation lets NOT NULL / unique-blocked Fiix rows land.

Clones canonical tables that previously failed on NOT NULL (asset_categories.category_name,
work_order_tasks.title) into a scratch schema, runs _relax_fiix_target_constraints, then
writes the real Fiix rows and asserts they now insert. Drops the scratch schema.
"""
import asyncio
import os
import sys
from uuid import uuid4

_SVC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _SVC_ROOT)
sys.path.insert(0, os.path.join(_SVC_ROOT, "..", "shared-lib"))

from sqlalchemy import text  # noqa: E402

from src.db import get_async_session_factory  # noqa: E402
from src.connectors.fiix_data_connector import FiixDataConnector  # noqa: E402
from src.graph.nodes.fiix_preprocess_node import _process_object, _table_clean  # noqa: E402
from src.graph.nodes.fiix_write_node import fiix_write_node  # noqa: E402
from src.graph.nodes.schema_write_node import _relax_fiix_target_constraints  # noqa: E402
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
    scratch = f"fiix_relax_{os.getpid()}"
    sf = get_async_session_factory()
    conn = FiixDataConnector(subdomain=sub, app_key=app, access_key=acc, secret_key=sec, timeout=30)
    plan = [("AssetCategory", "asset_categories"), ("WorkOrderTask", "work_order_tasks")]

    preprocessed = {}
    for obj, table in plan:
        raw, _ = conn.fetch_object_page(obj, start_index=0)
        warnings = []
        preprocessed[table] = _table_clean(table, _process_object(obj, raw, warnings), warnings)
        print(f"     pulled {obj}: {len(preprocessed[table])} rows -> {table}")

    try:
        async with sf() as s:
            await s.execute(text(f"CREATE SCHEMA IF NOT EXISTS {scratch}"))
            for _, t in plan:
                await s.execute(text(f"CREATE TABLE {scratch}.{t} (LIKE plenum_cafm.{t} INCLUDING ALL)"))
            await s.commit()

        # Relax constraints on the clone (the new behaviour)
        async with sf() as s:
            cols, uniques = await _relax_fiix_target_constraints(
                s, scratch, [t for _, t in plan])
        print(f"     relaxed: {cols} NOT NULL cols, {uniques} unique constraints")
        check("relaxed at least one NOT NULL column", cols > 0)

        ingestion_id = uuid4()
        async with sf() as s:
            s.add(FiixIngestionJob(id=ingestion_id, organization_id=uuid4(),
                                   created_by="test_relax", status="writing", progress_pct=66.0))
            await s.commit()

        async with sf() as ws:
            state = {
                "ingestion_id": str(ingestion_id), "target_schema": scratch,
                "db_session": ws, "preprocessed_tables": preprocessed,
                "status": "writing", "current_node": 3, "notes": [],
            }
            result = await fiix_write_node(state)

        wr = result.get("write_results", {})
        print(f"     write_results = {wr}")
        check("asset_categories now inserts after relax",
              wr.get("asset_categories", {}).get("inserted", 0) > 0)
        check("work_order_tasks now inserts after relax",
              wr.get("work_order_tasks", {}).get("inserted", 0) > 0)
        print("\nALL TESTS PASSED - constraint relaxation lets the blocked tables land")
    finally:
        try:
            async with sf() as s:
                await s.execute(text(f"DROP SCHEMA IF EXISTS {scratch} CASCADE"))
                await s.commit()
            print(f"     cleaned up scratch schema {scratch}")
        except Exception as exc:
            print(f"     WARNING: failed to drop {scratch}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
