"""Reading document ids out of what an ingest reported. Pure, no database.

The binding is only as good as this: a document created but reported under a key nobody reads
goes unbound, lands with building_id null, appears in no drawer, and the upload still says it
worked. Which is the failure this whole change exists to remove.
"""
from __future__ import annotations

from src.services.building_binding import document_ids_from

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


def test_the_plain_case():
    assert document_ids_from([{"output": {"document_id": A}}]) == [A]


def test_every_spelling_extractors_use():
    # Different extractors report the same thing under different names. Reading only one
    # would silently drop whatever the others produced.
    for key in ("document_id", "documentId", "doc_id", "id"):
        assert document_ids_from([{"output": {key: A}}]) == [A], key


def test_an_id_nested_under_upsert_is_found():
    # The compliance extractor reports its write under `upsert`, with the id of the document
    # it was read from inside rather than beside it.
    calls = [{"output": {"status": "ok", "upsert": {"document_id": B}}}]
    assert document_ids_from(calls) == [B]


def test_ids_are_deduplicated_and_keep_their_order():
    calls = [
        {"output": {"document_id": B}},
        {"output": {"document_id": A}},
        {"output": {"upsert": {"document_id": B}}},
    ]
    assert document_ids_from(calls) == [B, A]


def test_things_that_are_not_uuids_are_not_document_ids():
    # Tool outputs are full of ids — a migration id, a batch id, a queue key. Binding on a
    # non-uuid would either error or, worse, match nothing and look like success.
    calls = [{"output": {"id": "migration-42"}}, {"output": {"document_id": "not-a-uuid"}},
             {"output": {"id": 7}}, {"output": {"document_id": ""}}]
    assert document_ids_from(calls) == []


def test_nothing_reported_is_an_empty_list_not_an_error():
    for bad in (None, [], [{}], [{"output": None}], ["not a dict"], [{"output": "text"}]):
        assert document_ids_from(bad) == []


def test_a_structured_migration_reports_no_document():
    # CSV and Excel go through the migration flow and produce no document row. Binding must
    # find nothing here rather than latching onto the migration id.
    calls = [{"tool": "run_migration",
              "output": {"migration_id": "8ac3f0e2-1", "status": "awaiting_gate"}}]
    assert document_ids_from(calls) == []
