"""Fiix → CAFM table-name derivation: an object whose target already exists must MATCH the
existing CAFM table (incl. proper plural forms) instead of defaulting to 'create new'.
Run: python tests/test_schema_table_naming.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared-lib"))

from graph.nodes.schema_pre_semantic_node import (  # noqa: E402
    schema_candidate_table_names,
    schema_pluralize,
    schema_snake,
    schema_suggest_table_name,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── snake + acronym collapse ──
check("AssetEvent -> asset_event", schema_snake("AssetEvent") == "asset_event")
check("RFQ -> rfq (acronym collapsed)", schema_snake("RFQ") == "rfq")
check("MoveStatus -> move_status", schema_snake("MoveStatus") == "move_status")

# ── proper pluralization (fixes countrys / prioritys / move_status) ──
check("country -> countries", schema_pluralize("country") == "countries")
check("priority -> priorities", schema_pluralize("priority") == "priorities")
check("currency -> currencies", schema_pluralize("currency") == "currencies")
check("move_status -> move_statuses", schema_pluralize("move_status") == "move_statuses")
check("account -> accounts", schema_pluralize("account") == "accounts")
check("asset_event -> asset_events", schema_pluralize("asset_event") == "asset_events")
check("rfq -> rfqs", schema_pluralize("rfq") == "rfqs")

# ── the existing-table auto-match: candidate list must contain the real CAFM table ──
# (subset of the live plenum_cafm tables from the screenshot/audit)
EXISTING = {
    "accounts", "asset_events", "asset_event_types", "asset_users", "asset_warranties",
    "maintenance_types", "meter_reading_units", "countries", "currencies", "priorities",
    "move_statuses", "rfqs", "work_order_statuses", "purchase_order_statuses",
}


def matches_existing(obj):
    return next((c for c in schema_candidate_table_names(obj) if c in EXISTING), None)


for obj, expected in [
    ("Account", "accounts"),
    ("AssetEvent", "asset_events"),
    ("AssetEventType", "asset_event_types"),
    ("AssetUser", "asset_users"),
    ("MaintenanceType", "maintenance_types"),
    ("MeterReadingUnit", "meter_reading_units"),
    ("Country", "countries"),
    ("Currency", "currencies"),
    ("Priority", "priorities"),
    ("MoveStatus", "move_statuses"),
    ("RFQ", "rfqs"),
    ("WorkOrderStatus", "work_order_statuses"),
]:
    check(f"{obj} auto-matches existing '{expected}'", matches_existing(obj) == expected)

# ── a genuinely new object (no existing table) gets a well-formed suggested name ──
check("new object -> proper plural name", schema_suggest_table_name("SpecialGadget") == "special_gadgets")
check("truly-new object does NOT match existing", matches_existing("SpecialGadget") is None)

print("\nALL TESTS PASSED")
