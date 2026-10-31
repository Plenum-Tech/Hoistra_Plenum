"""Planning a full export of one company's data — which tables go in, and which never do.

The export answers "give me everything we hold for this org". The danger is in the word
everything: this schema has 145 tables, and three kinds of them must never reach a customer's
zip file.

  - `auth_sessions`, `auth_otp_codes`, `approval_action_tokens` hold live credentials.
  - `regulation_packs`, `prompt_templates`, the udr_* ontology are the platform's, not theirs.
  - Anything with no path to an organization would export EVERY tenant's rows if included.

Only 74 tables carry an org column (and 13 of those call it `org_id`, not `organization_id`).
The rest reach an organization through a building or a parent row, or they do not reach one
at all — and a table that cannot be scoped is excluded by name, never exported hopefully.

The load-bearing test here is the last one: every table in the shipped schema must be either
scoped or explicitly excluded. A table added later that nobody classified fails that test
rather than silently leaking or silently going missing.

Pure planning over an introspected column map. No database.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.engines.admin import org_export as ox

SCHEMA = (Path(__file__).resolve().parents[6] / "db" / "01_schema.sql")


def live_columns() -> dict[str, set[str]]:
    """The real shipped schema, parsed — so these tests fail when the schema moves."""
    src = SCHEMA.read_text()
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"^CREATE TABLE plenum_cafm\.(\w+) \(\n(.*?)^\);", src, re.M | re.S):
        out[m.group(1)] = {
            re.match(r"\s+(\w+)", line).group(1)
            for line in m.group(2).splitlines()
            if re.match(r"\s+\w+\s", line)
        }
    return out


@pytest.fixture(scope="module")
def cols() -> dict[str, set[str]]:
    assert SCHEMA.exists(), f"schema not found at {SCHEMA}"
    return live_columns()


@pytest.fixture(scope="module")
def plan(cols):
    return ox.plan_export(cols)


# ── nothing dangerous is ever in the file ────────────────────────────────────

def test_credential_tables_are_excluded_by_name(plan):
    """A customer admin clicks Export. Session tokens and one-time codes must not be in
    what lands in their downloads folder."""
    included, excluded = plan
    for table in ("auth_sessions", "auth_otp_codes", "auth_role_changes",
                  "approval_action_tokens"):
        assert table not in included, f"{table} would export credentials"
        assert table in excluded and "credential" in excluded[table].lower()


def test_platform_reference_data_is_not_sold_back_to_the_customer(plan):
    """Regulation packs and the UDR ontology are the platform's work, not this company's
    records. Shipping them in an export labelled 'your data' misrepresents both."""
    included, excluded = plan
    for table in ("regulation_packs", "prompt_templates", "udr_canonical_table",
                  "udr_ontology_chunk", "compliance_verification_sources"):
        assert table not in included, f"{table} is platform data, not the org's"
        assert table in excluded


def test_no_included_table_is_ever_unfiltered(plan):
    """The whole risk in one assertion: an included table with no WHERE clause exports
    every tenant in the database."""
    included, _ = plan
    for table, rule in included.items():
        assert rule.where.strip(), f"{table} would be exported unfiltered"
        assert ":org" in rule.where, f"{table}'s filter does not bind the org"


# ── the org's own records do get in ──────────────────────────────────────────

def test_the_tables_behind_every_screen_are_included(plan):
    """What the user actually asked for. Each of these is a screen in the product; an
    export missing one is not 'everything'."""
    included, excluded = plan
    for table in ("assets", "work_orders", "vendors", "buildings", "documents",
                  "compliance_certificates", "spare_parts", "meters", "spaces", "floors"):
        assert table in included, f"{table} missing — excluded as: {excluded.get(table)}"


def test_org_id_is_honoured_as_well_as_organization_id(plan):
    """13 tables spell it org_id. Reading only organization_id drops them into the
    unscopable pile and quietly loses the company's saved spaces and activity log."""
    included, _ = plan
    for table in ("saved_space", "activity_log_entry", "duty_holders", "manufacturers"):
        assert table in included
        assert included[table].kind == "direct"


def test_the_organizations_row_is_the_companys_own_and_filtered_by_id(plan):
    """organizations has no organization_id — it has id. Scoped on the wrong column this
    either exports every company or nothing."""
    included, _ = plan
    rule = included["organizations"]
    assert rule.kind == "root"
    assert re.search(r"\bid\b", rule.where) and ":org" in rule.where


# ── the two indirect paths ───────────────────────────────────────────────────

def test_buildings_are_reached_backwards_because_sites_cannot_be_joined(plan):
    """buildings has no org column. The path that should work — buildings.site_id ->
    sites.organization_id — is broken: sites.organization_id is an INTEGER while every
    other org id in this schema is a uuid. So a building is this org's when an org-scoped
    row points at it, which is derivable today and does not need a schema change."""
    included, _ = plan
    rule = included["buildings"]
    assert rule.kind == "building"
    assert "sites" not in rule.where, "must not join sites — the integer/uuid break"
    assert "organization_id" in rule.where, "reached through org-scoped rows"


def test_a_child_whose_parent_is_itself_indirect_still_resolves(plan):
    """receipts -> purchase_orders is one hop; receipt_line_items -> receipts is a second,
    and rca_grouping_actions -> rca_groupings -> work_orders is a third. Resolving in a
    single pass leaves these unscoped, so the planner iterates to a fixed point."""
    included, excluded = plan
    for table in ("receipts", "receipt_line_items", "rca_groupings",
                  "rca_grouping_actions", "ingestion_documents"):
        assert table in included, f"{table} unresolved — {excluded.get(table)}"
        assert included[table].kind == "parent"


def test_a_parent_scoped_table_names_a_parent_that_is_itself_included(plan):
    """A filter that selects from an unscoped parent is an unfiltered export wearing a
    subquery."""
    included, _ = plan
    for table, rule in included.items():
        if rule.kind == "parent":
            assert rule.parent in included, f"{table} hangs off unscoped {rule.parent}"


def test_work_order_children_all_ride_on_the_work_order(plan):
    included, _ = plan
    for table in ("work_order_tasks", "work_order_comments", "work_order_parts",
                  "work_order_history", "work_order_attachments", "work_order_users"):
        assert included[table].parent == "work_orders"


# ── the guard that keeps this honest as the schema grows ─────────────────────

def test_every_table_in_the_schema_is_either_scoped_or_excluded_by_name(cols, plan):
    """The one that matters. A table added to the schema and classified by nobody must
    fail here — the alternatives are exporting it unfiltered (a tenancy breach) or
    dropping it silently (an export that lies about being complete)."""
    included, excluded = plan
    unclassified = set(cols) - set(included) - set(excluded)
    assert not unclassified, (
        "these tables are neither scoped nor excluded — classify them in org_export.py: "
        + ", ".join(sorted(unclassified))
    )


def test_tables_that_exist_only_on_the_deployed_database_are_mapped():
    """db/01_schema.sql is 145 tables; the deployed database is 225. The extra 80 are real
    customer records — work-order approvals, stock history, asset moves, document chunks —
    and a planner built only against the checked-in file drops every one of them out of an
    export that claims to be complete. They are mapped from the live information_schema, so
    this test pins the mapping using the production shape rather than the file."""
    prod = {
        # building_id here is load-bearing and true of production: the org's buildings are
        # derived from org-scoped rows that point at them, so if NO directly-scoped table
        # carried a building_id there would be nothing to derive from and every
        # building-tier table would drop out. assets is that table.
        "assets": {"id", "organization_id", "building_id"},
        "work_orders": {"id", "organization_id"},
        "work_order_tasks": {"id", "work_order_id"},
        "purchase_orders": {"id", "organization_id"},
        "spare_parts": {"id", "organization_id"},
        "rfqs": {"id", "organization_id"},
        "moves": {"id", "organization_id"},
        "report_cards": {"id", "organization_id"},
        "users": {"id", "organization_id"},
        "documents": {"document_id", "building_id"},
        "ingestion_documents": {"id"},
        # The deployed-only tables under test.
        "wo_approval_requests": {"id", "work_order_id"},
        "wo_status_history": {"id", "work_order_id"},
        "wo_journey_logs": {"id", "work_order_id"},
        "stock_history": {"id", "part_id"},
        "rfq_line_items": {"id", "rfq_id"},
        "move_assets": {"id", "move_id", "asset_id"},
        "report_card_runs": {"id", "card_id"},
        "work_order_task_files": {"id", "work_order_task_id"},
        "site_users": {"id", "user_id"},
        # Production's document_chunks has no org_id — 05_docrag_compat.sql recreated it
        # with ingestion_id only. Scoped through the ingested file it came from.
        "document_chunks": {"id", "ingestion_id"},
    }
    included, excluded = ox.plan_export(prod)
    for table in prod:
        assert table in included, f"{table} would be dropped from the export: {excluded.get(table)}"
    assert included["document_chunks"].parent == "ingestion_documents"
    assert included["stock_history"].parent == "spare_parts"
    assert included["work_order_task_files"].parent == "work_order_tasks"


def test_migration_run_state_is_excluded_parents_included():
    """The mapping decisions a migration made (`migration_field_mappings`,
    `migration_hierarchy`) were already excluded as run state. The job rows they hang off
    carry organization_id, so without being named they resolved as `direct` and the export
    shipped exactly what their children were excluded for.

    It is also the single most expensive table in the export: 829 rows of LangGraph
    node state and gate payloads, 31MB, and 110 of the export's 222 seconds — all of it
    before the first row arrived."""
    shape = {
        "migration_jobs": {"id", "organization_id", "node_state_json"},
        "schema_mapping_jobs": {"id", "organization_id", "node_state_json"},
        "migration_field_mappings": {"id", "migration_id"},
        "migration_hierarchy": {"id", "migration_id"},
    }
    included, excluded = ox.plan_export(shape)
    assert included == {}, f"migration run state would be exported: {list(included)}"
    for table in shape:
        assert "working state" in excluded[table]


def test_integration_connector_config_is_never_exported():
    """`connectors` holds the configuration for reaching a customer's OTHER systems. Even
    where no column is literally named `secret`, connection config is not something to put
    in a file that leaves the platform. These tables exist only on the deployed database,
    so the check is made against a shape that has them."""
    shape = {t: {"id", "connector_id"} for t in
             ("connectors", "field_maps", "import_jobs", "uploaded_files", "import_errors")}
    included, excluded = ox.plan_export(shape)
    for table in shape:
        assert table not in included, f"{table} would be exported"
        assert table in excluded and len(excluded[table]) > 20


def test_every_exclusion_gives_a_reason_a_person_can_read(plan):
    """The reason is shown in the manifest. 'excluded: true' tells the reader nothing about
    whether their data is missing or was never theirs."""
    _, excluded = plan
    for table, reason in excluded.items():
        assert len(reason) > 20, f"{table}'s exclusion reason is not an explanation: {reason}"


def test_the_plan_covers_the_whole_schema_and_is_mostly_included(cols, plan):
    included, excluded = plan
    assert len(included) + len(excluded) == len(cols)
    assert len(included) > 100, f"only {len(included)} of {len(cols)} tables would export"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
