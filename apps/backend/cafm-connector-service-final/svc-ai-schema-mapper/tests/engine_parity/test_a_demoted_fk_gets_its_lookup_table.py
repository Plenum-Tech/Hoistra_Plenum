"""An FK the person demotes to Shared Attribute gets its lookup table — on the write path Hoistra uses.

The gate promises "a FK demoted to Shared Attribute is not enforced and gets a lookup table
instead". The table only ever came from the SQL artifact's B22.1 block, which the schema-aligned
write (every run with a building, and every engine run) never applies — and that block has no
company column, so one company's values would sit in a table every company reads. After the rows
are written, the lookup is built from what was written, for the writing company only.
"""
from __future__ import annotations

import pytest

from src.graph.nodes import write_node as wn

from .conftest import DSN, reset_parity_run

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


async def _sql(q, *args, fetch=False):
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        return await (conn.fetch(q, *args) if fetch else conn.execute(q, *args))
    finally:
        await conn.close()


def _state(**kw):
    state = {
        "organization_id": ORG,
        "table_routing": {"Assets": "assets"},
        "tier1_mappings_by_table": {"Assets": [
            {"source_field": "Maker", "target_field": "manufacturer"}]},
        "column_intelligence": {"classification": [
            {"group_id": "G7", "verdict": "Shared Attribute", "human_override": "fk_to_shared",
             "canonical_name": "maker", "members": ["Assets.Maker"]},
            {"group_id": "G8", "verdict": "Shared Attribute", "canonical_name": "status",
             "members": ["Assets.Status"]},  # detected, not the person's answer: unchanged
        ]},
    }
    state.update(kw)
    return state


@pytest.fixture
async def written(parity_db):
    await reset_parity_run()
    await _sql(f"INSERT INTO plenum_cafm.organizations (id, name) VALUES ('{OTHER}', 'Other Org')")
    await _sql("INSERT INTO plenum_cafm.assets (id, asset_name, asset_code, organization_id, manufacturer) "
               f"SELECT gen_random_uuid(), c, c, o::uuid, m FROM (VALUES ('A1', '{ORG}', 'Daikin'), "
               f"('A2', '{ORG}', 'ABB'), ('A3', '{ORG}', 'Daikin'), ('A4', '{ORG}', NULL), "
               f"('X1', '{OTHER}', 'Otis')) v(c, o, m)")
    yield


async def test_the_lookup_holds_the_companys_values_only(written):
    out = await wn._write_shared_attribute_lookups(_state())
    assert out == [{"table": "maker", "values": 2}]
    rows = await _sql("SELECT organization_id::text AS org, maker FROM plenum_cafm.maker "
                      "ORDER BY maker", fetch=True)
    assert [(r["org"], r["maker"]) for r in rows] == [(ORG, "ABB"), (ORG, "Daikin")]
    assert not await _sql("SELECT 1 FROM pg_class WHERE relname = 'status'", fetch=True)


async def test_a_member_written_as_a_resolved_id_adds_no_ids(written):
    # A member mapped onto a reference column (Contracts.Vendor -> vendor_contracts.vendor_id) is
    # written as the resolved UUID; those are ids, not the attribute's values (seen 29 Oct 2026).
    await _sql(f"UPDATE plenum_cafm.assets SET parent_asset_id = id WHERE organization_id = '{ORG}'")
    state = _state()
    state["tier1_mappings_by_table"]["Assets"].append(
        {"source_field": "Parent", "target_field": "parent_asset_id"})
    state["column_intelligence"]["classification"][0]["members"].append("Assets.Parent")
    assert await wn._write_shared_attribute_lookups(state) == [{"table": "maker", "values": 2}]


async def test_a_second_run_adds_nothing_twice(written):
    await wn._write_shared_attribute_lookups(_state())
    await wn._write_shared_attribute_lookups(_state())
    (r,) = await _sql("SELECT count(*) AS n FROM plenum_cafm.maker", fetch=True)
    assert r["n"] == 2


async def test_a_name_already_taken_moves_to_ref(written):
    await _sql("CREATE TABLE plenum_cafm.maker (code text)")  # someone else's table
    out = await wn._write_shared_attribute_lookups(_state())
    assert out == [{"table": "maker_ref", "values": 2}]
    assert not await _sql("SELECT 1 FROM plenum_cafm.maker", fetch=True)


async def test_no_company_no_lookup(written):
    assert await wn._write_shared_attribute_lookups(_state(organization_id=None)) == []


async def test_a_shared_attribute_promoted_to_a_fk_gets_its_reference_table(written):
    # The promotion builds the group's reference table (column_intelligence, 29 Oct 2026) for the
    # members to point at; on the schema-aligned write that table has to exist too.
    state = _state()
    row = state["column_intelligence"]["classification"][0]
    row.update(verdict="Foreign Key relationship", human_override="shared_to_fk")
    state["column_intelligence"]["shared_attribute_tables"] = [
        {"from_group": "G7", "table_name": "maker", "pk_column": "maker"}]
    assert await wn._write_shared_attribute_lookups(state) == [{"table": "maker", "values": 2}]


async def test_a_promotion_onto_a_primary_key_builds_no_reference_table(written):
    # A member that is some table's primary key is what the others reference: no lookup is kept.
    state = _state()
    state["column_intelligence"]["classification"][0].update(
        verdict="Foreign Key relationship", human_override="shared_to_fk", has_pk="Assets.Maker")
    assert await wn._write_shared_attribute_lookups(state) == []
