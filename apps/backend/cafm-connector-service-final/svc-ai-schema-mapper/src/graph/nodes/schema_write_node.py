"""Node 8: Schema Write — Execute DDL against plenum_cafm + write audit records.

Two-phase execution:

Phase 1 — DDL Execution (single transaction)
  Reads extra_fields_config (from Node 4) and approved FK relationships (from Node 6).
  Generates and executes:
    - ALTER TABLE plenum_cafm.<table> ADD COLUMN ...  (custom fields on existing tables)
    - CREATE TABLE plenum_cafm.<table> (...)           (brand-new entity tables)
    - ALTER TABLE ... ADD CONSTRAINT FOREIGN KEY ...   (approved FK relationships)
  All statements run inside a single BEGIN/COMMIT block.
  On ANY failure → ROLLBACK everything, set status="ddl_failed" with detailed error.
  The user can then correct their field definitions and re-submit.

Phase 2 — Audit Records (only runs if Phase 1 succeeds)
  Writes all field mappings (T1, T2, custom, unmapped) to schema_mapping_field_mappings.
  Marks the job as complete.
"""

import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema_state import SchemaMappingState, ExtraFieldConfig
from ...matchers.registry import registry_append
from ...db import get_async_session_factory

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# Source schema that gets cloned
_SOURCE_SCHEMA = "plenum_cafm"


def _make_new_schema_name(external_cmms_name: str) -> str:
    """Generate a safe PostgreSQL identifier for the new schema."""
    slug = re.sub(r"[^a-z0-9]+", "_", external_cmms_name.lower()).strip("_")[:30]
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    name = f"plenum_cafm_{slug}_{ts}"
    # PostgreSQL identifiers max 63 chars
    return name[:63]


# ── DDL Generation ────────────────────────────────────────────────────────────

def _build_ddl_statements(
    extra_fields_config: list[ExtraFieldConfig],
    approved_fks: list[dict],
    existing_canonical_tables: set[str],
    target_schema: str = _SOURCE_SCHEMA,
) -> list[dict]:
    """
    Generate DDL statements from Node 4 + Node 6 decisions.

    Args:
        target_schema: PostgreSQL schema to apply DDL to (default: plenum_cafm).
                       Pass the new schema name here so Node 8 writes to the clone.

    Returns a list of:
        {"sql": "...", "description": "..."}

    Order:
      1. CREATE TABLE for new tables (must come before ALTER TABLE on those tables)
      2. ALTER TABLE ADD COLUMN for existing tables
      3. ALTER TABLE ADD CONSTRAINT FOREIGN KEY for approved FKs
    """
    ddl: list[dict] = []

    # ── Group custom fields by target_table ──────────────────────────
    new_tables: dict[str, list[ExtraFieldConfig]] = {}      # new tables to create
    existing_table_cols: dict[str, list[ExtraFieldConfig]] = {}  # cols for existing tables

    for entry in extra_fields_config:
        if entry.get("storage_strategy") != "custom":
            continue  # raw_metadata and skip need no DDL

        target_table = entry.get("target_table")
        if not target_table:
            continue

        if entry.get("is_new_table", False):
            new_tables.setdefault(target_table, []).append(entry)
        else:
            existing_table_cols.setdefault(target_table, []).append(entry)

    # ── 1. CREATE TABLE for brand-new tables ─────────────────────────
    for table_name, columns in new_tables.items():
        pk_col = columns[0].get("new_table_pk", "id") or "id"
        pk_lower = pk_col.lower()
        # created_at / updated_at are always appended below — reserve them so a
        # source column of the same name can't be emitted twice.
        reserved = {"created_at", "updated_at"}

        # If a source column already provides the PK name (e.g. Fiix "id"), use it as
        # the primary key with its real type instead of adding a clashing synthetic UUID.
        pk_source_col = next(
            (c for c in columns if (c.get("custom_column_name") or "").lower() == pk_lower),
            None,
        )

        col_defs: list[str] = []
        emitted: set[str] = {pk_lower}
        if pk_source_col is not None:
            col_defs.append(f"    {pk_col} {pk_source_col.get('data_type', 'TEXT')} PRIMARY KEY")
        else:
            col_defs.append(f"    {pk_col} UUID PRIMARY KEY DEFAULT gen_random_uuid()")

        for col in columns:
            col_name = col.get("custom_column_name")
            if not col_name:
                continue
            name_lower = col_name.lower()
            # Skip the PK column (already emitted), reserved timestamps, and duplicates.
            if name_lower in emitted or name_lower in reserved:
                continue
            emitted.add(name_lower)
            data_type = col.get("data_type", "TEXT")
            nullable_clause = "" if col.get("nullable", True) else " NOT NULL"
            col_defs.append(f"    {col_name} {data_type}{nullable_clause}")

        col_defs.append("    created_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        col_defs.append("    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")

        col_block = ",\n".join(col_defs)
        sql = (
            f"CREATE TABLE IF NOT EXISTS {target_schema}.{table_name} (\n"
            f"{col_block}\n"
            f");"
        )
        ddl.append({
            "sql": sql,
            "description": (
                f"CREATE TABLE {target_schema}.{table_name} "
                f"({len(columns)} custom columns + PK + timestamps)"
            ),
        })

    # ── 2. ALTER TABLE ADD COLUMN for existing tables ─────────────────
    _seen_cols: set[tuple[str, str]] = set()
    for table_name, columns in existing_table_cols.items():
        for col in columns:
            col_name = col.get("custom_column_name")
            if not col_name:
                continue
            _dedup_key = (table_name, col_name)
            if _dedup_key in _seen_cols:
                continue
            _seen_cols.add(_dedup_key)
            data_type = col.get("data_type", "TEXT")
            nullable_clause = "" if col.get("nullable", True) else " NOT NULL"
            sql = (
                f"ALTER TABLE {target_schema}.{table_name} "
                f"ADD COLUMN IF NOT EXISTS {col_name} {data_type}{nullable_clause};"
            )
            ddl.append({
                "sql": sql,
                "description": (
                    f"ALTER TABLE {target_schema}.{table_name} "
                    f"ADD COLUMN {col_name} {data_type}"
                ),
            })

    # ── 3. ALTER TABLE ADD CONSTRAINT FOREIGN KEY for approved FKs ───
    for fk in approved_fks:
        if not fk.get("user_confirmed", False):
            continue

        source_table = fk.get("source_table")
        source_column = fk.get("source_column")
        target_table = fk.get("target_table")
        target_column = fk.get("target_column", "id")

        if not source_table or not source_column or not target_table:
            continue

        constraint_name = f"fk_{source_table}_{source_column}_{target_table}"
        sql = (
            f"ALTER TABLE {target_schema}.{source_table} "
            f"ADD CONSTRAINT IF NOT EXISTS {constraint_name} "
            f"FOREIGN KEY ({source_column}) "
            f"REFERENCES {target_schema}.{target_table}({target_column});"
        )
        ddl.append({
            "sql": sql,
            "description": (
                f"FK: {target_schema}.{source_table}.{source_column} → "
                f"{target_schema}.{target_table}.{target_column}"
            ),
        })

    return ddl


def _is_fiix_source(state: SchemaMappingState) -> bool:
    """True when this schema-mapping run originated from the Fiix connector."""
    src = (state.get("external_schema_source") or "").lower()
    cmms = (state.get("external_cmms_name") or "").lower()
    return src in ("fiix_api", "fiix_live", "fiix") or cmms == "fiix"


async def _relax_fiix_target_constraints(
    db_session: AsyncSession,
    schema: str,
    tables: list[str],
) -> tuple[int, int]:
    """
    Make the migration clone a permissive staging copy for Fiix data.

    The clone is created with LIKE ... INCLUDING ALL, so it inherits the canonical
    NOT NULL and UNIQUE constraints. Fiix's external rows rarely satisfy the
    platform's *operational* invariants (organization_id, category_name, title,
    part_id, unique codes), so on the Fiix ingestion target tables we:
      - DROP NOT NULL on every non-primary-key column, and
      - DROP every non-PK UNIQUE constraint
    Non-fatal: any failure is logged and skipped. Returns (cols_relaxed, uniques_dropped).
    """
    relaxed_cols = 0
    dropped_uniques = 0
    for table in tables:
        try:
            pk = await db_session.execute(
                text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = (:rel)::regclass AND i.indisprimary"
                ),
                {"rel": f"{schema}.{table}"},
            )
            pk_cols = {r[0] for r in pk.fetchall()}

            nn = await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table AND is_nullable = 'NO'"
                ),
                {"schema": schema, "table": table},
            )
            for (col,) in nn.fetchall():
                if col in pk_cols:
                    continue  # PK columns are NOT NULL by definition — leave them
                await db_session.execute(
                    text(f'ALTER TABLE {schema}."{table}" ALTER COLUMN "{col}" DROP NOT NULL')
                )
                relaxed_cols += 1

            uq = await db_session.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = (:rel)::regclass AND contype = 'u'"
                ),
                {"rel": f"{schema}.{table}"},
            )
            for (cname,) in uq.fetchall():
                await db_session.execute(
                    text(f'ALTER TABLE {schema}."{table}" DROP CONSTRAINT IF EXISTS "{cname}"')
                )
                dropped_uniques += 1
        except Exception as exc:  # noqa: BLE001 — relaxing is best-effort
            logger.warning(f"[Node 8] Relax constraints failed for {schema}.{table}: {exc}")

    try:
        await db_session.commit()
    except Exception as exc:
        logger.warning(f"[Node 8] Relax-constraints commit failed (non-fatal): {exc}")
        try:
            await db_session.rollback()
        except Exception:
            pass

    logger.info(
        f"[Node 8] Relaxed Fiix target constraints: {relaxed_cols} NOT NULL dropped, "
        f"{dropped_uniques} unique constraints dropped across {len(tables)} tables"
    )
    return relaxed_cols, dropped_uniques


async def _maybe_trigger_fiix_ingestion(
    state: SchemaMappingState,
    new_schema: str,
) -> str | None:
    """
    After a Fiix schema-mapping run completes, kick off the Fiix data ingestion
    so the freshly created schema is populated with real Fiix records (rows).

    The schema-write node only clones the canonical *structure* (and any existing
    plenum_cafm seed rows) — it does NOT pull Fiix data. Without this trigger the
    new schema's tables stay empty of Fiix records. Mirrors POST /api/fiix-ingestion.

    Returns the new FiixIngestionJob id (str) or None if not triggered.
    """
    from ...config import get_settings

    if not _is_fiix_source(state):
        return None

    settings = get_settings()
    if not getattr(settings, "fiix_auto_ingest", True):
        logger.info("[Node 8] FIIX_AUTO_INGEST disabled — skipping automatic data pull")
        return None

    schema_mapping_id = state.get("schema_mapping_id")
    created_by = state.get("created_by") or "auto:schema_mapping"

    try:
        from uuid import uuid4 as _uuid4
        from sqlalchemy import select as _select
        from ...models.migration import FiixIngestionJob, SchemaMappingJob

        session_factory = get_async_session_factory()

        # Resolve organization from the schema-mapping job (best-effort).
        org_uuid = None
        async with session_factory() as _s:
            if schema_mapping_id:
                _r = await _s.execute(
                    _select(SchemaMappingJob).where(
                        SchemaMappingJob.id == UUID(schema_mapping_id)
                    )
                )
                _sm = _r.scalar_one_or_none()
                if _sm and _sm.organization_id:
                    org_uuid = _sm.organization_id
            if org_uuid is None:
                org_uuid = UUID("00000000-0000-0000-0000-000000000000")

            ingestion_id = _uuid4()
            _s.add(
                FiixIngestionJob(
                    id=ingestion_id,
                    organization_id=org_uuid,
                    created_by=created_by,
                    status="pending",
                    progress_pct=0.0,
                )
            )
            await _s.commit()

        # Enqueue via ARQ; fall back to an inline asyncio task (dev/no-worker mode).
        try:
            from arq.connections import create_pool, RedisSettings as _ArqRedis
            _arq = await create_pool(
                _ArqRedis.from_dsn(settings.redis_url or "redis://localhost:6379")
            )
            await _arq.enqueue_job(
                "run_fiix_data_ingestion",
                ingestion_id=str(ingestion_id),
                organization_id=str(org_uuid),
                created_by=created_by,
                schema_mapping_id=schema_mapping_id,
                target_schema=new_schema,
            )
            await _arq.aclose()
        except Exception as _arq_exc:
            logger.warning(
                f"[Node 8] ARQ enqueue failed, running Fiix ingestion inline: {_arq_exc}"
            )
            import asyncio as _aio
            from ...worker import run_fiix_data_ingestion as _run_ingest

            async def _inline():
                await _run_ingest(
                    None, str(ingestion_id), str(org_uuid), created_by,
                    schema_mapping_id, new_schema,
                )

            _aio.create_task(_inline())

        logger.info(
            f"[Node 8] ✓ Auto-triggered Fiix data ingestion {ingestion_id} "
            f"→ schema '{new_schema}' (schema_mapping_id={schema_mapping_id})"
        )
        return str(ingestion_id)

    except Exception as _exc:
        # Never fail the schema-mapping run because the data pull couldn't be queued.
        logger.exception(f"[Node 8] Failed to auto-trigger Fiix ingestion (non-fatal): {_exc}")
        return None


# ── Main node ─────────────────────────────────────────────────────────────────

async def schema_write_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 8: Clone plenum_cafm into a new schema, apply mapping DDL, write audit records.

    Instead of mutating the live plenum_cafm schema, this node:
      1. Creates a brand-new PostgreSQL schema  (plenum_cafm_<cmms>_<timestamp>)
      2. Clones every table from plenum_cafm into the new schema (structure + data)
      3. Applies custom column additions and new tables to the new schema only
      4. Applies approved FK constraints within the new schema
      5. Writes audit field-mapping records and marks the job complete

    Phase 1: Schema creation + clone + DDL (single transaction — full rollback on failure)
    Phase 2: Audit records (only runs if Phase 1 succeeded)
    """

    _node_started_at = datetime.utcnow()
    schema_mapping_id = state.get("schema_mapping_id")
    db_session: AsyncSession = state.get("db_session")
    extra_fields_config: list[ExtraFieldConfig] = state.get("extra_fields_config", [])
    detected_fks = state.get("detected_foreign_keys", [])
    canonical_tables = state.get("canonical_tables", {})
    external_cmms_name = state.get("external_cmms_name", "unknown")

    logger.info(f"[Node 10] Writing schema: mapping_id={schema_mapping_id}")

    # Ensure we have a DB session
    if not db_session:
        logger.warning("[Node 10] No db_session in state — opening fallback session")
        db_session = get_async_session_factory()()

    # ── Phase 1: Create new schema + clone + apply DDL ───────────────
    # Use schema name provided by the user (from artifacts review gate) or auto-generate
    _preset_name = (state.get("new_schema_name") or "").strip()
    if _preset_name:
        new_schema = _preset_name
        logger.info(f"[Node 10] Using user-provided schema name: {new_schema}")
    else:
        new_schema = _make_new_schema_name(external_cmms_name)
        logger.info(f"[Node 10] Auto-generated schema name: {new_schema}")

    approved_fks = [fk for fk in detected_fks if fk.get("user_confirmed", False)]
    existing_canonical_tables = set(canonical_tables.keys())

    # Build custom DDL targeting the new schema
    custom_ddl = _build_ddl_statements(
        extra_fields_config, approved_fks, existing_canonical_tables,
        target_schema=new_schema,
    )

    custom_count = sum(1 for e in extra_fields_config if e.get("storage_strategy") == "custom")
    new_table_count = sum(1 for e in extra_fields_config if e.get("is_new_table"))
    logger.info(
        f"[Node 10] Plan: new_schema={new_schema}, "
        f"clone {len(existing_canonical_tables)} tables, "
        f"{len(custom_ddl)} custom DDL statements "
        f"({custom_count} cols, {new_table_count} new tables, {len(approved_fks)} FKs)"
    )

    # Pre-fetch insertable columns (excludes GENERATED ALWAYS columns like downtime_minutes).
    # SELECT * fails on tables with generated columns; we need explicit column lists.
    insertable_cols: dict[str, list[str]] = {}
    try:
        _tables_list = sorted(existing_canonical_tables)
        _col_result = await db_session.execute(
            text(
                "SELECT table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema "
                "  AND table_name = ANY(:tables) "
                "  AND is_generated != 'ALWAYS' "
                "ORDER BY table_name, ordinal_position"
            ),
            {"schema": _SOURCE_SCHEMA, "tables": _tables_list},
        )
        for _row in _col_result.fetchall():
            insertable_cols.setdefault(_row[0], []).append(_row[1])
        logger.info(f"[Node 10] Column info pre-fetched for {len(insertable_cols)} tables")
    except Exception as _col_exc:
        logger.warning(f"[Node 10] Column pre-fetch failed — will use SELECT * (may fail on generated cols): {_col_exc}")

    executed_descs: list[str] = []
    all_statements: list[dict] = []

    # 1. CREATE SCHEMA
    all_statements.append({
        "sql": f"CREATE SCHEMA IF NOT EXISTS {new_schema};",
        "description": f"CREATE SCHEMA {new_schema}",
    })

    # 2. Clone every canonical table (structure first, data second)
    for table_name in sorted(existing_canonical_tables):
        all_statements.append({
            "sql": (
                f"CREATE TABLE IF NOT EXISTS {new_schema}.{table_name} "
                f"(LIKE {_SOURCE_SCHEMA}.{table_name} INCLUDING ALL);"
            ),
            "description": f"CLONE structure: {_SOURCE_SCHEMA}.{table_name} → {new_schema}.{table_name}",
        })
    for table_name in sorted(existing_canonical_tables):
        cols = insertable_cols.get(table_name)
        if cols:
            # Explicit column list skips GENERATED ALWAYS columns
            col_list = ", ".join(f'"{c}"' for c in cols)
            insert_sql = (
                f"INSERT INTO {new_schema}.{table_name} ({col_list}) "
                f"SELECT {col_list} FROM {_SOURCE_SCHEMA}.{table_name};"
            )
        else:
            insert_sql = (
                f"INSERT INTO {new_schema}.{table_name} "
                f"SELECT * FROM {_SOURCE_SCHEMA}.{table_name};"
            )
        all_statements.append({
            "sql": insert_sql,
            "description": f"COPY data: {_SOURCE_SCHEMA}.{table_name} → {new_schema}.{table_name}",
        })

    # 3. Custom DDL (new tables, ADD COLUMN, FK constraints) on the new schema
    all_statements.extend(custom_ddl)

    try:
        for stmt in all_statements:
            sql = stmt["sql"]
            desc = stmt["description"]
            logger.info(f"[Node 10] Executing: {desc}")
            await db_session.execute(text(sql))
            executed_descs.append(desc)

        await db_session.commit()
        logger.info(
            f"[Node 10] ✓ Schema created and DDL committed "
            f"({len(executed_descs)} statements, new_schema={new_schema})"
        )
        state["new_schema_name"] = new_schema

    except Exception as ddl_exc:
        try:
            await db_session.rollback()
        except Exception:
            pass

        failed_idx = len(executed_descs)
        failed_desc = (
            all_statements[failed_idx]["description"]
            if failed_idx < len(all_statements)
            else "unknown"
        )
        error_detail = (
            f"DDL execution failed at statement {failed_idx + 1}/{len(all_statements)}: "
            f"'{failed_desc}'. "
            f"Database error: {str(ddl_exc)[:300]}. "
            f"All {len(executed_descs)} previously executed statements were rolled back."
        )
        logger.error(f"[Node 10] DDL ROLLBACK: {error_detail}")

        state["status"] = "ddl_failed"
        state["error_message"] = error_detail
        state["error_node"] = 8

        try:
            from .schema_db_writer import schema_write_error
            await schema_write_error(
                db_session, schema_mapping_id, error_detail,
                error_node=8, status="ddl_failed"
            )
        except Exception:
            pass

        return state

    # ── Phase 2: Audit Records ────────────────────────────────────────
    # Only reaches here if Phase 1 succeeded (or had nothing to do).
    try:
        tier1_mappings = state.get("tier1_mappings", [])
        tier2_auto = state.get("tier2_auto_mapped", [])
        tier2_flagged = state.get("tier2_flagged", [])
        tier2_unmappable = state.get("tier2_unmappable", [])

        all_mappings: list[dict[str, Any]] = []

        # Tier 1 mappings
        for mapping in tier1_mappings:
            source_field = mapping.get("source_field")
            if not source_field:
                continue
            all_mappings.append({
                "schema_mapping_id": UUID(schema_mapping_id),
                "source_field": source_field,
                "source_table": mapping.get("source_table", "unknown"),
                "target_field": mapping.get("target_field"),
                "confidence": mapping.get("confidence", 0.0),
                "tier": mapping.get("tier", "T1_deterministic"),
                "rationale": mapping.get("rationale", ""),
                "mapped_at": datetime.utcnow(),
            })

        # Tier 2 auto-mapped
        for mapping in tier2_auto:
            source_field = mapping.get("source_field")
            if not source_field:
                continue
            all_mappings.append({
                "schema_mapping_id": UUID(schema_mapping_id),
                "source_field": source_field,
                "source_table": mapping.get("source_table", "unknown"),
                "target_field": mapping.get("target_field"),
                "confidence": mapping.get("confidence", 0.0),
                "tier": "T2_semantic_auto",
                "rationale": mapping.get("rationale", ""),
                "mapped_at": datetime.utcnow(),
            })

        # Tier 2 flagged
        for mapping in tier2_flagged:
            source_field = mapping.get("source_field")
            if not source_field:
                continue
            all_mappings.append({
                "schema_mapping_id": UUID(schema_mapping_id),
                "source_field": source_field,
                "source_table": mapping.get("source_table", "unknown"),
                "target_field": mapping.get("target_field", "UNMAPPED"),
                "confidence": mapping.get("confidence", 0.0),
                "tier": "T2_semantic_flagged",
                "rationale": mapping.get("rationale", ""),
                "mapped_at": datetime.utcnow(),
            })

        # Unmapped fields — record their storage strategy as rationale
        for field_info in tier2_unmappable:
            source_field = field_info.get("field_name") or field_info.get("source_field")
            if not source_field:
                continue

            # Find DDL decision for this field (if any)
            ddl_decision = next(
                (e for e in extra_fields_config if e.get("source_field") == source_field),
                None,
            )
            strategy = ddl_decision.get("storage_strategy", "unmapped") if ddl_decision else "unmapped"
            if strategy == "custom" and ddl_decision:
                target_field = (
                    f"NEW_COLUMN:{ddl_decision.get('target_table')}."
                    f"{ddl_decision.get('custom_column_name')}"
                )
                rationale = (
                    f"Custom DDL: {ddl_decision.get('data_type')} column "
                    f"added to {new_schema}.{ddl_decision.get('target_table')}"
                )
            elif strategy == "raw_metadata":
                target_field = "RAW_METADATA"
                rationale = "Stored in raw_metadata JSONB column"
            elif strategy == "skip":
                target_field = "SKIPPED"
                rationale = "User chose to discard this field"
            else:
                target_field = "UNMAPPED"
                rationale = "No confident mapping found; no custom strategy assigned"

            all_mappings.append({
                "schema_mapping_id": UUID(schema_mapping_id),
                "source_field": source_field,
                "source_table": field_info.get("source_table", "unknown"),
                "target_field": target_field,
                "confidence": 0.0 if strategy in ("unmapped", "skip") else 1.0,
                "tier": strategy,
                "rationale": rationale,
                "mapped_at": datetime.utcnow(),
            })

        if all_mappings and db_session:
            from ...models.migration import SchemaMappingFieldMapping
            for mapping_data in all_mappings:
                db_session.add(SchemaMappingFieldMapping(**mapping_data))
            await db_session.commit()
            logger.info(f"[Node 10] ✓ Wrote {len(all_mappings)} audit records")
        elif all_mappings:
            logger.warning("[Node 10] No db_session — audit records not persisted")

        # ── Update registry — only after confirmed DB write ───────────
        # Promote semantic auto-maps and human-approved/overridden mappings
        # into the learned registry so future runs can skip the LLM.
        external_cmms_name = state.get("external_cmms_name", "Unknown")
        registry_tiers = {"T2_semantic", "T2_semantic_auto", "T1_human_approved", "T1_human_override"}
        registry_updated = 0
        for m in all_mappings:
            tier = m.get("tier", "")
            if tier not in registry_tiers:
                continue
            source_field = m.get("source_field")
            target_field = m.get("target_field")
            if not source_field or not target_field:
                continue
            # Skip non-canonical targets (unmapped / raw_metadata / skipped)
            if target_field.startswith(("UNMAPPED", "RAW_METADATA", "SKIPPED", "NEW_COLUMN:")):
                continue
            approved_by = "human" if "human" in tier else "auto"
            try:
                await registry_append(
                    alias=source_field,
                    canonical=target_field,
                    source_cmms=external_cmms_name,
                    confidence=m.get("confidence", 0.85),
                    approved_by=approved_by,
                    migration_id=str(schema_mapping_id),
                )
                registry_updated += 1
            except Exception as _reg_err:
                logger.debug(f"[Node 10] Registry append failed for {source_field} (non-fatal): {_reg_err}")

        if registry_updated:
            logger.info(f"[Node 10] ✓ Registry updated: {registry_updated} entries added")

        # ── Auto-pull Fiix records into the new schema (Fiix sources only) ──
        # The DDL above only clones canonical *structure* — it does not fetch any
        # Fiix rows. For Fiix runs, first relax the clone's operational constraints
        # (so external rows land as a faithful staging copy), then kick off the
        # 3-node data-ingestion pipeline.
        if _is_fiix_source(state) and db_session:
            try:
                from ...connectors.fiix_data_connector import OBJECT_TABLE_MAP as _FIIX_MAP
                _fiix_tables = sorted(
                    set(_FIIX_MAP.values()) & set(canonical_tables.keys())
                )
                if _fiix_tables:
                    await _relax_fiix_target_constraints(db_session, new_schema, _fiix_tables)
            except Exception as _relax_exc:
                logger.warning(f"[Node 8] Constraint relaxation skipped (non-fatal): {_relax_exc}")

        fiix_ingestion_id = await _maybe_trigger_fiix_ingestion(state, new_schema)
        if fiix_ingestion_id:
            state["fiix_ingestion_id"] = fiix_ingestion_id

        state["notes"] = state.get("notes", []) + [
            f"Node 8: new_schema={new_schema}, "
            f"{len(all_statements)} DDL statements executed, "
            f"{len(all_mappings)} audit records written"
            + (f", Fiix data ingestion {fiix_ingestion_id} started" if fiix_ingestion_id else "")
        ]
        state["status"] = "complete"

        if db_session and schema_mapping_id:
            import json as _json
            from .schema_db_writer import schema_update_node_progress
            _skip_keys = {"db_session"}
            _state_raw = {k: v for k, v in state.items() if k not in _skip_keys}
            try:
                _state_snapshot = _json.loads(_json.dumps(_state_raw, default=str))
            except Exception:
                _state_snapshot = None

            _fmc = state.get("final_mapping_config")
            try:
                _fmc_safe = _json.loads(_json.dumps(_fmc, default=str)) if _fmc else None
            except Exception:
                _fmc_safe = None

            _summary = state.get("final_summary") if isinstance(state.get("final_summary"), dict) else {}
            _audit = (_fmc or {}).get("audit") if isinstance(_fmc, dict) else {}
            if not isinstance(_audit, dict):
                _audit = {}

            await schema_update_node_progress(
                db_session, schema_mapping_id, 10,
                status="complete",
                progress_pct=100.0,
                total_tables=_summary.get("canonical_tables_touched")
                or len((_fmc or {}).get("tables", {}) if isinstance(_fmc, dict) else {}),
                total_fields=_summary.get("total_source_fields") or _audit.get("total_source_fields"),
                tier1_mapped=_summary.get("tier1_auto_mapped") or _audit.get("tier1_mapped"),
                tier2_auto_mapped=_summary.get("tier2_auto_mapped"),
                tier2_flagged=_summary.get("tier2_flagged"),
                unmapped=_summary.get("unmappable")
                or sum(1 for m in all_mappings if m["target_field"] in ("UNMAPPED", "SKIPPED")),
                detected_fk_count=_summary.get("detected_fk_count"),
                hierarchy_depth=_summary.get("max_hierarchy_depth"),
                mapping_coverage_pct=_summary.get("mapping_coverage_pct")
                or _audit.get("mapping_coverage_pct"),
                final_summary=_summary or None,
                node_state_json=_state_snapshot,
                final_mapping_config=_fmc_safe,
                new_schema_name=new_schema,
            )

        # Write node log entry so the frontend nodes[] array shows it as complete
        if schema_mapping_id:
            try:
                from .schema_db_writer import schema_append_node_log_auto
                _custom_count = sum(1 for e in extra_fields_config if e.get("storage_strategy") == "custom")
                await schema_append_node_log_auto(
                    schema_mapping_id, 10, "Write to Database",
                    _node_started_at, datetime.utcnow(),
                    output={
                        "new_schema_name": new_schema,
                        "ddl_statements_executed": len(all_statements),
                        "audit_records_written": len(all_mappings),
                        "custom_columns": _custom_count,
                        "fiix_ingestion_id": state.get("fiix_ingestion_id"),
                    },
                    logs=[
                        f"[Node 10] Created schema: {new_schema}",
                        f"[Node 10] Executed {len(all_statements)} DDL statements",
                        f"[Node 10] Wrote {len(all_mappings)} audit field-mapping records",
                    ] + (
                        [f"[Node 10] Started Fiix data ingestion {state.get('fiix_ingestion_id')} → {new_schema}"]
                        if state.get("fiix_ingestion_id") else []
                    ),
                )
            except Exception as _log_exc:
                logger.warning(f"[Node 10] Failed to write node log (non-fatal): {_log_exc}")

        logger.info(f"[Node 10] ✓ Schema mapping complete for {schema_mapping_id}")
        return state

    except Exception as e:
        logger.exception(f"[Node 10] ✗ Audit record write failed: {e}")
        state["status"] = "error"
        state["error_message"] = f"Audit records failed (DDL was committed): {str(e)}"
        if db_session:
            try:
                await db_session.rollback()
            except Exception:
                pass
        return state
