"""How invoices reach a building, per deployment. Pure string-building, no DB.

The rollup used to join invoices to contracts on ``i.contract_id``. plenum_cafm.invoices is a
view over invoice_verifications and has no such column, so every request logged
``column i.contract_id does not exist``, the count came back absent, and every building's
invoice count rendered as "?" — correctly, since nobody had counted it.

The column is introspected now, and these are the shapes that occur.
"""
from __future__ import annotations

from src.engines.energy.building_rollup import invoices_count_sql


def _shape(invoice_cols, *, contracts=True, contract_cols=("building_id",)):
    return {
        "invoices": {"exists": True, "key": "invoice_id", "columns": set(invoice_cols)},
        "contracts": {"exists": contracts, "key": "contract_id",
                      "columns": set(contract_cols)},
    }


def test_the_canonical_view_is_counted_directly():
    # What db/01_schema.sql gives: the view resolves the building through the document the
    # invoice was read from, so there is nothing to join.
    sql = invoices_count_sql(_shape({"invoice_id", "building_id", "document_id"}))
    assert "building_id" in sql
    assert "JOIN" not in sql, "no join is needed when the invoice knows its building"
    assert "contract_id" not in sql, "the column that did not exist must not appear"


def test_contract_only_reaches_the_building_through_the_contract():
    sql = invoices_count_sql(_shape({"invoice_id", "contract_id"}))
    assert "JOIN plenum_cafm.contracts" in sql
    assert "c.building_id" in sql


def test_both_columns_prefer_the_direct_link_and_fall_back():
    sql = invoices_count_sql(_shape({"invoice_id", "building_id", "contract_id"}))
    assert "COALESCE(i.building_id::text, c.building_id::text)" in sql
    assert "LEFT JOIN" in sql, "the fallback must not drop invoices with no contract"


def test_neither_column_is_none_not_a_query_that_cannot_run():
    # An invoice that cannot be tied to a building is not zero invoices; it is an uncounted
    # relation. None keeps it absent, which is how this API says "nobody counted this".
    assert invoices_count_sql(_shape({"invoice_id", "vendor_id"})) is None


def test_a_contract_that_cannot_reach_a_building_is_not_a_route_to_one():
    sql = invoices_count_sql(
        _shape({"invoice_id", "contract_id"}, contract_cols=("contract_id",)))
    assert sql is None


def test_no_contracts_table_does_not_stop_a_direct_count():
    # Invoices counting used to sit inside `if contracts exists`, so a deployment without
    # contracts lost its invoice count too, even where the invoice knew its own building.
    sql = invoices_count_sql(_shape({"invoice_id", "building_id"}, contracts=False))
    assert sql is not None
    assert "contracts" not in sql


def test_absent_invoices_table_is_none():
    assert invoices_count_sql({"invoices": {"exists": False, "columns": set()}}) is None
