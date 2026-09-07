from typing import Any
from pydantic import BaseModel, Field


# ── Agent endpoint schemas ────────────────────────────────────────────────────

class AgentQueryRequest(BaseModel):
    message: str = Field(..., description="Natural-language request for the UDR agent.")


class AgentQueryResponse(BaseModel):
    success: bool
    reply: Any  # str for plain text, dict/list when the agent returns structured JSON
    tool_calls_made: int


# ── Direct CRUD endpoint schemas ──────────────────────────────────────────────

class ReadRecordsRequest(BaseModel):
    filters: dict[str, Any] | None = None
    columns: list[str] | None = None
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)
    order_by: str | None = None
    order_dir: str = Field("asc", pattern="^(asc|desc)$")


class CreateRecordRequest(BaseModel):
    data: dict[str, Any] = Field(..., description="Column-value pairs for the new record.")


class UpdateRecordRequest(BaseModel):
    data: dict[str, Any] = Field(..., description="Columns and new values to apply.")
    id_column: str = Field("id", description="Primary key column name.")


class DeleteRecordResponse(BaseModel):
    deleted: bool
    message: str


class SearchRequest(BaseModel):
    search_term: str
    search_columns: list[str]
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class ExecuteSelectRequest(BaseModel):
    sql: str = Field(..., description="SELECT statement with :param_name placeholders.")
    params: dict[str, Any] | None = None


# ── Shared error schema (mirrors svc-work-order-management) ───────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class ErrorResponse(BaseModel):
    errors: list[ErrorDetail]
