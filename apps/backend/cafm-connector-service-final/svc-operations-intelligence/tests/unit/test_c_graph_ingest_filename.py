"""Where a document row gets its name when the caller has none to give.

The Documents panel identifies a file by its name; with file_name NULL it falls back to a
truncated uuid, which is the one thing that tells a reader nothing. The name is never lost —
doc-rag writes it to ingestion_documents.original_filename under the same id before any of
these callers run — it is just dropped in transit, because extract_and_verify_invoice has no
file_name parameter to pass along.

No database: the fallback is exercised against a session stub that records what it was asked.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.engines.energy import graph_ingest


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _Nested:
    async def __aenter__(self) -> "_Nested":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class Session:
    """Answers the two queries the fallback makes, and remembers them."""

    def __init__(self, *, has_column: bool = True, filename: Any = "Invoice.pdf",
                 raises: bool = False) -> None:
        self.has_column = has_column
        self.filename = filename
        self.raises = raises
        self.queries: list[str] = []

    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = " ".join(str(stmt).split())
        self.queries.append(sql)
        if self.raises:
            raise RuntimeError("connection lost")
        if "information_schema.columns" in sql:
            return _Result((1,) if self.has_column else None)
        return _Result((self.filename,) if self.filename is not None else None)


def run(session: Session, doc_id: str = "d-1") -> Any:
    return asyncio.run(graph_ingest._ingested_filename(session, doc_id))


def test_the_name_comes_from_the_ingest_record():
    s = Session(filename="Invoice-HAL-B006-0042-Riverside-Court.pdf")
    assert run(s) == "Invoice-HAL-B006-0042-Riverside-Court.pdf"


def test_the_column_is_checked_before_it_is_read():
    # Introspected, like everything else that touches this schema: a deployment without the
    # ingestion tables must not have its ingest fail over a name.
    s = Session(has_column=False)
    assert run(s) is None
    assert any("information_schema.columns" in q for q in s.queries)
    assert not any("FROM plenum_cafm.ingestion_documents" in q for q in s.queries)


def test_no_matching_row_is_none():
    assert run(Session(filename=None)) is None


def test_a_blank_name_is_none_not_an_empty_string():
    # An empty file_name would satisfy COALESCE downstream and leave the panel showing a
    # blank cell — worse than the uuid, because it looks like a rendering bug.
    assert run(Session(filename="   ")) is None


def test_a_database_error_never_fails_the_ingest():
    # A missing name is a cosmetic loss; a failed ingest is not. This is the one place the
    # exception is worth swallowing.
    assert run(Session(raises=True)) is None
