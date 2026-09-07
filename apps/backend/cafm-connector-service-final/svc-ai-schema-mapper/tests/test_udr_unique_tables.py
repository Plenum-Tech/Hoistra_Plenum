"""Tests for Feature 7 7.3 unique-table identification (pure).
Run: python tests/test_udr_unique_tables.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.primitives import build_table_metadata  # noqa: E402
from udr.unique_tables import (  # noqa: E402
    apply_consolidation,
    identify_unique_tables,
    metadata_similarity,
    table_name_similarity,
)
from udr.pipeline import run_udr_pipeline  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# rows for the same entity (assets) under three names
assets_rows = [
    {"asset_id": "A1", "make": "Siemens", "site": "S1"},
    {"asset_id": "A2", "make": "Trane", "site": "S2"},
]
# 'Assets' — same data, near-identical name (case) -> AUTO-consolidate
assets_caps = [{"asset_id": "A3", "make": "Carrier", "site": "S1"}]
# 'work_tasks' — SAME metadata as assets, DIFFERENT name -> CANDIDATE (confirm)
work_tasks = [
    {"asset_id": "A1", "make": "Siemens", "site": "S1"},
    {"asset_id": "A4", "make": "Daikin", "site": "S3"},
]
# vendors — different entity entirely -> UNIQUE
vendors = [{"vendor_id": "V1", "vendor_name": "Apex", "phone": "111"}]


def meta(name, rows):
    cols = list({k for r in rows for k in r})
    return build_table_metadata(name, rows, sorted(cols))


# ── name + metadata similarity primitives ───────────────────────────────────
check("identical normalized names -> 1.0", table_name_similarity("assets", "Assets") == 1.0)
check("very different names -> low", table_name_similarity("assets", "work_tasks") < 0.5)
m_assets, m_caps, m_wt, m_ven = meta("assets", assets_rows), meta("Assets", assets_caps), meta("work_tasks", work_tasks), meta("vendors", vendors)
check("same-entity metadata sim high (>=0.8)", metadata_similarity(m_assets, m_wt) >= 0.80)
check("different-entity metadata sim low (<0.8)", metadata_similarity(m_assets, m_ven) < 0.80)

# ── identify_unique_tables: auto / candidate / unique split (7.3 AC1-AC3) ────
report = identify_unique_tables([m_assets, m_caps, m_wt, m_ven])
check("assets + Assets auto-consolidated (name>=0.95 & meta>=0.80)",
      ["Assets", "assets"] in report["auto_consolidated"])
cand_pairs = [sorted(c["tables"]) for c in report["candidates"]]
check("work_tasks vs an assets-table is a candidate (name<0.95 & meta>=0.80)",
      any("work_tasks" in p for p in cand_pairs))
check("candidate carries name + metadata similarity + confidence",
      all({"name_similarity", "metadata_similarity", "confidence"} <= set(c) for c in report["candidates"]))
check("vendors stays unique", "vendors" in report["unique"])

# ── apply_consolidation merges grouped rows under one representative ─────────
merged = apply_consolidation(
    {"assets": assets_rows, "Assets": assets_caps, "vendors": vendors},
    [["Assets", "assets"]],
)
check("merged under representative 'Assets'", "Assets" in merged and "assets" not in merged)
check("merged rows = union (2+1)", len(merged["Assets"]) == 3)
check("vendors passthrough untouched", merged["vendors"] == vendors)

# ── pipeline integration: auto-consolidation happens before classify ────────
res = run_udr_pipeline(
    {"assets": assets_rows, "Assets": assets_caps, "vendors": vendors},
    run_id="u1",
)
check("pipeline reports the consolidated group", ["Assets", "assets"] in res.consolidated_groups)
# after consolidation: 2 tables (Assets+vendors), not 3
check("pipeline collapsed to 2 tables", res.table_count == 2)
table_names = {t["table"] for t in res.table_metadata}
check("consolidated representative present, dup gone", "Assets" in table_names and "assets" not in table_names)

# opt-out keeps tables separate
res_off = run_udr_pipeline(
    {"assets": assets_rows, "Assets": assets_caps, "vendors": vendors},
    run_id="u2", consolidate_tables=False,
)
check("consolidate_tables=False keeps 3 tables", res_off.table_count == 3)

print("\nALL TESTS PASSED")
