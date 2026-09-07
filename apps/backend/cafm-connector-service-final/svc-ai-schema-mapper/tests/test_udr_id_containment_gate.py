"""B19.1 grouping fix — id_code/integer/decimal/date columns now require value-set
containment in addition to format + shape, so unrelated PKs from different tables
no longer collide into one canonical group.

Earlier behaviour:
  - WorkOrders.id (W-####) and Resources.id (R-##) merged into one 'id' group
    just because both profile as id_code with the A-#### shape skeleton.
  - Sites.id (PK) merged with Resources.engineer_id (E-###) for the same reason.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared-lib"))

from udr.column_intelligence import build_column_intelligence  # noqa: E402
from udr.primitives import _containment_overlap, group_similar_columns  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


tables = {
    "Sites": {
        "rows": [{"id": f"S-{n:02d}", "asset_id": f"A-{n:03d}"} for n in [1, 2, 3]],
        "columns": ["id", "asset_id"], "pk": ["id"],
    },
    "Vendors": {
        "rows": [{"id": f"V-{n:02d}"} for n in [1, 2, 3]],
        "columns": ["id"], "pk": ["id"],
    },
    "WorkOrders": {
        "rows": [{"id": f"W-{n:04d}", "asset_id": f"A-{n:03d}"} for n in [1, 2, 3]],
        "columns": ["id", "asset_id"], "pk": ["id"],
    },
    "Resources": {
        "rows": [{"id": f"R-{n:02d}", "engineer_id": f"E-{n:03d}", "site_ref": f"S-{n:02d}"} for n in [1, 2, 3]],
        "columns": ["id", "engineer_id", "site_ref"], "pk": ["id"],
    },
    "works": {
        "rows": [{"id": f"K-{n:04d}", "tagnum": f"A-{n:03d}", "property_ref": f"S-{n:02d}"} for n in [1, 2, 3]],
        "columns": ["id", "tagnum", "property_ref"], "pk": ["id"],
    },
}

# ── 1. _containment_overlap helper ──
rs, rr = tables["Sites"]["rows"], tables["Resources"]["rows"]
rw, rv = tables["WorkOrders"]["rows"], tables["Vendors"]["rows"]

check("Sites.id ⊆ Resources.site_ref  (containment >= 0.5)",
      _containment_overlap(rs, "id", rr, "site_ref") >= 0.5)
check("WorkOrders.id ∩ Resources.id = ∅ (containment ~ 0)",
      _containment_overlap(rw, "id", rr, "id") == 0.0)
check("Sites.id ∩ Resources.engineer_id = ∅",
      _containment_overlap(rs, "id", rr, "engineer_id") == 0.0)
check("WorkOrders.asset_id ⊆ Sites.asset_id (FK→PK style)",
      _containment_overlap(rw, "asset_id", rs, "asset_id") >= 0.5)

# ── 2. group_similar_columns drops the wrong merges ──
multi = {tuple(sorted(g)) for g in group_similar_columns(tables) if len(g) > 1}


def in_group_with(member, other):
    for g in multi:
        if member in g and other in g:
            return True
    return False


# These were wrongly grouped before the fix.
check("WorkOrders.id NOT grouped with Resources.id (different PKs)",
      not in_group_with(("WorkOrders", "id"), ("Resources", "id")))
check("Sites.id NOT grouped with Resources.engineer_id",
      not in_group_with(("Sites", "id"), ("Resources", "engineer_id")))
check("Vendors.id NOT grouped with Sites.asset_id (V-## ≠ A-###)",
      not in_group_with(("Vendors", "id"), ("Sites", "asset_id")))
check("works.id NOT grouped with Sites.id (K-#### ≠ S-##)",
      not in_group_with(("works", "id"), ("Sites", "id")))

# These should still group (the legitimate FK→PK / shared-attribute cases).
check("WorkOrders.asset_id ↔ Sites.asset_id grouped (same A-### values)",
      in_group_with(("WorkOrders", "asset_id"), ("Sites", "asset_id")))
check("works.tagnum ↔ Sites.asset_id grouped (same A-### values)",
      in_group_with(("works", "tagnum"), ("Sites", "asset_id")))
check("Resources.site_ref ↔ Sites.id grouped (S-## FK→PK)",
      in_group_with(("Resources", "site_ref"), ("Sites", "id")))
check("works.property_ref ↔ Sites.id grouped (S-## FK→PK)",
      in_group_with(("works", "property_ref"), ("Sites", "id")))

# ── 3. canonical names are now sane ──
rep = build_column_intelligence(tables)
by_key = {f"{m['source_table']}.{m['column']}": m["canonical_name"] for m in rep["metadata"]}

check("Sites.id canonical is 'site_id' (generic PK qualified)",
      by_key["Sites.id"] == "site_id")
check("WorkOrders.id canonical is 'work_order_id' (singleton, qualified)",
      by_key["WorkOrders.id"] == "work_order_id")
check("Resources.id canonical is 'resource_id' (singleton, qualified)",
      by_key["Resources.id"] == "resource_id")
check("WorkOrders.asset_id canonical is 'asset_id'", by_key["WorkOrders.asset_id"] == "asset_id")
check("Resources.engineer_id canonical is its own (singleton)",
      by_key["Resources.engineer_id"] == "engineer_id")

print("\nALL TESTS PASSED")
