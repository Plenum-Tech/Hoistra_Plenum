"""One file, uploaded four times, filed as four documents.

Riverside Court's drawer listed Invoice-HAL-B006-0042 four times and Town Hall the same
certificate eight times. The invoice itself de-duplicated correctly — one verification, one
row in the invoices view — because an invoice number identifies it. A document had no such
key: plenum_cafm.documents is upserted on document_id, which doc-rag mints fresh per upload,
so re-uploading a file always made a second document for the same bytes.

file_hash_sha256 had been on ingestion_documents the whole time, NULL on all 45 rows,
because nothing computed it. The uploader has the bytes, so it computes it there.

These cover the hashing and the rules around the collapse. The grouping SQL itself is
exercised against a real database.
"""
from __future__ import annotations

import asyncio
import hashlib
import re

import pytest

from src.services import building_binding as bb

SOURCE = open(bb.__file__, encoding="utf-8").read()


def test_a_files_hash_is_its_content(tmp_path):
    f = tmp_path / "invoice.pdf"
    f.write_bytes(b"%PDF-1.7 Invoice no. HAL-B006-0042")
    assert bb.file_sha256(str(f)) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_the_same_bytes_under_two_names_hash_the_same(tmp_path):
    # The whole point: "{sessionA}_x.pdf" and "{sessionB}_x.pdf" are one file.
    a, b = tmp_path / "s1_x.pdf", tmp_path / "s2_x.pdf"
    a.write_bytes(b"same"); b.write_bytes(b"same")
    assert bb.file_sha256(str(a)) == bb.file_sha256(str(b))


def test_different_bytes_under_one_name_do_not(tmp_path):
    # And the reason the hash is preferred over the filename: two different invoices can be
    # named identically, and collapsing those would lose one.
    a, b = tmp_path / "a" / "invoice.pdf", tmp_path / "b" / "invoice.pdf"
    for p, data in ((a, b"one"), (b, b"two")):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    assert bb.file_sha256(str(a)) != bb.file_sha256(str(b))


def test_an_unreadable_file_costs_the_hash_not_the_upload(tmp_path):
    # A hash is what lets two uploads be recognised as one file. Failing to compute it
    # should cost that recognition and nothing else.
    assert bb.file_sha256(str(tmp_path / "does-not-exist.pdf")) is None


def test_a_large_file_is_read_in_blocks():
    # Not read whole into memory: an ingest can carry a 100MB export and the uploader runs
    # inside the request.
    assert "1024 * 1024" in SOURCE and "iter(lambda" in SOURCE


def test_nothing_to_hash_opens_no_session():
    # AsyncSessionLocal is not stubbed; reaching Postgres here would fail the test.
    assert asyncio.run(bb.stamp_upload_hashes("s1", None)) == 0
    assert asyncio.run(bb.stamp_upload_hashes("s1", [])) == 0


def test_hashes_are_stamped_by_basename(tmp_path, monkeypatch):
    # doc-rag records original_filename verbatim as "{session}_{name}". Stamping the full
    # server path would match no row at all.
    seen: list[dict] = []

    class R:
        rowcount = 1

    class S:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def execute(self, _s, params): seen.append(params); return R()
        async def commit(self): pass

    f = tmp_path / "abc_invoice.pdf"
    f.write_bytes(b"bytes")
    monkeypatch.setattr(bb.database, "AsyncSessionLocal", lambda: S())
    assert asyncio.run(bb.stamp_upload_hashes("abc", [str(f)])) == 1
    assert seen[0]["n"] == "abc_invoice.pdf"
    assert seen[0]["h"] == hashlib.sha256(b"bytes").hexdigest()


def test_an_existing_hash_is_never_overwritten():
    # It was computed from the same bytes: rewriting it can only replace it with itself or
    # with a mistake.
    stamp = SOURCE[SOURCE.index("async def stamp_upload_hashes"):]
    stamp = stamp[: stamp.index("async def _referencing_columns")]
    assert "file_hash_sha256 IS NULL" in stamp


def test_the_survivor_is_the_earliest_row():
    for sql in (bb._GROUP_BY_CONTENT, bb._GROUP_BY_NAME):
        assert "ORDER BY d.uploaded_at" in sql
    collapse = SOURCE[SOURCE.index("async def collapse_duplicate_documents"):]
    assert "ids[0]" in collapse and "ids[1:]" in collapse


def test_the_survivors_gaps_are_filled_before_the_others_go():
    # Keeping the earliest row while deleting a later one that carried the blob_url would
    # trade a duplicate for a document that has lost its file.
    collapse = SOURCE[SOURCE.index("async def collapse_duplicate_documents"):]
    fill = collapse.index("UPDATE plenum_cafm.documents k")
    delete = collapse.index("DELETE FROM plenum_cafm.documents")
    assert fill < delete
    for column in ("blob_url", "doc_type", "title", "file_name"):
        assert f"COALESCE(k.{column}" in collapse


def test_soft_references_are_repointed_before_the_delete():
    # Nothing declares a foreign key to documents, so a delete orphans every referrer in
    # silence unless they are re-pointed by name first.
    collapse = SOURCE[SOURCE.index("async def collapse_duplicate_documents"):]
    assert collapse.index("_referencing_columns") < collapse.index(
        "DELETE FROM plenum_cafm.documents"
    )


@pytest.mark.parametrize(
    "table,column",
    [
        ("compliance_certificates", "document_id"),
        ("compliance_certificates", "source_document_id"),
        ("contract_sla_parameters", "document_id"),
        ("contract_documents", "document_id"),
        ("invoice_verifications", "document_id"),
        ("ppm_visits", "source_document_id"),
    ],
)
def test_every_soft_reference_in_the_schema_is_listed(table: str, column: str):
    # Read off information_schema on the live database. A referrer missing from this list
    # is a row that quietly points at a document that no longer exists.
    assert (table, column) in bb._DOCUMENT_REFERENCES


def test_the_run_history_array_is_deliberately_left_alone():
    # udr_run_versions.document_ids records which documents a run consumed. Rewriting one
    # element would edit history rather than repair a link.
    assert not any(t == "udr_run_versions" for t, _ in bb._DOCUMENT_REFERENCES)
    assert "udr_run_versions" in SOURCE  # the reason is written down


def test_only_columns_the_database_has_are_named_in_sql():
    # These are interpolated into the UPDATE, so they are a fixed list filtered through
    # information_schema — never a name taken from a request.
    assert "information_schema.columns" in SOURCE
    for table, column in bb._DOCUMENT_REFERENCES:
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", table)
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", column)


def test_content_is_grouped_only_where_a_hash_exists():
    # Same bytes are one document however the file was named. A null hash groups nothing:
    # every unhashed row would otherwise land in one bucket together.
    assert "i.file_hash_sha256 IS NOT NULL" in bb._GROUP_BY_CONTENT
    assert "i.file_hash_sha256 AS group_key" in bb._GROUP_BY_CONTENT


def test_a_name_group_merges_only_when_nothing_proves_the_files_differ():
    """The rule that the first version got wrong, and the reason it needs two passes.

    Keying on "hash, else name" puts an old unhashed row and a freshly hashed one in
    different groups — which is the one pair that matters while hashes are being filled in.
    That version shipped and let a sixth copy of one invoice through.

    count(DISTINCT h) ignores nulls, so {null, 'abc'} counts 1 and merges, while
    {'abc', 'def'} counts 2 and is left alone: two different files that share a name.
    """
    assert "count(DISTINCT i.file_hash_sha256) <= 1" in bb._GROUP_BY_NAME
    assert "regexp_replace(d.file_name" in bb._GROUP_BY_NAME


def test_content_is_tried_before_names():
    collapse = SOURCE[SOURCE.index("async def collapse_duplicate_documents"):]
    order = collapse.index("for sql in (_GROUP_BY_CONTENT, _GROUP_BY_NAME)")
    assert order > 0


def test_collapsing_never_fails_an_ingest():
    # It runs after the work is committed. A duplicate is a blemish; losing the upload over
    # tidying one is not a trade worth making.
    bind = SOURCE[SOURCE.index("async def bind_and_log"):]
    assert "ingest.collapse_failed" in bind
    assert "except Exception" in bind


def test_neither_pass_splits_on_doc_type():
    """doc_type is derived, and was unstable enough to have earned its own fix earlier.

    The same certificate has been filed as `compliance_certificate` on one upload and left
    NULL on another. Keying on it splits one file into two documents for a reason that says
    nothing about the file — Town Hall held the same certificate as an 8-row group and a
    2-row group purely because of that.
    """
    for sql in (bb._GROUP_BY_CONTENT, bb._GROUP_BY_NAME):
        assert "doc_type" not in sql


def test_both_queries_take_the_building_as_a_bound_parameter():
    # They are module-level constants, so nothing a caller sends is ever formatted in.
    for sql in (bb._GROUP_BY_CONTENT, bb._GROUP_BY_NAME):
        assert "CAST(:b AS uuid)" in sql
        # No f-string placeholder survived import. Braces themselves are fine and expected:
        # the name query carries a regex, and "[0-9a-f]{8}" is a quantifier, not a hole.
        assert "{_" not in sql
