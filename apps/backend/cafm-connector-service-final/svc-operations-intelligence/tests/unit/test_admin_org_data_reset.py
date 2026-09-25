"""Planning a data reset for one company - which page data goes, and that nobody else's does.

The reset is clear_asset_energy_maintenance.py as an API, narrowed twice: to one
organization's rows, and to the data behind the Compliance, Contracts, Assets, Energy and
Maintenance pages. The load-bearing property is the first test - no delete is ever issued
without a WHERE bound to the organization (or its buildings, read with it). A delete that lost
its filter would empty every tenant's table, which is the script's behaviour and exactly what
an API any company admin can call must never do.

Pure planning over the shipped schema and the foreign keys read from hoistra_test. No database.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from src.engines.admin import org_data_reset as rs
from src.engines.admin import org_export as ox

# The export tests' schema parser, loaded by path: the tests directory is not a package.
_spec = importlib.util.spec_from_file_location(
    "_org_export_tests", Path(__file__).with_name("test_admin_org_export.py"))
_ox_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ox_tests)
SCHEMA, live_columns = _ox_tests.SCHEMA, _ox_tests.live_columns

SCRIPT = SCHEMA.parent / "tools" / "clear_asset_energy_maintenance.py"

#: Foreign keys into the page tables, as hoistra_test declares them (25 Sep 2026):
#: (child, column, parent, parent column, NOT NULL).
LIVE_FKS = [rs.Fk(*f) for f in [
    ("asset_documents", "asset_id", "assets", "id", True),
    ("asset_offline_log", "asset_id", "assets", "id", True),
    ("asset_readings", "asset_id", "assets", "id", True),
    ("asset_warranties", "asset_id", "assets", "id", True),
    ("compliance_certificates", "asset_id", "assets", "id", False),
    ("inspections", "asset_id", "assets", "id", False),
    ("maintenance_history", "asset_id", "assets", "id", True),
    ("maintenance_plans", "asset_id", "assets", "id", True),
    ("scheduled_maintenance_assets", "asset_id", "assets", "id", True),
    ("scheduled_tasks", "asset_id", "assets", "id", False),
    ("work_order_assets", "asset_id", "assets", "id", True),
    ("work_orders", "asset_id", "assets", "id", False),
    ("meter_readings", "meter_id", "energy_meters", "id", True),
    ("scheduled_tasks", "maintenance_plan_id", "maintenance_plans", "id", False),
    ("work_orders", "maintenance_plan_id", "maintenance_plans", "id", False),
    ("work_orders", "sla_id", "sla_policies", "id", False),
    ("inventory_transactions", "part_id", "spare_parts", "id", True),
    ("work_order_parts", "part_id", "spare_parts", "id", True),
    ("receipt_line_items", "part_id", "spare_parts", "id", False),
    ("purchase_order_line_items", "part_id", "spare_parts", "id", False),
    ("work_orders", "assigned_technician", "technicians", "id", False),
    ("purchase_orders", "supplier_id", "vendors", "id", False),
    ("resources", "vendor_id", "vendors", "id", False),
    ("spare_parts", "supplier_id", "vendors", "id", False),
    ("vendor_contacts", "vendor_id", "vendors", "id", True),
    ("vendor_contracts", "vendor_id", "vendors", "id", True),
    ("work_orders", "assigned_vendor", "vendors", "id", False),
    ("work_order_tasks", "work_order_id", "work_orders", "id", True),
    ("misc_costs", "work_order_id", "work_orders", "id", True),
    ("ops_email_log", "queue_item_id", "approvals_queue_items", "id", False),
]]


@pytest.fixture(scope="module")
def cols() -> dict[str, set[str]]:
    assert SCHEMA.exists(), f"schema not found at {SCHEMA}"
    shape = live_columns()
    # Deployed-only tables and columns the reset reaches, in their live shape.
    shape.setdefault("approvals_queue_items", {"id", "organization_id", "item_type"})
    shape.setdefault("approval_action_tokens", {"id", "queue_item_id", "token_hash"})
    shape.setdefault("ops_email_log", {"id", "organization_id", "queue_item_id"})
    shape.setdefault("building_sections", {"section_id", "organization_id", "building_id", "name"})
    shape.setdefault("energy_meters", {"id", "organization_id", "building_id", "meter_ref"})
    shape["energy_meters"] |= {"asset_id", "section_id"}
    shape.setdefault("meter_readings", {"id", "organization_id", "meter_id", "reading_at"})
    shape.setdefault("compliance_certificates", {"id", "organization_id", "building_id"})
    shape["compliance_certificates"] |= {"asset_id", "vendor_id"}
    for f in LIVE_FKS:
        shape.setdefault(f.child, {"id", "organization_id"}).add(f.column)
    return shape


def steps_for(cols, areas=None, fks=LIVE_FKS):
    return rs.plan_reset(cols, areas, fks)


def test_every_delete_binds_the_organization(cols):
    for areas in (None, ["energy"], ["compliance"], ["contracts"], ["maintenance"], ["assets", "maintenance"]):
        plan = steps_for(cols, areas)
        assert plan.steps
        for s in plan.steps:
            # :bids is the company's building list, itself read with :org before any change.
            assert ":org" in s.where or ":bids" in s.where, f"{s.table} unbound for {areas}"
        for d in plan.detaches:
            assert ":org" in d.sql or ":bids" in d.sql, f"{d.table}.{d.column} cleared for everyone"


def test_only_the_chosen_pages_are_touched(cols):
    plan = steps_for(cols, ["energy"])
    tables = {s.table for s in plan.steps}
    assert "meter_readings" in tables and "energy_meters" in tables
    for other in ("assets", "work_orders", "vendors", "compliance_certificates"):
        assert other not in tables
    with pytest.raises(ValueError):
        rs.parse_areas(["energy", "users"])


def test_nothing_outside_the_five_pages_is_deleted(cols):
    plan = steps_for(cols)
    deleted = {s.table for s in plan.steps}
    for kept in rs.KEPT:
        assert kept not in deleted, f"{kept} is deleted but is not page data"
    assert "claude_api_usage" not in deleted


def test_only_the_approvals_an_area_raised_go_with_it(cols):
    for s in steps_for(cols).steps:
        if s.table == "approvals_queue_items":
            assert "item_type" in s.where, "the whole approvals queue would go"


def test_a_row_is_deleted_before_anything_it_points_at(cols):
    """Both halves of a declared foreign key are being deleted: the child must go first, or
    the parent's delete is refused and the whole reset rolls back."""
    plan = steps_for(cols)
    first = {}
    for i, s in enumerate(plan.steps):
        first.setdefault(s.table, i)
    for f in LIVE_FKS:
        if f.child in first and f.parent in first and f.child != f.parent:
            assert first[f.child] < first[f.parent], f"{f.child} after {f.parent}"


def test_a_child_is_deleted_before_the_parent_it_is_scoped_through(cols):
    plan = steps_for(cols)
    position = {s.table: i for i, s in enumerate(plan.steps)}
    for i, s in enumerate(plan.steps):
        if s.scoped_by.startswith("parent: "):
            parent = s.scoped_by.split(": ", 1)[1]
            if parent in position:
                assert i < position[parent], f"{s.table} is scoped through {parent} but deleted after it"


def test_buildings_are_a_fixed_list_read_before_the_first_delete(cols):
    derived = ox._buildings_clause(cols)
    assert derived
    plan = steps_for(cols)
    for s in plan.steps:
        assert derived not in s.where, f"{s.table} still derives buildings mid-reset"
    assert any(":bids" in s.where for s in plan.steps)


def test_a_row_with_no_organization_on_a_company_building_is_cleared(cols):
    by = {s.table: s for s in steps_for(cols).steps}
    assert "IS NULL" in by["building_sections"].where and ":bids" in by["building_sections"].where


def test_clearing_assets_alone_is_blocked_by_maintenance_that_needs_them(cols):
    plan = steps_for(cols, ["assets"])
    blocking = {(b.table, b.column) for b in plan.blocks}
    assert ("maintenance_plans", "asset_id") in blocking
    assert ("work_order_assets", "asset_id") in blocking
    cleared = {(d.table, d.column) for d in plan.detaches}
    assert ("compliance_certificates", "asset_id") in cleared, "a kept certificate should lose its asset"
    assert ("work_orders", "asset_id") in cleared
    assert ("energy_meters", "asset_id") in cleared, "undeclared link, cleared from the hints"


def test_clearing_assets_with_maintenance_leaves_nothing_blocked(cols):
    plan = steps_for(cols, ["assets", "maintenance"])
    assert not [b for b in plan.blocks if b.table in rs.AREAS["maintenance"].tables]
    assert not [d for d in plan.detaches if d.table in rs.AREAS["maintenance"].tables]


def test_clearing_contracts_keeps_the_work_orders_that_named_a_vendor(cols):
    plan = steps_for(cols, ["contracts"])
    cleared = {(d.table, d.column) for d in plan.detaches}
    assert ("work_orders", "assigned_vendor") in cleared
    assert ("spare_parts", "supplier_id") in cleared
    assert not plan.blocks


def test_every_page_table_is_one_the_script_clears():
    """The script and the API are one reset. A page table the script does not know would make
    "reset" mean two things; the script's documents and approvals groups are page-less and
    deliberately not here."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    script: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "PLAN":
            for group in node.value.elts:
                script |= {e.value for e in group.elts[1].elts}
    api = {t for a in rs.AREAS.values() for t in a.tables}
    assert script, "could not read PLAN from the script"
    assert api <= script, f"not in the script: {sorted(api - script)}"
    left = script - api
    assert left == {"document_chunks", "documents", "review_queue", "corrections_log",
                    "ingestion_audit_log", "claude_api_usage", "ingestion_documents",
                    "ops_email_log", "approval_action_tokens", "approvals_queue_items"}, sorted(left)
