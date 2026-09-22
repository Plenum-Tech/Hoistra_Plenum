"""Deleting a document, and everything that was read out of it.

The Buildings drawer lists documents. Removing one has to remove what it became — the
certificate on Compliance, the contract terms on Vendors, the invoice lines, the chunks the
assistant answers from — because nothing in the database enforces that. Of 188 foreign keys
in this schema, not one points at plenum_cafm.documents: every document_id is a loose uuid.
Delete the document row alone and every one of those becomes a dangling reference that no
constraint reports and no screen admits to.

So the cascade is written out by hand, and these tests are what stop it rotting: a table
dropped from the list fails here rather than silently outliving the document it belonged to.

No database. The engine is given a session that records SQL and answers from a canned shape.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.engines.energy import document_delete as dd

DOC = "3a1a4f60-0000-0000-0000-00000000dead"
BID = "b0000000-0000-0000-0000-00000000000b"

#: Every column the cascade could match on, as a fully migrated database has them.
LIVE_COLUMNS: dict[str, set[str]] = {
    "documents": {"document_id", "building_id", "doc_type", "file_name", "blob_url"},
    "ingestion_documents": {"id", "original_filename", "blob_url"},
    "document_chunks": {"id", "ingestion_id", "chunk_text"},
    "compliance_certificates": {"certificate_id", "document_id", "source_document_id"},
    "contract_sla_parameters": {"id", "document_id", "contract_ref"},
    "contract_documents": {"id", "document_id", "contract_id"},
    "invoice_verifications": {"id", "document_id"},
    "ppm_visits": {"id", "source_document_id"},
    "compliance_vector_membership_audit": {"id", "document_id"},
    "corrections_log": {"id", "ingestion_id"},
    "ingestion_audit_log": {"id", "ingestion_id"},
    "review_queue": {"id", "ingestion_id"},
    "claude_api_usage": {"id", "ingestion_id"},
    "inspections": {"id", "ingestion_id"},
}


class Recorder:
    """A session that answers from `columns` and remembers every statement it was given."""

    def __init__(self, columns=None, *, doc_row=True, counts=1):
        self.columns = LIVE_COLUMNS if columns is None else columns
        self.doc_row = doc_row
        self.counts = counts
        self.sql: list[str] = []
        self.committed = False
        self.rolled_back = False

    # -- what the engine calls ------------------------------------------------
    def begin_nested(self):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                return outer

            async def __aexit__(self, *a):
                return False

        return _Ctx()

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(" ".join(sql.split()))
        return _Result(self, sql)

    def add(self, _row):  # write_audit adds an ORM row
        self.sql.append("AUDIT ROW ADDED")

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    # -- assertions -----------------------------------------------------------
    @property
    def writes(self) -> list[str]:
        return [s for s in self.sql
                if s.startswith(("DELETE", "UPDATE", "INSERT")) or s == "AUDIT ROW ADDED"]

    def deletes_from(self, table: str) -> list[str]:
        return [s for s in self.sql
                if s.startswith("DELETE") and f"plenum_cafm.{table} " in s + " "]


class _Result:
    def __init__(self, rec: Recorder, sql: str):
        self.rec, self.sql = rec, sql
        self.rowcount = rec.counts

    def all(self):
        if "information_schema.columns" in self.sql:
            return [(t, c) for t, cols in self.rec.columns.items() for c in cols]
        return []

    def scalar(self):
        return self.rec.counts

    def mappings(self):
        return self

    def first(self):
        if not self.rec.doc_row:
            return None
        return {"document_id": DOC, "building_id": BID, "doc_type": "service_contract",
                "file_name": "01_UKRI-2852_FM-Services-Contract_redacted.pdf"}


def run(session, **kw) -> dict[str, Any]:
    return asyncio.run(dd.delete_document(session, DOC, **kw))


# ── the safety property ──────────────────────────────────────────────────────

def test_a_dry_run_writes_nothing_at_all():
    """The dialog opens by calling this. It runs against production, so "reports what it
    would do" has to mean it — not "deletes, then tells you"."""
    rec = Recorder()
    out = run(rec)
    assert out["dry_run"] is True
    assert rec.writes == [], f"a dry run executed writes: {rec.writes}"
    assert rec.committed is False


def test_an_id_that_is_not_a_uuid_is_refused_before_any_sql():
    """The id reaches SQL as a bound parameter, never interpolated — but a malformed id
    should not get as far as the database to find that out."""
    rec = Recorder()
    out = asyncio.run(dd.delete_document(rec, "'; DROP TABLE plenum_cafm.documents; --"))
    assert out["ok"] is False and out["status"] == 400
    assert rec.sql == []


def test_a_document_that_does_not_exist_is_a_404_and_changes_nothing():
    rec = Recorder(doc_row=False)
    out = run(rec, confirm=True)
    assert out["status"] == 404 and out["ok"] is False
    assert rec.writes == []


# ── the cascade itself ───────────────────────────────────────────────────────

def test_every_screen_the_user_named_is_in_the_cascade():
    """Hussain asked for compliance and vendors. These are the tables those screens read:
    a certificate on Compliance, contract terms and invoice lines on Vendors. If one is
    dropped from _CASCADE, the document disappears from Buildings and stays on that screen
    forever, pointing at a document_id that no longer resolves."""
    tables = {link.table for link in dd._CASCADE}
    assert "compliance_certificates" in tables, "Compliance would keep the certificate"
    assert "contract_sla_parameters" in tables, "Vendors would keep the contract terms"
    assert "contract_documents" in tables, "Vendors would keep the contract link"
    assert "invoice_verifications" in tables, "Vendors would keep the invoice lines"
    assert "document_chunks" in tables, "the assistant would keep answering from the file"
    assert "documents" in tables and "ingestion_documents" in tables


def test_a_certificate_is_matched_on_either_column_that_can_name_the_document():
    """compliance_certificates carries document_id AND source_document_id, and which one is
    set depends on which ingest wrote it. Matching one column leaves the other's rows."""
    cert = next(l for l in dd._CASCADE if l.table == "compliance_certificates")
    assert set(cert.columns) == {"document_id", "source_document_id"}


def test_the_document_row_is_deleted_after_everything_read_out_of_it():
    """Children first. A failure partway must not leave rows whose document is already
    gone — that is precisely the dangling state this exists to prevent."""
    order = [l.table for l in dd._CASCADE]
    for child in ("compliance_certificates", "contract_sla_parameters",
                  "invoice_verifications", "document_chunks"):
        assert order.index(child) < order.index("documents"), f"{child} after documents"
    assert order.index("documents") < order.index("ingestion_documents")


def test_confirming_deletes_from_every_available_table_and_commits_once():
    rec = Recorder()
    out = run(rec, confirm=True)
    assert out["ok"] is True and out.get("dry_run") is not True
    for link in dd._CASCADE:
        assert rec.deletes_from(link.table), f"nothing deleted from {link.table}"
    assert rec.committed is True


def test_the_delete_is_audited_with_what_it_removed():
    rec = Recorder()
    out = run(rec, confirm=True)
    assert "AUDIT ROW ADDED" in rec.sql, "a destructive action left no audit row"
    assert out["deleted"]["compliance_certificates"] == rec.counts


# ── deployments that are not the reference schema ────────────────────────────

def test_the_chunk_table_is_matched_on_whichever_column_it_actually_has():
    """01_schema.sql gives document_chunks a document_id; 05_docrag_compat.sql drops that
    table and recreates it with ingestion_id. Both are live somewhere, so the column is
    read off the database rather than assumed — guessing wrong silently leaves the
    embeddings behind and the assistant keeps citing a deleted document."""
    chunks = next(l for l in dd._CASCADE if l.table == "document_chunks")
    assert set(chunks.columns) == {"ingestion_id", "document_id"}

    old = {**LIVE_COLUMNS, "document_chunks": {"id", "document_id"}}
    rec = Recorder(old)
    run(rec, confirm=True)
    stmt = rec.deletes_from("document_chunks")[0]
    assert "document_id" in stmt and "ingestion_id" not in stmt


def test_a_table_this_database_does_not_have_is_skipped_not_fatal():
    """A partially migrated deployment must still be able to delete a document."""
    thin = {k: v for k, v in LIVE_COLUMNS.items() if k not in ("ppm_visits", "review_queue")}
    rec = Recorder(thin)
    out = run(rec, confirm=True)
    assert out["ok"] is True
    assert rec.deletes_from("ppm_visits") == []
    assert "ppm_visits" in out["unavailable"]


def test_a_table_that_exists_without_the_link_column_is_skipped():
    bad = {**LIVE_COLUMNS, "invoice_verifications": {"id", "invoice_ref"}}
    rec = Recorder(bad)
    out = run(rec, confirm=True)
    assert rec.deletes_from("invoice_verifications") == []
    assert "invoice_verifications" in out["unavailable"]


# ── what the database takes with it, reported rather than hidden ─────────────

def test_the_plan_reports_the_rows_the_database_cascades_on_its_own():
    """ingestion_documents DOES have foreign keys: corrections_log, ingestion_audit_log and
    review_queue are deleted by the database itself, and claude_api_usage and inspections
    are unlinked. None of that is in the engine's SQL, so none of it would appear in the
    dialog unless it is counted deliberately."""
    out = run(Recorder())
    cascaded = out["database_cascades"]
    assert set(cascaded["deleted"]) == {"corrections_log", "ingestion_audit_log", "review_queue"}
    assert set(cascaded["unlinked"]) == {"claude_api_usage", "inspections"}


def test_the_original_file_is_never_deleted_from_blob_storage():
    """Decided deliberately: every trace goes from the platform, the PDF stays in storage
    so a mistaken delete can be re-ingested. Nothing here may issue a blob delete."""
    rec = Recorder()
    out = run(rec, confirm=True)
    assert out["blob_url_kept"] is True
    assert not any("blob" in s.lower() and s.startswith("DELETE") for s in rec.sql)


def test_the_plan_totals_only_what_this_engine_will_delete():
    out = run(Recorder())
    assert out["removes_total"] == sum(out["removes"].values())
    assert all(v > 0 for v in out["removes"].values())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
