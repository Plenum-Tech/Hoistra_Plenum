"""Rows between steps travel as the engine's Arrow files, never through the checkpoint."""
import pytest

from src.engine import store
from src.engine.store import BridgeTypeError, EngineStoreMissing, read_tables, write_tables


def test_bridge_round_trip_keeps_order_none_and_the_int_zero(tmp_path):
    tables = {
        "Work Orders": [{"wo": "W1", "qty": 0, "note": None}, {"wo": "W2", "qty": "5", "note": ""}],
        "Empty": [],
        "Ünïcode ✓": [{"naïve": "é"}],
    }
    write_tables(tmp_path, tables)
    back = read_tables(tmp_path)
    assert back == tables
    assert list(back) == list(tables)                      # table order
    assert list(back["Work Orders"][0]) == ["wo", "qty", "note"]   # column order
    assert back["Work Orders"][0]["qty"] == 0 and type(back["Work Orders"][0]["qty"]) is int


@pytest.mark.parametrize("bad", [
    {"T": [{"a": True}]},                    # bool
    {"T": [{"a": 1.5}]},                     # float
    {"T": [{"a": 5}]},                       # an int that is not the null fill
    {"T": [{"a": 0}, {"a": None}]},          # int0 and a real null in one column
    {"T": [{"a": "x"}, {"b": "y"}]},         # ragged rows
])
def test_the_bridge_refuses_what_it_cannot_carry_exactly(tmp_path, bad):
    with pytest.raises(BridgeTypeError):
        write_tables(tmp_path, bad)


class _FakeBlobs:
    """In-memory stand-in for azure.storage.blob.aio.BlobServiceClient."""

    def __init__(self):
        self.data = {}
        self.order = []
        self.gate = None  # an asyncio.Event every upload waits on, when set

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get_blob_client(self, container, blob):
        outer = self

        class _C:
            async def upload_blob(self, data, overwrite=False):
                if outer.gate is not None:
                    await outer.gate.wait()
                outer.data[blob] = data.read() if hasattr(data, "read") else bytes(data)
                outer.order.append(blob)

            async def download_blob(self):
                payload = outer.data[blob]

                class _S:
                    async def readall(self):
                        return payload
                return _S()
        return _C()


@pytest.fixture
def blobs(monkeypatch, tmp_path):
    fake = _FakeBlobs()
    monkeypatch.setattr(store, "ENGINE_DIR", tmp_path / "engine")
    monkeypatch.setattr(store, "_blob_conf", lambda: ("UseDevelopmentStorage=true", "c"))
    monkeypatch.setattr(store, "_open_blob_service", lambda conn: fake)
    store._CACHE.clear()
    return fake


async def test_publish_then_fetch_in_a_process_without_the_files(blobs):
    mid = "0b498dfd-0000-4000-8000-000000000001"
    state = {"migration_id": mid, "engine": "go"}
    ref = await store.save_tables(state, "cleaned", {"T": [{"a": "1"}]})
    assert ref["complete"] and set(ref["blobs"]) == {"manifest.json", "t0001.arrow"}
    await store.published(mid)
    import shutil

    shutil.rmtree(store.ENGINE_DIR / mid)                  # the other container: no local files
    store._CACHE.clear()
    state["engine_refs"] = {"cleaned": ref}
    assert await store.load_tables(state, "cleaned") == {"T": [{"a": "1"}]}


async def test_missing_engine_files_fail_the_node(blobs):
    mid = "0b498dfd-0000-4000-8000-000000000002"
    ref = {"kind": "full", "dir": str(store.kind_dir(mid, "full")), "version": "v1",
           "blobs": {"manifest.json": "migrations/x/engine/full/manifest.json"}, "complete": True}
    with pytest.raises(EngineStoreMissing):
        await store.load_tables({"migration_id": mid, "engine": "go", "engine_refs": {"full": ref}}, "full")


def test_a_migration_id_cannot_escape_the_engine_dir():
    with pytest.raises(ValueError):
        store.kind_dir("../../etc", "full")


async def test_a_go_run_never_offloads_rows_into_the_checkpoint(blobs):
    from src.graph import bulk_tables

    mid = "0b498dfd-0000-4000-8000-000000000003"
    state = {"migration_id": mid, "engine": "go", "full_tables": {"T": [{"a": "1"}]}}
    await bulk_tables.dehydrate(state, mid, ["full_tables"])
    assert state["full_tables"] == {} and "full_tables_ref" not in state
    assert state["engine_refs"]["full"]["version"]
    store._CACHE.clear()
    await bulk_tables.hydrate(state, ["full_tables"])
    assert state["full_tables"] == {"T": [{"a": "1"}]}


async def test_a_bridge_type_error_downgrades_the_run_instead_of_losing_rows(blobs, monkeypatch):
    from src.graph import bulk_tables

    async def _no_blob_offload(*a, **k):
        return None  # legacy offload unavailable → rows stay inline, the pre-offload behaviour
    monkeypatch.setattr(bulk_tables, "offload_tables", _no_blob_offload)
    mid = "0b498dfd-0000-4000-8000-000000000004"
    state = {"migration_id": mid, "engine": "go", "cleaned_tables": {"T": [{"a": 1.5}]}}
    await bulk_tables.dehydrate(state, mid, ["cleaned_tables"])
    assert state["engine"] == "python"
    assert state["cleaned_tables"] == {"T": [{"a": 1.5}]}


async def test_the_bridge_is_all_or_nothing_across_channels(blobs, monkeypatch):
    from src.graph import bulk_tables

    async def _no_blob_offload(*a, **k):
        return None
    monkeypatch.setattr(bulk_tables, "offload_tables", _no_blob_offload)
    mid = "0b498dfd-0000-4000-8000-000000000005"
    state = {"migration_id": mid, "engine": "go",
             "full_tables": {"T": [{"a": "1"}]}, "cleaned_tables": {"T": [{"a": 1.5}]}}
    await bulk_tables.dehydrate(state, mid, ["full_tables", "cleaned_tables"])
    assert state["engine"] == "python"
    assert state["full_tables"] == {"T": [{"a": "1"}]}       # not cleared: nothing was handed over
    assert "full" not in (state.get("engine_refs") or {})


async def test_a_run_that_fell_back_to_python_still_reads_what_the_engine_holds(blobs):
    from src.graph import bulk_tables

    mid = "0b498dfd-0000-4000-8000-000000000006"
    state = {"migration_id": mid, "engine": "go"}
    state["engine_refs"] = {"full": await store.save_tables(state, "full", {"T": [{"a": "1"}]})}
    state["engine"] = "python"
    store._CACHE.clear()
    await bulk_tables.hydrate(state, ["full_tables"])
    assert state["full_tables"] == {"T": [{"a": "1"}]}


async def test_python_reads_a_go_written_dir(tmp_path):
    """The bridge format is one format: what the engine writes, Python reads — the same cells."""
    from src.engine.client import engine_available, run_engine

    if not engine_available():
        pytest.skip("hoist-engine binary not mounted")
    await run_engine("arrow-selftest", {"out_dir": str(tmp_path / "go")}, workdir=tmp_path)
    assert read_tables(tmp_path / "go") == {
        "Work Orders": [{"wo": "W1", "qty": 0, "note": None}, {"wo": "W2", "qty": "5", "note": ""}],
        "Empty": [],
        "Ünïcode ✓": [{"naïve": "é"}],
    }


async def test_a_ref_reads_its_own_version_never_a_later_ones_files(blobs):
    # A step re-run publishes a new version of a data set. A step that then runs with the earlier
    # ref (a retry from a checkpoint taken before the re-run) must read the rows that ref names,
    # never whatever files the later publish left on disk.
    mid = "0b498dfd-0000-4000-8000-000000000007"
    state = {"migration_id": mid, "engine": "go"}
    v1 = await store.save_tables(state, "cleaned", {"T": [{"a": "old mapping"}]})
    v2 = await store.save_tables(state, "cleaned", {"T": [{"a": "new mapping"}]})
    assert v1["version"] != v2["version"]
    store._CACHE.clear()
    assert await store.load_tables({**state, "engine_refs": {"cleaned": v1}}, "cleaned") == {"T": [{"a": "old mapping"}]}
    assert await store.load_tables({**state, "engine_refs": {"cleaned": v2}}, "cleaned") == {"T": [{"a": "new mapping"}]}


async def test_an_earlier_version_is_fetched_from_blob_after_a_later_publish(blobs):
    # The same on a container with no local files at all: each version keeps its own Blob copy.
    import shutil

    mid = "0b498dfd-0000-4000-8000-000000000008"
    state = {"migration_id": mid, "engine": "go"}
    v1 = await store.save_tables(state, "cleaned", {"T": [{"a": "old mapping"}]})
    await store.save_tables(state, "cleaned", {"T": [{"a": "new mapping"}]})
    await store.published(mid)
    shutil.rmtree(store.ENGINE_DIR / mid)
    store._CACHE.clear()
    assert await store.load_tables({**state, "engine_refs": {"cleaned": v1}}, "cleaned") == {"T": [{"a": "old mapping"}]}


async def test_a_partly_published_data_set_fails_visibly_where_its_files_are_missing(blobs, monkeypatch):
    # One upload of a publish fails (in the background, after the step went on). Where the local
    # copy is gone, reading it must stop with EngineStoreMissing naming the step to re-run, not hand
    # back a directory with a table file missing.
    import shutil

    real = blobs.get_blob_client

    def flaky(container, blob):
        c = real(container, blob)
        if blob.endswith("t0002.arrow"):
            async def dropped(data, overwrite=False):
                raise OSError("uplink dropped")
            c.upload_blob = dropped
        return c
    monkeypatch.setattr(blobs, "get_blob_client", flaky)
    mid = "0b498dfd-0000-4000-8000-000000000009"
    state = {"migration_id": mid, "engine": "go"}
    ref = await store.save_tables(state, "cleaned", {"A": [{"a": "1"}], "B": [{"b": "2"}]})
    await store.published(mid)
    assert not any(b.endswith("t0002.arrow") for b in blobs.data)
    shutil.rmtree(store.ENGINE_DIR / mid)
    store._CACHE.clear()
    with pytest.raises(EngineStoreMissing, match="Preprocess"):
        await store.load_tables({**state, "engine_refs": {"cleaned": ref}}, "cleaned")


async def test_a_completed_go_run_releases_its_engine_files(blobs, monkeypatch):
    from src.graph.nodes import udr_node as un

    mid = "0b498dfd-0000-4000-8000-00000000000a"
    state = {"migration_id": mid, "engine": "go"}
    await store.save_tables(state, "cleaned", {"T": [{"a": "1"}]})

    async def finished_udr(st):
        return {"udr_status": "completed"}
    monkeypatch.setattr(un, "udr_node", finished_udr)
    assert await un.udr_node_and_release({**state, "status": "complete"}) == {"udr_status": "completed"}
    assert not (store.ENGINE_DIR / mid).exists()


async def test_a_run_that_did_not_complete_keeps_its_engine_files_for_a_retry(blobs, monkeypatch):
    from src.graph.nodes import udr_node as un

    mid = "0b498dfd-0000-4000-8000-00000000000b"
    state = {"migration_id": mid, "engine": "go"}
    await store.save_tables(state, "cleaned", {"T": [{"a": "1"}]})

    async def skipped_udr(st):
        return {"udr_status": "skipped"}
    monkeypatch.setattr(un, "udr_node", skipped_udr)
    await un.udr_node_and_release({**state, "status": "failed"})
    assert (store.ENGINE_DIR / mid).exists()


def test_engine_dirs_untouched_for_a_week_are_swept(blobs):
    import os
    import time

    old, recent = store.ENGINE_DIR / "0b498dfd-old", store.ENGINE_DIR / "0b498dfd-recent"
    for d in (old, recent):
        (d / "cleaned").mkdir(parents=True)
        (d / "cleaned" / "manifest.json").write_text("{}")
    week_ago = time.time() - 8 * 86400
    for p in [old, *old.rglob("*")]:
        os.utime(p, (week_ago, week_ago))
    assert store.sweep_stale(max_age_s=7 * 86400) == 1
    assert not old.exists() and recent.exists()


async def test_the_worker_sweeps_stale_engine_dirs_when_it_starts(blobs, monkeypatch):
    import os
    import time

    from src import worker

    old = store.ENGINE_DIR / "0b498dfd-left-behind"
    (old / "full").mkdir(parents=True)
    (old / "full" / "manifest.json").write_text("{}")
    week_ago = time.time() - 8 * 86400
    for p in [old, *old.rglob("*")]:
        os.utime(p, (week_ago, week_ago))
    monkeypatch.setattr(worker, "get_settings", lambda: type("S", (), {"openai_api_key": ""})())
    await worker.on_startup({})
    assert not old.exists()


def test_the_cleanup_job_runs_daily():
    from src import worker

    jobs = getattr(worker.WorkerSettings, "cron_jobs", None) or []
    assert any(getattr(j, "coroutine", None) is worker.cleanup_expired_migrations for j in jobs)


async def test_publish_does_not_wait_for_the_upload(blobs, monkeypatch):
    # Blob holds the durable copy for a step that resumes on another machine; the step that made
    # the data set must not wait for it (on a 2.8 Mbit/s uplink that was 25 s per data set).
    import asyncio

    monkeypatch.setenv("HOIST_BACKGROUND_UPLOADS", "1")
    blobs.gate = asyncio.Event()
    mid = "0b498dfd-0000-4000-8000-00000000000d"
    ref = await asyncio.wait_for(store.save_tables({"migration_id": mid, "engine": "go"}, "cleaned",
                                                   {"A": [{"a": "1"}], "B": [{"b": "2"}]}), timeout=5)
    assert ref["complete"] and set(ref["blobs"]) == {"manifest.json", "t0001.arrow", "t0002.arrow"}
    assert blobs.data == {}                                   # nothing has gone yet
    blobs.gate.set()
    await store.published(mid)
    assert set(blobs.data) == set(ref["blobs"].values())
    assert blobs.order[-1].endswith("manifest.json")          # the manifest last: a copy without it is incomplete


async def test_releasing_a_run_waits_for_its_uploads(blobs, monkeypatch):
    # The run finishes while a data set is still going up: its local files stay until the upload
    # has read them, then go.
    import asyncio

    from src.graph.nodes import udr_node as un

    monkeypatch.setenv("HOIST_BACKGROUND_UPLOADS", "1")
    blobs.gate = asyncio.Event()
    mid = "0b498dfd-0000-4000-8000-00000000000e"
    state = {"migration_id": mid, "engine": "go"}
    ref = await asyncio.wait_for(store.save_tables(state, "cleaned", {"T": [{"a": "1"}]}), timeout=5)

    async def finished_udr(st):
        return {"udr_status": "completed"}
    monkeypatch.setattr(un, "udr_node", finished_udr)
    release = asyncio.create_task(un.udr_node_and_release({**state, "status": "complete"}))
    await asyncio.sleep(0.05)
    assert (store.ENGINE_DIR / mid).exists() and not release.done()
    blobs.gate.set()
    await asyncio.wait_for(release, timeout=5)
    assert not (store.ENGINE_DIR / mid).exists()
    assert set(blobs.data) == set(ref["blobs"].values())


async def test_without_the_switch_publish_returns_once_blob_has_the_copy(blobs, monkeypatch):
    # Production: in-region Blob is fast, and its local disk is lost on a restart while there may
    # be other replicas — a step returns only once the durable copy is complete, as it always did.
    monkeypatch.delenv("HOIST_BACKGROUND_UPLOADS", raising=False)
    mid = "0b498dfd-0000-4000-8000-00000000000f"
    ref = await store.save_tables({"migration_id": mid, "engine": "go"}, "cleaned", {"T": [{"a": "1"}]})
    assert ref["complete"] and set(blobs.data) == set(ref["blobs"].values())
    assert blobs.order[-1].endswith("manifest.json")


async def test_without_the_switch_a_failed_upload_marks_the_ref_incomplete(blobs, monkeypatch):
    monkeypatch.delenv("HOIST_BACKGROUND_UPLOADS", raising=False)
    real = blobs.get_blob_client

    def flaky(container, blob):
        c = real(container, blob)
        if blob.endswith("t0001.arrow"):
            async def dropped(data, overwrite=False):
                raise OSError("uplink dropped")
            c.upload_blob = dropped
        return c
    monkeypatch.setattr(blobs, "get_blob_client", flaky)
    ref = await store.save_tables({"migration_id": "0b498dfd-0000-4000-8000-000000000010", "engine": "go"},
                                  "cleaned", {"T": [{"a": "1"}]})
    assert ref["complete"] is False and "t0001.arrow" not in ref["blobs"]
