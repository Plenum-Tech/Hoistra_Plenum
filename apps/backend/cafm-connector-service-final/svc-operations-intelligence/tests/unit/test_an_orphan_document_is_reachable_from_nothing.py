"""Step 9 of repair_links may only remove a document row nothing can reach.

The rule is narrow on purpose. A row in plenum_cafm.documents is what a certificate, a
contract or an invoice reaches a building through, so removing one that is still pointed at
would break the link it exists to make. It qualifies only when no file was ever uploaded
under its id — the download route reads ingestion_documents and nothing else, so such a row
answers 404 forever — and when nothing references it, including the graph_document_id a
certificate keeps in raw_metadata, which no column check would find.

The columns come from information_schema, so a database missing one narrows the rule. The
failure that must never happen is the opposite: a missing table silently dropping its own
NOT EXISTS and taking rows that table still points at.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "db" / "tools" / "repair_links.py"
_spec = importlib.util.spec_from_file_location("repair_links", _PATH)
repair_links = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair_links)


class _Conn:
    """information_schema.columns, as this database would answer it."""

    def __init__(self, columns):
        self._columns = columns

    async def fetch(self, _sql):
        return [{"table_name": t, "column_name": c} for t, c in self._columns]


_FULL = [
    ("documents", "document_id"),
    ("ingestion_documents", "id"),
    ("compliance_certificates", "document_id"),
    ("compliance_certificates", "source_document_id"),
    ("compliance_certificates", "raw_metadata"),
    ("compliance_vector_membership_audit", "document_id"),
    ("contract_documents", "document_id"),
    ("contract_sla_parameters", "document_id"),
    ("invoice_verifications", "document_id"),
    ("ppm_visits", "source_document_id"),
]


def _step(columns):
    return asyncio.run(repair_links.orphan_document_step(_Conn(columns)))


class TestEveryReferenceThisDatabaseHasIsChecked:
    @pytest.mark.parametrize("table,column", [
        ("compliance_certificates", "document_id"),
        ("compliance_certificates", "source_document_id"),
        ("compliance_vector_membership_audit", "document_id"),
        ("contract_documents", "document_id"),
        ("contract_sla_parameters", "document_id"),
        ("invoice_verifications", "document_id"),
        ("ppm_visits", "source_document_id"),
    ])
    def test_the_column_appears_in_both_halves(self, table, column):
        _, count_sql, apply_sql = _step(_FULL)
        for sql in (count_sql, apply_sql):
            assert f"plenum_cafm.{table} r WHERE r.{column} = d.document_id" in sql

    def test_the_graph_document_id_inside_raw_metadata_is_checked(self):
        _, count_sql, apply_sql = _step(_FULL)
        for sql in (count_sql, apply_sql):
            assert "raw_metadata->>'graph_document_id'" in sql

    def test_a_row_with_an_uploaded_file_is_never_taken(self):
        _, count_sql, apply_sql = _step(_FULL)
        for sql in (count_sql, apply_sql):
            assert "plenum_cafm.ingestion_documents i WHERE i.id = d.document_id" in sql

    def test_the_two_halves_ask_the_same_question(self):
        _, count_sql, apply_sql = _step(_FULL)
        assert count_sql.split("WHERE ", 1)[1] == apply_sql.split("WHERE ", 1)[1], (
            "the dry run must count exactly the rows --apply would remove"
        )


class TestADatabaseMissingATableIsHandled:
    def test_a_missing_table_drops_only_its_own_check(self):
        columns = [c for c in _FULL if c[0] != "ppm_visits"]
        _, count_sql, _ = _step(columns)
        assert "ppm_visits" not in count_sql
        assert "invoice_verifications" in count_sql

    def test_no_documents_table_means_no_step_at_all(self):
        assert _step([("ingestion_documents", "id")]) is None


class TestItIsNotAWildcard:
    def test_the_count_is_not_an_unqualified_select(self):
        _, count_sql, apply_sql = _step(_FULL)
        # Every clause is a NOT EXISTS; one per reference plus the upload check plus
        # raw_metadata. A formulation that lost them would still parse and would take
        # every row in the table.
        assert count_sql.count("NOT EXISTS") == 9
        assert apply_sql.count("NOT EXISTS") == 9
