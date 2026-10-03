"""The migration that stops the boundary from blanking the pages.

Scoping every read by the caller's company made a row with organization_id NULL invisible:
the energy meters, their readings, the gap records, the risk snapshots and the scoring
weights predate the column meaning anything, so the meters list came back empty and the
energy page went blank. This stamps the only company onto those rows — the same rule
access_control.sql already used for buildings — and these pin the two things that make it
safe to run on every startup.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SQL = (Path(__file__).resolve().parents[2] / "migrations" / "phase3_stamp_single_org.sql").read_text(encoding="utf-8")


def test_it_only_acts_when_there_is_exactly_one_company():
    assert "SELECT count(*) INTO org_count FROM plenum_cafm.organizations" in SQL
    assert "IF org_count <> 1 THEN" in SQL
    assert "RETURN;" in SQL


def test_it_only_ever_fills_nulls():
    updates = [l for l in SQL.splitlines() if "UPDATE plenum_cafm" in l or "SET organization_id" in l]
    assert updates, "expected an UPDATE"
    assert "WHERE organization_id IS NULL" in SQL
    # nothing may overwrite a company that is already set
    assert "organization_id IS NOT NULL" not in SQL


def test_a_table_that_cannot_take_the_stamp_is_skipped_not_fatal():
    """The first version stamped every table in one statement. compliance_risk_snapshots is
    unique on (organization_id, snapshot_date) and two unplaced rows shared a date with a
    placed one, so the UPDATE raised a unique violation — which failed the migration, which
    stopped operations-intelligence from starting at all and took the whole app down behind
    a 502. Each table now runs in its own subtransaction and a table that refuses the stamp
    is left exactly as it was."""
    assert "EXCEPTION" in SQL and "unique_violation" in SQL
    # the handler must not swallow the loop: the UPDATE and its handler sit inside BEGIN/END
    body = SQL[SQL.index("LOOP"):SQL.index("END LOOP")]
    assert "BEGIN" in body and "EXCEPTION" in body and "RAISE NOTICE" in body
    assert "foreign_key_violation" in SQL and "check_violation" in SQL


@pytest.mark.parametrize("table", ["ops_audit_log", "sites"])
def test_the_two_tables_it_must_not_touch_are_excluded(table):
    # ops_audit_log is append-only by trigger; sites.organization_id is an INTEGER against a
    # UUID-keyed table and stamping it would make a broken link look sound.
    assert f"'{table}'" in SQL


def test_it_only_considers_uuid_columns_on_real_tables():
    assert "c.data_type = 'uuid'" in SQL
    assert "tb.table_type = 'BASE TABLE'" in SQL


def test_the_table_name_reaches_sql_as_an_identifier_not_a_string():
    # format(%I) quotes it as an identifier; the company id goes through USING, never
    # interpolated — the same rule as every other dynamic statement in this repo.
    assert "format(" in SQL and "%I" in SQL
    assert "USING only_org" in SQL
