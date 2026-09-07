"""
Core CRUD engine for the Universal Database Reader.

All table/column identifiers are validated against a strict regex and then
confirmed to exist in information_schema before any SQL runs — same pattern
as cafm-connector-service/table_customizer.py but extended with search and
agent-friendly responses.
"""
import re
from decimal import Decimal
from datetime import datetime, date, time
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..config import settings
from ..core.exceptions import (
    UnsafeIdentifierError,
    TableNotFoundError,
    ColumnNotFoundError,
    RecordNotFoundError,
    UnsafeQueryError,
)
from ..core.logging import get_logger

log = get_logger(__name__)

# Same guard used by table_customizer.py — never relax this.
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")

SCHEMA = settings.db_schema


def _validate_ident(name: str) -> str:
    if not _SAFE_IDENT.match(name):
        log.warning(
            "db.security.unsafe_identifier",
            identifier=name,
            reason="failed _SAFE_IDENT regex — rejected before any DB call",
        )
        raise UnsafeIdentifierError(f"Unsafe identifier rejected: {name!r}")
    return name


def _serialize(value: Any) -> Any:
    """Convert non-JSON-serializable DB types to safe primitives."""
    if isinstance(value, (UUID, datetime, date, time)):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _serialize_row(row: dict) -> dict:
    return {k: _serialize(v) for k, v in row.items()}


class DatabaseService:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # ── Schema introspection ──────────────────────────────────────────────────

    async def list_tables(self) -> list[dict]:
        """Return all base tables in the schema with live row estimates."""
        log.debug("db.list_tables.start", schema=SCHEMA)
        result = await self._db.execute(
            text("""
                SELECT t.table_name,
                       COALESCE(s.n_live_tup, 0) AS row_estimate
                FROM information_schema.tables t
                LEFT JOIN pg_stat_user_tables s
                    ON s.relname = t.table_name
                    AND s.schemaname = t.table_schema
                WHERE t.table_schema = :schema
                  AND t.table_type = 'BASE TABLE'
                ORDER BY t.table_name
            """),
            {"schema": SCHEMA},
        )
        tables = [
            {"table": r.table_name, "row_estimate": int(r.row_estimate)}
            for r in result
        ]
        log.info("db.list_tables.done", schema=SCHEMA, table_count=len(tables))
        return tables

    async def describe_table(self, table: str) -> dict:
        """Return columns, primary keys, and foreign keys for a table."""
        log.debug("db.describe_table.start", table=table, schema=SCHEMA)
        _validate_ident(table)
        await self._assert_table_exists(table)

        cols_result = await self._db.execute(
            text("""
                SELECT column_name,
                       data_type,
                       is_nullable,
                       column_default,
                       character_maximum_length,
                       numeric_precision,
                       numeric_scale
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name   = :table
                ORDER BY ordinal_position
            """),
            {"schema": SCHEMA, "table": table},
        )
        columns = [
            {
                "name": c.column_name,
                "type": c.data_type,
                "nullable": c.is_nullable == "YES",
                "default": c.column_default,
                "max_length": c.character_maximum_length,
                "numeric_precision": c.numeric_precision,
                "numeric_scale": c.numeric_scale,
            }
            for c in cols_result
        ]

        pk_result = await self._db.execute(
            text("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema   = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema    = :schema
                  AND tc.table_name      = :table
                ORDER BY kcu.ordinal_position
            """),
            {"schema": SCHEMA, "table": table},
        )
        primary_keys = [r.column_name for r in pk_result]

        fk_result = await self._db.execute(
            text("""
                SELECT kcu.column_name,
                       ccu.table_name  AS references_table,
                       ccu.column_name AS references_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema   = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema    = :schema
                  AND tc.table_name      = :table
            """),
            {"schema": SCHEMA, "table": table},
        )
        foreign_keys = [
            {
                "column": r.column_name,
                "references_table": r.references_table,
                "references_column": r.references_column,
            }
            for r in fk_result
        ]

        log.info(
            "db.describe_table.done",
            table=table,
            column_count=len(columns),
            pk_count=len(primary_keys),
            fk_count=len(foreign_keys),
        )
        return {
            "table": table,
            "schema": SCHEMA,
            "columns": columns,
            "primary_keys": primary_keys,
            "foreign_keys": foreign_keys,
        }

    # ── Read operations ───────────────────────────────────────────────────────

    async def read_records(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        columns: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        order_by: str | None = None,
        order_dir: str = "asc",
    ) -> dict:
        log.debug(
            "db.read_records.start",
            table=table,
            filters=list(filters.keys()) if filters else None,
            columns=columns,
            limit=limit,
            offset=offset,
            order_by=order_by,
            order_dir=order_dir,
        )
        _validate_ident(table)
        await self._assert_table_exists(table)
        limit = min(max(1, limit), settings.max_query_rows)
        offset = max(0, offset)

        col_list = "*"
        if columns:
            for c in columns:
                _validate_ident(c)
            col_list = ", ".join(f'"{c}"' for c in columns)

        where_clause = ""
        filter_params: dict[str, Any] = {}
        if filters:
            parts = []
            for i, (col, val) in enumerate(filters.items()):
                _validate_ident(col)
                key = f"f{i}"
                parts.append(f'"{col}" = :{key}')
                filter_params[key] = val
            where_clause = "WHERE " + " AND ".join(parts)

        order_clause = ""
        if order_by:
            _validate_ident(order_by)
            direction = "DESC" if order_dir.lower() == "desc" else "ASC"
            order_clause = f'ORDER BY "{order_by}" {direction}'

        rows_sql = f"""
            SELECT {col_list}
            FROM {SCHEMA}."{table}"
            {where_clause}
            {order_clause}
            LIMIT :limit OFFSET :offset
        """
        rows_result = await self._db.execute(
            text(rows_sql),
            {**filter_params, "limit": limit, "offset": offset},
        )
        rows = [_serialize_row(dict(r._mapping)) for r in rows_result]

        count_sql = f'SELECT COUNT(*) FROM {SCHEMA}."{table}" {where_clause}'
        total = (await self._db.execute(text(count_sql), filter_params)).scalar() or 0

        log.info(
            "db.read_records.done",
            table=table,
            rows_returned=len(rows),
            total=int(total),
            has_more=(offset + len(rows)) < int(total),
            offset=offset,
            limit=limit,
        )
        return {
            "table": table,
            "rows": rows,
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "has_more": (offset + len(rows)) < int(total),
        }

    async def get_record(
        self,
        table: str,
        record_id: Any,
        id_column: str = "id",
    ) -> dict:
        log.debug("db.get_record.start", table=table, record_id=record_id, id_column=id_column)
        _validate_ident(table)
        _validate_ident(id_column)
        await self._assert_table_exists(table)

        result = await self._db.execute(
            text(f'SELECT * FROM {SCHEMA}."{table}" WHERE "{id_column}" = :rid'),
            {"rid": record_id},
        )
        row = result.fetchone()
        if row is None:
            log.warning(
                "db.get_record.not_found",
                table=table,
                id_column=id_column,
                record_id=record_id,
            )
            raise RecordNotFoundError(
                f"No record found in {table} where {id_column} = {record_id!r}"
            )
        log.info("db.get_record.found", table=table, id_column=id_column, record_id=record_id)
        return _serialize_row(dict(row._mapping))

    async def search_records(
        self,
        table: str,
        search_term: str,
        search_columns: list[str],
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        log.debug(
            "db.search_records.start",
            table=table,
            search_term=search_term[:80],
            search_columns=search_columns,
            limit=limit,
            offset=offset,
        )
        _validate_ident(table)
        await self._assert_table_exists(table)
        for col in search_columns:
            _validate_ident(col)
        limit = min(max(1, limit), settings.max_query_rows)

        like_parts = " OR ".join(f'"{c}"::text ILIKE :term' for c in search_columns)
        sql = f"""
            SELECT * FROM {SCHEMA}."{table}"
            WHERE {like_parts}
            LIMIT :limit OFFSET :offset
        """
        result = await self._db.execute(
            text(sql),
            {"term": f"%{search_term}%", "limit": limit, "offset": offset},
        )
        rows = [_serialize_row(dict(r._mapping)) for r in result]
        log.info(
            "db.search_records.done",
            table=table,
            search_term=search_term[:80],
            search_columns=search_columns,
            results=len(rows),
        )
        return {
            "table": table,
            "search_term": search_term,
            "rows": rows,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
        }

    async def execute_select(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Run a caller-supplied SELECT query. Non-SELECT statements are rejected."""
        stripped = sql.strip()
        if not stripped.upper().startswith("SELECT"):
            log.warning(
                "db.security.non_select_blocked",
                sql_preview=stripped[:120],
                reason="execute_select only permits SELECT statements",
            )
            raise UnsafeQueryError("Only SELECT statements are permitted in execute_select.")
        if ";" in stripped.rstrip(";"):
            log.warning(
                "db.security.multi_statement_blocked",
                sql_preview=stripped[:120],
                reason="semicolon detected — multiple statements not allowed",
            )
            raise UnsafeQueryError("Multiple statements (semicolon) are not allowed.")

        log.debug(
            "db.execute_select.start",
            sql_preview=stripped[:120],
            param_count=len(params) if params else 0,
        )
        result = await self._db.execute(text(sql), params or {})
        rows = result.fetchmany(settings.max_query_rows)
        serialized = [_serialize_row(dict(r._mapping)) for r in rows]
        log.info(
            "db.execute_select.done",
            rows_returned=len(serialized),
            sql_preview=stripped[:120],
        )
        return serialized

    # ── Write operations ──────────────────────────────────────────────────────

    async def create_record(self, table: str, data: dict[str, Any]) -> dict:
        log.debug(
            "db.create_record.start",
            table=table,
            columns=list(data.keys()),
        )
        _validate_ident(table)
        await self._assert_table_exists(table)
        for col in data:
            _validate_ident(col)

        cols = ", ".join(f'"{c}"' for c in data)
        placeholders = ", ".join(f":v_{c}" for c in data)
        named = {f"v_{c}": v for c, v in data.items()}

        result = await self._db.execute(
            text(f'INSERT INTO {SCHEMA}."{table}" ({cols}) VALUES ({placeholders}) RETURNING *'),
            named,
        )
        await self._db.commit()
        row = result.fetchone()
        serialized = _serialize_row(dict(row._mapping))  # type: ignore[union-attr]
        log.info(
            "db.create_record.done",
            table=table,
            columns_written=list(data.keys()),
            record_id=serialized.get("id"),
        )
        return serialized

    async def update_record(
        self,
        table: str,
        record_id: Any,
        data: dict[str, Any],
        id_column: str = "id",
    ) -> dict:
        log.debug(
            "db.update_record.start",
            table=table,
            record_id=record_id,
            id_column=id_column,
            columns=list(data.keys()),
        )
        _validate_ident(table)
        _validate_ident(id_column)
        await self._assert_table_exists(table)
        for col in data:
            _validate_ident(col)

        set_clause = ", ".join(f'"{c}" = :u_{c}' for c in data)
        named = {f"u_{c}": v for c, v in data.items()}
        named["_rid"] = record_id

        result = await self._db.execute(
            text(
                f'UPDATE {SCHEMA}."{table}" SET {set_clause} '
                f'WHERE "{id_column}" = :_rid RETURNING *'
            ),
            named,
        )
        await self._db.commit()
        row = result.fetchone()
        if row is None:
            log.warning(
                "db.update_record.not_found",
                table=table,
                id_column=id_column,
                record_id=record_id,
            )
            raise RecordNotFoundError(
                f"No record in {table} where {id_column} = {record_id!r}"
            )
        serialized = _serialize_row(dict(row._mapping))
        log.info(
            "db.update_record.done",
            table=table,
            id_column=id_column,
            record_id=record_id,
            columns_updated=list(data.keys()),
        )
        return serialized

    async def delete_record(
        self,
        table: str,
        record_id: Any,
        id_column: str = "id",
    ) -> bool:
        log.debug(
            "db.delete_record.start",
            table=table,
            record_id=record_id,
            id_column=id_column,
        )
        _validate_ident(table)
        _validate_ident(id_column)
        await self._assert_table_exists(table)

        result = await self._db.execute(
            text(
                f'DELETE FROM {SCHEMA}."{table}" '
                f'WHERE "{id_column}" = :rid RETURNING "{id_column}"'
            ),
            {"rid": record_id},
        )
        await self._db.commit()
        deleted = result.fetchone() is not None
        if deleted:
            log.info(
                "db.delete_record.done",
                table=table,
                id_column=id_column,
                record_id=record_id,
                deleted=True,
            )
        else:
            log.warning(
                "db.delete_record.not_found",
                table=table,
                id_column=id_column,
                record_id=record_id,
                deleted=False,
            )
        return deleted

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _assert_table_exists(self, table: str) -> None:
        result = await self._db.execute(
            text("""
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = :schema
                  AND table_name   = :table
                  AND table_type   = 'BASE TABLE'
            """),
            {"schema": SCHEMA, "table": table},
        )
        if result.fetchone() is None:
            log.warning(
                "db.security.table_not_found",
                table=table,
                schema=SCHEMA,
                reason="table does not exist in information_schema — request blocked",
            )
            raise TableNotFoundError(f"Table '{table}' not found in schema '{SCHEMA}'.")

    async def _assert_column_exists(self, table: str, column: str) -> None:
        result = await self._db.execute(
            text("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name   = :table
                  AND column_name  = :col
            """),
            {"schema": SCHEMA, "table": table, "col": column},
        )
        if result.fetchone() is None:
            log.warning(
                "db.security.column_not_found",
                table=table,
                column=column,
                schema=SCHEMA,
            )
            raise ColumnNotFoundError(
                f"Column '{column}' not found in '{SCHEMA}.{table}'."
            )
