"""Node 1: Schema Ingest — Parse external CMMS schema definition.

Handles:
1. Introspect database OR parse YAML/JSON/DDL schema definition
2. Extract table names, columns, data types, FK info
3. Generate column metadata and descriptions
4. Build schema summary
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ...matchers import describe_dataset
from ..schema_state import SchemaMappingState, SchemaTableInfo, SchemaMappingFieldInfo

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def schema_ingest_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 1: Ingest and parse external CMMS schema definition.

    Supports:
    - Live database introspection (via DB URL)
    - YAML schema definition
    - JSON schema definition
    - SQL DDL statements

    Args:
        state: SchemaMappingState with schema_source and schema_format

    Returns:
        Updated state with external_tables, table_count, total_columns, schema_summary
    """

    _node_started_at = datetime.utcnow()
    schema_mapping_id = state.get("schema_mapping_id")
    schema_source = state.get("external_schema_source")  # database_url | yaml_file | json_file | ddl_sql | fiix_api
    schema_format = state.get("external_schema_format")  # sql | yaml | json
    external_cmms_name = state.get("external_cmms_name", "Unknown")
    schema_content = state.get("schema_content")

    logger.info(
        f"[Node 1] Starting schema ingest: mapping_id={schema_mapping_id}, "
        f"source={schema_source}, format={schema_format}, cmms={external_cmms_name}"
    )
    logger.info(
        f"[Node 1] State keys: {list(state.keys())}, "
        f"schema_content_present={schema_content is not None}, "
        f"schema_content_length={len(schema_content) if schema_content else 0}"
    )

    try:
        # ── Step 1: Parse schema definition ────────────────────────────
        if schema_source == "database_url":
            logger.info("[Node 1] Introspecting live database...")
            external_tables = await _introspect_database(state)
        elif schema_source == "yaml_file":
            logger.info("[Node 1] Parsing YAML schema definition...")
            external_tables = _parse_yaml_schema(state)
        elif schema_source == "json_file":
            logger.info("[Node 1] Parsing JSON schema definition...")
            external_tables = _parse_json_schema(state)
        elif schema_source == "ddl_sql":
            logger.info("[Node 1] Parsing SQL DDL statements...")
            external_tables = _parse_ddl_schema(state)
        elif schema_source == "fiix_api":
            logger.info("[Node 1] Parsing Fiix API mapper config (JSON format)...")
            external_tables = _parse_fiix_mapper_config(state)  # Fiix has different structure
        else:
            raise ValueError(f"Unknown schema_source: {schema_source}")

        if not external_tables:
            raise ValueError("No tables found in schema definition")

        # ── Step 2: Validate and count ─────────────────────────────────
        table_count = len(external_tables)
        total_columns = sum(len(table_info.get("columns", [])) for table_info in external_tables.values())

        logger.info(f"[Node 1] Parsed schema: {table_count} tables, {total_columns} columns")

        # ── Step 3: Generate human-readable summary ────────────────────
        schema_summary = _generate_schema_summary(external_tables, external_cmms_name)

        # ── Step 4: EL validation ──────────────────────────────────────
        # EL-1.0: Schema must have at least 1 table with 1 column
        if table_count < 1:
            raise ValueError("Schema must contain at least 1 table")
        if total_columns < 1:
            raise ValueError("Schema must contain at least 1 column")

        logger.info(f"[Node 1] ✓ Schema validation passed")

        # ── Step 5: Update state ───────────────────────────────────────
        state["external_tables"] = external_tables
        state["table_count"] = table_count
        state["total_columns"] = total_columns
        state["schema_summary"] = schema_summary
        state["status"] = "mapping"
        state["notes"] = state.get("notes", []) + [
            f"Ingested {table_count} tables with {total_columns} columns from {external_cmms_name}"
        ]

        schema_mapping_id = state.get("schema_mapping_id")
        if schema_mapping_id:
            from .schema_db_writer import schema_write_step_pause_auto
            # Build table-wise data with ALL column names for per-table display
            tables_data = [
                {
                    "table": t,
                    "column_count": len(external_tables[t].get("columns", [])),
                    "columns": len(external_tables[t].get("columns", [])),
                    "all_cols": [c["field_name"] for c in external_tables[t].get("columns", [])],
                    "column_details": external_tables[t].get("columns", []),
                }
                for t in sorted(external_tables.keys())
            ]
            payload = {
                "node": 1,
                "title": "Schema Ingested",
                "external_cmms_name": external_cmms_name,
                "schema_source": schema_source,
                "table_count": table_count,
                "total_columns": total_columns,
                "tables_data": tables_data,
            }
            await schema_write_step_pause_auto(
                schema_mapping_id, 1, "step_1_ingest", payload
            )
            from .schema_db_writer import schema_append_node_log_auto
            await schema_append_node_log_auto(
                schema_mapping_id, 1, "Schema Ingestion", _node_started_at, datetime.utcnow(),
                output={"table_count": table_count, "total_columns": total_columns,
                        "tables": sorted(external_tables.keys())},
                logs=[f"Parsed schema from {schema_source}", f"Found {table_count} tables with {total_columns} columns"],
            )

        logger.info(f"[Node 1] ✓ Schema ingest complete")
        return state

    except Exception as e:
        logger.exception(f"[Node 1] ✗ Error: {e}")
        state["status"] = "error"
        state["error_message"] = f"Schema ingest failed: {str(e)}"
        return state


# ────────────────────────────────────────────────────────────────────────────
# Schema Parsing Implementations
# ────────────────────────────────────────────────────────────────────────────


async def _introspect_database(state: SchemaMappingState) -> dict[str, SchemaTableInfo]:
    """Introspect live database schema via SQLAlchemy."""
    db_url = state.get("db_url")
    if not db_url:
        raise ValueError("db_url required for database_url schema source")

    logger.info(f"[Node 1] Connecting to: {db_url[:50]}...")

    from ...db import _async_engine_connect_args, _strip_sslmode_from_async_url

    engine = create_async_engine(
        _strip_sslmode_from_async_url(db_url),
        echo=False,
        poolclass=NullPool,
        connect_args=_async_engine_connect_args(db_url),
    )

    try:
        async with engine.begin() as conn:
            # Get table names
            result = await conn.execute(
                text("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """)
            )
            table_names = [row[0] for row in result.fetchall()]

        logger.info(f"[Node 1] Found {len(table_names)} tables")

        external_tables: dict[str, SchemaTableInfo] = {}

        for table_name in table_names:
            logger.info(f"[Node 1] Introspecting table: {table_name}")

            async with engine.begin() as conn:
                # Get columns
                result = await conn.execute(
                    text(f"""
                        SELECT
                            column_name,
                            data_type,
                            is_nullable,
                            column_default
                        FROM information_schema.columns
                        WHERE table_name = '{table_name}'
                        AND table_schema = 'public'
                        ORDER BY ordinal_position
                    """)
                )
                columns_raw = result.fetchall()

                # Get PK
                result = await conn.execute(
                    text(f"""
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_attribute a ON a.attrelid = i.indrelid
                        WHERE i.indrelname = '{table_name}_pkey'
                        LIMIT 1
                    """)
                )
                pk_row = result.fetchone()
                pk_column = pk_row[0] if pk_row else None

                # Get FKs
                result = await conn.execute(
                    text(f"""
                        SELECT
                            kcu.column_name,
                            ccu.table_name as foreign_table,
                            ccu.column_name as foreign_column
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                            ON tc.constraint_name = kcu.constraint_name
                        JOIN information_schema.constraint_column_usage ccu
                            ON ccu.constraint_name = tc.constraint_name
                        WHERE tc.table_name = '{table_name}'
                        AND tc.constraint_type = 'FOREIGN KEY'
                    """)
                )
                fk_rows = result.fetchall()
                fk_map = {row[0]: (row[1], row[2]) for row in fk_rows}

            # Build columns list
            columns: list[SchemaMappingFieldInfo] = []
            for col_name, col_type, is_nullable, col_default in columns_raw:
                is_fk = col_name in fk_map
                fk_target_table, fk_target_col = fk_map[col_name] if is_fk else (None, None)

                field_info: SchemaMappingFieldInfo = {
                    "field_name": col_name,
                    "data_type": col_type,
                    "nullable": is_nullable == "YES",
                    "is_primary_key": col_name == pk_column,
                    "is_foreign_key": is_fk,
                    "fk_target_table": fk_target_table,
                    "fk_target_column": fk_target_col,
                    "description": f"{col_name} ({col_type})",
                    "sample_values": None,
                    "avg_char_length": None,
                    "max_char_length": None,
                }
                columns.append(field_info)

            # Fetch sample values for text-type columns (single query per table)
            text_cols = [
                c["field_name"] for c in columns
                if _is_text_type(c["data_type"]) and not c["is_primary_key"]
            ]
            if text_cols:
                async with engine.begin() as conn_s:
                    samples = await _fetch_column_samples(conn_s, table_name, text_cols)
                for col in columns:
                    raw = samples.get(col["field_name"])
                    if raw:
                        col["sample_values"] = raw
                        lengths = [len(str(v)) for v in raw if v is not None]
                        col["avg_char_length"] = int(sum(lengths) / len(lengths)) if lengths else 0
                        col["max_char_length"] = max(lengths) if lengths else 0

            table_info: SchemaTableInfo = {
                "table_name": table_name,
                "primary_key": pk_column,
                "columns": columns,
                "row_count": None,  # Optional
                "description": f"Table {table_name} with {len(columns)} columns",
            }
            external_tables[table_name] = table_info

        logger.info(f"[Node 1] ✓ Database introspection complete: {len(external_tables)} tables")
        return external_tables

    finally:
        await engine.dispose()


_TEXT_TYPES = {
    "text", "varchar", "character varying", "character", "char",
    "string", "nvarchar", "nchar", "clob", "longtext", "mediumtext",
}

def _is_text_type(data_type: str) -> bool:
    """Return True if the SQL data type is a string/text type."""
    if not data_type:
        return False
    low = data_type.lower().split("(")[0].strip()
    return low in _TEXT_TYPES or low == "string"


async def _fetch_column_samples(conn, table_name: str, text_cols: list[str]) -> dict[str, list]:
    """
    Fetch up to 5 non-null sample values per text column in a single query.
    Returns {col_name: [val, ...]} for columns that had non-null data.
    """
    samples: dict[str, list] = {}
    try:
        # Quote column names to handle reserved words
        quoted = ", ".join(f'"{c}"' for c in text_cols)
        result = await conn.execute(
            text(f'SELECT {quoted} FROM "{table_name}" LIMIT 50')
        )
        rows = result.fetchall()

        for i, col_name in enumerate(text_cols):
            vals = []
            for row in rows:
                v = row[i]
                if v is not None and str(v).strip():
                    vals.append(str(v))
                    if len(vals) >= 5:
                        break
            if vals:
                samples[col_name] = vals
    except Exception as exc:
        logger.debug(f"[Node 1] Sample fetch failed for {table_name}: {exc}")
    return samples


def _parse_yaml_schema(state: SchemaMappingState) -> dict[str, SchemaTableInfo]:
    """Parse YAML schema definition."""
    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML not installed; install with: pip install pyyaml")

    schema_content = state.get("schema_content")
    if not schema_content:
        raise ValueError("schema_content required for YAML parsing")

    schema_dict = yaml.safe_load(schema_content)
    return _build_tables_from_dict(schema_dict)


def _parse_json_schema(state: SchemaMappingState) -> dict[str, SchemaTableInfo]:
    """Parse JSON schema definition."""
    import json

    schema_content = state.get("schema_content")
    if not schema_content:
        raise ValueError("schema_content required for JSON parsing")

    schema_dict = json.loads(schema_content)
    return _build_tables_from_dict(schema_dict)


def _parse_ddl_schema(state: SchemaMappingState) -> dict[str, SchemaTableInfo]:
    """Parse SQL DDL statements."""
    # Simplified: extract table and column definitions from SQL
    # This is a basic implementation; real-world might use SQL parser
    schema_content = state.get("schema_content")
    if not schema_content:
        raise ValueError("schema_content required for DDL parsing")

    # For now, return empty - user would need to provide structured schema
    # In practice, could use sqlparse or similar
    logger.warning("[Node 1] DDL parsing not fully implemented; please provide YAML or JSON")
    return {}


def _parse_fiix_mapper_config(state: SchemaMappingState) -> dict[str, SchemaTableInfo]:
    """
    Parse Fiix API mapper config response.

    Fiix returns a mapper config with:
    {
      "source_system": "Fiix",
      "preserve_fiix_source_names": true,
      "canonical_fields": {plenum_column: description, ...},
      "vendor_aliases": {plenum_column: [fiix_field, ...], ...},
      "field_aliases_by_object": {object: {fiix_field: plenum_column}},
      "tables_by_object": {object_name: {fiix_field: plenum_column | null, ...}, ...}
    }

    External columns use native Fiix field names. ``migration_target`` on each column
    holds the plenum_cafm alias when known (context-aware per object).
    """
    schema_content = state.get("schema_content")
    if not schema_content:
        raise ValueError("schema_content required for Fiix mapper config parsing")

    import json
    mapper_config = json.loads(schema_content)

    tables_by_object = mapper_config.get("tables_by_object", {})
    sample_values_by_field = mapper_config.get("sample_values_by_field", {})

    if tables_by_object:
        # Preferred path — one table per Fiix source object type
        logger.info(f"[Node 1] Parsing Fiix config: {len(tables_by_object)} object types")
        external_tables: dict[str, SchemaTableInfo] = {}

        for obj_name, fields in tables_by_object.items():
            columns: list[SchemaMappingFieldInfo] = []
            for fiix_field, plenum_target in fields.items():
                raw_sample = sample_values_by_field.get(fiix_field)
                samples = [str(raw_sample)] if raw_sample is not None else []
                lengths = [len(s) for s in samples if s]
                is_fk = fiix_field.startswith("int") and fiix_field.endswith("ID")
                desc = (
                    f"{fiix_field} → {plenum_target}"
                    if plenum_target
                    else f"Fiix field {fiix_field} (unmapped)"
                )
                field_info: SchemaMappingFieldInfo = {
                    "field_name": fiix_field,
                    "data_type": "string",
                    "nullable": True,
                    "is_primary_key": fiix_field == "id",
                    "is_foreign_key": is_fk,
                    "fk_target_table": None,
                    "fk_target_column": None,
                    "description": desc,
                    "migration_target": plenum_target,
                    "sample_values": samples or None,
                    "avg_char_length": int(sum(lengths) / len(lengths)) if lengths else None,
                    "max_char_length": max(lengths) if lengths else None,
                }
                columns.append(field_info)

            table_info: SchemaTableInfo = {
                "table_name": obj_name,
                "primary_key": "id",
                "columns": columns,
                "row_count": None,
                "description": f"Fiix {obj_name} object ({len(columns)} fields)",
            }
            external_tables[obj_name] = table_info
            logger.info(f"[Node 1] Fiix object '{obj_name}': {len(columns)} fields")

        return external_tables

    # Fallback path — flat canonical_fields (old format without tables_by_object)
    canonical_fields = mapper_config.get("canonical_fields", {})
    if not canonical_fields:
        raise ValueError("Fiix mapper config has no canonical_fields or tables_by_object")

    logger.info(f"[Node 1] Parsing Fiix config (flat): {len(canonical_fields)} canonical fields")

    columns_flat: list[SchemaMappingFieldInfo] = []
    for field_name, description in canonical_fields.items():
        field_info = {
            "field_name": field_name,
            "data_type": "string",
            "nullable": True,
            "is_primary_key": False,
            "is_foreign_key": False,
            "fk_target_table": None,
            "fk_target_column": None,
            "description": description,
        }
        columns_flat.append(field_info)

    table_info_flat: SchemaTableInfo = {
        "table_name": "fiix_fields",
        "primary_key": "field_name",
        "columns": columns_flat,
        "row_count": None,
        "description": f"Fiix canonical fields ({len(columns_flat)} fields)",
    }
    external_tables_flat = {"fiix_fields": table_info_flat}
    logger.info(f"[Node 1] Created flat 'fiix_fields' table with {len(columns_flat)} columns")
    return external_tables_flat


def _build_tables_from_dict(schema_dict: dict[str, Any]) -> dict[str, SchemaTableInfo]:
    """Build SchemaTableInfo objects from parsed schema dictionary."""
    external_tables: dict[str, SchemaTableInfo] = {}

    tables = schema_dict.get("tables", [])
    if isinstance(tables, dict):
        tables = list(tables.values())

    for table_def in tables:
        table_name = table_def.get("name") or table_def.get("table_name")
        if not table_name:
            continue

        columns_raw = table_def.get("columns", [])
        if isinstance(columns_raw, dict):
            columns_raw = list(columns_raw.values())

        columns: list[SchemaMappingFieldInfo] = []
        for col_def in columns_raw:
            col_name = col_def.get("name") or col_def.get("column_name")
            if not col_name:
                continue

            field_info: SchemaMappingFieldInfo = {
                "field_name": col_name,
                "data_type": col_def.get("type") or col_def.get("data_type") or "string",
                "nullable": col_def.get("nullable", True),
                "is_primary_key": col_def.get("primary_key", False),
                "is_foreign_key": "foreign_key" in col_def or "fk_target_table" in col_def,
                "fk_target_table": col_def.get("fk_target_table"),
                "fk_target_column": col_def.get("fk_target_column"),
                "description": col_def.get("description", f"{col_name}"),
            }
            columns.append(field_info)

        table_info: SchemaTableInfo = {
            "table_name": table_name,
            "primary_key": table_def.get("primary_key"),
            "columns": columns,
            "row_count": table_def.get("row_count"),
            "description": table_def.get("description", f"Table {table_name}"),
        }
        external_tables[table_name] = table_info

    return external_tables


def _generate_schema_summary(
    external_tables: dict[str, SchemaTableInfo],
    cmms_name: str
) -> str:
    """Generate human-readable schema summary."""
    lines = [f"Schema for {cmms_name}:"]
    lines.append(f"  Total tables: {len(external_tables)}")

    total_cols = sum(len(t.get("columns", [])) for t in external_tables.values())
    lines.append(f"  Total columns: {total_cols}")

    lines.append("\nTables:")
    for table_name, table_info in sorted(external_tables.items()):
        cols = table_info.get("columns", [])
        lines.append(f"  • {table_name}: {len(cols)} columns")
        for col in cols[:3]:  # Show first 3 columns as preview
            pk = " (PK)" if col.get("is_primary_key") else ""
            fk = " (FK)" if col.get("is_foreign_key") else ""
            lines.append(f"    - {col['field_name']}: {col['data_type']}{pk}{fk}")
        if len(cols) > 3:
            lines.append(f"    ... and {len(cols) - 3} more columns")

    return "\n".join(lines)
