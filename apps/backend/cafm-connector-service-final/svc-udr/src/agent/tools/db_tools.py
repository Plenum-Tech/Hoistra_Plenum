"""
Thin wrappers around DatabaseService that normalise outputs for the agent
and wrap exceptions into dict error responses (so the agent can read them).
"""
from typing import Any

from ...services.database_service import DatabaseService
from ...core.exceptions import UDRError
from ...core.logging import get_logger

log = get_logger(__name__)


def _err(msg: str) -> dict:
    return {"success": False, "error": msg}


class DBTools:
    def __init__(self, svc: DatabaseService) -> None:
        self._svc = svc

    async def list_tables(self) -> dict:
        log.debug("tool.list_tables.called")
        try:
            tables = await self._svc.list_tables()
            log.info("tool.list_tables.success", table_count=len(tables))
            return {"success": True, "tables": tables, "count": len(tables)}
        except UDRError as e:
            log.error("tool.list_tables.error", error=str(e), exc_type=type(e).__name__)
            return _err(str(e))

    async def describe_table(self, table: str) -> dict:
        log.debug("tool.describe_table.called", table=table)
        try:
            info = await self._svc.describe_table(table)
            log.info(
                "tool.describe_table.success",
                table=table,
                column_count=len(info.get("columns", [])),
                fk_count=len(info.get("foreign_keys", [])),
            )
            return {"success": True, **info}
        except UDRError as e:
            log.error("tool.describe_table.error", table=table, error=str(e), exc_type=type(e).__name__)
            return _err(str(e))

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
            "tool.read_records.called",
            table=table,
            filter_keys=list(filters.keys()) if filters else None,
            columns=columns,
            limit=limit,
            offset=offset,
        )
        try:
            result = await self._svc.read_records(
                table=table,
                filters=filters,
                columns=columns,
                limit=limit,
                offset=offset,
                order_by=order_by,
                order_dir=order_dir,
            )
            log.info(
                "tool.read_records.success",
                table=table,
                rows_returned=len(result.get("rows", [])),
                total=result.get("total"),
                has_more=result.get("has_more"),
            )
            return {"success": True, **result}
        except UDRError as e:
            log.error("tool.read_records.error", table=table, error=str(e), exc_type=type(e).__name__)
            return _err(str(e))

    async def get_record(
        self,
        table: str,
        record_id: str,
        id_column: str = "id",
    ) -> dict:
        log.debug("tool.get_record.called", table=table, record_id=record_id, id_column=id_column)
        try:
            row = await self._svc.get_record(table, record_id, id_column)
            log.info("tool.get_record.success", table=table, record_id=record_id)
            return {"success": True, "record": row}
        except UDRError as e:
            log.error(
                "tool.get_record.error",
                table=table,
                record_id=record_id,
                error=str(e),
                exc_type=type(e).__name__,
            )
            return _err(str(e))

    async def search_records(
        self,
        table: str,
        search_term: str,
        search_columns: list[str],
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        log.debug(
            "tool.search_records.called",
            table=table,
            search_term=search_term[:80],
            search_columns=search_columns,
            limit=limit,
        )
        try:
            result = await self._svc.search_records(
                table=table,
                search_term=search_term,
                search_columns=search_columns,
                limit=limit,
                offset=offset,
            )
            log.info(
                "tool.search_records.success",
                table=table,
                results=result.get("count"),
            )
            return {"success": True, **result}
        except UDRError as e:
            log.error("tool.search_records.error", table=table, error=str(e), exc_type=type(e).__name__)
            return _err(str(e))

    async def create_record(self, table: str, data: dict[str, Any]) -> dict:
        log.debug("tool.create_record.called", table=table, columns=list(data.keys()))
        try:
            record = await self._svc.create_record(table, data)
            log.info(
                "tool.create_record.success",
                table=table,
                record_id=record.get("id"),
                columns_written=list(data.keys()),
            )
            return {"success": True, "record": record, "message": f"Record created in {table}."}
        except UDRError as e:
            log.error("tool.create_record.error", table=table, error=str(e), exc_type=type(e).__name__)
            return _err(str(e))
        except Exception as e:
            log.error("tool.create_record.unexpected_error", table=table, error=str(e), exc_type=type(e).__name__, exc_info=True)
            return _err(f"Database error: {e}")

    async def update_record(
        self,
        table: str,
        record_id: str,
        data: dict[str, Any],
        id_column: str = "id",
    ) -> dict:
        log.debug(
            "tool.update_record.called",
            table=table,
            record_id=record_id,
            id_column=id_column,
            columns=list(data.keys()),
        )
        try:
            record = await self._svc.update_record(table, record_id, data, id_column)
            log.info(
                "tool.update_record.success",
                table=table,
                record_id=record_id,
                columns_updated=list(data.keys()),
            )
            return {"success": True, "record": record, "message": f"Record updated in {table}."}
        except UDRError as e:
            log.error(
                "tool.update_record.error",
                table=table,
                record_id=record_id,
                error=str(e),
                exc_type=type(e).__name__,
            )
            return _err(str(e))
        except Exception as e:
            log.error("tool.update_record.unexpected_error", table=table, record_id=record_id, error=str(e), exc_type=type(e).__name__, exc_info=True)
            return _err(f"Database error: {e}")

    async def delete_record(
        self,
        table: str,
        record_id: str,
        id_column: str = "id",
    ) -> dict:
        log.debug("tool.delete_record.called", table=table, record_id=record_id, id_column=id_column)
        try:
            deleted = await self._svc.delete_record(table, record_id, id_column)
            if deleted:
                log.info("tool.delete_record.success", table=table, record_id=record_id, deleted=True)
                return {"success": True, "deleted": True, "message": f"Record deleted from {table}."}
            log.warning("tool.delete_record.not_found", table=table, record_id=record_id, deleted=False)
            return {"success": True, "deleted": False, "message": "Record not found — nothing deleted."}
        except UDRError as e:
            log.error("tool.delete_record.error", table=table, record_id=record_id, error=str(e), exc_type=type(e).__name__)
            return _err(str(e))
        except Exception as e:
            log.error("tool.delete_record.unexpected_error", table=table, record_id=record_id, error=str(e), exc_type=type(e).__name__, exc_info=True)
            return _err(f"Database error: {e}")

    async def execute_select(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> dict:
        log.debug(
            "tool.execute_select.called",
            sql_preview=sql.strip()[:120],
            param_count=len(params) if params else 0,
        )
        try:
            rows = await self._svc.execute_select(sql, params)
            log.info("tool.execute_select.success", rows_returned=len(rows))
            return {"success": True, "rows": rows, "count": len(rows)}
        except UDRError as e:
            log.error("tool.execute_select.error", error=str(e), exc_type=type(e).__name__, sql_preview=sql.strip()[:120])
            return _err(str(e))
        except Exception as e:
            log.error("tool.execute_select.unexpected_error", error=str(e), exc_type=type(e).__name__, exc_info=True)
            return _err(f"Query error: {e}")
