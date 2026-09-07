"""Live Fiix WorkOrder data-pull + transform test.

Proves the Fiix DataExport (FindRequest) API actually returns WorkOrder *rows*
and that the ingestion preprocess step maps them onto plenum_cafm.work_orders
columns. This is the data-pull (rows) path that runs AFTER schema mapping —
auto-triggered by schema_write_node for Fiix sources.

Requires live Fiix credentials in the environment:
    FIIX_SUBDOMAIN, FIIX_APP_KEY, FIIX_ACCESS_KEY, FIIX_SECRET_KEY

Run inside the schema-mapper container:
    docker exec final-plenum-cafm-schema-mapper-app-1 \
        sh -lc 'cd /app && python src/../svc-ai-schema-mapper/tests/test_fiix_workorder_pull.py'
or simply (paths already on sys.path inside the image):
    python tests/test_fiix_workorder_pull.py
"""
import os
import sys

# The app is rooted at the `src` package (uvicorn runs `src.app:app`), and the
# Fiix nodes use `from ...connectors ...` relative imports — so we must put the
# directory *containing* `src` on the path and import via the `src.` prefix.
_SVC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _SVC_ROOT)
sys.path.insert(0, os.path.join(_SVC_ROOT, "..", "shared-lib"))

from src.connectors.fiix_data_connector import FiixDataConnector  # noqa: E402
from src.connectors.fiix_plenum_mappings import resolve_plenum_column  # noqa: E402
from src.graph.nodes.fiix_preprocess_node import _process_object  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def _is_uuid(value) -> bool:
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def main():
    sub = os.environ.get("FIIX_SUBDOMAIN")
    app = os.environ.get("FIIX_APP_KEY")
    acc = os.environ.get("FIIX_ACCESS_KEY")
    sec = os.environ.get("FIIX_SECRET_KEY")
    if not all([sub, app, acc, sec]):
        print("SKIP - FIIX_* credentials not set in environment")
        return

    connector = FiixDataConnector(
        subdomain=sub, app_key=app, access_key=acc, secret_key=sec, timeout=30
    )

    # ── 1. Connectivity ──────────────────────────────────────────────
    check("Fiix API reachable", connector.api.test_connection() is True)

    # ── 2. Pull a page of WorkOrder rows (DataExport / FindRequest) ──
    records, has_more = connector.fetch_object_page("WorkOrder", start_index=0)
    print(f"     fetched {len(records)} WorkOrder records (has_more={has_more})")
    check("WorkOrder rows returned (>0)", len(records) > 0)
    check("WorkOrder rows are dicts with a Fiix id", all("id" in r for r in records))

    sample = records[0]
    print(f"     sample raw id={sample.get('id')} strCode={sample.get('strCode')}")

    # ── 3. Field mapping: Fiix field -> plenum_cafm column ───────────
    check("WorkOrder.strCode maps to wo_code",
          resolve_plenum_column("WorkOrder", "strCode") == "wo_code")

    # ── 4. Full preprocess transform (UUID, rename, FK resolve, tag) ─
    warnings: list[str] = []
    processed = _process_object("WorkOrder", records, warnings)
    check("preprocess kept all rows", len(processed) == len(records))

    row0 = processed[0]
    check("row id is a deterministic UUID", _is_uuid(row0.get("id")))
    check("fiix_source_id preserved", row0.get("fiix_source_id") == sample.get("id"))
    check("source_system tagged 'fiix'", row0.get("source_system") == "fiix")
    check("wo_code present after rename", "wo_code" in row0)

    # FK columns should be resolved to UUIDs (when the raw int*ID was present)
    if sample.get("intSiteID") is not None:
        site_col = resolve_plenum_column("WorkOrder", "intSiteID") or "intSiteID"
        check(f"FK {site_col} resolved to UUID", _is_uuid(row0.get(site_col)))

    print(f"     transformed row keys (first 12): {sorted(row0.keys())[:12]}")
    print(f"\nALL TESTS PASSED - {len(records)} WorkOrder rows pulled + transformed")


if __name__ == "__main__":
    main()
