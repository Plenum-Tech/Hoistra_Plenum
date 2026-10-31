"""The uploaded source file reaches the worker without a round trip through Blob.

The app and the worker share ENGINE_DIR (one container in production, the hoist-engine-cache volume
locally). On 2 Oct 2026 an 8.7 MB workbook went up to Blob before the run could start (~25 s on a
2.8 Mbit/s uplink) and the worker then downloaded it back (9 s). The app now keeps the file on the
shared volume, the durable Blob copy goes up in the background, and Node 1 reads the local copy —
falling back to Blob (waiting for an upload still in flight) only where the file is not on disk.
"""
from __future__ import annotations

import asyncio

import pytest

from src.engine import store
from src.graph.nodes import ingest_node as ing

MID = "5a5a5a5a-0000-4000-8000-0000000000aa"


class _Blobs:
    def __init__(self, gate=None, missing_first=0):
        self.data = {}
        self.gate = gate
        self.missing_first = missing_first
        self.downloads = 0

    @classmethod
    def factory(cls, fake):
        return lambda conn: fake

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
                outer.data[blob] = bytes(data)

            async def download_blob(self):
                from azure.core.exceptions import ResourceNotFoundError

                outer.downloads += 1
                if outer.downloads <= outer.missing_first or blob not in outer.data:
                    raise ResourceNotFoundError("The specified blob does not exist.")
                payload = outer.data[blob]

                class _S:
                    async def readall(self):
                        return payload
                return _S()
        return _C()


@pytest.fixture
def azure(monkeypatch, tmp_path):
    from src import config

    monkeypatch.setattr(store, "ENGINE_DIR", tmp_path / "engine")
    settings = config.get_settings()
    monkeypatch.setattr(settings, "azure_storage_connection_string", "UseDevelopmentStorage=true", raising=False)
    monkeypatch.setattr(settings, "azure_blob_container_name", "c", raising=False)
    return settings


async def test_the_upload_keeps_a_local_copy_and_does_not_wait_for_blob(azure, monkeypatch):
    from azure.storage.blob.aio import BlobServiceClient

    from src import app

    monkeypatch.setenv("HOIST_BACKGROUND_UPLOADS", "1")

    fake = _Blobs(gate=asyncio.Event())
    monkeypatch.setattr(BlobServiceClient, "from_connection_string", _Blobs.factory(fake))
    data = b"site_id,name\nS-1,Tower\n"
    path = await asyncio.wait_for(app._store_migration_source(MID, "B 301 export.csv", data), timeout=5)
    assert path == f"migrations/{MID}/source/B_301_export.csv"
    assert store.source_path(MID, "B_301_export.csv").read_bytes() == data
    assert fake.data == {}                      # Blob has not been waited for
    fake.gate.set()
    await store.published(MID)
    assert fake.data == {path: data}            # the durable copy still goes up


async def test_originals_go_to_blob_as_before(azure, monkeypatch):
    # Only the file a run reads is kept on the volume; the archived originals of a combined upload
    # go to Blob alone, as they always did.
    from azure.storage.blob.aio import BlobServiceClient

    from src import app

    fake = _Blobs()
    monkeypatch.setattr(BlobServiceClient, "from_connection_string", _Blobs.factory(fake))
    path = await app._store_migration_source(MID, "a.csv", b"x\n1\n", subdir="originals")
    assert path == f"migrations/{MID}/originals/a.csv" and fake.data == {path: b"x\n1\n"}
    assert not (store.ENGINE_DIR / MID).exists()


async def test_node_1_reads_the_shared_copy_without_touching_blob(azure, monkeypatch):
    from azure.storage.blob.aio import BlobServiceClient

    def no_blob(conn):
        raise AssertionError("Blob was read although the file is on the shared volume")
    monkeypatch.setattr(BlobServiceClient, "from_connection_string", no_blob)
    p = store.source_path(MID, "b.csv")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"a\n1\n")
    got = await ing._read_source({"migration_id": MID, "source_blob_path": f"migrations/{MID}/source/b.csv"})
    assert got == b"a\n1\n"


async def test_node_1_waits_for_a_blob_copy_still_going_up(azure, monkeypatch):
    # A worker that does not share the volume: the file is not on disk and the app's background
    # upload has not landed yet. Node 1 keeps asking for a while instead of failing the run.
    from azure.storage.blob.aio import BlobServiceClient

    blob = f"migrations/{MID}/source/c.csv"
    monkeypatch.setenv("HOIST_BACKGROUND_UPLOADS", "1")
    fake = _Blobs(missing_first=2)
    fake.data[blob] = b"a\n2\n"
    monkeypatch.setattr(BlobServiceClient, "from_connection_string", _Blobs.factory(fake))
    monkeypatch.setattr(ing, "_SOURCE_RETRY_DELAYS_S", (0.01, 0.01, 0.01))
    got = await ing._read_source({"migration_id": MID, "source_blob_path": blob})
    assert got == b"a\n2\n" and fake.downloads == 3


async def test_a_source_that_never_lands_fails_with_the_blob_error(azure, monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError
    from azure.storage.blob.aio import BlobServiceClient

    monkeypatch.setenv("HOIST_BACKGROUND_UPLOADS", "1")
    fake = _Blobs()
    monkeypatch.setattr(BlobServiceClient, "from_connection_string", _Blobs.factory(fake))
    monkeypatch.setattr(ing, "_SOURCE_RETRY_DELAYS_S", (0.01, 0.01))
    with pytest.raises(ResourceNotFoundError):
        await ing._read_source({"migration_id": MID, "source_blob_path": f"migrations/{MID}/source/d.csv"})
    assert fake.downloads == 3


async def test_without_the_switch_a_missing_source_fails_at_once(azure, monkeypatch):
    # Production uploads the source before the run is queued, so a missing blob is gone for good:
    # Node 1 fails straight away, as it always did, instead of waiting a minute for it.
    from azure.core.exceptions import ResourceNotFoundError
    from azure.storage.blob.aio import BlobServiceClient

    monkeypatch.delenv("HOIST_BACKGROUND_UPLOADS", raising=False)
    fake = _Blobs()
    monkeypatch.setattr(BlobServiceClient, "from_connection_string", _Blobs.factory(fake))
    monkeypatch.setattr(ing, "_SOURCE_RETRY_DELAYS_S", (0.01, 0.01))
    with pytest.raises(ResourceNotFoundError):
        await ing._read_source({"migration_id": MID, "source_blob_path": f"migrations/{MID}/source/e.csv"})
    assert fake.downloads == 1


def test_a_source_name_cannot_escape_the_run_directory():
    with pytest.raises(ValueError):
        store.source_path(MID, "../../etc/passwd")


async def test_without_the_switch_the_upload_waits_for_blob_as_before(azure, monkeypatch):
    # Production: the file is in Blob before the run is queued (another replica's worker may run
    # it, and a restart wipes local disk), and no local copy is left behind.
    from azure.storage.blob.aio import BlobServiceClient

    from src import app

    monkeypatch.delenv("HOIST_BACKGROUND_UPLOADS", raising=False)
    fake = _Blobs()
    monkeypatch.setattr(BlobServiceClient, "from_connection_string", _Blobs.factory(fake))
    path = await app._store_migration_source(MID, "B 301 export.csv", b"a\n1\n")
    assert fake.data == {path: b"a\n1\n"}
    assert not (store.ENGINE_DIR / MID).exists()
