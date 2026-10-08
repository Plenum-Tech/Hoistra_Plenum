"""Blob-offload for the bulk row-data channels (``full_tables`` / ``cleaned_tables``, and the
output step's ``intermediate_schema`` / ``output_sql_script``).

These two channels hold the ENTIRE dataset (records-orient dicts — every column name
repeated per row). Left in the LangGraph ``MigrationState`` they get serialized into the
PostgreSQL checkpoint after every node; once a single value passes Postgres's hard 1 GB
``MaxAllocSize`` the run dies with ``invalid memory alloc request size N`` (observed at
~1.65 GiB on a 1.887M-row run, where the post-preprocess checkpoint carries BOTH
full_tables and cleaned_tables).

Fix: keep the bulk rows OUT of the checkpoint. Write them to Azure Blob keyed by
``migration_id`` and store only a tiny blob-path ``*_ref`` string in the state. The graph
builder wraps each node so the data is HYDRATED (loaded from Blob into the in-memory state)
before a node that needs it runs, and DEHYDRATED (offloaded + cleared) from the returned
state before the checkpoint — so the checkpoint only ever carries the ref.

Safety invariant: inline data is only CLEARED once a ref exists. If Blob is unconfigured
``offload_tables`` returns ``None`` and the data stays inline exactly as before (the
pre-fix behaviour — correct, just still subject to the 1 GB ceiling).
"""

from __future__ import annotations

import gzip
import json
import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The channels that hold the full dataset. Mapping: state key -> state ref key.
# intermediate_schema and output_sql_script are the output step's copies of every row (the
# second a str of INSERTs); left inline they made the post-output checkpoint ~150 MB
# (migration f87078d7, 29 Sep 2026).
BULK_CHANNELS: dict[str, str] = {
    "full_tables": "full_tables_ref",
    "cleaned_tables": "cleaned_tables_ref",
    "intermediate_schema": "intermediate_schema_ref",
    "output_sql_script": "output_sql_script_ref",
}

_BLOB_PREFIX = "migrations"  # migrations/{migration_id}/bulk/{kind}.json.gz

# In-process cache (blob path -> tables dict). A single run executes its nodes
# sequentially in one process, so this lets consecutive nodes reuse the loaded copy
# instead of re-downloading ~1 GB per node. Same RAM footprint as the old in-state
# behaviour (the data used to live in the checkpointed state anyway). Bounded FIFO so a
# long-lived worker doesn't accumulate every run's data.
_CACHE: "dict[str, Any]" = {}
_CACHE_MAX = 4


def _cache_put(path: str, tables: Any) -> None:
    # A newer version of the same blob replaces the older one: refs are versioned (path#v),
    # and keeping both held a second full dataset in RAM on a large run (re-review, 29 Sep).
    base = path.split("#", 1)[0]
    for k in [k for k in _CACHE if k.split("#", 1)[0] == base and k != path]:
        _CACHE.pop(k, None)
    _CACHE[path] = tables
    # Evict oldest while over the cap (dicts preserve insertion order).
    while len(_CACHE) > _CACHE_MAX:
        oldest = next(iter(_CACHE))
        _CACHE.pop(oldest, None)


def _blob_conf() -> "tuple[str, str]":
    from ..config import get_settings

    s = get_settings()
    conn = getattr(s, "azure_storage_connection_string", "") or ""
    container = getattr(s, "azure_blob_container_name", "") or "plenum-agentic-ai-attachments"
    return conn, container


def _blob_path(migration_id: str, kind: str) -> str:
    return f"{_BLOB_PREFIX}/{migration_id}/bulk/{kind}.json.gz"


async def offload_tables(migration_id: Optional[str], kind: str, tables: Any) -> Optional[str]:
    """Serialize ``tables`` (a dict such as dict[table] = [records], or a str such as the SQL
    script) to gzip-JSON in Blob and return the blob path. Returns ``None`` — caller MUST keep
    the inline value — when the migration id or Blob config is missing, or on any upload error
    (never lose data on a storage hiccup).
    """
    if not migration_id or not isinstance(tables, (dict, str)) or not tables:
        return None
    conn, container = _blob_conf()
    if not conn:
        logger.warning(
            "[bulk] Azure storage not configured — keeping %s inline (checkpoint may exceed "
            "Postgres 1 GB limit for large datasets)",
            kind,
        )
        return None
    path = _blob_path(migration_id, kind)
    # The ref carries a version: each process caches by ref, the path per migration and kind is
    # fixed, and after rerun-from/9 regenerated the output in the app the worker's cache still
    # answered the fixed path with the pre-rerun SQL (pre-push review, 29 Sep 2026). The blob is
    # still overwritten in place — one per migration and kind.
    ref = f"{path}#{uuid.uuid4().hex[:12]}"
    try:
        raw = gzip.compress(json.dumps(tables, default=str).encode("utf-8"))
        from azure.storage.blob.aio import BlobServiceClient

        async with BlobServiceClient.from_connection_string(conn) as svc:
            bc = svc.get_blob_client(container=container, blob=path)
            await bc.upload_blob(raw, overwrite=True)
        _cache_put(ref, tables)
        logger.info(
            "[bulk] offloaded %s for %s -> %s (%d items, %d bytes gz)",
            kind, migration_id, ref, len(tables), len(raw),
        )
        return ref
    except Exception as exc:  # noqa: BLE001 — never fail the run on an offload hiccup
        logger.warning("[bulk] offload of %s for %s FAILED (keeping inline): %s", kind, migration_id, exc)
        return None


async def _load_tables(ref: str) -> dict:
    if not ref:
        return {}
    cached = _CACHE.get(ref)
    if cached is not None:
        return cached
    conn, container = _blob_conf()
    if not conn:
        return {}
    from azure.storage.blob.aio import BlobServiceClient

    async with BlobServiceClient.from_connection_string(conn) as svc:
        # A ref written before the version was added is the bare blob path.
        bc = svc.get_blob_client(container=container, blob=ref.split("#", 1)[0])
        stream = await bc.download_blob()
        raw = await stream.readall()
    tables = json.loads(gzip.decompress(raw).decode("utf-8"))
    _cache_put(ref, tables)
    return tables


#: The two row channels a Go-engine run keeps in the engine's Arrow files instead (engine/store.py).
_ENGINE_KIND = {"full_tables": "full", "cleaned_tables": "cleaned"}


async def hydrate(state: dict, channels: "list[str]") -> None:
    """Load the requested bulk channels from Blob into the in-memory ``state`` (only when
    they're not already inline). Called before a node that reads them.

    Rows the engine holds (``engine_refs``) are read from its Arrow files — whatever the run's
    engine is now, so a run that fell back to Python mid-way still finds them. A newer gzip-JSON
    offload of the same channel wins."""
    rest = []
    for ch in channels:
        kind = _ENGINE_KIND.get(ch)
        engine_ref = (state.get("engine_refs") or {}).get(kind) if kind else None
        if engine_ref and not state.get(ch) and not state.get(BULK_CHANNELS[ch]):
            from ..engine.store import load_tables

            state[ch] = await load_tables(state, kind)
        else:
            rest.append(ch)
    channels = rest
    for ch in channels:
        ref_key = BULK_CHANNELS.get(ch)
        if not ref_key:
            continue
        if state.get(ch):  # already inline (small run / not offloaded) — leave it
            continue
        ref = state.get(ref_key)
        if ref:
            state[ch] = await _load_tables(ref)


async def dehydrate(state: dict, migration_id: Optional[str], offload_channels: "list[str]") -> None:
    """Remove the bulk channels from ``state`` before the checkpoint write.

    - ``offload_channels`` (the node's SETTER channels): re-serialize to Blob, set the ref,
      then clear the inline dict.
    - Every other bulk channel that is inline but already has a ref (a READER hydrated it,
      read-only): just clear the inline dict — the Blob copy is still authoritative.
    - A channel that is inline WITHOUT a ref and isn't a setter channel is left untouched
      (no safe place to put it — preserves the pre-fix inline behaviour).

    In a Go-engine run the rows a node SET go to the engine's files (the bridge) — all of them or
    none: should the bridge refuse a single value, nothing is cleared, the run continues on the
    Python path (``engine`` = "python") and the offload below handles every channel, so no row is
    ever dropped. A reader's hydrated copy is simply cleared.
    """
    if state.get("engine") == "go":
        from ..engine.store import BridgeTypeError, save_tables

        setters = [ch for ch in _ENGINE_KIND if ch in offload_channels and state.get(ch)]
        refs: dict = {}
        try:
            for ch in setters:
                refs[_ENGINE_KIND[ch]] = await save_tables(state, _ENGINE_KIND[ch], state[ch])
        except BridgeTypeError as exc:
            logger.warning("[bulk] rows cannot go through the engine bridge (%s); this run continues "
                           "on the Python path", exc)
            state["engine"] = "python"
        else:
            state["engine_refs"] = {**(state.get("engine_refs") or {}), **refs}
            for ch, kind in _ENGINE_KIND.items():
                if state.get(ch) and (state.get("engine_refs") or {}).get(kind):
                    state[ch] = {}
            offload_channels = [c for c in offload_channels if c not in _ENGINE_KIND]
    for ch, ref_key in BULK_CHANNELS.items():
        if not state.get(ch):
            continue
        # Cleared to an empty value of its own type: readers do (state.get(ch) or "").strip().
        empty: Any = "" if isinstance(state.get(ch), str) else {}
        if ch in offload_channels:
            ref = await offload_tables(migration_id, ch, state.get(ch))
            if ref:
                state[ref_key] = ref
                state[ch] = empty
            # else: offload failed / no Blob — keep inline (data safety over checkpoint size)
        elif state.get(ref_key):
            # Reader hydrated this read-only channel — the ref is authoritative, drop inline.
            state[ch] = empty
