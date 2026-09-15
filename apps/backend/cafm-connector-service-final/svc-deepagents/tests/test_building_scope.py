"""The caller's buildings reach every direct read the agents make.

query_table, the svc-udr record reads, the compliance agent's asset queries and the
orchestrator's two direct SQL reads all used to answer for the whole database, whoever
asked. These pin the predicate each of them now carries — derived from the table's real
columns, never from the question — and the post-hoc check applied to rows another service
returned unfiltered.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.services import principal as P

MINE, THEIRS = uuid4(), uuid4()


def make(building_ids):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=None,
                       role="user", can_ingest=False, building_ids=building_ids)


@pytest.fixture(autouse=True)
def _reset():
    P._columns_cache.clear()
    token = P.caller_principal.set(None)
    yield
    P.caller_principal.reset(token)


class FakeSession:
    """Answers the information_schema question with the columns given, and records every
    other statement so a test can read what would have run."""
    def __init__(self, columns, keep=()):
        self.columns, self.keep, self.statements = columns, keep, []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append((sql, params or {}))
        if "information_schema.columns" in sql:
            return [(c,) for c in self.columns.get(params["t"], ())]
        return [(k,) for k in self.keep]


# ── building_clause / certificate_clause ──────────────────────────────────────

def test_no_caller_means_no_clause_so_background_jobs_keep_working():
    assert P.building_clause("building_id") == ("", {})
    assert P.certificate_clause() == ("", {})


def test_an_admin_gets_no_clause():
    P.caller_principal.set(make(None))
    assert P.building_clause("building_id") == ("", {})


def test_a_user_allocated_to_nothing_matches_no_row():
    P.caller_principal.set(make(()))
    assert P.building_clause("building_id") == (" AND FALSE", {})


def test_a_user_is_narrowed_to_their_buildings():
    P.caller_principal.set(make((MINE,)))
    sql, params = P.building_clause("w.building_id")
    assert sql == " AND w.building_id = ANY(CAST(:scope_buildings AS uuid[]))"
    assert params == {"scope_buildings": [str(MINE)]}


def test_certificates_keep_vendor_accreditations_which_name_no_building():
    P.caller_principal.set(make((MINE,)))
    sql, params = P.certificate_clause("c")
    assert "c.building_id = ANY(CAST(:scope_buildings AS uuid[]))" in sql
    assert "c.building_id IS NULL AND lower(c.cert_scope) = 'vendor'" in sql
    P.caller_principal.set(make(()))
    sql, params = P.certificate_clause("c")
    assert "ANY(" not in sql and "cert_scope) = 'vendor'" in sql and params == {}


# ── table_building_clause: the column decides ─────────────────────────────────

@pytest.mark.asyncio
async def test_a_building_keyed_table_filters_on_its_building_column():
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({"work_orders": ["id", "building_id", "status"]})
    sql, params = await P.table_building_clause(s, "work_orders")
    assert sql == " AND building_id = ANY(CAST(:scope_buildings AS uuid[]))"
    assert params["scope_buildings"] == [str(MINE)]


@pytest.mark.asyncio
async def test_a_vendor_keyed_table_filters_through_the_vendors_on_those_buildings():
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({"contract_sla_parameters": ["id", "vendor_id", "status"]})
    sql, _ = await P.table_building_clause(s, "contract_sla_parameters")
    assert sql.startswith(" AND vendor_id::text IN (SELECT vendor_id::text FROM (")
    for t in ("compliance_certificates", "work_orders", "invoices"):
        assert f"plenum_cafm.{t}" in sql


@pytest.mark.asyncio
async def test_the_vendors_table_itself_is_narrowed_the_same_way():
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({"vendors": ["id", "vendor_name"]})
    sql, _ = await P.table_building_clause(s, "vendors", alias="v")
    assert sql.startswith(" AND v.id::text IN (SELECT vendor_id::text FROM (")


@pytest.mark.asyncio
async def test_energy_tables_key_the_building_as_building_id():
    """The energy tables called that column site_id until Sep 2026 and it never held a site
    id. After the rename they take the same first branch as every other building-keyed table."""
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({"energy_meters": ["id", "building_id"], "meter_readings": ["id", "meter_id"]})
    sql, _ = await P.table_building_clause(s, "energy_meters")
    assert sql == " AND building_id = ANY(CAST(:scope_buildings AS uuid[]))"
    sql, _ = await P.table_building_clause(s, "meter_readings")
    assert "meter_id IN (SELECT m.id FROM plenum_cafm.energy_meters m WHERE m.building_id" in sql


@pytest.mark.asyncio
async def test_an_unmigrated_database_still_scopes_energy_on_the_old_name():
    """A database that has not run the rename yet must still be narrowed, not read whole."""
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({"energy_anomalies": ["id", "site_id"]})
    sql, _ = await P.table_building_clause(s, "energy_anomalies")
    assert sql == " AND site_id = ANY(CAST(:scope_buildings AS uuid[]))"


@pytest.mark.asyncio
async def test_asset_keyed_tables_go_through_assets():
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({"asset_criticality": ["id", "asset_id"]})
    sql, _ = await P.table_building_clause(s, "asset_criticality")
    assert "asset_id::text IN (SELECT a.id::text FROM plenum_cafm.assets a WHERE a.building_id" in sql


@pytest.mark.asyncio
async def test_a_reference_table_with_nothing_to_narrow_on_is_read_whole():
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({"certificate_types": ["id", "code", "name"]})
    assert await P.table_building_clause(s, "certificate_types") == ("", {})


@pytest.mark.asyncio
async def test_an_unknown_or_unsafe_table_yields_no_row():
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({})
    assert await P.table_building_clause(s, "no_such_table") == (" AND FALSE", {})
    assert await P.table_building_clause(s, "users; drop table x") == (" AND FALSE", {})
    # The unsafe name never reached the catalogue query.
    assert all(p.get("t") != "users; drop table x" for _, p in s.statements)


@pytest.mark.asyncio
async def test_an_admin_never_pays_for_the_catalogue_lookup():
    P.caller_principal.set(make(None))
    s = FakeSession({"work_orders": ["building_id"]})
    assert await P.table_building_clause(s, "work_orders") == ("", {})
    assert s.statements == []


# ── restrict_records: rows another service returned unfiltered ────────────────

@pytest.mark.asyncio
async def test_rows_are_rechecked_against_the_database_by_id():
    P.caller_principal.set(make((MINE,)))
    a, b = str(uuid4()), str(uuid4())
    s = FakeSession({"work_orders": ["id", "building_id"]}, keep=[a])
    kept, note = await P.restrict_records(s, "work_orders", [{"id": a}, {"id": b}])
    assert [r["id"] for r in kept] == [a] and note is None
    sql, params = s.statements[-1]
    assert "WHERE id::text = ANY(:ids) AND building_id = ANY(CAST(:scope_buildings AS uuid[]))" in sql
    assert sorted(params["ids"]) == sorted([a, b])


@pytest.mark.asyncio
async def test_nothing_allocated_means_nothing_kept_and_says_so():
    P.caller_principal.set(make(()))
    s = FakeSession({"work_orders": ["id", "building_id"]})
    kept, note = await P.restrict_records(s, "work_orders", [{"id": "x"}])
    assert kept == [] and "not allocated" in note


@pytest.mark.asyncio
async def test_an_admin_keeps_every_row_without_a_query():
    P.caller_principal.set(make(None))
    s = FakeSession({})
    rows = [{"id": 1}, {"id": 2}]
    assert await P.restrict_records(s, "work_orders", rows) == (rows, None)
    assert s.statements == []


@pytest.mark.asyncio
async def test_rows_without_an_id_fall_back_to_their_building_field_or_are_refused():
    P.caller_principal.set(make((MINE,)))
    s = FakeSession({"work_orders": ["building_id", "status"]})
    kept, note = await P.restrict_records(
        s, "work_orders", [{"building_id": str(MINE)}, {"building_id": str(THEIRS)}])
    assert kept == [{"building_id": str(MINE)}] and note is None
    kept, note = await P.restrict_records(s, "work_orders", [{"status": "open"}])
    assert kept == [] and "cannot be checked" in note


# ── the orchestrator's WHERE composition ──────────────────────────────────────

def test_a_clause_becomes_a_where_when_the_spec_asked_for_nothing():
    P.caller_principal.set(make((MINE,)))
    bsql, _ = P.certificate_clause("c")
    where_sql = ""
    where_sql = (where_sql + bsql) if where_sql else "WHERE " + bsql[len(" AND "):]
    assert where_sql.startswith("WHERE (c.building_id = ANY(")
    where_sql = "WHERE (lower(c.status::text) = lower(:p0))"
    assert (where_sql + bsql).count("WHERE") == 1
