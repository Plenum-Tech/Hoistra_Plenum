"""Where a Go-engine run keeps its rows between steps.

Each data set (``full`` parse output, ``cleaned`` and ``renamed_full`` preprocess output) is a
directory under ENGINE_DIR/<migration>/<kind>/<version>/ holding a manifest and one Arrow IPC file
per table; a step that runs again writes a new version beside the last, never over it, so a ref
reads exactly the rows it was published with. The app and the worker are separate containers locally
(they share the ``hoist-engine-cache`` volume) and one container in production; Blob holds the
durable copy of every version so a step that resumes anywhere can fetch what it needs. The
checkpoint only ever carries the ref. A run's local files go when it completes (udr_node), and a
sweep removes those of runs that never reach an end.

The bridge (write_tables/read_tables) is how Python steps hand rows to Go steps and back while
both kinds of step exist: it carries str, None, and the integer 0 that preprocess's numeric null
fill leaves — exactly, or not at all (BridgeTypeError).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import pyarrow as pa

from ..graph.bulk_tables import _CACHE, _blob_conf, _cache_put

logger = logging.getLogger(__name__)

ENGINE_DIR = Path(os.environ.get("HOIST_ENGINE_DIR") or "/var/hoist-engine")
_SAFE_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
_SAFE_VERSION = re.compile(r"^[0-9a-f]{12}$")
_KINDS = frozenset({"full", "cleaned", "outputs", "write", "parse", "combine", "preprocess", "renamed_full",
                    "source"})
_SAFE_NAME = re.compile(r"^[0-9A-Za-z_.-]{1,255}$")
_INT0 = {b"hoist.null_fill": b"int0"}
_BATCH = 65536
#: What a data set is, in the words the run card uses, and the step that makes it.
_MADE_BY = {"full": ("uploaded rows", "Ingest"), "renamed_full": ("uploaded rows", "Preprocess"),
            "cleaned": ("cleaned rows", "Preprocess")}
#: A run directory nothing has written to for this long belongs to a run that never reached an end.
STALE_AFTER_S = 7 * 86400


class BridgeTypeError(TypeError):
    """A value the Arrow bridge cannot carry exactly."""


class EngineStoreMissing(RuntimeError):
    """An engine data set is neither on this machine nor in Blob."""


def kind_dir(migration_id: str, kind: str) -> Path:
    if not _SAFE_ID.match(str(migration_id or "")) or kind not in _KINDS:
        raise ValueError(f"unsafe engine path: {migration_id!r}/{kind!r}")
    return ENGINE_DIR / str(migration_id) / kind


def new_data_dir(migration_id: str, kind: str) -> Path:
    """A new, empty directory for the next version of a data set (its name is the version)."""
    d = kind_dir(migration_id, kind) / uuid.uuid4().hex[:12]
    d.mkdir(parents=True)
    return d


def _gone(kind: str) -> str:
    what, step = _MADE_BY.get(kind, ("data", "the step that made it"))
    return (f"This run's {what} are no longer on this machine or in Blob (the service may have restarted, "
            f"or an upload failed). Re-run the migration from {step}.")


def _column_array(name: str, values: list) -> "tuple[pa.Field, pa.Array]":
    has_int0 = has_null = False
    out: list = []
    for v in values:
        if v is None:
            has_null = True
            out.append(None)
        elif isinstance(v, str):
            out.append(v)
        elif type(v) is int and v == 0:
            has_int0 = True
            out.append(None)
        else:
            raise BridgeTypeError(f"column {name!r}: cannot carry {type(v).__name__} {v!r}")
    if has_int0 and has_null:
        raise BridgeTypeError(f"column {name!r}: both a null and a filled 0")
    field = pa.field(name, pa.string(), nullable=True, metadata=_INT0 if has_int0 else None)
    return field, pa.array(out, type=pa.string())


def write_tables(dirpath: Path, tables: dict) -> None:
    dirpath = Path(dirpath)
    # Encode every table before writing anything, so a refusal leaves no half-written data set.
    encoded: list = []
    for idx, (name, rows) in enumerate((tables or {}).items(), 1):
        rows = list(rows or [])
        if not rows:
            encoded.append((name, None, 0, [], None))
            continue
        columns = list(rows[0].keys())
        for r in rows:
            if not isinstance(r, dict) or list(r.keys()) != columns:
                raise BridgeTypeError(f"table {name!r}: rows do not all carry the same columns")
        fields, arrays = zip(*(_column_array(c, [r[c] for r in rows]) for c in columns))
        table = pa.Table.from_arrays(list(arrays), schema=pa.schema(list(fields)))
        encoded.append((name, f"t{idx:04d}.arrow", len(rows), columns, table))
    dirpath.mkdir(parents=True, exist_ok=True)
    for stale in dirpath.glob("t*.arrow"):
        stale.unlink()
    manifest: list[dict] = []
    opts = pa.ipc.IpcWriteOptions(compression="zstd")
    for name, fname, nrows, columns, table in encoded:
        if table is not None:
            with pa.OSFile(str(dirpath / fname), "wb") as sink, pa.ipc.new_file(sink, table.schema, options=opts) as w:
                w.write_table(table, max_chunksize=_BATCH)
        manifest.append({"name": name, "file": fname, "rows": nrows, "columns": columns})
    (dirpath / "manifest.json").write_text(json.dumps({"version": 1, "tables": manifest}), encoding="utf-8")


def read_heads(dirpath: Path, n: int) -> dict:
    """The first n rows of every table of a data set (read_tables' shape), reading only the record
    batches that hold them."""
    dirpath = Path(dirpath)
    meta = json.loads((dirpath / "manifest.json").read_text(encoding="utf-8"))
    out: dict = {}
    for entry in meta.get("tables") or []:
        if not entry.get("file") or n <= 0:
            out[entry["name"]] = []
            continue
        with pa.OSFile(str(dirpath / entry["file"]), "rb") as src:
            reader = pa.ipc.open_file(src)
            batches, got = [], 0
            for i in range(reader.num_record_batches):
                b = reader.get_batch(i)
                batches.append(b)
                got += b.num_rows
                if got >= n:
                    break
            table = pa.Table.from_batches(batches, schema=reader.schema).slice(0, n)
        keep = entry.get("columns") or table.column_names
        table = table.select(keep)
        rows = table.to_pylist()
        for f in table.schema:
            if (f.metadata or {}).get(b"hoist.null_fill") == b"int0":
                for r in rows:
                    if r[f.name] is None:
                        r[f.name] = 0
        out[entry["name"]] = rows
    return out


def read_tables(dirpath: Path) -> dict:
    dirpath = Path(dirpath)
    meta = json.loads((dirpath / "manifest.json").read_text(encoding="utf-8"))
    out: dict = {}
    for entry in meta.get("tables") or []:
        if not entry.get("file"):
            out[entry["name"]] = []
            continue
        with pa.OSFile(str(dirpath / entry["file"]), "rb") as src:
            table = pa.ipc.open_file(src).read_all()
        keep = entry.get("columns") or table.column_names
        table = table.select(keep)
        rows = table.to_pylist()
        int0 = [f.name for f in table.schema if (f.metadata or {}).get(b"hoist.null_fill") == b"int0"]
        for col in int0:
            for r in rows:
                if r[col] is None:
                    r[col] = 0
        out[entry["name"]] = rows
    return out


def _open_blob_service(conn: str):
    from azure.storage.blob.aio import BlobServiceClient

    return BlobServiceClient.from_connection_string(conn)


def _blob_path(migration_id: str, kind: str, version: str, rel: str) -> str:
    return f"migrations/{migration_id}/engine/{kind}/{version}/{rel}"


#: Uploads still going, by migration: publish does not wait for them (see publish).
_UPLOADS: dict[str, set] = {}


async def publish(migration_id: str, kind: str, d: Path) -> dict:
    """The ref to the data set in ``d`` (a new_data_dir of this kind), naming the Blob copy of
    each of its files under its own version. The copy goes up in the background: the step that made
    the data set reads it from this machine, and so does every later step that runs here (the app and
    the worker share ENGINE_DIR), so nothing waits for Blob — on a slow uplink that was half a minute
    a data set. The manifest goes last, so a copy cut short is an incomplete one, which ensure_local
    reports as missing. ``complete`` is False only when no Blob is configured: the local copy then
    serves this machine only."""
    d = Path(d)
    version = d.name
    if d.parent != kind_dir(migration_id, kind) or not _SAFE_VERSION.match(version):
        raise ValueError(f"not a data directory of {kind!r}: {d}")
    files = sorted(p for p in d.rglob("*") if p.is_file())
    rels = [p.relative_to(d).as_posix() for p in files]
    ref: dict[str, Any] = {"kind": kind, "dir": str(d), "version": version, "blobs": {}, "complete": True}
    conn, container = _blob_conf()
    if not conn:
        ref["complete"] = False  # local only (no Azure configured): fine within one machine
        return ref
    blobs = {rel: _blob_path(migration_id, kind, version, rel) for rel in rels}
    if background_uploads():
        ref["blobs"] = blobs
        in_background(migration_id, _upload(conn, container, d, dict(blobs)))
        return ref
    uploaded = await _upload(conn, container, d, blobs)
    ref["blobs"] = {rel: blob for rel, blob in blobs.items() if rel in uploaded}
    ref["complete"] = len(uploaded) == len(blobs)
    return ref


def background_uploads() -> bool:
    """HOIST_BACKGROUND_UPLOADS=1: Blob copies go up without the step waiting for them — for a
    machine whose app and worker share ENGINE_DIR on a slow uplink (the local stack). Off, a step
    returns once its copy is complete: production's disk is lost on a restart and another replica
    may run the next step, and its Blob is in-region and fast."""
    return os.environ.get("HOIST_BACKGROUND_UPLOADS") == "1"


async def _upload(conn: str, container: str, d: Path, blobs: dict) -> set:
    """Every file of a data set to Blob, the manifest last; the files that went up. A failure is
    logged (the local copy still serves this machine, and a reader elsewhere is told which step to
    re-run); the manifest goes only when every other file did."""
    sem = asyncio.Semaphore(4)
    done: set = set()
    try:
        async with _open_blob_service(conn) as svc:
            async def _up(rel: str) -> bool:
                async with sem:
                    try:
                        with (d / rel).open("rb") as fh:
                            await svc.get_blob_client(container=container, blob=blobs[rel]).upload_blob(
                                fh, overwrite=True)
                        done.add(rel)
                        return True
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[engine-store] upload of %s failed: %s", blobs[rel], exc)
                        return False

            ok = await asyncio.gather(*(_up(rel) for rel in blobs if rel != "manifest.json"))
            if all(ok) and "manifest.json" in blobs:
                await _up("manifest.json")
            elif "manifest.json" in blobs:
                logger.warning("[engine-store] %s: not publishing its manifest — a file did not upload", d)
    except Exception as exc:  # noqa: BLE001 — Blob unreachable: the local copy still serves
        logger.warning("[engine-store] publishing %s failed: %s", d, exc)
    return done


def in_background(migration_id: str, coro) -> asyncio.Task:
    """Run an upload for the run without waiting for it; published() waits for it."""
    task = asyncio.get_running_loop().create_task(coro)
    mid = str(migration_id)
    _UPLOADS.setdefault(mid, set()).add(task)

    def _done(t: asyncio.Task) -> None:
        left = _UPLOADS.get(mid)
        if left is not None:
            left.discard(t)
            if not left:
                _UPLOADS.pop(mid, None)
    task.add_done_callback(_done)
    return task


def source_path(migration_id: str, name: str) -> Path:
    """Where the app keeps a run's uploaded file for Node 1, on the volume the worker shares."""
    if not _SAFE_NAME.match(str(name or "")) or name in (".", ".."):
        raise ValueError(f"unsafe source name: {name!r}")
    return kind_dir(migration_id, "source") / name


async def keep_source(migration_id: str, name: str, data: bytes) -> bool:
    """Write the uploaded file where Node 1 looks first; False when it cannot be kept here."""
    try:
        p = source_path(migration_id, name)

        def _write() -> None:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(p)
        await asyncio.to_thread(_write)
        return True
    except (OSError, ValueError) as exc:
        logger.warning("[engine-store] could not keep %s/%s on this machine: %s", migration_id, name, exc)
        return False


async def published(migration_id: str, timeout: float | None = None) -> None:
    """Wait for the run's background uploads (all of them, however they end), at most timeout s."""
    pending = list(_UPLOADS.get(str(migration_id)) or ())
    if pending:
        await asyncio.wait(pending, timeout=timeout)


def _local_dir(migration_id: str, ref: dict) -> Path:
    """Where this machine keeps the ref's files: its own version directory, or — for a ref
    published before data sets had versions — the kind's directory."""
    base = kind_dir(migration_id, ref["kind"])
    version = str(ref.get("version") or "")
    if ref.get("dir") and Path(str(ref["dir"])).name == version and _SAFE_VERSION.match(version):
        return base / version
    return base


def _missing_files(d: Path) -> list[str]:
    """The data set's files not on disk: every file its manifest names, or the manifest itself."""
    manifest = d / "manifest.json"
    if not manifest.exists():
        return ["manifest.json"]
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    return [e["file"] for e in meta.get("tables") or [] if e.get("file") and not (d / e["file"]).exists()]


async def ensure_local(migration_id: str, ref: dict) -> Path:
    """The ref's data set, complete, on this machine: fetched from Blob where files are missing.
    EngineStoreMissing when a file it needs is neither here nor in Blob."""
    kind = str(ref.get("kind") or "")
    d = _local_dir(migration_id, ref)
    if not _missing_files(d):
        return d
    blobs: dict = ref.get("blobs") or {}
    conn, container = _blob_conf()
    if not conn:
        raise EngineStoreMissing(_gone(kind))
    d.mkdir(parents=True, exist_ok=True)
    async with _open_blob_service(conn) as svc:
        async def fetch(rel: str) -> None:
            if rel not in blobs:  # its upload failed when the step published it
                raise EngineStoreMissing(_gone(kind))
            try:
                stream = await svc.get_blob_client(container=container, blob=blobs[rel]).download_blob()
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                (d / rel).write_bytes(await stream.readall())
            except Exception as exc:  # noqa: BLE001
                raise EngineStoreMissing(f"{_gone(kind)} (Could not fetch {blobs[rel]} from Blob: {exc})") from exc

        if not (d / "manifest.json").exists():
            await fetch("manifest.json")
        for rel in _missing_files(d):
            await fetch(rel)
    return d


async def save_tables(state: dict, kind: str, tables: dict) -> dict:
    mid = str(state.get("migration_id") or "")
    d = new_data_dir(mid, kind)
    await asyncio.to_thread(write_tables, d, tables)
    ref = await publish(mid, kind, d)
    _cache_put(f"engine:{mid}:{kind}:{ref['version']}", tables)
    return ref


def discard_run(migration_id: str) -> None:
    """Remove a run's local engine files (Blob keeps its copy of every data set)."""
    if _SAFE_ID.match(str(migration_id or "")):
        shutil.rmtree(ENGINE_DIR / str(migration_id), ignore_errors=True)


def sweep_stale(max_age_s: float = STALE_AFTER_S) -> int:
    """Remove the run directories nothing has written to for max_age_s; how many went."""
    if not ENGINE_DIR.is_dir():
        return 0
    cutoff = time.time() - max_age_s
    removed = 0
    for d in ENGINE_DIR.iterdir():
        if not d.is_dir() or not _SAFE_ID.match(d.name):
            continue
        try:
            newest = max([d.stat().st_mtime, *(p.stat().st_mtime for p in d.rglob("*"))])
        except OSError:
            continue  # being written or removed right now
        if newest < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


async def load_tables(state: dict, kind: str) -> dict:
    mid = str(state.get("migration_id") or "")
    ref = (state.get("engine_refs") or {}).get(kind)
    if not ref:
        return {}
    key = f"engine:{mid}:{kind}:{ref.get('version')}"
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    d = await ensure_local(mid, ref)
    tables = await asyncio.to_thread(read_tables, d)
    _cache_put(key, tables)
    return tables
