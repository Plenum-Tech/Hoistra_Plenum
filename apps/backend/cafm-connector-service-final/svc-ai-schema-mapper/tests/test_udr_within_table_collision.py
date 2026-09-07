"""Within-table canonical collision guard — when applying the canonical override
would create a duplicate (table, column) key in the same source table, the
override is suppressed for the loser only.

Two collision shapes:
  (a) The canonical happens to match another column ALREADY in that table.
  (b) Two members of the SAME table land on the same canonical because they
      were merged into the same multi-member group (e.g. works.id +
      works.tagnum both resolving to 'asset_id').

Generic-PK promotion (Sites.id → 'site_id') eliminates many of the case-(a)
collisions on its own; the guard now mostly handles case (b).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared-lib"))

from udr.column_intelligence import build_column_intelligence  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# Tables crafted so the canonical-name picker chooses 'id' for the shared
# group containing Sites.asset_id + Vendors.id + WorkOrders.asset_id + works.id.
tables = {
    "Sites": {
        "rows": [{"id": f"S-{n:02d}", "asset_id": f"A-{n:03d}"} for n in [1, 2, 3]],
        "columns": ["id", "asset_id"], "pk": ["id"],
    },
    "Vendors": {
        "rows": [{"id": f"A-{n:03d}", "trade": t} for n, t in zip([1, 2, 3], ["Lift", "Mech", "Fire"])],
        "columns": ["id", "trade"], "pk": ["id"],
    },
    "WorkOrders": {
        "rows": [{"id": f"W-{n:04d}", "asset_id": f"A-{n:03d}"} for n in [1, 2, 3]],
        "columns": ["id", "asset_id"], "pk": ["id"],
    },
    "works": {
        "rows": [{"id": f"A-{n:03d}", "tagnum": f"A-{n:03d}", "property_ref": f"S-{n:02d}"} for n in [1, 2, 3]],
        "columns": ["id", "tagnum", "property_ref"], "pk": ["id"],
    },
}

rep = build_column_intelligence(tables)

# ── 1. No table ends up with duplicate canonical_prefixed_key rows ──
keys_per_table: dict[str, list[str]] = {}
for p in rep["prefixing"]:
    keys_per_table.setdefault(p["source_table"], []).append(p["canonical_prefixed_key"])
for t, keys in keys_per_table.items():
    dups = {k for k in keys if keys.count(k) > 1}
    check(f"{t}: no duplicate canonical_prefixed_key", not dups)

# ── 2. The asset_id group has two members in 'works' table (works.id PK +
#       works.tagnum). Both want canonical 'asset_id' (Tier 3 non-generic
#       consensus); the PK wins, the other is suppressed.
by_key = {f"{p['source_table']}.{p['column']}": p for p in rep["prefixing"]}
check("works.id wins the asset_id canonical (PK wins the collision)",
      by_key["works.id"]["canonical_name"] == "asset_id" and not by_key["works.id"]["canonical_suppressed"])
check("works.tagnum loses, keeps source name (suppressed)",
      by_key["works.tagnum"]["canonical_name"] == "tagnum" and by_key["works.tagnum"]["canonical_suppressed"] is True)

# ── 3. Generic-PK promotion gives each SINGLE-table-PK group a qualified
#       canonical. Vendors.id and Sites.asset_id sit in a multi-PK group
#       (Vendors and works both have a PK there because their values
#       coincidentally overlap with the asset_ids), so they fall through to
#       Tier 3 and land on 'asset_id'.
check("Sites.id canonical promoted to 'site_id' (single-PK group)",
      by_key["Sites.id"]["canonical_name"] == "site_id")
check("WorkOrders.id canonical promoted to 'work_order_id' (singleton)",
      by_key["WorkOrders.id"]["canonical_name"] == "work_order_id")
check("Vendors.id falls through to Tier 3 -> 'asset_id' (multi-PK group)",
      by_key["Vendors.id"]["canonical_name"] == "asset_id")

# ── 4. Suppressed columns surface under their ORIGINAL header ──
check("Suppressed works.tagnum surfaces at works.tagnum, not works.asset_id",
      by_key["works.tagnum"]["canonical_prefixed_key"] == "works.tagnum")

# ── 5. canonical_suppressed mirrors in B14.1 metadata too ──
meta_by_key = {f"{m['source_table']}.{m['column']}": m for m in rep["metadata"]}
check("metadata row carries canonical_suppressed=True for works.tagnum",
      meta_by_key["works.tagnum"].get("canonical_suppressed") is True)

print("\nALL TESTS PASSED")
