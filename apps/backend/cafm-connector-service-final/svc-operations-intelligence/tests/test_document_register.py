"""The document register: types, the personal rule, masking - the rules that hold without a database.

Checked read-only on hoistra_test (2 Oct 2026): Bishopsgate lists 152 documents with how each reached
it; the indexed passport scan is in no register row, so register-scoped search cannot return it."""
from __future__ import annotations

from src.engines import document_register as reg


def test_identity_papers_are_personal_by_type_or_by_flag():
    assert reg.is_personal("visa", None) and reg.is_personal("emirates_id", "standard")
    assert reg.is_personal("staff_list", "personal") and not reg.is_personal("staff_list", None)
    assert not reg.is_personal("vendor_invoice", None) and reg.type_key("vendor_invoice") == "invoice"
    assert {t["key"] for t in reg.doc_types() if t["personal"]} == {"visa", "emirates_id", "passport", "labour_card"}


def test_an_identity_number_shows_only_its_last_four():
    out = reg.mask_numbers("Emirates ID 784-1987-1234567-1, passport N1234567, valid until 2027")
    assert "1234567" not in out and "****4567" in out and "2027" in out


def test_documents_link_to_records_the_register_knows():
    assert set(reg.LINKABLE) >= {"building", "asset", "vendor"}
