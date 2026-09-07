"""Unit tests for Feature 7 value-centric UDR primitives. Run: python tests/test_udr_primitives.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.primitives import (  # noqa: E402
    detect_primary_key,
    is_primary_key_column,
    null_rate,
    uniqueness,
    cell_format,
    column_format,
    value_overlap,
    referential_integrity,
    classify_columns,
    build_table_metadata,
    build_column_metadata,
)

# ── fixtures ────────────────────────────────────────────────────────────────
assets = [
    {"asset_id": "A1", "make": "Siemens", "site_code": "S1"},
    {"asset_id": "A2", "make": "Siemens", "site_code": "S1"},
    {"asset_id": "A3", "make": "Trane", "site_code": "S2"},
]
sites = [
    {"site_code": "S1", "site_name": "Tower A", "city": "Sharjah"},
    {"site_code": "S2", "site_name": "Tower B", "city": "Ajman"},
]
work_orders = [  # all-null PK candidate + composite case
    {"wo_no": "W1", "asset_id": "A1", "date": "7/17/2009"},
    {"wo_no": "W2", "asset_id": "A1", "date": "2/15/2016"},
    {"wo_no": "W3", "asset_id": "A9", "date": "1/2/2020"},  # A9 not in assets (RI < 1)
]


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── PK detection ────────────────────────────────────────────────────────────
check("asset_id is PK", is_primary_key_column(assets, "asset_id"))
check("make is NOT PK (dups)", not is_primary_key_column(assets, "make"))
check("null_rate asset_id == 0", null_rate(assets, "asset_id") == 0.0)
check("uniqueness make < 1", uniqueness(assets, "make") < 1.0)

pk = detect_primary_key(assets, ["asset_id", "make", "site_code"])
check("detect natural PK asset_id", pk["kind"] == "natural" and pk["columns"] == ["asset_id"])

# composite: neither a nor b unique alone, but (a,b) unique
comp = [
    {"a": "x", "b": "1"},
    {"a": "x", "b": "2"},
    {"a": "y", "b": "1"},
]
pkc = detect_primary_key(comp, ["a", "b"])
check("detect composite PK", pkc["kind"] == "composite" and set(pkc["columns"]) == {"a", "b"})

# surrogate: a column that repeats and no unique combo of allowed size
surr = [{"x": "1"}, {"x": "1"}, {"x": "1"}]
pks = detect_primary_key(surr, ["x"])
check("detect surrogate PK", pks["kind"] == "surrogate" and pks["surrogate"] is True)

# ── cell / column format ────────────────────────────────────────────────────
check("cell_format integer", cell_format("42") == "integer")
check("cell_format decimal", cell_format("3.14") == "decimal")
check("cell_format date slash", cell_format("7/17/2009") == "date")
check("cell_format date iso", cell_format("2009-07-17") == "date")
check("cell_format id_code", cell_format("AHU-012") == "id_code")
check("cell_format boolean", cell_format("Yes") == "boolean")
check("column_format date", column_format(work_orders, "date") == "date")

# ── value overlap + referential integrity ───────────────────────────────────
# site_code in assets vs sites — every asset site exists in sites → overlap 1.0
check("overlap assets.site_code -> sites.site_code == 1.0",
      value_overlap(assets, "site_code", sites, "site_code") == 1.0)
check("RI assets.site_code -> sites.site_code == 1.0",
      referential_integrity(assets, "site_code", sites, "site_code") == 1.0)
# wo.asset_id has A9 not in assets → RI = 2/3
ri = referential_integrity(work_orders, "asset_id", assets, "asset_id")
check("RI wo.asset_id -> assets.asset_id == 2/3", abs(ri - (2 / 3)) < 1e-9)

# ── classification PK / FK / Shared ──────────────────────────────────────────
tables = {
    "assets": {"rows": assets, "columns": ["asset_id", "make", "site_code"]},
    "sites": {"rows": sites, "columns": ["site_code", "site_name", "city"]},
}
pk_by_table = {"assets": ["asset_id"], "sites": ["site_code"]}
cls = classify_columns(tables, pk_by_table)
check("sites.site_code classified PK", cls[("sites", "site_code")]["classification"] == "PK")
check("assets.site_code classified FK (RI>=0.95)",
      cls[("assets", "site_code")]["classification"] == "FK"
      and cls[("assets", "site_code")]["references"] == {"table": "sites", "column": "site_code"})
check("assets.asset_id classified PK", cls[("assets", "asset_id")]["classification"] == "PK")

# make: grouped by format with other categorical text but no PK link → shared attribute
make_cls = cls.get(("assets", "make"), {}).get("classification")
check("assets.make is Shared/None (not FK)", make_cls in ("SHARED_ATTRIBUTE", "PK", None) and make_cls != "FK")

# ── metadata builders ────────────────────────────────────────────────────────
tm = build_table_metadata("assets", assets, ["asset_id", "make", "site_code"])
check("table metadata pk + counts", tm["primary_key"] == ["asset_id"] and tm["column_count"] == 3
      and len(tm["samples_by_column"]["make"]) <= 3)
cm = build_column_metadata("assets", "site_code", assets, classification="FK", dest_table="sites")
check("column metadata 4 dims", cm["dest_udr_table"] == "sites" and cm["classification"] == "FK"
      and cm["cell_format"] in ("id_code", "categorical") and len(cm["sample_values"]) <= 5)

print("\nALL TESTS PASSED")
