"""Stage-5 gate fix — free_text / categorical columns no longer over-group, and
heterogeneous groups never adopt a misleading member name as the canonical.

Earlier behaviour (regression we're guarding against):
  - All free_text columns (site_name, postcode, vendor_name, phone,
    fault_description, …) collapsed into one B19.1 group with canonical='phone'.
  - All categorical columns (city, trade, status, …) collapsed with
    canonical='trade'.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared-lib"))

from udr.column_intelligence import build_column_intelligence  # noqa: E402
from udr.primitives import _jaccard_value_overlap, group_similar_columns  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── synthetic corpus mirroring the user's screenshot ──
tables = {
    "Sites": {
        "rows": [
            {"id": "S-01", "site_name": "Bishopsgate Tower", "city": "London", "postcode": "EC2M 4NR", "asset_id": "A-001"},
            {"id": "S-02", "site_name": "Riverside Campus", "city": "Manchester", "postcode": "M1 2WD", "asset_id": "A-002"},
            {"id": "S-03", "site_name": "Dock Logistics Park", "city": "Liverpool", "postcode": "L3 4BR", "asset_id": "A-003"},
        ],
        "columns": ["id", "site_name", "city", "postcode", "asset_id"],
        "pk": ["id"],
    },
    "Vendors": {
        "rows": [
            {"id": "V-01", "vendor_name": "Acme Lifts", "phone": "+44 207 555 0100", "trade": "Lift"},
            {"id": "V-02", "vendor_name": "BetaMech", "phone": "+44 207 555 0200", "trade": "Mechanical"},
            {"id": "V-03", "vendor_name": "FireGuard", "phone": "+44 207 555 0300", "trade": "Fire"},
        ],
        "columns": ["id", "vendor_name", "phone", "trade"],
        "pk": ["id"],
    },
    "WorkOrders": {
        "rows": [
            {"id": "W-1001", "asset_id": "A-001", "fault_description": "Pump leaking", "status": "Open"},
            {"id": "W-1002", "asset_id": "A-002", "fault_description": "AHU noisy", "status": "Closed"},
            {"id": "W-1003", "asset_id": "A-001", "fault_description": "Filter blocked", "status": "Open"},
        ],
        "columns": ["id", "asset_id", "fault_description", "status"],
        "pk": ["id"],
    },
    "Resources": {
        "rows": [
            {"id": "R-01", "full_name": "Alice", "engineer_id": "E-001", "site_ref": "S-01", "trade": "Lift"},
            {"id": "R-02", "full_name": "Bob", "engineer_id": "E-002", "site_ref": "S-02", "trade": "Mechanical"},
            {"id": "R-03", "full_name": "Cara", "engineer_id": "E-003", "site_ref": "S-03", "trade": "Fire"},
        ],
        "columns": ["id", "full_name", "engineer_id", "site_ref", "trade"],
        "pk": ["id"],
    },
}

# ── 1. Jaccard helper ──
rs = tables["Vendors"]["rows"]
rr = tables["Resources"]["rows"]
check("trade columns share full value set", _jaccard_value_overlap(rs, "trade", rr, "trade") == 1.0)

city_rows = tables["Sites"]["rows"]
check("city ↔ trade no value overlap",     _jaccard_value_overlap(city_rows, "city", rr, "trade") == 0.0)

# ── 2. group_similar_columns no longer merges heterogeneous free-text ──
groups = group_similar_columns(tables)
multi = {tuple(sorted(g)) for g in groups if len(g) > 1}

def member_set(canonical_target_table_col):
    """Return the multi-member group containing a given (table, col), or None."""
    for g in multi:
        if canonical_target_table_col in g:
            return set(g)
    return None

# site_name was grouped with phone + fault_description + product_name + ... — must STOP.
site_name_group = member_set(("Sites", "site_name"))
check("Sites.site_name is now a singleton (no free-text merge)", site_name_group is None)

# postcode same story.
postcode_group = member_set(("Sites", "postcode"))
check("Sites.postcode is now a singleton",                       postcode_group is None)

# city should NOT merge with trade (no value overlap).
city_group = member_set(("Sites", "city"))
check("Sites.city is now a singleton (city ≠ trade)",            city_group is None)

# But Vendors.trade ↔ Resources.trade SHOULD still group — values genuinely overlap.
trade_group = member_set(("Vendors", "trade"))
check("Vendors.trade ↔ Resources.trade still grouped (Jaccard=1)",
      trade_group == {("Vendors", "trade"), ("Resources", "trade")})

# Asset-id-shaped columns still grouped across tables.
asset_id_group = member_set(("WorkOrders", "asset_id"))
check("WorkOrders.asset_id ↔ Sites.asset_id ↔ Resources.engineer_id grouped",
      asset_id_group is not None and ("Sites", "asset_id") in asset_id_group)

# ── 3. canonical_name picks no longer mislead ──
rep = build_column_intelligence(tables)
by_key = {f"{m['source_table']}.{m['column']}": m["canonical_name"] for m in rep["metadata"]}

check("Sites.site_name canonical is 'site_name' (not 'phone')", by_key["Sites.site_name"] == "site_name")
check("Sites.city canonical is 'city' (not 'trade')",            by_key["Sites.city"] == "city")
check("Sites.postcode canonical is 'postcode' (not 'phone')",    by_key["Sites.postcode"] == "postcode")
check("Vendors.phone canonical is 'phone' (singleton, kept)",    by_key["Vendors.phone"] == "phone")
check("Vendors.trade canonical is 'trade' (consensus)",          by_key["Vendors.trade"] == "trade")
check("Resources.trade canonical is 'trade' (consensus)",        by_key["Resources.trade"] == "trade")

# ── 4. PK groups qualify generic 'id' with the singular table name ──
check("Sites.id canonical is 'site_id' (generic PK promoted)",   by_key["Sites.id"] == "site_id")
check("Vendors.id canonical is 'vendor_id' (generic PK promoted)", by_key["Vendors.id"] == "vendor_id")

print("\nALL TESTS PASSED")
