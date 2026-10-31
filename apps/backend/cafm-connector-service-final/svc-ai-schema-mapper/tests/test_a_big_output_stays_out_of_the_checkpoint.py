"""The output step's two big values ride in Blob, not in the checkpoint.

The output step puts ``intermediate_schema`` (every row of the migration) and
``output_sql_script`` (every row again, as INSERTs) into the state. Only ``full_tables`` and
``cleaned_tables`` were offloaded, so on 29 Sep 2026 (migration f87078d7, 289,606 rows) the
checkpoint after the output step carried ~150 MB to Postgres over a laptop link — many minutes,
during which the job said "paused" and an advance restarted the output step beside it
(tests/test_an_advance_waits_for_the_run_that_paused.py). They now go the way the tables go:
offloaded after the output step, read back before the write step, a ref in the checkpoint.

Blob is faked in memory; nothing here reaches a network.
"""
from __future__ import annotations

import asyncio

import pytest

from src.graph import bulk_tables
from src.graph.migration_graph import BULK_IO
from src.graph.state import MigrationState

SQL = "INSERT INTO plenum_cafm.sites (id) VALUES ('s1');\n" * 3
SCHEMA = {"entities": {"sites": [{"id": "s1"}], "meter_readings": [{"v": 1.5}] * 4}}


class _Blob:
    def __init__(self, store, path):
        self.store, self.path = store, path

    async def upload_blob(self, raw, overwrite=False):
        self.store[self.path] = bytes(raw)

    async def download_blob(self):
        data = self.store[self.path]

        class _Stream:
            async def readall(self_inner):
                return data

        return _Stream()


class _Service:
    store: dict = {}

    @classmethod
    def from_connection_string(cls, conn):
        return cls()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get_blob_client(self, container, blob):
        return _Blob(_Service.store, blob)


@pytest.fixture
def blob(monkeypatch):
    import azure.storage.blob.aio as aio

    _Service.store = {}
    monkeypatch.setattr(aio, "BlobServiceClient", _Service)
    monkeypatch.setattr(bulk_tables, "_blob_conf", lambda: ("fake-connection", "container"))
    bulk_tables._CACHE.clear()
    yield _Service.store
    bulk_tables._CACHE.clear()


def test_the_output_steps_two_big_values_are_bulk_channels():
    assert bulk_tables.BULK_CHANNELS["intermediate_schema"] == "intermediate_schema_ref"
    assert bulk_tables.BULK_CHANNELS["output_sql_script"] == "output_sql_script_ref"


def test_the_state_declares_where_they_went():
    # LangGraph drops a key the state does not declare, and a lost ref is a lost SQL script.
    keys = MigrationState.__annotations__
    assert "intermediate_schema_ref" in keys and "output_sql_script_ref" in keys


def test_the_output_step_offloads_them_and_the_write_step_reads_them_back():
    assert {"intermediate_schema", "output_sql_script"} <= set(BULK_IO["output_generator_node"][1])
    assert {"intermediate_schema", "output_sql_script"} <= set(BULK_IO["write_node"][0])


def test_offloaded_they_leave_only_a_ref_and_come_back_whole(blob):
    state = {"migration_id": "m-1", "output_sql_script": SQL, "intermediate_schema": SCHEMA}
    asyncio.run(bulk_tables.dehydrate(state, "m-1", ["intermediate_schema", "output_sql_script"]))
    assert state["output_sql_script"] == "" and state["intermediate_schema"] == {}
    assert state["output_sql_script_ref"] and state["intermediate_schema_ref"]
    assert len(blob) == 2

    bulk_tables._CACHE.clear()  # the write step may run in another process: read from Blob
    asyncio.run(bulk_tables.hydrate(state, ["intermediate_schema", "output_sql_script"]))
    assert state["output_sql_script"] == SQL
    assert state["intermediate_schema"] == SCHEMA


def test_a_reader_drops_its_copy_again_before_the_next_checkpoint(blob):
    state = {"migration_id": "m-2", "output_sql_script": SQL, "intermediate_schema": SCHEMA}
    asyncio.run(bulk_tables.dehydrate(state, "m-2", ["intermediate_schema", "output_sql_script"]))
    asyncio.run(bulk_tables.hydrate(state, ["intermediate_schema", "output_sql_script"]))
    asyncio.run(bulk_tables.dehydrate(state, "m-2", []))
    assert state["output_sql_script"] == "" and state["intermediate_schema"] == {}


def test_without_blob_storage_they_stay_in_the_state(monkeypatch):
    monkeypatch.setattr(bulk_tables, "_blob_conf", lambda: ("", "container"))
    state = {"migration_id": "m-3", "output_sql_script": SQL, "intermediate_schema": SCHEMA}
    asyncio.run(bulk_tables.dehydrate(state, "m-3", ["intermediate_schema", "output_sql_script"]))
    assert state["output_sql_script"] == SQL and state["intermediate_schema"] == SCHEMA


def test_a_rerun_output_is_never_answered_from_another_processes_stale_copy(blob):
    # Each process caches what it offloaded or read, by ref. The path per migration and kind
    # is fixed, so after rerun-from/9 regenerated the output in the app, the worker's cache
    # still answered the old ref with the pre-rerun SQL — and Confirm wrote it. Every offload
    # now carries its own version in the ref.
    state = {"migration_id": "m-4", "output_sql_script": "OLD;", "intermediate_schema": {"a": 1}}
    asyncio.run(bulk_tables.dehydrate(state, "m-4", ["output_sql_script"]))
    old_ref = state["output_sql_script_ref"]
    state["output_sql_script"] = "NEW;"
    asyncio.run(bulk_tables.dehydrate(state, "m-4", ["output_sql_script"]))
    new_ref = state["output_sql_script_ref"]
    assert new_ref != old_ref
    bulk_tables._CACHE[old_ref] = "OLD;"      # another process still holds the old copy
    asyncio.run(bulk_tables.hydrate(state, ["output_sql_script"]))
    assert state["output_sql_script"] == "NEW;"
    assert len(blob) == 1, "one blob per migration and kind; the version lives in the ref"



def test_only_the_newest_version_of_a_blob_is_kept_in_the_process(blob):
    # Versioned refs made each offload a new cache key; the previous version of the same
    # blob stayed in RAM beside it — a second full dataset on a large run (re-review, 29 Sep).
    state = {"migration_id": "m-5", "output_sql_script": "V1;"}
    asyncio.run(bulk_tables.dehydrate(state, "m-5", ["output_sql_script"]))
    first = state["output_sql_script_ref"]
    state["output_sql_script"] = "V2;"
    asyncio.run(bulk_tables.dehydrate(state, "m-5", ["output_sql_script"]))
    assert first not in bulk_tables._CACHE
    assert state["output_sql_script_ref"] in bulk_tables._CACHE
