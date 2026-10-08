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
    # energy_building_id_rename.sql: the energy tables' site_id always held a building id and
    # is building_id on the deployed databases.
    for t in ("energy_meters", "energy_anomalies", "eui_snapshots", "building_energy_profiles"):
        if "site_id" in shape.get(t, set()):
            shape[t] = (shape[t] - {"site_id"}) | {"building_id"}
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


def _approval_types(cols, area):
    step = next(s for s in steps_for(cols, [area]).steps if s.table == "approvals_queue_items")
    return step.where


def test_a_certificate_reset_takes_the_scan_items_about_those_certificates(cols):
    # The compliance scan raises these about a certificate. They survived a Compliance reset
    # and sat pending over an empty register (28 Sep 2026).
    where = _approval_types(cols, "compliance")
    for t in ("alert", "booking_request", "adversary_gate"):
        assert f"item_type = '{t}'" in where, t


def test_a_vendor_reset_takes_the_blocks_raised_on_those_vendors(cols):
    # block_ack / block_lift / passport_share name a vendor; Contracts is where vendors go.
    where = _approval_types(cols, "contracts")
    for t in ("block_ack", "block_lift", "passport_share"):
        assert f"item_type = '{t}'" in where, t


def test_a_maintenance_reset_keeps_the_renewal_bookings_of_the_certificates_it_keeps(cols):
    # Review, 28 Sep 2026: booking_request sat in Maintenance as well as Compliance, so a
    # Maintenance-only reset deleted the renewal bookings — with their emails and tokens —
    # of certificates it left in the register. A booking names a certificate.
    assert "booking_request" not in _approval_types(cols, "maintenance")


def test_no_approval_item_type_goes_with_two_areas():
    # Each area's deletes run on their own, so a type listed twice goes with whichever area
    # is cleared first, whatever the other area keeps.
    seen: dict[str, str] = {}
    for area, spec in rs.AREAS.items():
        for pattern in spec.approvals:
            assert pattern not in seen, f"{pattern!r} is in both {seen[pattern]} and {area}"
            seen[pattern] = area


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


# ── one building ────────────────────────────────────────────────────────────────────────
# The same reset narrowed a third time, to a single building the admin picks. Bound twice:
# every delete keeps its company filter and adds the building, so a building id from another
# company matches nothing even if it got past the route.

import re  # noqa: E402

_BUILDING = re.compile(r":building\b")


def building_plan(cols, areas=None, fks=LIVE_FKS):
    return rs.plan_reset(cols, areas, fks, building=True)


def test_a_building_reset_binds_every_delete_to_the_building_and_the_company(cols):
    for areas in (None, ["energy"], ["compliance"], ["contracts"], ["maintenance"], ["assets", "maintenance"]):
        plan = building_plan(cols, areas)
        # Nothing on the Contracts page names a building in this schema: all company-wide.
        assert plan.steps or areas == ["contracts"], areas
        for s in plan.steps:
            assert _BUILDING.search(s.where), f"{s.table} not narrowed to the building for {areas}"
            assert ":org" in s.where or ":bids" in s.where, f"{s.table} lost its company for {areas}"
        for d in plan.detaches:
            assert _BUILDING.search(d.sql), f"{d.table}.{d.column} cleared beyond the building"


def test_a_company_reset_never_names_a_single_building(cols):
    plan = steps_for(cols)
    assert not [s.table for s in plan.steps if _BUILDING.search(s.where)]
    assert not plan.company_wide


def test_a_building_reset_keeps_what_belongs_to_the_whole_company(cols):
    plan = building_plan(cols)
    deleted = {s.table for s in plan.steps}
    for shared in ("vendors", "vendor_contacts", "sla_policies", "spare_parts", "technicians",
                   "compliance_scan_runs", "vendor_monthly_scorecards"):
        if shared in cols:
            assert shared not in deleted, f"{shared} serves every building but is deleted"
            assert plan.company_wide.get(shared), f"{shared} kept without saying so"
    for per_building in ("assets", "work_orders", "energy_meters", "meter_readings",
                         "compliance_certificates", "asset_readings", "work_order_tasks"):
        assert per_building in deleted, f"{per_building} is per-building but not cleared"


def _where(plan, table):
    return next(s.where for s in plan.steps if s.table == table)


def test_a_row_reaches_its_building_through_what_it_hangs_off(cols):
    plan = building_plan(cols)
    assert "plenum_cafm.energy_meters" in _where(plan, "meter_readings")
    assert "plenum_cafm.work_orders" in _where(plan, "work_order_tasks")
    assert "plenum_cafm.assets" in _where(plan, "asset_readings")
    assert "plenum_cafm.maintenance_plans" in _where(plan, "scheduled_maintenance_parts")


def test_a_row_with_its_own_building_is_not_claimed_through_a_link(cols):
    # A work order filed on building B for an asset that sits in building A is B's. The link
    # only decides for a row that names no building of its own.
    where = _where(building_plan(cols, ["maintenance"]), "work_orders")
    assert "building_id::text = :building" in where
    assert "building_id IS NULL AND" in where
    assert where.index("building_id::text = :building") < where.index("plenum_cafm.assets")


def test_a_building_reset_deletes_children_first(cols):
    plan = building_plan(cols)
    first = {}
    for i, s in enumerate(plan.steps):
        first.setdefault(s.table, i)
    for f in LIVE_FKS:
        if f.child in first and f.parent in first and f.child != f.parent:
            assert first[f.child] < first[f.parent], f"{f.child} after {f.parent}"
    last = {s.table: i for i, s in enumerate(plan.steps)}
    for i, s in enumerate(plan.steps):
        if s.scoped_by.startswith("parent: "):
            parent = s.scoped_by.split(": ", 1)[1]
            if parent in last:
                assert i < last[parent], f"{s.table} is matched through {parent} but deleted after it"


def test_a_building_reset_takes_only_the_approvals_about_rows_it_deletes(cols):
    plan = building_plan(cols, ["compliance"])
    items = [i for i, s in enumerate(plan.steps) if s.table == "approvals_queue_items"]
    assert items, "the certificates' approval items would outlive them"
    where = plan.steps[items[0]].where
    assert "related_entity_id" in where and "plenum_cafm.compliance_certificates" in where
    assert "item_type" in where
    # Read while the certificates are still there to be matched against.
    cert = next(i for i, s in enumerate(plan.steps) if s.table == "compliance_certificates")
    assert items[0] < cert


def test_kept_rows_in_another_building_block_or_lose_their_link(cols):
    # Assets and Maintenance both cleared for building A: a work order on building B naming
    # an asset in A is kept. Its nullable link is cleared; a NOT NULL one blocks the reset.
    plan = building_plan(cols, ["assets", "maintenance"])
    cleared = {(d.table, d.column): d for d in plan.detaches}
    assert ("work_orders", "asset_id") in cleared
    assert "IS NOT TRUE" in cleared[("work_orders", "asset_id")].sql
    blocking = {(b.table, b.column): b for b in plan.blocks}
    # A work order's asset links go with the work order, so B's link to A's asset is kept.
    assert ("work_order_assets", "asset_id") in blocking
    assert "IS NOT TRUE" in blocking[("work_order_assets", "asset_id")].count_sql


class _Rows:
    def __init__(self, rows=(), scalar=0, rowcount=0):
        self._rows, self._scalar, self.rowcount = list(rows), scalar, rowcount

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class FakeSession:
    """Answers the schema reads from the test shape and records every statement. Counts are
    zero; no statement reaches a database."""

    def __init__(self, shape, bids):
        self.shape, self.bids, self.sql, self.committed = shape, bids, [], False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append((sql, dict(params or {})))
        if "information_schema.columns" in sql:
            return _Rows([(t, c, "YES") for t, cs in self.shape.items() for c in cs])
        if "pg_constraint" in sql:
            return _Rows([tuple(f) for f in LIVE_FKS])
        if "org_buildings" in sql:
            return _Rows([(b,) for b in self.bids])
        return _Rows()

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


ORG = "11111111-1111-5111-8111-111111111111"
HARBOUR, ASHGROVE = "c343c566-0000-4000-8000-000000000001", "4a451a94-af44-486f-b660-8e19518cd19f"


async def test_a_building_the_company_does_not_have_is_refused_before_anything_changes(cols):
    s = FakeSession(cols, [HARBOUR])
    with pytest.raises(rs.ResetForeignBuilding):
        await rs.run_reset(s, ORG, areas=["assets"], apply=True, building_id=ASHGROVE)
    assert not [q for q, _ in s.sql if q.lstrip().startswith(("DELETE", "UPDATE"))]
    assert not s.committed


async def test_a_building_dry_run_counts_that_building_only(cols):
    s = FakeSession(cols, [HARBOUR, ASHGROVE])
    out = await rs.run_reset(s, ORG, areas=["energy", "maintenance"], apply=False, building_id=HARBOUR)
    assert out["building_id"] == HARBOUR and out["buildings"] == 1
    counts = [(q, p) for q, p in s.sql if q.startswith("SELECT count(*) FROM plenum_cafm.")]
    assert counts and all(p.get("building") == HARBOUR and p.get("org") == ORG for _, p in counts)
    assert not s.committed
    # What stays because it serves every building is said, page by page.
    assert {"table": "spare_parts", "area": "maintenance", "label": "Maintenance"} in out["company_wide"]


async def test_a_company_dry_run_is_unchanged_by_the_building_option(cols):
    s = FakeSession(cols, [HARBOUR, ASHGROVE])
    out = await rs.run_reset(s, ORG, areas=["contracts"], apply=False)
    assert out["building_id"] is None and out["buildings"] == 2 and out["company_wide"] == []


# ── review, 6 Oct 2026 ──────────────────────────────────────────────────────────────────

def test_what_serves_every_building_stays_kept_even_with_a_building_column(cols):
    # A column can appear at run time (the migration engine and the table editor add them).
    # One building_id must not turn vendors or technicians into one building's rows — and
    # with them, clear every other building's work orders' links to them.
    shape = {t: set(c) for t, c in cols.items()}
    for t in rs.COMPANY_WIDE:
        if t in shape:
            shape[t].add("building_id")
    plan = building_plan(shape)
    deleted = {s.table for s in plan.steps}
    for t in rs.COMPANY_WIDE:
        if t in shape and any(t in a.tables for a in rs.AREAS.values()):
            assert t not in deleted, f"{t} became one building's"
            assert t in plan.company_wide
    detached = {(d.table, d.column) for d in plan.detaches}
    assert ("work_orders", "assigned_technician") not in detached
    assert ("work_orders", "assigned_vendor") not in detached


def test_rows_elsewhere_are_only_ever_the_companys_own(cols):
    # Another company's row naming one of ours is refused by its foreign key, as a company
    # reset is — never quietly unlinked and described as "on another building".
    company = {s.table: s.where for s in steps_for(cols, ["assets", "maintenance"]).steps}
    plan = building_plan(cols, ["assets", "maintenance"])
    where = {s.table: s.where for s in plan.steps}
    d = next(d for d in plan.detaches if (d.table, d.column) == ("work_orders", "asset_id"))
    assert d.sql.endswith(f" AND ({company['work_orders']}) AND ({where['work_orders']}) IS NOT TRUE")
    for b in plan.blocks:
        if b.elsewhere:
            assert f"AND ({company[b.table]}) AND (" in b.count_sql, b.table


def test_a_row_never_loses_the_only_link_that_makes_it_the_companys(cols):
    # scheduled_tasks belongs to a company only through its plan. Kept on another building,
    # clearing that link would leave it nobody's: unreachable by any later reset or export.
    plan = building_plan(cols, ["assets", "maintenance"])
    detached = {(d.table, d.column) for d in plan.detaches}
    blocking = {(b.table, b.column) for b in plan.blocks}
    rule = ox.plan_export(cols)[0]["scheduled_tasks"]
    assert rule.kind == "parent" and rule.column == "maintenance_plan_id"
    assert ("scheduled_tasks", "maintenance_plan_id") not in detached
    assert ("scheduled_tasks", "maintenance_plan_id") in blocking


def test_approval_items_are_matched_on_every_page_being_cleared_but_never_on_readings(cols):
    plan = building_plan(cols, ["assets", "maintenance", "energy"])
    items = {s.area: s.where for s in plan.steps if s.table == "approvals_queue_items"}
    # A work-order item about an asset goes when both pages are cleared, as a company reset does.
    assert "plenum_cafm.assets" in items["maintenance"]
    for w in items.values():
        assert "plenum_cafm.meter_readings" not in w and "plenum_cafm.asset_readings" not in w


async def test_an_empty_building_id_is_refused_not_read_as_every_building(cols):
    s = FakeSession(cols, [HARBOUR])
    for bad in ("", "not-a-uuid"):
        with pytest.raises(rs.ResetForeignBuilding):
            await rs.run_reset(s, ORG, areas=["assets"], apply=True, building_id=bad)
    assert not [q for q, _ in s.sql if q.lstrip().startswith(("DELETE", "UPDATE"))]
