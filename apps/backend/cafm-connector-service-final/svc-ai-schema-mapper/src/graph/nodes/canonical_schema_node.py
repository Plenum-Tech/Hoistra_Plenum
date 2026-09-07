"""Node 0: Canonical Schema Fetch — Introspect plenum_cafm database schema.

This is the reference schema that external CMMS schemas will be mapped TO.
Uses the same pattern as SchemaIntrospectionService to fetch the actual plenum_cafm schema.
"""

import logging
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ..schema_state import SchemaMappingState, SchemaTableInfo, SchemaMappingFieldInfo

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

SCHEMA_NAME = "plenum_cafm"

# Field description patterns (from SchemaIntrospectionService)
FIELD_PATTERNS = {
    "code": "Unique identifier or code",
    "id": "Primary or foreign key identifier",
    "name": "Human-readable name or title",
    "description": "Detailed description or notes",
    "status": "Current status or state",
    "priority": "Priority level or ranking",
    "type": "Type or category classification",
    "date": "Date value",
    "timestamp": "Date and time value",
}


async def canonical_schema_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 0: Fetch and parse plenum_cafm canonical schema.

    This is the reference schema that will be mapped TO.
    All external CMMS fields will be mapped to columns in this schema.

    Args:
        state: SchemaMappingState (should have db_url from settings)

    Returns:
        Updated state with canonical_tables, canonical_table_count, canonical_column_count
    """
    schema_mapping_id = state.get("schema_mapping_id")
    db_url = state.get("db_url")
    _node_started_at = datetime.utcnow()

    logger.info(f"[Node 0] Starting canonical schema fetch: mapping_id={schema_mapping_id}")

    try:
        if not db_url:
            raise ValueError("db_url required for canonical schema introspection")

        logger.info(f"[Node 0] Connecting to plenum_cafm database...")

        # Introspect the plenum_cafm schema
        canonical_tables = await _introspect_canonical_schema(db_url)

        if not canonical_tables:
            raise ValueError(f"No tables found in {SCHEMA_NAME} schema")

        # ── Count and validate ─────────────────────────────────────────
        canonical_table_count = len(canonical_tables)
        canonical_column_count = sum(len(table_info.get("columns", [])) for table_info in canonical_tables.values())

        logger.info(
            f"[Node 0] ✓ Canonical schema fetched: {canonical_table_count} tables, {canonical_column_count} columns"
        )

        # ── Update state ───────────────────────────────────────────────
        state["canonical_tables"] = canonical_tables
        state["canonical_table_count"] = canonical_table_count
        state["canonical_column_count"] = canonical_column_count
        state["notes"] = state.get("notes", []) + [
            f"Node 0: Fetched {canonical_table_count} canonical tables from {SCHEMA_NAME} ({canonical_column_count} total columns)"
        ]

        schema_mapping_id = state.get("schema_mapping_id")
        if schema_mapping_id:
            from .schema_db_writer import schema_write_step_pause_auto
            table_names = sorted(canonical_tables.keys())
            payload = {
                "node": 0,
                "title": "Canonical Schema Fetched",
                "canonical_table_count": canonical_table_count,
                "canonical_column_count": canonical_column_count,
                "tables_data": [
                    {
                        "table": t,
                        "column_count": len(canonical_tables[t].get("columns", [])),
                        "columns": len(canonical_tables[t].get("columns", [])),
                        "all_cols": [c["field_name"] for c in canonical_tables[t].get("columns", [])],
                        "column_details": canonical_tables[t].get("columns", []),
                    }
                    for t in table_names
                ],
            }
            await schema_write_step_pause_auto(
                schema_mapping_id, 0, "step_0_canonical", payload
            )
            from .schema_db_writer import schema_append_node_log_auto
            await schema_append_node_log_auto(
                schema_mapping_id, 0, "Canonical Schema Fetch", _node_started_at, datetime.utcnow(),
                output={"canonical_table_count": canonical_table_count, "canonical_column_count": canonical_column_count},
                logs=[f"Loaded {canonical_table_count} canonical tables with {canonical_column_count} columns from {SCHEMA_NAME}"],
            )

        return state

    except Exception as e:
        logger.exception(f"[Node 0] ✗ Error: {e}")
        state["status"] = "error"
        state["error_message"] = f"Canonical schema fetch failed: {str(e)}"
        return state


async def _introspect_canonical_schema(db_url: str) -> dict[str, SchemaTableInfo]:
    """
    Introspect plenum_cafm schema using information_schema.

    Same pattern as SchemaIntrospectionService._get_canonical_fields()
    but returns full SchemaTableInfo objects instead of just field descriptions.
    """
    from ...db import _async_engine_connect_args, _strip_sslmode_from_async_url

    engine = create_async_engine(
        _strip_sslmode_from_async_url(db_url),
        echo=False,
        poolclass=NullPool,
        connect_args=_async_engine_connect_args(db_url),
    )

    try:
        async with engine.connect() as conn:
            # Get all table names from plenum_cafm schema
            result = await conn.execute(
                text("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = :schema_name
                    AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """),
                {"schema_name": SCHEMA_NAME}
            )
            table_names = [row[0] for row in result.fetchall()]

        logger.info(f"[Node 0] Found {len(table_names)} tables in {SCHEMA_NAME} schema")

        canonical_tables: dict[str, SchemaTableInfo] = {}

        for table_name in table_names:
            logger.debug(f"[Node 0] Introspecting table: {table_name}")

            async with engine.connect() as conn:
                # Get columns for this table
                result = await conn.execute(
                    text("""
                        SELECT
                            column_name,
                            data_type,
                            is_nullable,
                            column_default
                        FROM information_schema.columns
                        WHERE table_name = :table_name
                        AND table_schema = :schema_name
                        ORDER BY ordinal_position
                    """),
                    {"table_name": table_name, "schema_name": SCHEMA_NAME}
                )
                columns_raw = result.fetchall()

                # Get PK using information_schema (more portable across PostgreSQL versions)
                result = await conn.execute(
                    text("""
                        SELECT a.attname
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                            ON tc.constraint_name = kcu.constraint_name
                        JOIN pg_attribute a ON a.attname = kcu.column_name
                        WHERE tc.table_name = :table_name
                        AND tc.table_schema = :schema_name
                        AND tc.constraint_type = 'PRIMARY KEY'
                        LIMIT 1
                    """),
                    {"table_name": table_name, "schema_name": SCHEMA_NAME}
                )
                pk_row = result.fetchone()
                pk_column = pk_row[0] if pk_row else None

                # Get FKs
                result = await conn.execute(
                    text("""
                        SELECT
                            kcu.column_name,
                            ccu.table_name as foreign_table,
                            ccu.column_name as foreign_column
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                            ON tc.constraint_name = kcu.constraint_name
                        JOIN information_schema.constraint_column_usage ccu
                            ON ccu.constraint_name = tc.constraint_name
                        WHERE tc.table_name = :table_name
                        AND tc.table_schema = :schema_name
                        AND tc.constraint_type = 'FOREIGN KEY'
                    """),
                    {"table_name": table_name, "schema_name": SCHEMA_NAME}
                )
                fk_rows = result.fetchall()
                fk_map = {row[0]: (row[1], row[2]) for row in fk_rows}

            # Build columns list with descriptions
            columns: list[SchemaMappingFieldInfo] = []
            for col_name, col_type, is_nullable, col_default in columns_raw:
                is_fk = col_name in fk_map
                fk_target_table, fk_target_col = fk_map[col_name] if is_fk else (None, None)

                # Generate description based on field patterns
                description = _generate_field_description(col_name, table_name, col_type)

                field_info: SchemaMappingFieldInfo = {
                    "field_name": col_name,
                    "data_type": col_type,
                    "nullable": is_nullable == "YES",
                    "is_primary_key": col_name == pk_column,
                    "is_foreign_key": is_fk,
                    "fk_target_table": fk_target_table,
                    "fk_target_column": fk_target_col,
                    "description": description,
                }
                columns.append(field_info)

            table_info: SchemaTableInfo = {
                "table_name": table_name,
                "primary_key": pk_column,
                "columns": columns,
                "row_count": None,
                "description": f"Table {table_name} with {len(columns)} columns in {SCHEMA_NAME} schema",
            }
            canonical_tables[table_name] = table_info

        logger.info(f"[Node 0] ✓ Canonical schema introspection complete: {len(canonical_tables)} tables")
        return canonical_tables

    finally:
        await engine.dispose()


def _generate_field_description(col_name: str, table_name: str, data_type: str) -> str:
    """Generate a human-readable description for a field based on naming patterns."""
    col_lower = col_name.lower()

    # Check if column name contains any field patterns
    for pattern, description in FIELD_PATTERNS.items():
        if pattern in col_lower:
            return f"{description} ({table_name}.{col_name}: {data_type})"

    # Default: just use column info
    return f"{col_name} ({table_name}: {data_type})"
