"""Strict Step-2 value-pattern gate — value-shape skeleton instead of coarse format class.

Verifies:
  - shape skeleton: collapses runs of digits / upper / lower letters with counts
  - value_shape_similarity: 1.0 when both columns follow the same shape, lower otherwise
  - group_similar_columns: 'A-001' and 'AHU-9000' (both 'id_code') no longer group
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared-lib"))

from udr.primitives import (  # noqa: E402
    column_format,
    format_similarity,
    group_similar_columns,
    value_shape,
    value_shape_similarity,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── 1. shape skeleton ──
check("A-001 → A-#3",       value_shape("A-001") == "A-#3")
check("AHU-9000 → A3-#4",   value_shape("AHU-9000") == "A3-#4")
check("Gen2 → Aa2#",        value_shape("Gen2") == "Aa2#")
check("S-01 → A-#2",        value_shape("S-01") == "A-#2")
check("W-1001 → A-#4",      value_shape("W-1001") == "A-#4")
check("Siemens → Aa6",      value_shape("Siemens") == "Aa6")
check("2026-01-04 → #4-#2-#2", value_shape("2026-01-04") == "#4-#2-#2")
check("empty → ''",         value_shape("") == "")
check("None → ''",          value_shape(None) == "")

# ── 2. value_shape_similarity discriminates same-format-class but different shapes ──
asset_codes = [{"c": v} for v in ["A-001", "A-002", "A-003", "A-004"]]
# Uniform id_code shape (A3-#3) — same FORMAT CLASS as asset_codes, different SHAPE
product_codes = [{"c": v} for v in ["AHU-001", "RTAC-002", "MOT-003", "BLR-004"]]
site_refs = [{"c": v} for v in ["S-01", "S-02", "S-03"]]
work_orders = [{"c": v} for v in ["W-1001", "W-1002", "W-1003", "W-1004"]]

# Both 100% 'id_code' under the coarse format classifier
check("asset_codes format=id_code",  column_format(asset_codes, "c") == "id_code")
check("product_codes format=id_code", column_format(product_codes, "c") == "id_code")

# OLD Step-2 (format_similarity) does NOT discriminate — both profile uniformly as id_code
print(f"  format_similarity (asset vs product)   = {format_similarity(asset_codes, 'c', product_codes, 'c'):.2f}")
check("OLD format gate lets asset↔product through (both 100% id_code)",
      format_similarity(asset_codes, "c", product_codes, "c") >= 0.80)

# NEW strict Step-2 catches the shape difference — A-#3 vs A3-#3
print(f"  value_shape_similarity (asset vs product) = {value_shape_similarity(asset_codes, 'c', product_codes, 'c'):.2f}")
check("NEW strict gate rejects asset↔product (A-#3 vs A3-#3 different shapes)",
      value_shape_similarity(asset_codes, "c", product_codes, "c") < 0.80)

# But still passes the genuine same-shape pair (asset_codes ↔ same-shape FK column)
asset_no = [{"c": v} for v in ["A-001", "A-003", "A-002"]]
print(f"  value_shape_similarity (asset vs asset_no) = {value_shape_similarity(asset_codes, 'c', asset_no, 'c'):.2f}")
check("NEW strict gate accepts asset↔asset_no (same A-#3 shape)",
      value_shape_similarity(asset_codes, "c", asset_no, "c") >= 0.80)

# And separates 'A-#3' (assets) from 'A-#4' (work orders) — different digit-run length
print(f"  value_shape_similarity (asset vs WO)    = {value_shape_similarity(asset_codes, 'c', work_orders, 'c'):.2f}")
check("NEW strict gate rejects asset↔WO (A-#3 vs A-#4)",
      value_shape_similarity(asset_codes, "c", work_orders, "c") < 0.80)

# And separates 'A-#3' (assets) from 'A-#2' (sites)
print(f"  value_shape_similarity (asset vs site)  = {value_shape_similarity(asset_codes, 'c', site_refs, 'c'):.2f}")
check("NEW strict gate rejects asset↔site (A-#3 vs A-#2)",
      value_shape_similarity(asset_codes, "c", site_refs, "c") < 0.80)


# ── 3. group_similar_columns end-to-end on a synthetic fixture ──
tables = {
    "qatty": {
        "rows": [{"tag_id": f"A-{n:03d}", "product_code": p} for n, p in zip(
            [1, 2, 3, 4, 5], ["AHU-001", "RTAC-002", "MOT-003", "FAP-004", "BLR-005"])],
        "columns": ["tag_id", "product_code"],
    },
    "WorkOrders": {
        "rows": [{"asset_no": f"A-{n:03d}"} for n in [1, 3, 2, 5, 1]],
        "columns": ["asset_no"],
    },
    "Sites": {
        "rows": [{"site_ref": f"S-{n:02d}"} for n in [1, 2, 3]],
        "columns": ["site_ref"],
    },
}
groups = {tuple(sorted(g)) for g in group_similar_columns(tables) if len(g) > 1}
print(f"  multi-member groups: {groups}")
check("asset_no ↔ tag_id grouped (A-#3 each)",
      (("WorkOrders", "asset_no"), ("qatty", "tag_id")) in groups)
check("tag_id ↔ product_code NOT grouped (A-#3 vs mixed shapes)",
      not any({("qatty", "tag_id"), ("qatty", "product_code")} <= set(g) for g in groups))
check("site_ref NOT grouped with assets (A-#2 vs A-#3)",
      not any({("Sites", "site_ref"), ("qatty", "tag_id")} <= set(g) for g in groups))

print("\nALL TESTS PASSED")
