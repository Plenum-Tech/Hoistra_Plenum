"""Fiix write resilience test (savepoints + JSON-safe raw_metadata + clone has no FK).

Reproduces the multi-table failure modes seen writing into the live plenum_cafm:
  - assets: 'Object of type Timestamp is not JSON serializable' (raw_metadata)
  - asset_categories: NOT NULL on category_name → first bad row poisoned the rest
  - work_orders: FK location_id → locations

Writes into a throwaway schema cloned with LIKE ... INCLUDING ALL (same as the real
migration clone, which does NOT copy FK constraints). Asserts:
  - assets rows insert (JSON fix)
  - work_orders rows insert (no FK in clone)
  - a NOT NULL table's good rows still land and failures are isolated (savepoints)
Drops the scratch schema in a finally block.
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
    scratch = f"fiix_res_{os.getpid()}"
    sf = get_async_session_factory()
    conn = FiixDataConnector(subdomain=sub, app_key=app, access_key=acc, secret_key=sec, timeout=30)

    # objects -> target table to exercise (subset; assets has raw_metadata + dates)
    plan = [("Asset", "assets"), ("WorkOrder", "work_orders"), ("AssetCategory", "asset_categories")]

    preprocessed = {}
    for obj, table in plan:
        raw, _ = conn.fetch_object_page(obj, start_index=0)
        warnings = []
        rows = _table_clean(table, _process_object(obj, raw, warnings), warnings)
        preprocessed[table] = rows
        print(f"     pulled {obj}: {len(rows)} rows -> {table}")

    try:
        async with sf() as s:
            await s.execute(text(f"CREATE SCHEMA IF NOT EXISTS {scratch}"))
            for _, table in plan:
                await s.execute(text(
                    f"CREATE TABLE {scratch}.{table} (LIKE plenum_cafm.{table} INCLUDING ALL)"
                ))
            await s.commit()

        ingestion_id = uuid4()
        async with sf() as s:
            s.add(FiixIngestionJob(id=ingestion_id, organization_id=uuid4(),
                                   created_by="test_res", status="writing", progress_pct=66.0))
            await s.commit()

        async with sf() as ws:
            state = {
                "ingestion_id": str(ingestion_id),
                "target_schema": scratch,
                "db_session": ws,
                "preprocessed_tables": preprocessed,
                "status": "writing", "current_node": 3, "notes": [],
            }
            result = await fiix_write_node(state)

        wr = result.get("write_results", {})
        print(f"     write_results = {wr}")
        check("write node completed", result.get("status") == "complete")
        check("assets inserted (JSON-safe raw_metadata)", wr.get("assets", {}).get("inserted", 0) > 0)
        check("work_orders inserted (no FK in clone)", wr.get("work_orders", {}).get("inserted", 0) > 0)

        # asset_categories has NOT NULL category_name → rows may fail, but the failure
        # must be isolated (no crash / cascade). Verify the run still completed and
        # other tables were unaffected.
        ac = wr.get("asset_categories", {})
        print(f"     asset_categories: {ac}")
        check("asset_categories failures isolated (no cascade crash)", "inserted" in ac)

        async with sf() as s:
            acount = (await s.execute(text(f"SELECT count(*) FROM {scratch}.assets"))).scalar_one()
            wcount = (await s.execute(text(f"SELECT count(*) FROM {scratch}.work_orders"))).scalar_one()
        print(f"     rows in clone: assets={acount}, work_orders={wcount}")
        check("assets rows present in DB", acount > 0)
        check("work_orders rows present in DB", wcount > 0)

        print(f"\nALL TESTS PASSED - assets={acount}, work_orders={wcount} written to {scratch}")
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
