"""DB-backed canonical registry cache.

Stores versioned snapshots of the full mapper config + learned aliases in
plenum_cafm.canonical_registry.  Replaces per-startup live schema introspection
with a single fast row fetch.

Version lifecycle
-----------------
v1   First startup ever — schema introspection runs once, result saved.
v(n) End of each schema mapper or ingestor run — any newly learned
     semantic_approved entries are merged in and a new version is written.

Stored JSON structure
---------------------
{
  "_meta": {
    "schema_hash":     "<16-char hex>",   # SHA256[:16] of sorted field names
    "canonical_count": 274,
    "learned_count":   12,
    "saved_at":        "2026-04-16T..."
  },
  "canonical_fields":  { field_name: description, ... },
  "vendor_aliases":    { canonical: [alias, ...], ... },
  "learned_mappings":  { alias: { canonical, confidence, tier, ... }, ... }
}

Startup logic (load_or_build)
------------------------------
1. Try SELECT latest row from canonical_registry.
2. Found → restore learned entries into registry._cache, return mapper config.
   Schema introspection is NOT called.
3. Not found → run SchemaIntrospectionService once, save as v1, return config.

Schema-change detection
-----------------------
At the end of each pipeline run save_new_version() receives the current
mapper_config.  If its schema_hash differs from the last stored hash a log
line is emitted so operators know canonical_fields have been updated.
The hash is purely informational — no blocking re-introspection on startup.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS plenum_cafm.canonical_registry (
    version         SERIAL PRIMARY KEY,
    schema_hash     VARCHAR(64)  NOT NULL DEFAULT '',
    registry_json   JSONB        NOT NULL,
    canonical_count INT          NOT NULL DEFAULT 0,
    learned_count   INT          NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def compute_schema_hash(canonical_fields: dict) -> str:
    """
    SHA256[:16] of the sorted canonical field name list.

    Used to detect when new columns/tables have been added to plenum_cafm since
    the last snapshot.  Only field *names* are hashed (not descriptions) so
    description tweaks don't trigger unnecessary version bumps.
    """
    payload = json.dumps(sorted(canonical_fields.keys()), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _make_engine(db_url: str):
    from ..db import _async_engine_connect_args, _strip_sslmode_from_async_url

    return create_async_engine(
        _strip_sslmode_from_async_url(db_url),
        poolclass=NullPool,
        echo=False,
        connect_args=_async_engine_connect_args(db_url),
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def load_latest(db_url: str) -> Optional[dict]:
    """
    Fetch the latest registry snapshot from DB.

    Returns the parsed registry_json dict, or None if the table is empty
    (i.e. first run).
    """
    engine = _make_engine(db_url)
    try:
        async with engine.connect() as conn:
            # Ensure table exists (idempotent)
            await conn.execute(text(_CREATE_TABLE_SQL))
            await conn.commit()

            result = await conn.execute(text(
                """
                SELECT version, registry_json, canonical_count, learned_count
                FROM   plenum_cafm.canonical_registry
                ORDER  BY version DESC
                LIMIT  1
                """
            ))
            row = result.fetchone()

        if row:
            version, registry_json, cc, lc = row
            logger.info(
                f"[registry_cache] Loaded v{version} from DB "
                f"({cc} canonical, {lc} learned)"
            )
            return registry_json  # SQLAlchemy JSONB → already a dict
        return None

    except Exception as exc:
        logger.warning(f"[registry_cache] load_latest failed: {exc}")
        return None
    finally:
        await engine.dispose()


async def save_new_version(
    db_url: str,
    mapper_config: dict,
    schema_hash: str = "",
) -> int:
    """
    Persist a new versioned snapshot to DB.

    Merges the current in-memory learned aliases (from registry._cache) with
    the mapper_config (canonical_fields + vendor_aliases) and inserts a new row.

    Returns the new version number.
    """
    from ..matchers.registry import get_all_entries

    learned = get_all_entries(tier_filter="semantic_approved")
    canonical_fields = mapper_config.get("canonical_fields", {})

    if not schema_hash:
        schema_hash = compute_schema_hash(canonical_fields)

    canonical_count = len(canonical_fields)
    learned_count = len(learned)

    snapshot = {
        "_meta": {
            "schema_hash": schema_hash,
            "canonical_count": canonical_count,
            "learned_count": learned_count,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
        "canonical_fields": canonical_fields,
        "vendor_aliases": mapper_config.get("vendor_aliases", {}),
        "learned_mappings": learned,
    }

    engine = _make_engine(db_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO plenum_cafm.canonical_registry
                        (schema_hash, registry_json, canonical_count, learned_count)
                    VALUES (:hash, CAST(:json AS jsonb), :cc, :lc)
                    RETURNING version
                """),
                {
                    "hash": schema_hash,
                    "json": json.dumps(snapshot, default=str),
                    "cc": canonical_count,
                    "lc": learned_count,
                },
            )
            await conn.commit()
            version = result.scalar()

        logger.info(
            f"[registry_cache] Saved v{version}: "
            f"{canonical_count} canonical, {learned_count} learned "
            f"(hash={schema_hash})"
        )
        return version

    except Exception as exc:
        logger.error(f"[registry_cache] save_new_version failed: {exc}")
        return -1
    finally:
        await engine.dispose()


async def load_or_build(db_url: str) -> dict:
    """
    Main entry point called once at API startup.

    1. Try to load latest snapshot from DB.
       - Restore learned entries into registry._cache.
       - Return mapper_config portion (canonical_fields + vendor_aliases).
       - No schema introspection needed.

    2. If no snapshot exists (first ever boot):
       - Run SchemaIntrospectionService to build mapper_config from live DB.
       - Save as v1 in canonical_registry.
       - Return mapper_config.
    """
    from ..matchers.registry import load_from_snapshot

    snapshot = await load_latest(db_url)

    if snapshot:
        learned = snapshot.get("learned_mappings", {})
        if learned:
            load_from_snapshot(learned)
            logger.info(
                f"[registry_cache] Restored {len(learned)} learned entries "
                "into in-memory registry cache"
            )

        return {
            "version": "1.0",
            "source_system": "plenum_cafm",
            "canonical_fields": snapshot.get("canonical_fields", {}),
            "vendor_aliases": snapshot.get("vendor_aliases", {}),
        }

    # ── First run — introspect schema and save v1 ─────────────────────────────
    logger.info(
        "[registry_cache] No snapshot in DB. "
        "Running schema introspection (first run only)..."
    )
    from .schema_introspection import SchemaIntrospectionService

    svc = SchemaIntrospectionService(db_url)
    mapper_config = await svc.build_default_mapper_config()

    schema_hash = compute_schema_hash(mapper_config.get("canonical_fields", {}))
    await save_new_version(db_url, mapper_config, schema_hash)

    return mapper_config
