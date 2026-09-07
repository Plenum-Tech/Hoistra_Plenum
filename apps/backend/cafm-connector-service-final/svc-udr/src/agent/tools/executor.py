"""
Routes tool_call names from the Anthropic agent loop to DBTools implementations.
"""
from typing import Any

from ...services.database_service import DatabaseService
from ...core.logging import get_logger
from .db_tools import DBTools

log = get_logger(__name__)


class ToolExecutor:
    def __init__(self, svc: DatabaseService) -> None:
        self._tools = DBTools(svc)

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> dict:
        log.debug("executor.dispatch", tool=tool_name, input_keys=list(tool_input.keys()))

        match tool_name:
            case "list_tables":
                result = await self._tools.list_tables()

            case "describe_table":
                result = await self._tools.describe_table(
                    table=tool_input["table"],
                )

            case "read_records":
                result = await self._tools.read_records(
                    table=tool_input["table"],
                    filters=tool_input.get("filters"),
                    columns=tool_input.get("columns"),
                    limit=tool_input.get("limit", 50),
                    offset=tool_input.get("offset", 0),
                    order_by=tool_input.get("order_by"),
                    order_dir=tool_input.get("order_dir", "asc"),
                )

            case "get_record":
                result = await self._tools.get_record(
                    table=tool_input["table"],
                    record_id=tool_input["record_id"],
                    id_column=tool_input.get("id_column", "id"),
                )

            case "search_records":
                result = await self._tools.search_records(
                    table=tool_input["table"],
                    search_term=tool_input["search_term"],
                    search_columns=tool_input["search_columns"],
                    limit=tool_input.get("limit", 50),
                    offset=tool_input.get("offset", 0),
                )

            case "create_record":
                result = await self._tools.create_record(
                    table=tool_input["table"],
                    data=tool_input["data"],
                )

            case "update_record":
                result = await self._tools.update_record(
                    table=tool_input["table"],
                    record_id=tool_input["record_id"],
                    data=tool_input["data"],
                    id_column=tool_input.get("id_column", "id"),
                )

            case "delete_record":
                result = await self._tools.delete_record(
                    table=tool_input["table"],
                    record_id=tool_input["record_id"],
                    id_column=tool_input.get("id_column", "id"),
                )

            case "execute_select":
                result = await self._tools.execute_select(
                    sql=tool_input["sql"],
                    params=tool_input.get("params"),
                )

            case _:
                log.warning("executor.unknown_tool", tool=tool_name, input_keys=list(tool_input.keys()))
                result = {"success": False, "error": f"Unknown tool: {tool_name!r}"}

        log.debug(
            "executor.result",
            tool=tool_name,
            success=result.get("success"),
            has_error="error" in result,
        )
        return result
