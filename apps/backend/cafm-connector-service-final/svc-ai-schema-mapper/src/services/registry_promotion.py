"""Feature 7 — incremental global-admin canonical-structure growth.

When a UDR run CREATES canonical tables/columns the platform has not seen before (source
data that did not map to the existing ``plenum_cafm`` schema), those additions are promoted
into the GLOBAL, org-agnostic ``canonical_registry`` so that future runs — and future client
deployments — inherit them as legitimate canonical targets. This realises the "enrich the
target UDR structure for upcoming scenarios" half of Feature 7 aim #3 and the "updated
incrementally at a global admin level, not rebuilt from scratch per client" requirement.

Governance: by default additions land in a ``_pending_canonical`` holding area (admin
approval required) and only move into ``canonical_fields`` — the set the mappers read at
startup via :func:`services.registry_cache.load_or_build` — once an admin approves them.
Set ``auto_approve=True`` (or env ``UDR_REGISTRY_AUTO_APPROVE=1``) to promote straight into
``canonical_fields``. Idempotent: re-promoting an already-known key is a no-op.

The pure ``collect_canonical_additions`` is unit-testable; the DB writers reuse the versioned
snapshot store in :mod:`services.registry_cache` (one new ``canonical_registry`` row each).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .registry_cache import _make_engine, load_latest

logger = logging.getLogger(__name__)


def collect_canonical_additions(graph: dict, base_columns_by_table: dict) -> dict[str, str]:
    """PURE — from a UDR run graph, return ``{"table.column": description}`` for every
    (destination table, column) that does NOT already exist in the base plenum_cafm schema.

    ``base_columns_by_table`` is ``{table_lower: {col_lower, ...}}`` (the shape returned by
    ``db.get_plenum_cafm_columns_by_table``). Columns lacking a destination table are skipped.
    """
    base = base_columns_by_table or {}
    additions: dict[str, str] = {}
    for col in (graph or {}).get("columns", []) or []:
        dest = str(col.get("dest_udr_table") or "").strip()
        name = str(col.get("column") or "").strip()
        if not dest or not name:
            continue
        if name.lower() in base.get(dest.lower(), set()):
            continue  # already a real plenum_cafm column — nothing new to promote
        key = f"{dest}.{name}"
        if key in additions:
            continue
        cls = col.get("classification") or "SHARED_ATTRIBUTE"
        fmt = col.get("cell_format") or "free_text"
        additions[key] = f"{cls} on {dest} (format: {fmt}) — learned from a UDR run"
    return additions


def _snapshot(base: dict, *, canonical: dict, pending: dict, promoted: list) -> dict:
    """Assemble a full registry snapshot dict, preserving learned aliases + vendor aliases."""
    return {
        "_meta": {
            "canonical_count": len(canonical),
            "learned_count": len(base.get("learned_mappings", {})),
            "pending_count": len(pending),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
        "canonical_fields": canonical,
        "vendor_aliases": base.get("vendor_aliases", {}),
        "learned_mappings": base.get("learned_mappings", {}),
        "_pending_canonical": pending,
        "_promoted": promoted,
    }


async def _save_snapshot(db_url: str, snapshot: dict, *, schema_hash: str = "") -> int:
    """Insert a full registry snapshot dict as a new ``canonical_registry`` version row."""
    from sqlalchemy import text as _text

    engine = _make_engine(db_url)
    try:
        async with engine.connect() as conn:
            res = await conn.execute(
                _text(
                    """
                    INSERT INTO plenum_cafm.canonical_registry
                        (schema_hash, registry_json, canonical_count, learned_count)
                    VALUES (:hash, CAST(:json AS jsonb), :cc, :lc)
                    RETURNING version
                    """
                ),
                {
                    "hash": schema_hash,
                    "json": json.dumps(snapshot, default=str),
                    "cc": len(snapshot.get("canonical_fields", {})),
                    "lc": len(snapshot.get("learned_mappings", {})),
                },
            )
            await conn.commit()
            return res.scalar()
    finally:
        await engine.dispose()


async def promote_canonical_additions(
    db_url: str,
    additions: dict[str, str],
    *,
    auto_approve: bool = False,
    source_run_id=None,
    source_org=None,
) -> dict:
    """Promote ``{key: description}`` additions into the global registry, versioned.

    ``auto_approve=False`` → store under ``_pending_canonical`` (await admin approval).
    ``auto_approve=True``  → merge straight into ``canonical_fields`` (adopted next load).
    Skips keys already present in either bucket. Returns a small report.
    """
    if not additions:
        return {"promoted": 0, "pending": 0, "version": None, "added": []}

    base = await load_latest(db_url)
    if base is None:
        base = {"canonical_fields": {}, "vendor_aliases": {}, "learned_mappings": {}}
    canonical = dict(base.get("canonical_fields", {}))
    pending = dict(base.get("_pending_canonical", {}))
    promoted = list(base.get("_promoted", []))

    target = canonical if auto_approve else pending
    added: list[str] = []
    for key, desc in additions.items():
        if key in canonical or key in pending:
            continue
        target[key] = desc
        added.append(key)

    if not added:
        return {"promoted": 0, "pending": len(pending), "version": None, "added": []}

    promoted.append(
        {
            "run_id": str(source_run_id) if source_run_id else None,
            "organization_id": str(source_org) if source_org else None,
            "keys": added,
            "auto_approved": bool(auto_approve),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    version = await _save_snapshot(
        db_url, _snapshot(base, canonical=canonical, pending=pending, promoted=promoted)
    )
    logger.info(
        f"[registry_promotion] {'approved' if auto_approve else 'pending'} "
        f"{len(added)} canonical additions → v{version}: {added[:8]}"
    )
    return {
        "promoted": len(added) if auto_approve else 0,
        "pending": 0 if auto_approve else len(added),
        "version": version,
        "added": added,
    }


def _aged_pending_keys(base: dict, *, max_age_hours: float, now: datetime | None = None) -> list[str]:
    """Pending keys whose promotion batch is older than ``max_age_hours`` (7.11 AC5 backstop)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - max_age_hours * 3600.0
    pending = set((base or {}).get("_pending_canonical", {}) or {})
    aged: set[str] = set()
    for batch in (base or {}).get("_promoted", []) or []:
        if batch.get("auto_approved"):
            continue
        at = batch.get("at")
        try:
            ts = datetime.fromisoformat(str(at)).timestamp() if at else 0.0
        except (TypeError, ValueError):
            ts = 0.0
        if ts and ts <= cutoff:
            aged |= {k for k in (batch.get("keys") or []) if k in pending}
    return sorted(aged)


async def sweep_pending_canonical(db_url: str, *, max_age_hours: float = 24.0, now: datetime | None = None) -> dict:
    """7.11 AC5 backstop — auto-approve pending additions older than ``max_age_hours`` so a
    new table/column is appended to the global structure within (by default) 24h even when
    admin approval is required but not given. Returns ``{approved, version}``."""
    base = await load_latest(db_url)
    if base is None:
        return {"approved": 0, "version": None}
    keys = _aged_pending_keys(base, max_age_hours=max_age_hours, now=now)
    if not keys:
        return {"approved": 0, "version": None}
    return await approve_pending_canonical(db_url, keys)


async def get_pending_canonical(db_url: str) -> dict:
    """Admin read — the pending (awaiting-approval) canonical additions."""
    base = await load_latest(db_url)
    return dict((base or {}).get("_pending_canonical", {}))


async def approve_pending_canonical(db_url: str, keys: list[str] | None = None) -> dict:
    """Admin action — move pending additions (all, or the given ``keys``) into
    ``canonical_fields`` so the mappers adopt them on next load. Returns ``{approved, version}``."""
    base = await load_latest(db_url)
    if base is None:
        return {"approved": 0, "version": None}
    canonical = dict(base.get("canonical_fields", {}))
    pending = dict(base.get("_pending_canonical", {}))
    take = list(pending.keys()) if not keys else [k for k in keys if k in pending]
    if not take:
        return {"approved": 0, "version": None}
    for k in take:
        canonical[k] = pending.pop(k)
    version = await _save_snapshot(
        db_url, _snapshot(base, canonical=canonical, pending=pending, promoted=list(base.get("_promoted", [])))
    )
    logger.info(f"[registry_promotion] approved {len(take)} pending → canonical v{version}")
    return {"approved": len(take), "version": version}
