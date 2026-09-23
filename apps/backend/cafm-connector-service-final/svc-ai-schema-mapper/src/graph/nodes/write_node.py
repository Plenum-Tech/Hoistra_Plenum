"""Node 9: Write to platform — GATE 3 HITL and final DB write (MULTI-TABLE).

Final gate before handoff:
1. Present IntermediateSchema summary for customer approval
2. Wait for GATE 3 approval
3. Apply generated SQL artifact directly to target DB
4. (Fallback) POST IntermediateSchema to svc-ingestion/api/ingest if SQL absent
4. Update migration_jobs table with completion status and output URLs
5. Mark migration complete
6. EL-M.9: IntermediateSchema validated + customer confirmed

All per-table data is embedded in the IntermediateSchema sent to svc-ingestion.
"""

import asyncio
import logging
from datetime import datetime
from datetime import date as _date
from typing import Optional
from uuid import UUID as _UUID
import uuid
import re
from decimal import Decimal

import aiohttp
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..state import ExtraFieldConfig, MigrationState
from ..event_enrich import append_event
from .building_link import (
    ASSET_BUILDING_SQL, ASSET_LOOKUP_SQL, BuildingResolver, asset_match_code,
    build_asset_merge_update, building_hint, looks_like_uuid, site_names_from_run,
)
from .meter_link import (
    CREATE_METER_SQL, SECTION_LOOKUP_SQL, MeterResolver, is_sub_meter_for, meter_hint,
    meter_type_for, section_hint, supply_numbers,
)
from .reference_link import REFERENCES, ReferenceResolver, hint_for
from ...models.migration import MigrationJob
from ...db import get_async_session_factory

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# plenum_cafm schema prefix used in all DDL
_SCHEMA = "plenum_cafm"

#: Tables whose rows carry a building_id that can be resolved from a site reference.
#:
#: energy_meters joined this list on 22 Sep 2026. It went through the same writer and came out
#: unlinked, and unlike a missing asset link that failure is silent: energy_meters.building_id
#: is a plain uuid with no constraint behind it, so the row inserts, the anomaly scan sweeps
#: the meter, and every finding it raises is written with a null building. Those findings are
#: invisible on a building-scoped page and no EUI snapshot is produced at all, so the scan
#: reports success over an empty screen.
_BUILDING_LINKED_TABLES = ("assets", "work_orders", "energy_meters")

#: Tables whose rows are READ for a building reference. meter_readings does not carry a
#: building column of its own, but a reading sheet often names the site, and that is what
#: decides whether a meter can be created for it.
_BUILDING_HINT_TABLES = ("assets", "work_orders", "energy_meters", "meter_readings")

#: Tables that can take their building from the asset they name, when they name no site.
_BUILDING_VIA_ASSET_TABLES = ("work_orders", "energy_meters")

#: Tables whose rows carry references that only the schema-aligned path can resolve: a
#: building from a site name, a meter from a supply number.
#:
#: The primary write path applies a generated SQL artifact of literal values with no
#: resolution of any kind, and the aligned path was only ever reached by that one throwing
#: first. For these tables that is the difference between rows that link and rows that do
#: not, so the choice is made deliberately rather than left to whether an INSERT happens
#: to fail.
_NEEDS_RESOLUTION = frozenset({"assets", "work_orders", "energy_meters", "meter_readings"})
_SAFE_SQL_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Core plenum_cafm tables whose schema is managed by ORM migrations.
# The fallback write path must NEVER ALTER TABLE these — only filter to
# their existing columns.
_KNOWN_CORE_TABLES = frozenset({
    "assets", "work_orders", "spare_parts", "locations", "organizations",
    "users", "technicians", "vendors", "asset_categories", "maintenance_plans",
    "scheduled_maintenance", "work_order_tasks", "work_order_comments",
    "asset_readings", "asset_documents", "inventory_transactions",
})


# ── DDL Generation ────────────────────────────────────────────────────────────

def _infer_sql_type(sample_values: list) -> str:
    """Best-effort SQL type inference from sample values. Defaults to TEXT."""
    non_null = [v for v in (sample_values or []) if v is not None]
    if not non_null:
        return "TEXT"
    try:
        all(int(str(v)) for v in non_null[:5])
        return "INTEGER"
    except (ValueError, TypeError):
        pass
    try:
        all(float(str(v)) for v in non_null[:5])
        return "NUMERIC"
    except (ValueError, TypeError):
        pass
    return "TEXT"


def _build_migration_ddl_statements(
    extra_fields_config: list[ExtraFieldConfig],
    all_mappings_by_source_table: Optional[dict[str, list[dict]]] = None,
) -> list[dict]:
    """
    Generate DDL statements from Node 4 DDL intent decisions.

    For new tables, ALL confirmed T1+T2+human-mapped columns from the same source
    table are included in the CREATE TABLE — not just the explicitly-declared custom
    columns. This ensures the new table is complete.

    Returns a list of {"sql": "...", "description": "..."}.

    Order:
      1. CREATE TABLE for new tables (must come before ALTER TABLE on those tables)
      2. ALTER TABLE ADD COLUMN for existing tables
    """
    ddl: list[dict] = []

    # Group custom fields by target_table
    new_tables: dict[str, list[ExtraFieldConfig]] = {}
    existing_table_cols: dict[str, list[ExtraFieldConfig]] = {}

    for entry in extra_fields_config:
        if entry.get("storage_strategy") != "custom":
            continue  # raw_metadata and skip need no DDL

        target_table = entry.get("target_table")
        if not target_table:
            continue

        if entry.get("is_new_table", False):
            # Defensive: sanitize the new-table name to a safe, snake_cased unquoted
            # identifier even if an upstream caller passed a raw sheet name
            # ("Work Orders" → work_orders, "Distance (km)" → distance_km). This keeps
            # the CREATE TABLE DDL consistent with the DML sync path (which already
            # normalizes via _to_safe_identifier). Existing tables carry a real
            # canonical name, so they're left exactly as-is.
            safe_target = _to_safe_identifier(target_table) or target_table
            new_tables.setdefault(safe_target, []).append(entry)
        else:
            existing_table_cols.setdefault(target_table, []).append(entry)

    # ── 1. CREATE TABLE for brand-new tables ─────────────────────────
    for table_name, columns in new_tables.items():
        pk_col = columns[0].get("new_table_pk", "id") or "id"
        pk_lower = pk_col.lower()
        source_table = columns[0].get("source_table", "")  # source sheet that maps to this new table
        _source_mappings = (all_mappings_by_source_table or {}).get(source_table, [])

        # If a mapped source column targets the PK name (e.g. asset_id → id), that column
        # IS the business key used for PK/FK relationships: emit it AS the primary key with
        # its real type instead of a synthetic UUID that would clash. Same for a custom PK.
        _pk_mapping = next(
            (m for m in _source_mappings if (m.get("target_field") or "").lower() == pk_lower),
            None,
        )
        _pk_custom = next(
            (c for c in columns if (c.get("custom_column_name") or "").lower() == pk_lower),
            None,
        )
        if _pk_mapping is not None:
            col_defs = [
                f"    {pk_col} {_infer_sql_type(_pk_mapping.get('sample_values') or [])} PRIMARY KEY"
            ]
        elif _pk_custom is not None:
            col_defs = [f"    {pk_col} {_pk_custom.get('data_type', 'TEXT')} PRIMARY KEY"]
        else:
            col_defs = [f"    {pk_col} UUID PRIMARY KEY DEFAULT gen_random_uuid()"]
        # Case-insensitive collision guard; created_at/updated_at are appended below,
        # so reserve them too (a source column of the same name must not be re-emitted).
        seen_lower: set[str] = {pk_lower, "created_at", "updated_at"}
        total_data_cols = 0

        # Include ALL T1+T2+human-mapped columns from the same source sheet.
        # These have already been mapped to canonical field names, so we use
        # their target_field values as the column names in the new table.
        if _source_mappings:
            for mapping in _source_mappings:
                target_field = mapping.get("target_field", "")
                if not target_field or target_field.lower() in seen_lower:
                    continue
                # Prefer a user-chosen SQL type (new-table data-type dropdown);
                # otherwise infer it from the sampled values.
                sql_type = (mapping.get("data_type") or "").strip() or _infer_sql_type(
                    mapping.get("sample_values") or []
                )
                col_defs.append(f"    {target_field} {sql_type}")
                seen_lower.add(target_field.lower())
                total_data_cols += 1

        # Add explicitly-declared custom columns (user-named via DDL intent)
        custom_col_count = 0
        for col in columns:
            col_name = col.get("custom_column_name")
            if not col_name or col_name.lower() in seen_lower:
                continue
            data_type = col.get("data_type", "TEXT")
            nullable_clause = "" if col.get("nullable", True) else " NOT NULL"
            col_defs.append(f"    {col_name} {data_type}{nullable_clause}")
            seen_lower.add(col_name.lower())
            custom_col_count += 1
            total_data_cols += 1

        col_defs.append("    created_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        col_defs.append("    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")

        col_block = ",\n".join(col_defs)
        sql = (
            f"CREATE TABLE IF NOT EXISTS {_SCHEMA}.{table_name} (\n"
            f"{col_block}\n"
            f");"
        )
        ddl.append({
            "sql": sql,
            "description": (
                f"CREATE TABLE {_SCHEMA}.{table_name} "
                f"({total_data_cols} columns: mapped + {custom_col_count} custom + PK + timestamps)"
            ),
        })

    # ── 2. ALTER TABLE ADD COLUMN for existing tables ─────────────────
    for table_name, columns in existing_table_cols.items():
        for col in columns:
            col_name = col.get("custom_column_name")
            data_type = col.get("data_type", "TEXT")
            nullable_clause = "" if col.get("nullable", True) else " NOT NULL"
            sql = (
                f"ALTER TABLE {_SCHEMA}.{table_name} "
                f"ADD COLUMN IF NOT EXISTS {col_name} {data_type}{nullable_clause};"
            )
            ddl.append({
                "sql": sql,
                "description": (
                    f"ALTER TABLE {_SCHEMA}.{table_name} "
                    f"ADD COLUMN {col_name} {data_type}"
                ),
            })

    return ddl


async def write_node(state: MigrationState) -> MigrationState:
    """
    Node 9: GATE 3 HITL and final handoff to svc-ingestion.

    Entry conditions:
    - intermediate_schema is built and validated (EL-M.8 passed)

    Execution:
    1. Prepare final summary for customer approval
    2. Call interrupt() to pause for GATE 3
    3. On resume: POST IntermediateSchema to svc-ingestion
    4. Mark migration as complete
    5. EL-M.9: Validate response from svc-ingestion
    """

    migration_id = state.get("migration_id")
    intermediate_schema = state.get("intermediate_schema")
    db_session = state.get("db_session")
    extra_fields_config: list[ExtraFieldConfig] = state.get("extra_fields_config", [])

    logger.info(f"[Node 9] Starting: migration_id={migration_id}")

    # ── Phase 0: DDL Execution (before GATE 3) ───────────────────────────
    # Execute DDL for any custom fields decided at Node 4 (GATE 1).
    # All statements run in a single transaction — full rollback on ANY failure.

    # Build all confirmed mappings keyed by source table so CREATE TABLE DDL
    # can include every T1+T2+human-mapped column for new tables.
    all_mappings_by_source_table: dict[str, list[dict]] = {}
    for tbl, mappings in state.get("tier1_mappings_by_table", {}).items():
        all_mappings_by_source_table.setdefault(tbl, []).extend(
            [m if isinstance(m, dict) else dict(m) for m in mappings]
        )
    for tbl, mappings in state.get("tier2_auto_by_table", {}).items():
        all_mappings_by_source_table.setdefault(tbl, []).extend(
            [m if isinstance(m, dict) else dict(m) for m in mappings]
        )
    for tbl, mappings in state.get("tier2_human_decisions_by_table", {}).items():
        all_mappings_by_source_table.setdefault(tbl, []).extend(
            [m if isinstance(m, dict) else dict(m) for m in mappings]
        )

    # ── Safety net: a custom new-column name that collides with an existing column ──────────
    # An unmapped field offered as a "new column" can be submitted with a name that already
    # exists on the target table (e.g. vendors.id pinned to asset_id but sent as "id", the PK).
    # ADD COLUMN IF NOT EXISTS then no-ops and the data is silently dropped. When that happens,
    # fall back to the field's canonical name (column_intelligence) so the column is actually
    # created — and move the values in the cleaned records to the canonical key so they land in
    # it. Best-effort; skipped on any error. Belt-and-suspenders on top of the gate defaulting
    # the new-column name to the canonical.
    try:
        _col_canon = ((state.get("column_intelligence") or {}).get("column_canonical")) or {}
        _custom_existing = [
            e for e in extra_fields_config
            if e.get("storage_strategy") == "custom" and not e.get("is_new_table")
        ]
        if _col_canon and _custom_existing:
            from ...db import get_plenum_cafm_columns_by_table
            _cols_by_tbl = await get_plenum_cafm_columns_by_table()
            _cleaned = state.get("cleaned_tables")
            for _e in _custom_existing:
                _tgt = str(_e.get("target_table") or "").strip()
                _name = str(_e.get("custom_column_name") or "").strip()
                if not _tgt or not _name:
                    continue
                _existing_lower = {str(c).lower() for c in (_cols_by_tbl.get(_tgt.lower()) or set())}
                if _name.lower() not in _existing_lower:
                    continue  # no collision — the ADD COLUMN will create it fine
                _src_t, _src_f = _e.get("source_table"), _e.get("source_field")
                _canon = str(_col_canon.get(f"{_src_t}.{_src_f}") or "").strip()
                _canon_safe = _to_safe_identifier(_canon) if _canon else ""
                if (not _canon_safe or _canon_safe.lower() == _name.lower()
                        or _canon_safe.lower() in _existing_lower):
                    continue  # no usable canonical alternative
                logger.warning(
                    f"[Node 9] new-column '{_name}' already exists on '{_tgt}'; using canonical "
                    f"'{_canon_safe}' for {_src_t}.{_src_f} so the column is created (not dropped)"
                )
                _e["custom_column_name"] = _canon_safe
                if isinstance(_cleaned, dict) and isinstance(_cleaned.get(_src_t), list):
                    for _row in _cleaned[_src_t]:
                        if isinstance(_row, dict) and _name in _row and _canon_safe not in _row:
                            _row[_canon_safe] = _row.pop(_name)
    except Exception as _safety_exc:  # pragma: no cover — best-effort
        logger.warning(f"[Node 9] new-column collision safety net skipped: {_safety_exc}")

    ddl_statements = _build_migration_ddl_statements(
        extra_fields_config,
        all_mappings_by_source_table=all_mappings_by_source_table,
    )

    custom_count = sum(1 for e in extra_fields_config if e.get("storage_strategy") == "custom")
    new_table_count = sum(1 for e in extra_fields_config if e.get("is_new_table"))
    logger.info(
        f"[Node 9] DDL plan: {len(ddl_statements)} statements "
        f"({custom_count} custom columns, {new_table_count} new tables)"
    )

    if ddl_statements:
        if not db_session:
            logger.warning(
                "[Node 9] No db_session in state — opening fallback session for DDL execution"
            )
            db_session = get_async_session_factory()()

        executed: list[str] = []
        try:
            for stmt in ddl_statements:
                sql = stmt["sql"]
                desc = stmt["description"]
                logger.info(f"[Node 9] Executing DDL: {desc}")
                await db_session.execute(text(sql))
                executed.append(desc)

            await db_session.commit()
            logger.info(f"[Node 9] ✓ DDL transaction committed ({len(executed)} statements)")

        except Exception as ddl_exc:
            try:
                await db_session.rollback()
            except Exception:
                pass

            failed_desc = (
                ddl_statements[len(executed)]["description"]
                if len(executed) < len(ddl_statements)
                else "unknown"
            )
            error_detail = (
                f"DDL execution failed at statement {len(executed) + 1}/{len(ddl_statements)}: "
                f"'{failed_desc}'. "
                f"Database error: {str(ddl_exc)[:300]}. "
                f"All {len(executed)} previously executed statements were rolled back."
            )
            logger.error(f"[Node 9] DDL ROLLBACK: {error_detail}")

            state["status"] = "ddl_failed"
            state["error_message"] = error_detail
            state["error_node"] = 9

            if migration_id:
                try:
                    from .db_writer import write_error
                    await write_error(
                        migration_id, error_detail,
                        error_node=9, status="ddl_failed"
                    )
                except Exception:
                    pass

            return state
    else:
        logger.info("[Node 9] No DDL statements (no custom columns requested)")

    if not intermediate_schema:
        logger.error("[Node 9] No IntermediateSchema found")
        state["error_message"] = "Missing IntermediateSchema from Node 8"
        state["error_node"] = 9
        return state

    # ── Write all confirmed field mappings to migration_field_mappings ───────
    # This is the ONLY place mappings are persisted. Gates 0/1/2 only update
    # in-memory state; the DB write happens here once, after everything is confirmed.
    if migration_id:
        try:
            from ...models.migration import MigrationFieldMapping

            session_factory = get_async_session_factory()
            mapping_rows: list[MigrationFieldMapping] = []

            # Tier 1 mappings (all tables)
            for table_name, mappings in state.get("tier1_mappings_by_table", {}).items():
                for m in mappings:
                    mapping_rows.append(MigrationFieldMapping(
                        migration_id=migration_id,
                        source_field=m.get("source_field", ""),
                        target_field=m.get("target_field", ""),
                        confidence=m.get("confidence", 0.0),
                        tier=m.get("tier", "T1"),
                        rationale=m.get("rationale", ""),
                        sample_values=m.get("sample_values", {}),
                        transformation=m.get("transformation"),
                    ))

            # Tier 2 auto-accepted mappings
            for table_name, mappings in state.get("tier2_auto_by_table", {}).items():
                for m in mappings:
                    mapping_rows.append(MigrationFieldMapping(
                        migration_id=migration_id,
                        source_field=m.get("source_field", ""),
                        target_field=m.get("target_field", ""),
                        confidence=m.get("confidence", 0.0),
                        tier=m.get("tier", "T2_semantic"),
                        rationale=m.get("rationale", ""),
                        sample_values=m.get("sample_values", {}),
                        transformation=m.get("transformation"),
                    ))

            # Tier 2 human-confirmed mappings (Gate 1 decisions — in-memory only until now)
            for table_name, mappings in state.get("tier2_human_decisions_by_table", {}).items():
                for m in mappings:
                    sf = m.get("source_field") if isinstance(m, dict) else getattr(m, "source_field", "")
                    tf = m.get("target_field") if isinstance(m, dict) else getattr(m, "target_field", "")
                    conf = m.get("confidence", 0.0) if isinstance(m, dict) else getattr(m, "confidence", 0.0)
                    rat = m.get("rationale", "") if isinstance(m, dict) else getattr(m, "rationale", "")
                    rev_id = m.get("reviewer_id") if isinstance(m, dict) else getattr(m, "reviewer_id", None)
                    if rev_id and not isinstance(rev_id, _UUID):
                        try:
                            rev_id = _UUID(str(rev_id))
                        except Exception:
                            rev_id = None
                    mapping_rows.append(MigrationFieldMapping(
                        migration_id=migration_id,
                        source_field=sf,
                        target_field=tf,
                        confidence=conf,
                        tier="T2_human",
                        rationale=rat,
                        sample_values={},
                        reviewer_id=rev_id,
                    ))

            if mapping_rows:
                async with session_factory() as session:
                    session.add_all(mapping_rows)
                    await session.commit()
                logger.info(f"[Node 9] ✓ Wrote {len(mapping_rows)} field mappings to DB")
            else:
                logger.info("[Node 9] No field mappings to write")

        except Exception as map_err:
            logger.error(f"[Node 9] Failed to write field mappings (non-fatal): {map_err}")
            # Non-fatal — handoff can still proceed

    try:
        # ── Prepare GATE 3 approval payload ──────────────────────────────
        entity_counts = {}
        for entity_type, records in intermediate_schema.get("entities", {}).items():
            if records:
                entity_counts[entity_type] = len(records)

        gate3_payload = {
            "migration_id": migration_id,
            "summary": {
                "source_type": intermediate_schema.get("source_type"),
                "source_filename": intermediate_schema.get("source_filename"),
                "overall_confidence": intermediate_schema.get("confidence", {}).get("eval_score", 0),
                "entity_counts": entity_counts,
                "total_entities": sum(entity_counts.values()),
            },
            "instructions": (
                "Review the migration summary. Click CONFIRM to send to svc-ingestion or REJECT to return for corrections."
            ),
        }

        logger.info(f"[Node 9] Interrupting for GATE 3 final approval")
        for entity_type, count in entity_counts.items():
            logger.info(f"[Node 9]   {entity_type}: {count}")

        state["write_review_payload"] = gate3_payload

        # ── Write gate payload to DB so frontend can render GATE 3 UI ──
        if migration_id:
            from .db_writer import write_gate_payload
            await write_gate_payload(migration_id, "write", gate3_payload)

        # ── Interrupt for customer approval ──────────────────────────
        gate3_decision = interrupt(gate3_payload)

        # ── Clear gate payload now that we have a decision ─────────────
        if migration_id:
            from .db_writer import clear_gate_payload
            await clear_gate_payload(migration_id)

        # ── Process GATE 3 decision ──────────────────────────────────
        # Frontend sends { confirmed: true/false }; legacy format uses { action: "confirm" }
        if "confirmed" in gate3_decision:
            action = "confirm" if gate3_decision.get("confirmed") else "reject"
        else:
            action = gate3_decision.get("action", "reject")

        if action != "confirm":
            logger.warning(f"[Node 9] GATE 3 REJECTED by customer")
            state["handoff_status"] = "rejected"
            state["error_message"] = "Customer rejected handoff at GATE 3"
            state["current_step"] = 9
            state["event_log"].append({
                "timestamp": datetime.utcnow().isoformat(),
                "event": "gate3_rejected",
                "node": 9,
                "detail": "Customer rejected IntermediateSchema"
            })
            return state

        logger.info(f"[Node 9] GATE 3 CONFIRMED - proceeding with handoff")

        # ── Primary write path: apply generated SQL artifact directly ─────────
        sql_script = (state.get("output_sql_script") or "").strip()
        _routed = {str(v).lower() for v in (state.get("table_routing") or {}).values()}
        _needs = _routed & _NEEDS_RESOLUTION
        # The schema-aligned path is the ONLY one that resolves a building from a site name or
        # a meter from a supply number, so for these tables it is chosen rather than reached.
        #
        # It used to be reachable only from the SQL artifact's exception handler. Emptying the
        # artifact to force it therefore did the opposite: control fell past both writes to
        # the svc-ingestion POST below, a service this deployment does not run, and a
        # migration that had passed every gate died with "Cannot connect to host
        # svc-ingestion:8001" having written nothing.
        _use_aligned = bool(_needs) and isinstance(state.get("cleaned_tables"), dict)
        if _needs and not _use_aligned:
            logger.warning(
                "[Node 9] %s need reference resolution but no cleaned tables are on the state; "
                "falling back to the SQL artifact, which writes literals",
                ", ".join(sorted(_needs)),
            )

        if _use_aligned:
            logger.info(
                "[Node 9] %s need reference resolution - applying schema-aligned inserts",
                ", ".join(sorted(_needs)),
            )
            try:
                aligned_result = await _apply_records_with_schema_alignment(
                    cleaned_tables=state.get("cleaned_tables", {}),
                    organization_id=str(state.get("organization_id") or ""),
                    table_routing=state.get("table_routing", {}) or {},
                    approved_new_columns=_collect_approved_new_columns(state),
                    confirmed_hierarchies=state.get("confirmed_hierarchies") or [],
                    default_building_id=state.get("building_id") or None,
                )
                state["handoff_status"] = "applied_sql_aligned"
                state["svc_ingestion_response"] = {
                    "status": "applied_sql_aligned",
                    **aligned_result,
                }
                logger.info(
                    "[Node 9] Schema-aligned inserts applied: "
                    f"{aligned_result.get('rows_inserted', 0)} row(s) across "
                    f"{aligned_result.get('tables_written', 0)} table(s), "
                    f"{aligned_result.get('rows_skipped', 0)} skipped, "
                    f"{aligned_result.get('buildings_linked', 0)} building link(s), "
                    f"{aligned_result.get('meters_linked', 0)} reading(s) placed on a meter"
                )
            except Exception as aligned_exc:
                # A lost connection reaches here two ways. _insert_rows raises ConnectionLost
                # when it is the insert that died; but the first statement of the NEXT table is
                # a read of information_schema, and if the connection went during the previous
                # table that read is what fails, with PendingRollbackError, outside any handler
                # that knows what it means. Both are the same event and both must say so.
                if isinstance(aligned_exc, ConnectionLost) or _is_connection_lost(aligned_exc):
                    logger.error(
                        f"[Node 9] The database connection closed mid-write: {aligned_exc}"
                    )
                    state["error_message"] = (
                        "The database connection closed part way through the write, so the run "
                        "stopped. Nothing partial was kept. This is the connection, not the "
                        f"file — try the same upload again. ({str(aligned_exc)[:160]})"
                    )
                    state["error_node"] = 9
                    state["el_m9_passed"] = False
                    return state
                logger.exception(f"[Node 9] Schema-aligned inserts failed: {aligned_exc}")
                state["error_message"] = f"Schema-aligned write failed: {str(aligned_exc)[:300]}"
                state["error_node"] = 9
                state["el_m9_passed"] = False
                return state
        elif sql_script:
            logger.info("[Node 9] Applying output SQL artifact directly to target DB")
            try:
                sql_apply_result = await _apply_sql_artifact(sql_script)
                state["handoff_status"] = "applied_sql"
                state["svc_ingestion_response"] = {
                    "status": "applied_sql",
                    **sql_apply_result,
                }
                logger.info(
                    "[Node 9] ✓ SQL artifact applied: "
                    f"{sql_apply_result['statement_count']} statement(s)"
                )
            except Exception as e:
                # Primary SQL artifact uses schema-mapper canonical names which may
                # not match the target DB schema. Always fall back to schema-aligned
                # inserts from cleaned_tables when they are available.
                logger.warning(
                    f"[Node 9] SQL artifact failed ({type(e).__name__}: "
                    f"{str(e)[:150]}); trying schema-aligned inserts"
                )
                if isinstance(state.get("cleaned_tables"), dict):
                    try:
                        aligned_result = await _apply_records_with_schema_alignment(
                            cleaned_tables=state.get("cleaned_tables", {}),
                            organization_id=str(state.get("organization_id") or ""),
                            table_routing=state.get("table_routing", {}) or {},
                            approved_new_columns=_collect_approved_new_columns(state),
                            confirmed_hierarchies=state.get("confirmed_hierarchies") or [],
                            default_building_id=state.get("building_id") or None,
                        )
                        state["handoff_status"] = "applied_sql_aligned"
                        state["svc_ingestion_response"] = {
                            "status": "applied_sql_aligned",
                            **aligned_result,
                        }
                        logger.info(
                            "[Node 9] ✓ Schema-aligned inserts applied: "
                            f"{aligned_result.get('rows_inserted', 0)} row(s) across "
                            f"{aligned_result.get('tables_written', 0)} table(s), "
                            f"{aligned_result.get('rows_skipped', 0)} skipped, "
                            f"{aligned_result.get('rows_merged', 0)} merged, "
                            f"{aligned_result.get('buildings_linked', 0)} building link(s)"
                        )
                    except Exception as aligned_exc:
                        logger.exception(
                            f"[Node 9] Schema-aligned fallback also failed: {aligned_exc}"
                        )
                        state["error_message"] = (
                            f"SQL artifact failed ({type(e).__name__}): {str(e)[:200]}; "
                            f"schema-aligned fallback failed: {str(aligned_exc)[:200]}"
                        )
                        state["error_node"] = 9
                        state["el_m9_passed"] = False
                        return state
                else:
                    logger.error(
                        f"[Node 9] SQL artifact failed and no cleaned_tables for fallback: {e}"
                    )
                    state["error_message"] = f"SQL artifact apply failed: {str(e)[:300]}"
                    state["error_node"] = 9
                    state["el_m9_passed"] = False
                    return state
        else:
            # ── Fallback: POST to svc-ingestion when SQL artifact is unavailable ─
            svc_ingestion_url = await _get_svc_ingestion_url()
            endpoint = f"{svc_ingestion_url}/api/ingest"

            logger.info(f"[Node 9] SQL artifact missing; POSTing IntermediateSchema to {endpoint}")

            try:
                response_json = await _post_to_svc_ingestion(
                    endpoint=endpoint,
                    payload=intermediate_schema
                )

                logger.info(f"[Node 9] ✓ svc-ingestion accepted")
                state["handoff_status"] = "sent"
                state["svc_ingestion_response"] = response_json

            except Exception as e:
                logger.exception(f"[Node 9] Failed to POST to svc-ingestion: {e}")
                state["error_message"] = f"svc-ingestion handoff failed: {str(e)}"
                state["error_node"] = 9
                state["el_m9_passed"] = False
                return state

        # ── EL-M.9 Validation ────────────────────────────────────────
        if state.get("handoff_status") not in [
            "sent",
            "acknowledged",
            "applied_sql",
            "applied_sql_aligned",
        ]:
            logger.error("[Node 9] EL-M.9 FAILED: svc-ingestion did not acknowledge")
            state["el_m9_passed"] = False
            return state

        state["el_m9_passed"] = True
        logger.info("[Node 9] EL-M.9 PASSED: IntermediateSchema sent and acknowledged")

        # ── Update migration_jobs database record ──────────────────────
        try:
            session_factory = get_async_session_factory()
            async with session_factory() as session:
                db_migration = await session.get(MigrationJob, migration_id)
                if db_migration:
                    db_migration.status = "complete"
                    db_migration.completed_at = datetime.utcnow()
                    db_migration.output_json_url = state.get("output_json_url")
                    db_migration.output_csv_url = state.get("output_csv_url")
                    db_migration.output_sql_url = state.get("output_sql_url")
                    db_migration.output_structure_md_url = state.get("output_structure_md_url")
                    db_migration.migration_report_url = state.get("migration_report_url")
                    db_migration.progress_pct = 100.0
                    await session.commit()
                    logger.info(f"[Node 9] Updated migration_jobs: status=complete")
                else:
                    logger.warning(f"[Node 9] Migration record {migration_id} not found in DB")

        except Exception as e:
            logger.exception(f"[Node 9] Failed to update migration_jobs: {e}")
            # Continue anyway — state is already updated
            state["error_message"] = f"DB update failed (handoff sent): {str(e)}"

        # ── Mark migration complete ──────────────────────────────────
        state["status"] = "complete"
        state["current_step"] = 9
        append_event(
            state,
            node_id=9,
            node_name="write_node",
            event="node_complete",
            status="completed",
            outcome=f"Migration written — handoff {state.get('handoff_status')}",
            detail=f"Handoff to svc-ingestion: {state.get('handoff_status')}",
        )

        logger.info(f"[Node 9] ═══════════════════════════════════════════")
        logger.info(f"[Node 9] ✓ MIGRATION COMPLETE")
        logger.info(f"[Node 9] Status: {state.get('handoff_status')}")

        if migration_id:
            from .db_writer import update_node_progress
            await update_node_progress(
                migration_id, "9_complete",
                status="complete",
            )

        # ── Save updated registry snapshot to DB ─────────────────────────────
        # Persists any newly learned aliases from this migration run so that
        # future startups load them from the DB cache instead of introspecting.
        try:
            from ...services.registry_cache import save_new_version, compute_schema_hash
            from ...config import get_settings as _get_settings
            _db_url = _get_settings().db_url
            _mapper = state.get("mapper_config", {})
            _hash = compute_schema_hash(_mapper.get("canonical_fields", {}))
            _ver = await save_new_version(_db_url, _mapper, _hash)
            logger.info(f"[Node 9] Registry snapshot saved as v{_ver}")
        except Exception as _reg_exc:
            logger.warning(f"[Node 9] Registry snapshot save failed (non-fatal): {_reg_exc}")

        return state

    except GraphInterrupt:
        raise

    except Exception as e:
        logger.exception(f"[Node 9] Unhandled exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 9
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        state["el_m9_passed"] = False
        return state


async def _get_svc_ingestion_url() -> str:
    """Get svc-ingestion endpoint URL from config or default."""
    try:
        from ...config import settings
        return settings.svc_ingestion_url or "http://svc-ingestion:8001"
    except Exception:
        return "http://svc-ingestion:8001"


async def _post_to_svc_ingestion(endpoint: str, payload: dict) -> dict:
    """
    POST IntermediateSchema to svc-ingestion /api/ingest endpoint.

    Args:
        endpoint: Full URL to svc-ingestion API endpoint
        payload: IntermediateSchema dict to POST

    Returns:
        Response JSON from svc-ingestion

    Raises:
        Exception if POST fails or returns non-2xx status
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=3600),
            ) as response:
                response_text = await response.text()

                if response.status not in [200, 202, 201]:
                    error_msg = f"HTTP {response.status}: {response_text[:200]}"
                    logger.error(f"[Node 9] svc-ingestion error: {error_msg}")
                    raise Exception(error_msg)

                try:
                    response_json = await response.json()
                    logger.info(f"[Node 9] svc-ingestion response: {response.status}")
                    return response_json
                except Exception as e:
                    logger.warning(f"[Node 9] Could not parse response JSON: {e}")
                    # Return minimal response if JSON parsing fails
                    return {"status": "sent", "http_status": response.status}

    except aiohttp.ClientError as e:
        logger.error(f"[Node 9] Network error: {e}")
        raise Exception(f"Network error connecting to svc-ingestion: {str(e)}")
    except asyncio.TimeoutError:
        logger.error(f"[Node 9] Request timeout")
        raise Exception("Request to svc-ingestion timed out after 60 seconds")


def _split_sql_statements(sql_script: str) -> list[str]:
    """
    Split SQL script into executable statements while handling quoted semicolons.
    """
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    prev = ""

    for ch in sql_script:
        if ch == "'" and not in_double_quote and prev != "\\":
            in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote and prev != "\\":
            in_double_quote = not in_double_quote

        if ch == ";" and not in_single_quote and not in_double_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        prev = ch

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)

    cleaned: list[str] = []
    for stmt in statements:
        lines = []
        for line in stmt.splitlines():
            striped = line.strip()
            if not striped or striped.startswith("--"):
                continue
            lines.append(line)
        final_stmt = "\n".join(lines).strip()
        if final_stmt:
            cleaned.append(final_stmt)
    return cleaned


async def _apply_sql_artifact(sql_script: str) -> dict:
    """
    Execute generated SQL artifact inside one transaction.
    """
    statements = _split_sql_statements(sql_script)
    if not statements:
        raise Exception("output.sql is empty or contains no executable statements")

    session_factory = get_async_session_factory()
    async with session_factory() as session:
        try:
            for stmt in statements:
                await session.execute(text(stmt))
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return {"statement_count": len(statements)}


def _normalize_row_for_table(table_name: str, row: dict, organization_id: str) -> dict:
    """
    Best-effort canonicalization for common migration table payloads.
    """
    normalized = dict(row or {})
    t = table_name.lower()

    if organization_id:
        normalized.setdefault("organization_id", organization_id)

    if t == "assets":
        # asset_id → id: if the source carries a UUID-shaped PK, preserve it.
        # The insert path auto-generates id=uuid4() only when id is absent, so
        # mapping here lets idempotent re-runs hit the ON CONFLICT clause instead
        # of inserting duplicates. Non-UUID values are left to the per-row
        # exception handler (row skipped, logged).
        if "asset_id" in normalized:
            if "id" not in normalized:
                normalized["id"] = normalized.pop("asset_id")
            else:
                normalized.pop("asset_id")

        # asset_type has no direct column; repurpose as asset_code fallback.
        if "asset_type" in normalized:
            if not normalized.get("asset_code"):
                normalized["asset_code"] = normalized.pop("asset_type")
            else:
                normalized.pop("asset_type")

        # FK columns that need UUID resolution are not written as text. The site / building
        # reference is NOT lost, though: _apply_records_with_schema_alignment reads it off the
        # raw row (building_link.building_hint) and resolves it to buildings.building_id before
        # the insert — the ten building-less assets of 21 Sep 2026 came from dropping it here.
        for _fk_col in ("site_id", "location", "location_code", "category"):
            normalized.pop(_fk_col, None)

        # serial → serial_number (move so the old key doesn't surface as missing)
        if "serial" in normalized:
            if "serial_number" not in normalized:
                normalized["serial_number"] = normalized.pop("serial")
            else:
                normalized.pop("serial")

        # install_date → installation_date (move)
        if "install_date" in normalized:
            if "installation_date" not in normalized:
                normalized["installation_date"] = normalized.pop("install_date")
            else:
                normalized.pop("install_date")

        # asset_name is NOT NULL — derive it from any available identifier.
        if not normalized.get("asset_name"):
            normalized["asset_name"] = (
                normalized.get("name")
                or normalized.get("asset")
                or normalized.get("asset_code")
                or "Unknown Asset"
            )
    elif t == "locations":
        if "site_name" in normalized and "name" not in normalized:
            normalized["name"] = normalized.get("site_name")
        if "location_name" in normalized and "name" not in normalized:
            normalized["name"] = normalized.get("location_name")
        if "site_type" in normalized and "type" not in normalized:
            normalized["type"] = normalized.get("site_type")
        if not normalized.get("type"):
            normalized["type"] = "site"
    elif t == "energy_meters":
        # meter_type is NOT NULL with no default, so a meter sheet that does not carry one
        # fails every row before it ever reaches the building link. Read the fuel the sheet
        # states, or infer it from which supply number is present — the same rule the chat
        # upload path uses, so the two agree about what a row is.
        _mpan, _mprn = supply_numbers(row)
        if _mpan and not normalized.get("mpan"):
            normalized["mpan"] = _mpan
        if _mprn and not normalized.get("mprn"):
            normalized["mprn"] = _mprn
        if not normalized.get("meter_type"):
            normalized["meter_type"] = meter_type_for(row)
        # Source spellings that have now been read into a real column. Left in place they
        # would be proposed as new columns on the table.
        for _k in ("fuel", "fuel_type", "supply_type", "utility", "commodity", "energy_type",
                   "mpan_mprn", "meter_ref", "meter_reference", "supply_number",
                   "meter_number", "msn"):
            normalized.pop(_k, None)
    elif t == "meter_readings":
        # The meter itself is resolved in the writer, which has the session. Only the column
        # names are canonicalised here.
        for _src, _dst in (("timestamp", "reading_at"), ("read_at", "reading_at"),
                           ("datetime", "reading_at"), ("reading_date", "reading_at"),
                           ("kwh", "consumption_kwh"), ("consumption", "consumption_kwh"),
                           ("usage", "consumption_kwh"), ("value", "consumption_kwh")):
            if _src not in normalized:
                continue
            if not normalized.get(_dst):
                normalized[_dst] = normalized.pop(_src)
            else:
                normalized.pop(_src, None)
        # The reference the meter was named by; it is resolved to meter_id, not stored.
        for _k in ("mpan", "mprn", "mpan_mprn", "meter_ref", "meter_reference",
                   "supply_number", "meter_number", "msn", "meter"):
            normalized.pop(_k, None)
    elif t == "work_orders":
        # work_order_id is NOT NULL in the actual DB schema — map from any available code.
        if not normalized.get("work_order_id"):
            normalized["work_order_id"] = (
                normalized.get("wo_code")
                or normalized.get("work_order_number")
                or normalized.get("wo_number")
                or normalized.get("order_number")
            )
        if not normalized.get("title"):
            normalized["title"] = (
                normalized.get("description")
                or normalized.get("wo_code")
                or normalized.get("work_order_number")
                or normalized.get("work_order_id")
                or "Work Order"
            )
    return normalized


def _infer_sql_type_for_value(value: object) -> str:
    """
    Conservative type inference for newly added columns.
    """
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT"
    if isinstance(value, float):
        return "DOUBLE PRECISION"
    # Keep unknown/string-like values as TEXT to avoid write failures.
    return "TEXT"


def _to_safe_identifier(raw: str) -> str | None:
    """
    Normalize and validate SQL identifier for dynamic column creation.
    """
    if raw is None:
        return None
    normalized = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    if not _SAFE_SQL_IDENT.match(normalized):
        return None
    return normalized


#: How many rows go to the database in one statement.
#:
#: Row at a time is three round trips each and this path routinely carries a year of
#: half-hourly readings. Batching is not a speed nicety here: the connection was being held
#: open long enough for the server to close it mid-write.
_WRITE_CHUNK = 500

#: What a dead connection or a poisoned transaction looks like, whatever raised it.
_CONNECTION_LOST_SIGNS = (
    "connection was closed",
    "connection is closed",
    "server closed the connection",
    "terminating connection",
    "invalid transaction is rolled back",
    "connection does not exist",
    "cannot perform operation: another operation is in progress",
)


class ConnectionLost(RuntimeError):
    """The database went away. Not a bad row, and not something to retry per row."""


def _is_connection_lost(exc: BaseException) -> bool:
    text_ = str(exc).lower()
    return any(sign in text_ for sign in _CONNECTION_LOST_SIGNS)


async def _insert_rows(
    session: AsyncSession,
    *,
    schema_name: str,
    table_name: str,
    pending: list[tuple[dict, str, dict]],
    unique_sets: set[frozenset] | None,
    nullable_cols: set[str],
) -> dict:
    """Insert a batch in as few round trips as the data allows.

    Rows that share a column set share a statement, so they go in one executemany. A batch
    that fails is retried a row at a time, so one bad row costs its own row rather than the
    other four hundred and ninety-nine — and only then is the orphan-foreign-key retry worth
    doing, because it needs to know which row it was.

    Raises ConnectionLost rather than reporting skipped rows when the database has gone. The
    two are not the same thing and a run that confuses them tells the reader nothing.
    """
    inserted = 0
    skipped = 0
    errors: list[str] = []
    orphans: dict[str, int] = {}

    by_stmt: dict[str, list[tuple[dict, dict]]] = {}
    for filtered, dml_sql, params in pending:
        by_stmt.setdefault(dml_sql, []).append((filtered, params))

    for dml_sql, group in by_stmt.items():
        try:
            async with session.begin_nested():
                result = await session.execute(text(dml_sql), [p for _f, p in group])
            _n = int(getattr(result, "rowcount", 0) or 0)
            # executemany reports -1 on some drivers; the batch went in either way.
            inserted += _n if _n >= 0 else len(group)
            continue
        except Exception as batch_exc:
            if _is_connection_lost(batch_exc):
                raise ConnectionLost(str(batch_exc)) from batch_exc
            # Something in this batch is bad. Find out which, one row at a time.
            pass

        for filtered, params in group:
            try:
                async with session.begin_nested():
                    res = await session.execute(text(dml_sql), params)
                inserted += int(getattr(res, "rowcount", 0) or 0)
                continue
            except Exception as row_exc:
                if _is_connection_lost(row_exc):
                    raise ConnectionLost(str(row_exc)) from row_exc
                # Orphan foreign key: the row points at a parent that is not there. Null the
                # offending column IF it is nullable and retry once, so the row lands without
                # the broken link rather than not at all. A NOT NULL foreign key cannot be
                # nulled, so that row is skipped as before.
                _fk_cols = _foreign_key_columns_from_error(row_exc)
                _nullable_fk = [c for c in _fk_cols if c in nullable_cols and c in filtered]
                if _nullable_fk:
                    try:
                        _retry = {k: v for k, v in filtered.items() if k not in _nullable_fk}
                        _dml2, _p2 = _build_dml_for_row(
                            schema_name, table_name, _retry, unique_sets
                        )
                        async with session.begin_nested():
                            _res2 = await session.execute(text(_dml2), _p2)
                        inserted += int(getattr(_res2, "rowcount", 0) or 0)
                        for _c in _nullable_fk:
                            orphans[_c] = orphans.get(_c, 0) + 1
                        continue
                    except Exception as retry_exc:
                        if _is_connection_lost(retry_exc):
                            raise ConnectionLost(str(retry_exc)) from retry_exc
                skipped += 1
                if len(errors) < 20:
                    errors.append(f"{table_name}: {str(row_exc)[:220]}")

    return {"inserted": inserted, "skipped": skipped, "errors": errors, "orphans": orphans}


def _build_dml_for_row(
    schema_name: str,
    table_name: str,
    filtered: dict,
    unique_sets: set[frozenset] | None = None,
) -> tuple[str, dict]:
    """
    Build INSERT/UPSERT statement per table with conflict-safe behavior.

    ``unique_sets`` is the set of column-groups actually backed by a unique index on the target
    table. Postgres rejects ``ON CONFLICT (a, b)`` with "there is no unique or exclusion constraint
    matching the ON CONFLICT specification" unless such an index exists, and that error kills EVERY
    row. So a conflict TARGET is only emitted when the schema really supports it; otherwise we fall
    back to a bare ``ON CONFLICT DO NOTHING``, which needs no index and is always valid.
    """
    cols = list(filtered.keys())
    col_sql = ", ".join(cols)
    val_sql = ", ".join(f":{c}" for c in cols)
    _supported = unique_sets or set()

    def _stmt(target_cols: list[str] | None, set_sql: str) -> str:
        head = f"INSERT INTO {schema_name}.{table_name} ({col_sql}) VALUES ({val_sql}) "
        if not target_cols:
            return head + "ON CONFLICT DO NOTHING"
        target = ", ".join(target_cols)
        if set_sql:
            return head + f"ON CONFLICT ({target}) DO UPDATE SET {set_sql}"
        return head + f"ON CONFLICT ({target}) DO NOTHING"

    # Domain-aware upsert for assets to prevent duplicate-key failures and keep data fresh —
    # used ONLY when the matching unique index exists.
    if table_name == "assets":
        updatable = [
            c for c in cols
            if c not in {"id", "organization_id", "serial_number", "asset_code"}
        ]
        set_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in updatable)
        for _key in (
            ["organization_id", "serial_number"],
            ["organization_id", "asset_code"],
        ):
            if all(k in cols for k in _key) and frozenset(_key) in _supported:
                return _stmt(_key, set_sql), filtered

    return _stmt(None, ""), filtered


def _foreign_key_columns_from_error(exc: object) -> list[str]:
    """The column(s) named in a Postgres foreign-key violation.

    asyncpg surfaces "Key (asset_id)=(A0050145) is not present in table \"assets\"." — parse the
    ``Key (…)`` list so the caller can null just that column and retry. Returns [] for any other
    error, so the caller falls through to skipping the row.
    """
    msg = str(getattr(exc, "orig", exc) or exc)
    if "foreign key" not in msg.lower() and "is not present in table" not in msg.lower():
        return []
    m = re.search(r"Key \(([^)]+)\)=", msg)
    if not m:
        return []
    return [c.strip().strip('"') for c in m.group(1).split(",") if c.strip()]


# Sentinel: the value could not be represented in the destination column's type
# (e.g. 'Quarterly' → an INTEGER column). Distinct from None, which is a legitimate
# empty value. The write loop drops the field (keeps the row) and records the mismatch
# so it is surfaced to the user for re-mapping — instead of asyncpg rejecting the WHOLE
# row and silently losing every column of it.
_COERCE_TYPE_MISMATCH = object()
_NO_SYSTEM_DEFAULT = object()


def _coerce_value_for_db_type(value: object, db_type: str) -> object:
    """
    Convert incoming values to the Python type asyncpg expects for ``db_type``.

    On a genuine type mismatch (a non-numeric string bound to an INTEGER column, an
    unparseable date, an unrecognised boolean, …) returns ``_COERCE_TYPE_MISMATCH``
    rather than the raw string. Passing the raw string through made asyncpg reject the
    ENTIRE row ("'str' object cannot be interpreted as an integer"), so one wrongly
    mapped column dropped every row of the table (0 inserted, N skipped). The caller
    turns the sentinel into "drop just this field + record the mismatch".
    """
    if value is None:
        return None
    t = (db_type or "").lower()

    # Dispatch on the DESTINATION type, not on the incoming Python type. asyncpg binds strictly:
    # an int bound to a TEXT column fails with "expected str, got int" just as surely as a str
    # bound to an INTEGER column fails. Source cells arrive with mixed types (pandas ints/floats,
    # Decimals, datetimes, bools), so every branch must accept ANY input and produce exactly what
    # asyncpg wants — otherwise a single oddly-typed cell rejects the whole row.
    s: str | None = None
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return None

    # ── text-ish: asyncpg requires a str ────────────────────────────────────────────────
    if "char" in t or "text" in t or t in {"name", "citext"}:
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (datetime, _date)):
            return value.isoformat()
        return str(value)

    # "interval" contains "int" — keep it away from the integer branch below.
    if t.startswith("interval"):
        return value if not isinstance(value, str) else s

    # ── integer ─────────────────────────────────────────────────────────────────────────
    if "int" in t or "serial" in t:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, (float, Decimal)):
            try:
                return int(value)
            except Exception:
                return _COERCE_TYPE_MISMATCH
        if s is not None:
            try:
                return int(s)
            except Exception:
                # Tolerate a decimal string bound to an int column ('102.0' → 102);
                # a non-numeric string ('Quarterly') is a real mismatch.
                try:
                    return int(float(s))
                except Exception:
                    return _COERCE_TYPE_MISMATCH
        return _COERCE_TYPE_MISMATCH

    # ── numeric / floating point ────────────────────────────────────────────────────────
    if "numeric" in t or "decimal" in t or "double" in t or "real" in t:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, bool):
            return Decimal(int(value))
        if isinstance(value, (int, float)):
            try:
                return Decimal(str(value))
            except Exception:
                return _COERCE_TYPE_MISMATCH
        if s is not None:
            try:
                return Decimal(s)
            except Exception:
                return _COERCE_TYPE_MISMATCH
        return _COERCE_TYPE_MISMATCH

    # ── boolean ─────────────────────────────────────────────────────────────────────────
    if t == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float, Decimal)):
            return bool(value)
        if s is not None:
            low = s.lower()
            if low in {"1", "true", "t", "yes", "y"}:
                return True
            if low in {"0", "false", "f", "no", "n"}:
                return False
        return _COERCE_TYPE_MISMATCH

    # ── timestamp / date ────────────────────────────────────────────────────────────────
    if "timestamp" in t:
        if isinstance(value, datetime):   # NB: datetime is a subclass of date — check first
            return value
        if isinstance(value, _date):
            return datetime(value.year, value.month, value.day)
        if s is not None:
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return _COERCE_TYPE_MISMATCH
        return _COERCE_TYPE_MISMATCH
    if t == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, _date):
            return value
        if s is not None:
            try:
                return _date.fromisoformat(s[:10])
            except Exception:
                return _COERCE_TYPE_MISMATCH
        return _COERCE_TYPE_MISMATCH

    # ── json / jsonb: asyncpg wants serialised text ─────────────────────────────────────
    if "json" in t:
        if isinstance(value, str):
            return value
        try:
            import json as _json
            return _json.dumps(value, default=str)
        except Exception:
            return str(value)

    # ── uuid: accept the string form ────────────────────────────────────────────────────
    if t == "uuid":
        # Validate, like every other branch above. This used to return any string untouched,
        # so a code that reached a uuid column got as far as asyncpg and raised DataError
        # while BINDING the parameter. That happens before Postgres sees a statement, so the
        # orphan-FK recovery in the writer (which reads a constraint name out of the error)
        # could never match it, and the whole row was skipped. On 22 Sep 2026 that silently
        # cost 160 of 195 rows in a migration the UI reported as complete.
        #
        # reference_link now resolves asset_id, vendor_id, contract_id and part_id before
        # this runs, and drops the column when nothing matches — so those four can no longer
        # arrive here as a code. This is the floor under every OTHER uuid column, which has
        # no resolver of its own and would still lose its whole row to one bad field.
        #
        # Reported as a type mismatch instead: the caller drops this one field, keeps the
        # rest of the row, and surfaces the column at the mapping gate so it can be re-mapped.
        try:
            uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return _COERCE_TYPE_MISMATCH
        return value if isinstance(value, str) else str(value)

    # Unknown destination type — hand it over untouched.
    return value


# What identifies a row as "the same row" on a re-run, when the database has no unique index
# to say so.
#
# _build_dml_for_row falls back to a bare ON CONFLICT DO NOTHING wherever no unique index
# exists — but the writer mints a fresh uuid4() for `id` on every row, so there is never a PK
# collision and the clause never fires. Assets escaped this because they have an explicit
# merge-by-code path; nothing else did. Re-running the same workbook on 23 Sep 2026 therefore
# left 23 assets and 16 work orders correct while duplicating vendors, inspections and
# resources, and the Vendors list showed six entries for three firms.
#
# Candidate groups are tried in order; the first whose columns are ALL present in both the
# table and the row wins. A table absent here is simply not deduped, which is the old
# behaviour — never a guess at what "the same row" means.
_NATURAL_KEYS: dict[str, tuple[tuple[str, ...], ...]] = {
    "vendors": (("vendor_code",), ("vendor_name",)),
    "ppm_visits": (("ppm_ref",),),
    "resources": (("engineer_id",), ("resource_code",)),
    "spare_parts": (("part_code",),),
    "sites": (("site_id",),),
    "work_orders": (("wo_code",),),
    # No reference column of its own, so a finding is identified by what it is a finding ABOUT.
    "inspections": (("asset_code", "inspection_date", "finding_type"),),
}


def _natural_keys_for(table: str, row: dict, db_cols: set) -> list[tuple[tuple[str, ...], list]]:
    """EVERY key that could identify this row as one already written.

    All of them, not the first: the candidates are alternative names for the same row, and a
    row already on file may have been created by another route that filled a different one.
    Returning only the first got this wrong on 23 Sep 2026 — the certificate ingest had
    created vendors with vendor_code NULL, the workbook carried vendor_code, so the lookup
    asked for a code no existing row had, found nothing, and inserted a twin of a vendor it
    was holding the name of.
    """
    out: list[tuple[tuple[str, ...], list]] = []
    for group in _NATURAL_KEYS.get(table, ()):
        if all(c in db_cols and str(row.get(c) or "").strip() for c in group):
            out.append((group, [row[c] for c in group]))
    return out


def _system_default_for_db_type(col: str, db_type: str, org_id: str | None) -> object:
    """A value for a NOT NULL column the source cannot possibly supply.

    Only ever called for columns that are NOT NULL, have no DDL default, and were not
    present in the row. These are system/provenance fields — `conflict_flag`, `source`,
    `raw_metadata`, `org_id` — never business data, because business data the source DOES
    carry arrives in the row and never reaches here.

    Returns _NO_SYSTEM_DEFAULT when there is no honest value to invent (a date, a name),
    and the row is then skipped as before rather than filled with a fiction.
    """
    t = (db_type or "").lower()
    c = (col or "").lower()
    if c in {"org_id", "organization_id"} and org_id:
        return org_id
    if "bool" in t:
        return False
    if "json" in t:
        return "{}"
    if c in {"source", "origin", "created_by", "source_system"}:
        # True, and useful: it says where the row came from.
        return "migration"
    if "int" in t or "numeric" in t or "double" in t or "real" in t:
        return 0
    if "char" in t or "text" in t:
        return ""
    return _NO_SYSTEM_DEFAULT


def _collect_approved_new_columns(state) -> dict[str, set[str]]:
    """``{dest_table: {column}}`` for every column a CUSTOMER explicitly chose to create.

    Rule: if the user selected "new column" anywhere in the pipeline, that column gets created —
    even on an ORM-managed core table, which otherwise drops unknown columns. Silently discarding
    an explicit decision loses their data. Every customer-facing path is covered:

      * ``extra_fields_config`` (storage_strategy="custom") — the "create as a new column" choice at
        the semantic / field-mapping gate (human_review_node, schema_human_review_node).
        "skip" and "raw_metadata" strategies deliberately do NOT create a column.
      * ``column_dest_overrides`` → ``"__new__"`` — the B14.1 gate's "New column" button. The created
        name is the snake_cased source field, matching what output_generator re-targets it to.
      * tier-1/2 mappings stamped ``T1_new_table`` / ``T1_manual`` — columns the user assigned or
        created at the pre-semantic (Step 2) gate.

    Purely automatic decisions (e.g. the type-compatibility guard's demote-to-new-column) are NOT
    included: those are pipeline inferences, not customer choices, and must not silently widen an
    ORM-managed schema.
    """
    out: dict[str, set[str]] = {}

    def _add(tbl: object, col: object) -> None:
        _t = str(tbl or "").strip().lower()
        _c = str(col or "").strip().lower()
        if _t and _c:
            out.setdefault(_t, set()).add(_c)

    _routing = state.get("table_routing") or {}

    def _dest_for(src_table: object) -> str:
        _s = str(src_table or "")
        return str(_routing.get(_s) or _s)

    # 1. Explicit "create a custom column" entries from the review gates.
    for _e in (state.get("extra_fields_config") or []):
        if not isinstance(_e, dict):
            _e = getattr(_e, "__dict__", {}) or {}
        if _e.get("is_new_table"):
            continue  # a whole new TABLE, handled by CREATE TABLE, not ALTER
        if str(_e.get("storage_strategy") or "custom").lower() in {"skip", "raw_metadata"}:
            continue  # user deliberately chose NOT to create a column
        _add(_e.get("target_table") or _dest_for(_e.get("source_table")),
             _e.get("custom_column_name"))

    # 2. B14.1 column-mapping gate — "New column" button.
    for _src, _cols in (state.get("column_dest_overrides") or {}).items():
        if not isinstance(_cols, dict):
            continue
        for _field, _choice in _cols.items():
            if str(_choice) == "__new__":
                _add(_dest_for(_src), _snake_ident(_field))

    # 3. Pre-semantic (Step 2) gate — columns the user assigned / created there.
    for _bucket in ("tier1_mappings_by_table", "tier2_auto_by_table", "tier2_human_decisions_by_table"):
        for _tbl, _maps in (state.get(_bucket) or {}).items():
            for _m in _maps or []:
                if not isinstance(_m, dict):
                    continue
                if str(_m.get("tier") or "") in {"T1_new_table", "T1_manual"}:
                    _add(_dest_for(_tbl), _m.get("target_field"))

    return out


def _snake_ident(s: object) -> str:
    """snake_case a source field the same way output_generator names a '__new__' column."""
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_") or "column"


async def _apply_records_with_schema_alignment(
    cleaned_tables: dict,
    organization_id: str,
    schema_name: str = "plenum_cafm",
    table_routing: dict | None = None,
    approved_new_columns: dict[str, set[str]] | None = None,
    confirmed_hierarchies: list | None = None,
    default_building_id: str | None = None,
) -> dict:
    """
    Insert cleaned records while filtering to real DB columns.

    ``approved_new_columns`` maps ``dest_table -> {column}`` for columns a HUMAN explicitly asked
    to be created (the "New column" choice at the column-mapping / semantic gates). Core tables are
    ORM-managed so unknown columns are normally dropped rather than ALTERed in — but a column the
    user deliberately approved must be created, otherwise their decision is silently discarded and
    the data is lost.
    """
    session_factory = get_async_session_factory()
    rows_inserted = 0
    tables_written = 0
    rows_skipped = 0
    row_errors: list[str] = []

    async with session_factory() as session:
        try:
            # Defensive: the primary SQL-artifact path can fail mid-INSERT (e.g. a
            # mapped column that doesn't exist) and leave the pooled connection in an
            # aborted-transaction state. Roll back to a clean state BEFORE the first
            # query, otherwise it dies with asyncpg's "Can't reconnect until invalid
            # transaction is rolled back" and this fallback never gets to filter the
            # bad column out.
            try:
                await session.rollback()
            except Exception:
                pass
            effective_org_id = await _resolve_valid_organization_id(
                session=session,
                requested_org_id=organization_id,
                schema_name=schema_name,
            )
            # ── Building links ──────────────────────────────────────────────────────────────────
            # A source row names its building by a site reference, a name or a code; the target
            # needs buildings.building_id. Resolved here (building_link.py), once per distinct
            # hint, inside a savepoint so a lookup that fails cannot poison the write. Assets that
            # already exist under the same code are MERGED into their row instead of duplicated,
            # and a work order with no building inherits its asset's.
            async def _fetch(_sql: str, _params: dict) -> list:
                try:
                    async with session.begin_nested():
                        _rs = await session.execute(text(_sql), _params)
                        return [tuple(r) for r in _rs.fetchall()]
                except Exception as _lookup_exc:  # a missing column, a bad cast — never fatal
                    logger.debug(f"[Node 9] building lookup failed: {str(_lookup_exc)[:160]}")
                    return []

            _buildings = BuildingResolver(
                _fetch, effective_org_id, schema_name,
                site_names_from_run(cleaned_tables, table_routing),
            )
            # ── Meter links ─────────────────────────────────────────────────────────────
            # A reading names its meter by a supply number, and meter_readings.meter_id is NOT
            # NULL behind a real foreign key, so before this every reading row was rejected and
            # counted as skipped. A meter that does not exist yet is created — but only when the
            # row also names a building that resolves. See meter_link for why that refusal
            # matters more than it looks.
            _section_cache: dict[tuple[str, str], str | None] = {}

            async def _section_for(_bid: str | None, _hint: str | None) -> str | None:
                """This building's section by name, type, or the floor it sits on.

                Scoped to the building, so "Level 3" means this building's third floor and not
                another tower's. A hint that matches two sections resolves to neither.
                """
                if not _bid or not _hint:
                    return None
                key = (str(_bid), str(_hint).strip().lower())
                if key in _section_cache:
                    return _section_cache[key]
                _hit = await _fetch(SECTION_LOOKUP_SQL.format(schema=schema_name),
                                    {"b": key[0], "k": key[1]})
                _ids = sorted({str(r[0]) for r in _hit if r and r[0]})
                _section_cache[key] = _ids[0] if len(_ids) == 1 else None
                return _section_cache[key]

            async def _create_meter(*, mpan, mprn, meter_type, building_id,
                                    section_id=None, is_sub_meter=False) -> str | None:
                try:
                    async with session.begin_nested():
                        _rs = await session.execute(
                            text(CREATE_METER_SQL.format(schema=schema_name)),
                            {"org": effective_org_id, "bid": building_id,
                             "mtype": meter_type, "mpan": mpan, "mprn": mprn,
                             "sid": section_id, "is_sub": bool(is_sub_meter)},
                        )
                        _row = _rs.first()
                        return str(_row[0]) if _row and _row[0] else None
                except Exception as _create_exc:     # a constraint, a bad cast — never fatal
                    logger.debug(f"[Node 9] meter create failed: {str(_create_exc)[:160]}")
                    return None

            _meters = MeterResolver(_fetch, effective_org_id, schema_name, create=_create_meter)

            # Codes and names in the file, resolved to the ids the database keys on. A CSV
            # cannot carry a uuid anybody would type; it carries asset_code, vendor_name,
            # contract_name. Without this every one of those columns was written as null,
            # which for ppm_visits means the row is dropped by an inner join and never
            # appears at all.
            _refs = ReferenceResolver(_fetch, effective_org_id, schema_name)

            # Has this row been written by an earlier run? See _NATURAL_KEYS: the bare
            # ON CONFLICT DO NOTHING in _build_dml_for_row cannot answer it, because `id` is
            # a fresh uuid4() every time and so never collides. Cached per key, including
            # the misses, so re-running a workbook costs one read per distinct row, not one
            # per row.
            _nk_seen: dict[tuple, bool] = {}

            async def _already_written(_table: str, _cols: tuple, _vals: list, _has_org: bool) -> bool:
                _ck = (_table, _cols, tuple(str(v) for v in _vals))
                if _ck not in _nk_seen:
                    _where = " AND ".join(f"{c} = :v{i}" for i, c in enumerate(_cols))
                    _prm = {f"v{i}": v for i, v in enumerate(_vals)}
                    if _has_org:
                        _where += " AND organization_id::text = :org"
                        _prm["org"] = effective_org_id
                    _hit = await _fetch(
                        f"SELECT 1 FROM {schema_name}.{_table} WHERE {_where} LIMIT 1", _prm
                    )
                    _nk_seen[_ck] = bool(_hit)
                return _nk_seen[_ck]

            # The uploader's selection, checked once. A building_id that names no building of
            # this organisation is dropped rather than written, because a row pointing at
            # somebody else's building is worse than a row pointing at none.
            _default_building: str | None = None
            if default_building_id:
                _hit = await _fetch(
                    f"SELECT building_id::text FROM {schema_name}.buildings "
                    f"WHERE building_id::text = :b AND organization_id::text = :org",
                    {"b": str(default_building_id), "org": effective_org_id},
                )
                _default_building = str(_hit[0][0]) if _hit and _hit[0] and _hit[0][0] else None
                if _default_building:
                    logger.info("[Node 9] rows naming no site will be filed against building %s",
                                _default_building)
                else:
                    logger.warning("[Node 9] selected building %s is not a building of org %s "
                                   "- ignored", default_building_id, effective_org_id)

            _asset_ids: dict[str, str] = {}          # asset code → existing assets.id ("" = none)
            _asset_building_cache: dict[str, str] = {}  # asset ref → building_id ("" = none)
            rows_merged = 0
            buildings_linked = 0
            meters_linked = 0
            meters_matched = 0   # meter sheet rows that named a meter already on record


            async def _existing_asset_id(_code: str) -> str:
                if _code not in _asset_ids:
                    _hit = await _fetch(ASSET_LOOKUP_SQL.format(schema=schema_name),
                                        {"org": effective_org_id, "code": _code})
                    _asset_ids[_code] = str(_hit[0][0]) if _hit and _hit[0] and _hit[0][0] else ""
                return _asset_ids[_code]

            async def _asset_building(_ref: str) -> str:
                if not _ref:
                    return ""
                if _ref not in _asset_building_cache:
                    _hit = await _fetch(ASSET_BUILDING_SQL.format(schema=schema_name),
                                        {"org": effective_org_id, "ref": _ref})
                    _asset_building_cache[_ref] = str(_hit[0][0]) if _hit and _hit[0] and _hit[0][0] else ""
                return _asset_building_cache[_ref]

            # ── Parent-before-child write order ─────────────────────────────────────────────────
            # A child's FK (work_orders.asset_id → assets) can only be satisfied if the parent rows
            # were inserted first. Dict/source order doesn't guarantee that, so a work_orders sheet
            # listed before assets fails every row with "asset_id not present in assets". Order the
            # source tables by a topological sort of their DESTINATION tables, using the confirmed
            # hierarchy (child.source_table references parent.target_table). Ties + unknowns keep the
            # original order; a cycle falls back to original order. Additive + best-effort.
            def _dest_of(_src: str) -> str:
                return (table_routing or {}).get(_src, _src)

            _parents_of_dest: dict[str, set] = {}  # dest table -> set(parent dest tables)
            # Passed in by the caller. This read `state`, which is not in scope here,
            # so the moment this fallback was reached it raised NameError instead of
            # inserting anything — and it is only ever reached when the primary write
            # has already failed. The recovery path could not recover.
            for _h in (confirmed_hierarchies or []):
                _hd = _h if isinstance(_h, dict) else (getattr(_h, "__dict__", {}) or {})
                _child = _dest_of(str(_hd.get("source_table") or ""))
                _parent = _dest_of(str(_hd.get("target_table") or ""))
                if _child and _parent and _child != _parent:
                    _parents_of_dest.setdefault(_child, set()).add(_parent)

            def _ordered_source_tables() -> list[str]:
                _srcs = [s for s, r in cleaned_tables.items() if isinstance(r, list) and r]
                _orig_idx = {s: i for i, s in enumerate(_srcs)}
                _emitted: list[str] = []
                _seen: set[str] = set()

                def _visit(_s: str, _stack: set):
                    if _s in _seen or _s in _stack:
                        return  # already placed, or a cycle → break it
                    _stack.add(_s)
                    _sd = _dest_of(_s)
                    # place every parent-destination's source table(s) first
                    for _ps in sorted(
                        (x for x in _srcs if _dest_of(x) in _parents_of_dest.get(_sd, set())),
                        key=lambda x: _orig_idx[x],
                    ):
                        _visit(_ps, _stack)
                    _stack.discard(_s)
                    if _s not in _seen:
                        _seen.add(_s); _emitted.append(_s)

                for _s in _srcs:
                    _visit(_s, set())
                return _emitted

            _write_order = _ordered_source_tables()
            if _write_order != [s for s, r in cleaned_tables.items() if isinstance(r, list) and r]:
                logger.info(f"[Node 9] Write order (parents first): {_write_order}")
            _ordered_tables = [(s, cleaned_tables[s]) for s in _write_order]

            for source_table_name, records in _ordered_tables:
                if not isinstance(records, list) or not records:
                    continue
                # Route the source sheet to its target CAFM table (works → assets,
                # WorkOrders / WorkOrders_2 → work_orders) before writing. Without this the
                # writer created source-named tables (workorders, works) instead of writing into
                # the existing canonical tables. Falls back to the source name when unrouted; two
                # sources routed to one target both INSERT into it (work_orders).
                table_name = (table_routing or {}).get(source_table_name, source_table_name)
                safe_table = _to_safe_identifier(table_name)
                if not safe_table:
                    logger.warning(
                        f"[Node 9] Skipping unsafe table name for DB sync: "
                        f"{source_table_name!r} → {table_name!r}"
                    )
                    continue
                if source_table_name != table_name:
                    logger.info(
                        f"[Node 9] Routing source '{source_table_name}' → target '{safe_table}'"
                    )

                _tbl_started_at = datetime.utcnow()
                logger.info(f"[Node 9] ► Table '{safe_table}': {len(records)} records")
                cols_rs = await session.execute(
                    text(
                        """
                        SELECT column_name, data_type, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_schema = :schema_name AND table_name = :table_name
                        """
                    ),
                    {"schema_name": schema_name, "table_name": safe_table},
                )
                _col_rows = cols_rs.fetchall()
                db_col_type_map = {str(r[0]): str(r[1]) for r in _col_rows}
                # Columns that accept NULL — used to recover a row whose FK points at a parent that
                # isn't present (orphan reference): null the FK so the row still lands, instead of
                # dropping it. NOT NULL FKs can't be nulled, so those rows are skipped.
                db_nullable_cols = {str(r[0]) for r in _col_rows if str(r[2]).upper() == "YES"}
                # Required by the database, defaulted by nobody. This writer builds raw INSERT
                # statements, so a Python-side ORM default never runs — and the source file
                # cannot supply a system column it has never heard of. work_orders.conflict_flag
                # is a boolean flag the platform sets; ppm_visits.source records where the row
                # came from. Both are NOT NULL with no DDL default, so every row arrived with
                # NULL and Postgres rejected it: 16/16 work orders and 132/132 PPM visits lost
                # on 22 Sep 2026, reported only as "Skipping bad row".
                db_required_undefaulted = {
                    str(r[0]) for r in _col_rows
                    if str(r[2]).upper() == "NO" and r[3] is None
                }
                db_cols = set(db_col_type_map.keys())
                if db_cols:
                    logger.info(f"[Node 9]   {safe_table}: {len(db_cols)} DB columns found")

                # Column-groups backed by a real unique index (PK + UNIQUE constraints + unique
                # indexes). ON CONFLICT (a, b) is only legal against one of these — without this
                # check a hardcoded upsert key that the schema lacks fails EVERY row with
                # "no unique or exclusion constraint matching the ON CONFLICT specification".
                # Partial indexes are excluded (indpred IS NOT NULL) — they can't back a plain
                # ON CONFLICT target. Best-effort: on failure we simply emit DO NOTHING.
                unique_sets: set[frozenset] = set()
                try:
                    _uq_rs = await session.execute(
                        text(
                            """
                            SELECT i.indexrelid, a.attname
                            FROM pg_index i
                            JOIN pg_class t ON t.oid = i.indrelid
                            JOIN pg_namespace n ON n.oid = t.relnamespace
                            CROSS JOIN LATERAL unnest(i.indkey) AS k(attnum)
                            JOIN pg_attribute a
                              ON a.attrelid = t.oid AND a.attnum = k.attnum
                            WHERE n.nspname = :schema_name
                              AND t.relname = :table_name
                              AND i.indisunique
                              AND i.indpred IS NULL
                            """
                        ),
                        {"schema_name": schema_name, "table_name": safe_table},
                    )
                    _by_idx: dict = {}
                    for _idx, _col in _uq_rs.fetchall():
                        _by_idx.setdefault(_idx, set()).add(str(_col))
                    unique_sets = {frozenset(v) for v in _by_idx.values()}
                except Exception as _uq_exc:  # pragma: no cover — degrade to DO NOTHING
                    logger.warning(
                        f"[Node 9]   {safe_table}: could not read unique indexes "
                        f"({_uq_exc}) — using ON CONFLICT DO NOTHING"
                    )
                    unique_sets = set()
                if not db_cols:
                    # Table does not exist in target schema; create it from incoming shape.
                    sample_row = next(
                        (r for r in records if isinstance(r, dict) and r),
                        {},
                    )
                    inferred_cols: dict[str, str] = {
                        "id": "UUID PRIMARY KEY",
                        "organization_id": "UUID",
                    }
                    for raw_k, raw_v in sample_row.items():
                        safe_k = _to_safe_identifier(str(raw_k))
                        if not safe_k or safe_k in {"id", "organization_id"}:
                            continue
                        inferred_cols[safe_k] = _infer_sql_type_for_value(raw_v)

                    col_defs = ", ".join(f"{k} {v}" for k, v in inferred_cols.items())
                    await session.execute(
                        text(
                            f"CREATE TABLE IF NOT EXISTS {schema_name}.{safe_table} "
                            f"({col_defs})"
                        )
                    )
                    logger.warning(
                        f"[Node 9] Created missing table {schema_name}.{safe_table} "
                        f"with {len(inferred_cols)} columns"
                    )

                    # Re-read columns after CREATE TABLE
                    cols_rs = await session.execute(
                        text(
                            """
                            SELECT column_name, data_type
                            FROM information_schema.columns
                            WHERE table_schema = :schema_name AND table_name = :table_name
                            """
                        ),
                        {"schema_name": schema_name, "table_name": safe_table},
                    )
                    db_col_type_map = {str(r[0]): str(r[1]) for r in cols_rs.fetchall()}
                    db_cols = set(db_col_type_map.keys())
                    if not db_cols:
                        continue

                # Collect missing columns from normalized records and add them to DB first.
                missing_columns: dict[str, str] = {}
                normalized_records: list[dict] = []
                for row in records:
                    if not isinstance(row, dict):
                        continue
                    _hint = building_hint(row) if safe_table in _BUILDING_HINT_TABLES else None
                    normalized = _normalize_row_for_table(
                        safe_table, row, effective_org_id
                    )
                    safe_row: dict[str, object] = {}
                    for raw_k, raw_v in normalized.items():
                        safe_k = _to_safe_identifier(str(raw_k))
                        if not safe_k:
                            continue
                        safe_row[safe_k] = raw_v
                        if safe_k not in db_cols and safe_k not in missing_columns:
                            if raw_v is not None and str(raw_v) != "":
                                missing_columns[safe_k] = _infer_sql_type_for_value(raw_v)
                    # The building link. A hint that resolves becomes building_id; one that does
                    # not is removed rather than written into a UUID column as text. A work order
                    # with no hint of its own takes its asset's building.
                    if safe_table in _BUILDING_LINKED_TABLES and "building_id" in db_cols \
                            and not looks_like_uuid(safe_row.get("building_id")):
                        _bid = await _buildings.resolve(_hint) if _hint else None
                        if not _bid and safe_table in _BUILDING_VIA_ASSET_TABLES:
                            _bid = await _asset_building(str(safe_row.get("asset_id") or "").strip()) or None
                        # Last: the building the uploader had selected. A half-hourly export
                        # names an MPAN and nothing else, so this is the only thing that can
                        # place its meter. A site named in the file always wins over it,
                        # because the file is evidence and the selection is context.
                        if not _bid and _default_building:
                            _bid = _default_building
                        if _bid:
                            safe_row["building_id"] = _bid
                            buildings_linked += 1
                        else:
                            safe_row.pop("building_id", None)

                    # A meter sheet places each meter where it sits. A tower with a meter per
                    # floor produces rows identical but for that, and without it every one of
                    # them is created as the building's main meter — so the building's
                    # consumption is counted once per floor.
                    # Anything that sits in a section names it the same way, so the lookup is
                    # not the meter's alone. An asset carries one too, and without it the
                    # Assets page cannot group it under the part of the building it is in.
                    if "section_id" in db_cols and not looks_like_uuid(safe_row.get("section_id")):
                        _sid = await _section_for(safe_row.get("building_id"), section_hint(row))
                        if _sid:
                            safe_row["section_id"] = _sid
                        else:
                            safe_row.pop("section_id", None)

                    if safe_table == "energy_meters":
                        # A meter already on record under this supply number is THE meter, not
                        # a second one. Nothing in the schema makes an MPAN unique, so without
                        # this a re-ingest of the same export doubles the register — and a
                        # building with two rows for one supply counts its consumption twice.
                        if not looks_like_uuid(safe_row.get("id")):
                            _mh = meter_hint(row)
                            _known = await _meters.find(_mh) if _mh else None
                            if _known:
                                safe_row["id"] = _known
                                meters_matched += 1
                        if safe_row.get("is_sub_meter") in (None, ""):
                            safe_row["is_sub_meter"] = is_sub_meter_for(row)
                        if not safe_row.get("meter_type"):
                            safe_row["meter_type"] = meter_type_for(row)

                    # Codes and names to ids, for whichever of these columns this table has.
                    for _ref_col in REFERENCES:
                        if _ref_col not in db_cols:
                            continue
                        if looks_like_uuid(safe_row.get(_ref_col)):
                            continue
                        _rh = hint_for(_ref_col, row)
                        _rid = await _refs.resolve(_ref_col, _rh) if _rh else None
                        if _rid:
                            safe_row[_ref_col] = _rid
                        else:
                            # Absent rather than guessed. A name written into a uuid column
                            # fails the row; a wrong id is worse, because it succeeds.
                            safe_row.pop(_ref_col, None)

                    # The meter link. A reading carries no building of its own; it reaches one
                    # through its meter, so resolving the meter is what places the reading.
                    if safe_table == "meter_readings" \
                            and not looks_like_uuid(safe_row.get("meter_id")):
                        _mh = meter_hint(row)
                        _mid = None
                        if _mh:
                            _mpan, _mprn = supply_numbers(row)
                            _mbid = ((await _buildings.resolve(_hint)) if _hint else None)                                 or _default_building
                            _mid = await _meters.resolve(
                                _mh,
                                building_id=_mbid,
                                meter_type=meter_type_for(row), mpan=_mpan, mprn=_mprn,
                                section_id=await _section_for(_mbid, section_hint(row)),
                                is_sub_meter=is_sub_meter_for(row),
                            )
                        if _mid:
                            safe_row["meter_id"] = _mid
                            meters_linked += 1
                        else:
                            # Left absent rather than guessed. meter_id is NOT NULL, so the row
                            # is skipped and reported, which is the honest outcome: a reading on
                            # the wrong meter is a year of consumption on the wrong building.
                            safe_row.pop("meter_id", None)
                    normalized_records.append(safe_row)

                if missing_columns and safe_table in _KNOWN_CORE_TABLES:
                    # Columns the HUMAN explicitly approved as "New column" are created even on an
                    # ORM-managed core table — dropping them would silently discard an explicit
                    # decision and lose the data. Everything else is still dropped.
                    _approved = {
                        str(c).lower()
                        for c in (approved_new_columns or {}).get(safe_table, set())
                    }
                    _keep = {c: t for c, t in missing_columns.items() if c.lower() in _approved}
                    _drop = {c: t for c, t in missing_columns.items() if c.lower() not in _approved}
                    if _drop:
                        logger.warning(
                            f"[Node 9] Dropping {len(_drop)} unknown column(s) "
                            f"from core table {safe_table}: {sorted(_drop)!r} "
                            "— schema is ORM-managed, ALTER TABLE skipped"
                        )
                    if _keep:
                        logger.info(
                            f"[Node 9] Creating {len(_keep)} user-approved new column(s) on core "
                            f"table {safe_table}: {sorted(_keep)!r}"
                        )
                    missing_columns = _keep

                for col_name, col_type in missing_columns.items():
                    await session.execute(
                        text(
                            f"ALTER TABLE {schema_name}.{safe_table} "
                            f"ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                        )
                    )
                    logger.info(
                        f"[Node 9] Added missing column {schema_name}.{safe_table}.{col_name} "
                        f"({col_type})"
                    )
                if missing_columns:
                    # keep local set in sync for filtering inserts below
                    db_cols.update(missing_columns.keys())
                    # Newly created columns use inferred SQL types.
                    db_col_type_map.update(missing_columns)

                # Log column acceptance/rejection for this table
                if normalized_records:
                    _first_row = normalized_records[0]
                    _accepted = [k for k in _first_row if k in db_cols]
                    _rejected = [k for k in _first_row if k not in db_cols]
                    if _rejected:
                        logger.info(
                            f"[Node 9]   {safe_table}: accepting {len(_accepted)} columns, "
                            f"dropping {len(_rejected)} unknown: {_rejected[:8]!r}"
                        )
                    else:
                        logger.info(
                            f"[Node 9]   {safe_table}: {len(_accepted)} columns accepted"
                        )

                # If the table's PK is a serial/integer, never inject a UUID
                # string into it — let the DB sequence auto-generate it.
                _id_db_type = db_col_type_map.get("id", "").lower()
                _id_is_serial = any(t in _id_db_type for t in ("int", "serial", "bigint"))
                if _id_is_serial:
                    logger.info(
                        f"[Node 9]   {safe_table}: id is integer/serial — "
                        "skipping id column, DB will auto-generate"
                    )

                # ── Widen a NUMERIC destination column to TEXT when the source data is non-numeric ──
                # e.g. plenum_cafm.technicians.tech_id is INTEGER, but the source values are codes
                # like 'T00654'. Instead of skipping every such row (data loss), reconcile the column
                # to the RECEIVED datatype: ALTER it to text so all rows land. Only ever WIDENS
                # (int → text), never narrows. Scans a sample for a fast early-exit.
                _num_types = ("int", "bigint", "smallint", "numeric", "decimal", "double", "real", "serial")
                _SCAN_CAP = 5000
                for _col, _dbt in list(db_col_type_map.items()):
                    if (_col == "id" and _id_is_serial) or not any(t in _dbt.lower() for t in _num_types):
                        continue
                    _needs_text = False
                    for _i, _rec in enumerate(normalized_records):
                        if _i >= _SCAN_CAP:
                            break
                        _v = _rec.get(_col)
                        if _v is None or str(_v).strip() == "":
                            continue
                        try:
                            float(str(_v).replace(",", ""))
                        except (ValueError, TypeError):
                            _needs_text = True
                            break
                    if not _needs_text:
                        continue
                    try:
                        async with session.begin_nested():
                            await session.execute(text(
                                f'ALTER TABLE {schema_name}.{safe_table} '
                                f'ALTER COLUMN "{_col}" TYPE TEXT USING "{_col}"::text'
                            ))
                        db_col_type_map[_col] = "text"
                        logger.warning(
                            f"[Node 9] Widened {safe_table}.{_col} ({_dbt} → TEXT) — source data is "
                            "non-numeric (e.g. code values); rows kept instead of skipped"
                        )
                    except Exception as _alter_exc:
                        logger.warning(
                            f"[Node 9] Could not widen {safe_table}.{_col} to TEXT: {_alter_exc}"
                        )

                tbl_rows_skipped = 0
                table_rows = 0
                # Per-column type-mismatch tally for this table: {column: (count, dest_type, sample)}.
                # A value that cannot fit its destination column's type (e.g. frequency='Quarterly'
                # → frequency_value INTEGER) is dropped from the row (so the rest of the row still
                # inserts) and recorded here, then surfaced so the user can re-map that column at the
                # B14.1 column-mapping gate instead of losing every row to a single bad field.
                type_mismatch_by_col: dict[str, tuple[int, str, str]] = {}
                # Per-column orphan-FK tally: {fk_column: rows nulled because the parent was absent}.
                orphan_fk_by_col: dict[str, int] = {}
                # {column: rows} filled with a system default because the source had none.
                _sys_filled: dict[str, int] = {}
                # {table: rows} already present from an earlier run, skipped instead of duplicated.
                _dupes_skipped: dict[str, int] = {}
                #: Rows waiting to go in one statement. Flushed every _WRITE_CHUNK and
                #: again at the end of the table.
                _pending_rows: list[tuple[dict, str, dict]] = []
                for normalized in normalized_records:
                    filtered = {
                        k: v for k, v in normalized.items()
                        if k in db_cols and v is not None and str(v) != ""
                        and not (k == "id" and _id_is_serial)
                    }

                    if "id" in db_cols and "id" not in filtered and not _id_is_serial:
                        filtered["id"] = str(uuid.uuid4())

                    if not filtered:
                        continue

                    for k in list(filtered.keys()):
                        _dest_type = db_col_type_map.get(k, "")
                        _coerced = _coerce_value_for_db_type(filtered[k], _dest_type)
                        if _coerced is _COERCE_TYPE_MISMATCH:
                            # Drop just this field (keep the row) and remember the mismatch.
                            _prev = type_mismatch_by_col.get(k)
                            type_mismatch_by_col[k] = (
                                (_prev[0] if _prev else 0) + 1,
                                _dest_type,
                                _prev[2] if _prev else str(filtered[k])[:40],
                            )
                            del filtered[k]
                        else:
                            filtered[k] = _coerced

                    if not filtered:
                        continue

                    # An asset re-imported under a code the organisation already has is the SAME
                    # asset: merge into that row (building kept, code filled in) rather than insert
                    # a twin with a fresh id and no building. The June 2026 import stored the code
                    # in `id` with no asset_code, so the lookup matches on either.
                    if safe_table == "assets":
                        _code = asset_match_code(filtered)
                        _existing = await _existing_asset_id(_code) if _code else ""
                        if _existing:
                            _usql, _uparams = build_asset_merge_update(schema_name, filtered, _existing)
                            try:
                                async with session.begin_nested():
                                    _ures = await session.execute(text(_usql), _uparams)
                                    table_rows += int(getattr(_ures, "rowcount", 0) or 0)
                                rows_merged += 1
                                continue
                            except Exception as _merge_exc:
                                logger.warning(
                                    f"[Node 9] assets: merge into existing {_existing} for code "
                                    f"{_code!r} failed ({str(_merge_exc)[:120]}); inserting instead"
                                )

                    # Fill the columns the database requires, has no default for, and the
                    # source could not have known about. Done last, so anything the file DID
                    # supply always wins.
                    for _rc in db_required_undefaulted:
                        if _rc in filtered or _rc not in db_cols:
                            continue
                        _sv = _system_default_for_db_type(
                            _rc, db_col_type_map.get(_rc, ""), str(effective_org_id or "") or None
                        )
                        if _sv is not _NO_SYSTEM_DEFAULT:
                            filtered[_rc] = _sv
                            _sys_filled[_rc] = _sys_filled.get(_rc, 0) + 1

                    # Already written by an earlier run? Skip rather than insert a twin.
                    # The bare ON CONFLICT DO NOTHING below cannot catch this: `id` is a fresh
                    # uuid4() every time, so there is never a primary-key collision to catch.
                    _dupe = False
                    for _cols, _vals in _natural_keys_for(safe_table, filtered, db_cols):
                        if await _already_written(
                            safe_table, _cols, _vals, "organization_id" in db_cols
                        ):
                            _dupe = True
                            break
                    if _dupe:
                        _dupes_skipped[safe_table] = _dupes_skipped.get(safe_table, 0) + 1
                        continue

                    try:
                        dml_sql, params = _build_dml_for_row(
                            schema_name, safe_table, filtered, unique_sets
                        )
                        _pending_rows.append((dict(filtered), dml_sql, params))
                        if len(_pending_rows) >= _WRITE_CHUNK:
                            _batch = await _insert_rows(
                                session, schema_name=schema_name, table_name=safe_table,
                                pending=_pending_rows, unique_sets=unique_sets,
                                nullable_cols=db_nullable_cols,
                            )
                            _pending_rows = []
                            table_rows += _batch["inserted"]
                            rows_skipped += _batch["skipped"]
                            tbl_rows_skipped += _batch["skipped"]
                            for _c, _n in _batch["orphans"].items():
                                orphan_fk_by_col[_c] = orphan_fk_by_col.get(_c, 0) + _n
                            for _e in _batch["errors"]:
                                if len(row_errors) < 20:
                                    row_errors.append(_e)
                        continue
                    except ConnectionLost:
                        raise
                    except Exception as row_exc:
                        # Orphan foreign key: the row references a parent that isn't present (e.g.
                        # work_orders.asset_id = 'A0050145' with no such asset). Rather than drop the
                        # whole row, null the offending FK column IF it is nullable and retry once —
                        # the row lands without the broken link. A NOT NULL FK can't be nulled, so it
                        # is skipped as before. Parent-before-child write order (below) already
                        # resolves references whose parent DOES exist; this handles true orphans.
                        _fk_cols = _foreign_key_columns_from_error(row_exc)
                        _nullable_fk = [c for c in _fk_cols if c in db_nullable_cols and c in filtered]
                        if _nullable_fk:
                            try:
                                _retry = dict(filtered)
                                for _c in _nullable_fk:
                                    _retry.pop(_c, None)
                                _dml2, _p2 = _build_dml_for_row(schema_name, safe_table, _retry, unique_sets)
                                async with session.begin_nested():
                                    _res2 = await session.execute(text(_dml2), _p2)
                                    table_rows += int(getattr(_res2, "rowcount", 0) or 0)
                                _orph = orphan_fk_by_col
                                for _c in _nullable_fk:
                                    _orph[_c] = _orph.get(_c, 0) + 1
                                continue
                            except Exception:
                                pass  # retry failed → fall through to skip
                        rows_skipped += 1
                        tbl_rows_skipped += 1
                        if len(row_errors) < 20:
                            row_errors.append(
                                f"{safe_table}: {str(row_exc)[:220]}"
                            )
                        logger.warning(
                            f"[Node 9] Skipping bad row in {safe_table}: {row_exc}"
                        )
                        continue

                # Whatever is left over from the last partial batch.
                if _pending_rows:
                    _batch = await _insert_rows(
                        session, schema_name=schema_name, table_name=safe_table,
                        pending=_pending_rows, unique_sets=unique_sets,
                        nullable_cols=db_nullable_cols,
                    )
                    _pending_rows = []
                    table_rows += _batch["inserted"]
                    rows_skipped += _batch["skipped"]
                    tbl_rows_skipped += _batch["skipped"]
                    for _c, _n in _batch["orphans"].items():
                        orphan_fk_by_col[_c] = orphan_fk_by_col.get(_c, 0) + _n
                    for _e in _batch["errors"]:
                        if len(row_errors) < 20:
                            row_errors.append(_e)
                    if _batch["skipped"]:
                        logger.warning(
                            f"[Node 9] {safe_table}: {_batch['skipped']} row(s) skipped; "
                            f"first: {(_batch['errors'] or ['-'])[0]}"
                        )

                # Surface any type mismatches: a column whose source values don't fit the
                # destination column's type. These were dropped (row still inserted) — the user
                # should re-map that column at the B14.1 gate (e.g. frequency → frequency_type).
                for _col, (_cnt, _dtype, _sample) in type_mismatch_by_col.items():
                    _msg = (
                        f"{safe_table}.{_col}: {_cnt} value(s) did not fit column type "
                        f"'{_dtype}' (e.g. '{_sample}') — dropped; re-map this column to a "
                        f"compatible destination at the column-mapping gate."
                    )
                    if len(row_errors) < 20:
                        row_errors.append(_msg)
                    logger.warning(f"[Node 9] Type mismatch — {_msg}")

                # Surface orphan FKs: rows kept by nulling a foreign key whose parent was absent.
                for _col, _cnt in orphan_fk_by_col.items():
                    _msg = (
                        f"{safe_table}.{_col}: {_cnt} row(s) referenced a parent not present in the "
                        f"target — foreign key set to NULL so the row was kept. Load the parent rows "
                        f"(or confirm these are expected orphans) if the link is required."
                    )
                    if len(row_errors) < 20:
                        row_errors.append(_msg)
                    logger.warning(f"[Node 9] Orphan FK — {_msg}")

                _tbl_elapsed = (datetime.utcnow() - _tbl_started_at).total_seconds()
                if table_rows > 0:
                    tables_written += 1
                    rows_inserted += table_rows
                    logger.info(
                        f"[Node 9]   {safe_table}: ✓ {table_rows} inserted, "
                        f"{tbl_rows_skipped} skipped ({_tbl_elapsed:.1f}s)"
                    )
                elif tbl_rows_skipped:
                    logger.warning(
                        f"[Node 9]   {safe_table}: 0 inserted, {tbl_rows_skipped} skipped "
                        f"({_tbl_elapsed:.1f}s)"
                    )
                else:
                    logger.info(
                        f"[Node 9]   {safe_table}: 0 rows to insert ({_tbl_elapsed:.1f}s)"
                    )

                # Outside the branches above on purpose: a table whose every row was already
                # on file inserts nothing, and "0 inserted" on its own reads as a failure.
                # The reason it inserted nothing is the thing worth saying.
                if _dupes_skipped.get(safe_table):
                    logger.info(
                        f"[Node 9]   {safe_table}: {_dupes_skipped[safe_table]} row(s) already "
                        f"present from an earlier run — skipped, not duplicated"
                    )
                if _sys_filled:
                    logger.info(
                        f"[Node 9]   {safe_table}: filled required column(s) the source does "
                        f"not carry — "
                        + ", ".join(f"{c} x{n}" for c, n in sorted(_sys_filled.items()))
                    )

            logger.info(
                f"[Node 9] Schema-aligned write done — "
                f"{rows_inserted} row(s) across {tables_written} table(s), "
                f"{rows_skipped} skipped, {rows_merged} merged into existing assets, "
                f"{buildings_linked} building link(s) resolved, "
                f"{meters_linked} reading(s) placed on a meter "
                f"({_meters.created} meter(s) created), "
                f"{_refs.resolved} reference(s) resolved, "
                f"{meters_matched} meter(s) matched to one already on record"
                + (f"; ambiguous building hints: {_buildings.ambiguous[:5]!r}" if _buildings.ambiguous else "")
            )
            if _buildings.ambiguous and len(row_errors) < 20:
                row_errors.append(
                    "building: " + ", ".join(sorted(set(_buildings.ambiguous))[:5])
                    + " matched more than one building — the rows were written without a building link"
                )
            if _meters.unlinked and len(row_errors) < 20:
                row_errors.append(
                    "meter: " + ", ".join(sorted(set(_meters.unlinked))[:5])
                    + " named no meter already on record, and the rows named no building to "
                      "create one against — those readings were skipped rather than written "
                      "to a meter that belongs to no building"
                )
            if _meters.ambiguous and len(row_errors) < 20:
                row_errors.append(
                    "meter: " + ", ".join(sorted(set(_meters.ambiguous))[:5])
                    + " matched more than one meter — those readings were skipped"
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return {
        "rows_inserted": rows_inserted,
        "tables_written": tables_written,
        "rows_skipped": rows_skipped,
        "rows_merged": rows_merged,
        "buildings_linked": buildings_linked,
        "meters_linked": meters_linked,
        "meters_created": _meters.created,
        "meters_matched": meters_matched,
        "meters_unlinked": sorted(set(_meters.unlinked)),
        "references": _refs.report(),
        "row_errors": row_errors,
    }


async def _resolve_valid_organization_id(
    session: AsyncSession,
    requested_org_id: str,
    schema_name: str,
) -> str:
    """
    Ensure organization_id used for inserts exists in organizations table.
    """
    requested = (requested_org_id or "").strip()
    if requested:
        try:
            _found = False
            # SAVEPOINT: a failed SELECT must not abort the outer transaction.
            async with session.begin_nested():
                exists_rs = await session.execute(
                    text(
                        f"SELECT 1 FROM {schema_name}.organizations "
                        f"WHERE id::text = :org_id LIMIT 1"
                    ),
                    {"org_id": requested},
                )
                _found = exists_rs.first() is not None
            if _found:
                return requested
        except Exception as _lookup_err:
            logger.warning(
                f"[Node 9] org_id lookup failed ({_lookup_err}); falling back to first org"
            )

    try:
        _fallback_id = None
        async with session.begin_nested():
            fallback_rs = await session.execute(
                text(
                    f"SELECT id::text FROM {schema_name}.organizations "
                    f"ORDER BY created_at ASC LIMIT 1"
                )
            )
            _fallback_id = fallback_rs.scalar_one_or_none()
    except Exception as _fb_err:
        raise Exception(
            "org fallback query failed — no organizations found in target DB"
        ) from _fb_err
    fallback = _fallback_id
    if fallback:
        logger.warning(
            "[Node 9] Requested organization_id missing; using existing organization_id="
            f"{fallback}"
        )
        return str(fallback)

    raise Exception(
        "No organizations found in target DB; cannot satisfy assets.organization_id FK"
    )
