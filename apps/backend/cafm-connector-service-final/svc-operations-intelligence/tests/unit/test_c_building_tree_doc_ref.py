"""The document reference a certificate branch row carries, so a caller can build a real
download link instead of a label alone. Pure column-selection logic, no DB."""
from __future__ import annotations

from src.engines.energy.building_tree import _BRANCHES, _first


def test_compliance_certificates_declares_a_doc_ref_candidate_list():
    spec = _BRANCHES["compliance_certificates"]
    assert spec["doc_ref"] == ("document_id", "source_document_id")


def test_document_id_wins_when_both_columns_exist():
    cols = {"id", "document_id", "source_document_id", "certificate_type_code"}
    assert _first(cols, _BRANCHES["compliance_certificates"]["doc_ref"]) == "document_id"


def test_source_document_id_is_the_fallback():
    cols = {"id", "source_document_id", "certificate_type_code"}
    assert _first(cols, _BRANCHES["compliance_certificates"]["doc_ref"]) == "source_document_id"


def test_neither_column_present_is_none_not_an_error():
    cols = {"id", "certificate_type_code"}
    assert _first(cols, _BRANCHES["compliance_certificates"]["doc_ref"]) is None


def test_a_branch_with_no_doc_ref_spec_never_selects_one():
    # documents itself has no doc_ref entry — its own row IS the document, so nothing
    # downstream should expect a "documents inside documents" reference.
    assert "doc_ref" not in _BRANCHES["documents"]
