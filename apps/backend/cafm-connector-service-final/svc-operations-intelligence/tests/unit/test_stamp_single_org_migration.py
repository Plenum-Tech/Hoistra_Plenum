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
    assert "count(*) FROM plenum_cafm.organizations) <> 1" in SQL
    assert "RETURN;" in SQL


def test_it_only_ever_fills_nulls():
    updates = [l for l in SQL.splitlines() if "UPDATE plenum_cafm" in l or "SET organization_id" in l]
    assert updates, "expected an UPDATE"
    assert "WHERE organization_id IS NULL" in SQL
    # nothing may overwrite a company that is already set
    assert "organization_id IS NOT NULL" not in SQL


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
