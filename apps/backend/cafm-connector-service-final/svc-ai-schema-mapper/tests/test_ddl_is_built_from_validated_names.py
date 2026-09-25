"""Nothing reaches CREATE TABLE or ALTER TABLE without being checked first.

_build_ddl_statements interpolated target_table, custom_column_name and data_type straight into
DDL. That was survivable only because every one of those values was machine-generated from a
source column name — until the field-mapping gate learned to accept a column name typed by a
reviewer, which turns a text box into a route to arbitrary DDL. A data_type of

    TEXT; DROP TABLE plenum_cafm.assets; --

is an ordinary Python string and landed in the statement unaltered.

CLAUDE.md states the rule this file was missing: no raw table or column names in SQL, validated
against a regex and an allow-list. Identifiers are normalised as write_node._to_safe_identifier
does it; types must be one of a fixed set, optionally with a length or precision. Anything else
is dropped from the DDL and logged, never repaired into something the reviewer did not ask for.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

# The service's logging package is not installed in the test environment; the sibling writer
# tests stub it the same way so the node can be imported without its whole service chain.
if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src.graph.nodes.schema_write_node import (  # noqa: E402
    _build_ddl_statements, _safe_data_type, _safe_identifier,
)


def _cfg(**kw):
    base = {"storage_strategy": "custom", "target_table": "assets",
            "custom_column_name": "vendor_ref", "data_type": "VARCHAR(100)",
            "is_new_table": False, "nullable": True}
    base.update(kw)
    return base


def _sql(entries):
    return "\n".join(d["sql"] for d in _build_ddl_statements(entries, [], {"assets"}))


class TestAnIdentifier:
    @pytest.mark.parametrize("raw,expect", [
        ("asset_ref", "asset_ref"),
        ("Asset Ref", "asset_ref"),      # a spreadsheet header is an ordinary thing to type
        ("vendor-code", "vendor_code"),
        ("  Legacy ID  ", "legacy_id"),
    ])
    def test_is_normalised_the_way_the_writer_normalises_it(self, raw, expect):
        assert _safe_identifier(raw) == expect

    @pytest.mark.parametrize("raw", [None, "", "   ", "1col", "a" * 70, "!!!"])
    def test_is_refused_when_it_is_not_one(self, raw):
        assert _safe_identifier(raw) is None

    def test_cannot_carry_sql(self):
        out = _safe_identifier('x"; DROP TABLE plenum_cafm.assets; --')
        assert out is not None, "it normalises rather than failing, so check what survives"
        assert not set(out) & set(' ;"\'()-.'), out


class TestADataType:
    @pytest.mark.parametrize("raw,expect", [
        ("VARCHAR(255)", "VARCHAR(255)"),
        ("numeric(12, 2)", "NUMERIC(12,2)"),
        ("timestamp with time zone", "TIMESTAMP WITH TIME ZONE"),
        ("jsonb", "JSONB"),
        ("uuid", "UUID"),
    ])
    def test_is_re_emitted_from_the_parts_that_were_checked(self, raw, expect):
        assert _safe_data_type(raw) == expect

    def test_missing_means_the_default(self):
        assert _safe_data_type(None) == "TEXT"
        assert _safe_data_type("") == "TEXT"

    @pytest.mark.parametrize("raw", [
        "TEXT; DROP TABLE plenum_cafm.assets; --",
        "TEXT DEFAULT (SELECT 1)",
        "nonsense",
        "VARCHAR(255) NOT NULL",
        "int[]",
    ])
    def test_anything_that_is_not_a_type_is_refused_outright(self, raw):
        assert _safe_data_type(raw) is None


class TestWhatReachesTheStatement:
    def test_an_ordinary_column_still_builds(self):
        sql = _sql([_cfg()])
        assert "ALTER TABLE plenum_cafm.assets ADD COLUMN IF NOT EXISTS vendor_ref VARCHAR(100)" in sql

    def test_a_typed_header_arrives_normalised(self):
        assert "vendor_asset_ref" in _sql([_cfg(custom_column_name="Vendor Asset Ref")])

    def test_a_column_with_an_unusable_name_is_dropped_not_emitted(self):
        assert _build_ddl_statements([_cfg(custom_column_name="1bad")], [], {"assets"}) == []

    def test_a_column_with_a_bogus_type_is_dropped_not_emitted(self):
        assert _build_ddl_statements(
            [_cfg(data_type="TEXT; DROP TABLE plenum_cafm.assets; --")], [], {"assets"}) == []

    def test_no_statement_can_be_ended_early(self):
        sql = _sql([
            _cfg(custom_column_name='a"; DROP TABLE plenum_cafm.assets; --'),
            _cfg(custom_column_name="ok_col", data_type="TEXT"),
        ])
        assert "DROP TABLE" not in sql.upper()
        assert sql.count(";") == len([l for l in sql.splitlines() if l.strip()]), \
            "one terminator per statement and no extras"

    def test_a_new_table_is_checked_the_same_way(self):
        good = _sql([_cfg(is_new_table=True, target_table="Vendor Extras",
                          custom_column_name="Ref Code", data_type="text")])
        assert "CREATE TABLE IF NOT EXISTS plenum_cafm.vendor_extras" in good
        assert "ref_code TEXT" in good
        assert _build_ddl_statements(
            [_cfg(is_new_table=True, target_table="9bad")], [], set()) == []

    def test_a_good_column_survives_a_bad_one_beside_it(self):
        sql = _sql([_cfg(custom_column_name="!!!"), _cfg(custom_column_name="keeper")])
        assert "keeper" in sql


class TestANewTableThatIsNotNew:
    """"New table" is a claim, and a reviewer typing a name can be wrong about it.

    CREATE TABLE IF NOT EXISTS on a table that already exists succeeds and does nothing, so the
    column never arrives and the decision is lost silently. existing_canonical_tables has always
    been passed to the builder and was never consulted; it decides this now.
    """

    def test_a_table_already_on_the_schema_is_altered_not_recreated(self):
        ddl = _build_ddl_statements(
            [_cfg(is_new_table=True, target_table="assets", custom_column_name="vendor_ref")],
            [], {"assets"})
        sql = "\n".join(d["sql"] for d in ddl)
        assert "CREATE TABLE" not in sql.upper()
        assert "ALTER TABLE plenum_cafm.assets ADD COLUMN IF NOT EXISTS vendor_ref" in sql

    def test_the_check_uses_the_normalised_name(self):
        """"Assets" and "assets" are one table; the claim must not survive a capital letter."""
        ddl = _build_ddl_statements(
            [_cfg(is_new_table=True, target_table="Assets", custom_column_name="vendor_ref")],
            [], {"assets"})
        sql = "\n".join(d["sql"] for d in ddl)
        assert "CREATE TABLE" not in sql.upper()

    def test_a_genuinely_new_table_is_still_created(self):
        sql = "\n".join(d["sql"] for d in _build_ddl_statements(
            [_cfg(is_new_table=True, target_table="vendor_extras",
                  custom_column_name="ref_code", data_type="TEXT")], [], {"assets"}))
        assert "CREATE TABLE IF NOT EXISTS plenum_cafm.vendor_extras" in sql
        assert "ref_code TEXT" in sql
        assert "id UUID PRIMARY KEY DEFAULT gen_random_uuid()" in sql
