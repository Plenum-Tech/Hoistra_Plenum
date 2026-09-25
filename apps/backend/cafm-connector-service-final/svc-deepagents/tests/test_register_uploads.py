"""Every uploaded file earns a row, including the ones no engine indexes.

An energy CSV wrote 48 meter readings, a meter and a gap row, and produced nothing in
ingestion_documents or documents. Registration had always been a side effect of indexing:
doc-rag writes the row for what it indexes, and compliance, contract and invoice all reach
the building's drawer through it. The energy path does not index, so on that route the row
was never written by anyone — the building showed no document, the graph had nothing to
link, and the binder logged ingest.nothing_to_bind against an upload that had plainly
succeeded.

These tests cover the parts that need no database: which files get a type, and that the
uploader is identified by basename rather than by the server path it happened to be saved
at. The statement itself is compiled by test_binding_sql_compiles.
"""
from __future__ import annotations

import asyncio

import pytest

from src.services import building_binding as bb


def types(tool_calls):
    return bb._doc_types_by_file(tool_calls)


def test_an_unindexed_tool_names_the_file_it_consumed():
    calls = [{"tool": "ingest_meter_readings", "input": {"file": "s1_readings.csv"}}]
    assert types(calls) == {"s1_readings.csv": "meter_readings"}


def test_tools_that_register_their_own_row_are_not_second_guessed():
    # index_document already writes the ingestion_documents row for what it indexed. Giving
    # this map an opinion about that file is how one upload becomes two rows.
    calls = [
        {"tool": "index_document", "input": {"file": "s1_contract.pdf"}},
        {"tool": "extract_contract_from_document", "input": {"file": "s1_contract.pdf"}},
    ]
    assert types(calls) == {}


def test_a_mixed_upload_types_only_the_unindexed_file():
    calls = [
        {"tool": "index_document", "input": {"file": "s1_cert.pdf"}},
        {"tool": "ingest_meter_readings", "input": {"file": "s1_hh.csv"}},
    ]
    assert types(calls) == {"s1_hh.csv": "meter_readings"}


@pytest.mark.parametrize(
    "calls",
    [
        None,
        [],
        ["not a dict"],
        [{"tool": "ingest_meter_readings"}],  # consumed something, said nothing
        [{"tool": "ingest_meter_readings", "input": None}],
        [{"tool": "ingest_meter_readings", "input": {"file": ""}}],
        [{"tool": "ingest_meter_readings", "input": "readings.csv"}],
        [{"input": {"file": "x.csv"}}],  # no tool name at all
    ],
)
def test_malformed_tool_calls_yield_nothing_rather_than_raising(calls):
    # This runs on the return path of a completed ingest. An exception here would fail an
    # upload whose work is already committed.
    assert types(calls) == {}


def test_nothing_to_register_touches_no_database():
    # AsyncSessionLocal is not stubbed. If either of these opened a session the test would
    # fail trying to reach Postgres, which is exactly the assertion.
    assert asyncio.run(bb.register_uploads("s1", None)) == 0
    assert asyncio.run(bb.register_uploads("s1", [])) == 0
    assert asyncio.run(bb.register_uploads("s1", ["", "   "])) == 0


def test_files_are_registered_by_basename(monkeypatch):
    # The uploader saves to "{upload_dir}/{session_id}_{name}" and doc-rag records only the
    # last part. Registering the full server path would file the document under a name
    # nothing else uses, and the binder — which matches on that name — would never see it.
    seen: list[dict] = []

    class FakeResult:
        rowcount = 1

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, _stmt, params):
            seen.append(params)
            return FakeResult()

        async def commit(self):
            pass

    monkeypatch.setattr(bb.database, "AsyncSessionLocal", lambda: FakeSession())
    made = asyncio.run(
        bb.register_uploads(
            "s1",
            ["/data/uploads/s1_hh.csv", "C:\\data\\uploads\\s1_cert.pdf"],
            [{"tool": "ingest_meter_readings", "input": {"file": "s1_hh.csv"}}],
        )
    )
    assert made == 2
    assert [p["n"] for p in seen] == ["s1_hh.csv", "s1_cert.pdf"]
    # The type is carried only for the file the unindexed tool named. The other is left
    # unset rather than guessed — doc-rag's own row, if it wrote one, already says.
    assert [p["dt"] for p in seen] == ["meter_readings", None]


def test_a_registration_failure_does_not_take_the_ingest_down(monkeypatch):
    # bind_and_log runs after the engines have committed their work. A file that cannot be
    # registered is a missing link; raising here would turn it into a lost upload.
    async def boom(*_a, **_k):
        raise RuntimeError("no connection")

    monkeypatch.setattr(bb, "register_uploads", boom)
    out = asyncio.run(
        bb.bind_and_log(None, [], where="test", session_id="s1", file_paths=["/x/s1_a.csv"])
    )
    assert out == {}


def test_registration_runs_even_when_no_building_was_chosen(monkeypatch):
    # The file still arrived. Binding is what needs a building; having a row is not.
    called: list[tuple] = []

    async def spy(session_id, file_paths, tool_calls=None):
        called.append((session_id, tuple(file_paths or [])))
        return 1

    monkeypatch.setattr(bb, "register_uploads", spy)
    asyncio.run(
        bb.bind_and_log(None, [], where="test", session_id="s1", file_paths=["/x/s1_a.csv"])
    )
    assert called == [("s1", ("/x/s1_a.csv",))]
