"""
Table Editor — FastAPI sub-application (inner app + optional gateway shell).

In the main connector (`cafm_connector.api.app`), the inner app is mounted at
``/table-editor`` so public URLs are ``/table-editor/docs`` and
``/table-editor/tables/...``.

When this module is run as the dedicated table-editor process (e.g. port 8005),
set ``TABLE_EDITOR_STANDALONE_MOUNT=1`` so the same paths work; otherwise only
``/docs`` and ``/tables/...`` exist on that process.

Endpoints (all relative to the mount prefix when present, e.g. /table-editor):
  GET    /tables                          list tables + row counts
  GET    /tables/{table}/columns          column names, types, nullability
  GET    /tables/{table}/rows             paginated rows
  POST   /tables/{table}/rows             insert a row
  PATCH  /tables/{table}/rows/{row_id}    update a row
  DELETE /tables/{table}/rows/{row_id}    delete a row
  POST   /tables/{table}/columns          ADD COLUMN  (DDL — requires ?confirm=true)
  DELETE /tables/{table}/columns/{col}    DROP COLUMN (DDL — requires ?confirm=true)
"""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db

# ── Sub-application (inner — mounted at /table-editor in the main connector app) ─

table_editor_inner = FastAPI(
    title="Table Editor",
    description="Browse, edit rows, and manage columns on any plenum_cafm table.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

table_editor_inner.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@table_editor_inner.exception_handler(DBAPIError)
async def dbapi_error_handler(_request: Request, exc: DBAPIError) -> JSONResponse:
    """Turn DB-level errors (bad UUID, type mismatch, FK violation, etc.) into 422s."""
    cause = exc.orig
    detail = str(cause).split("\n")[0] if cause else str(exc)
    return JSONResponse(status_code=422, content={"detail": detail})


router = APIRouter(prefix="/tables", tags=["Table Customizer"])

_SCHEMA = "plenum_cafm"
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


# ── helpers ────────────────────────────────────────────────────────────────────

async def _valid_tables(db: AsyncSession) -> set[str]:
    rows = await db.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
        ),
        {"schema": _SCHEMA},
    )
    return {r[0] for r in rows}


async def _require_table(table: str, db: AsyncSession) -> None:
    if not _SAFE_IDENT.match(table):
        raise HTTPException(400, f"Invalid table name: {table!r}")
    valid = await _valid_tables(db)
    if table not in valid:
        raise HTTPException(404, f"Table '{table}' not found in schema '{_SCHEMA}'")


async def _valid_columns(table: str, db: AsyncSession) -> set[str]:
    rows = await db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table"
        ),
        {"schema": _SCHEMA, "table": table},
    )
    return {r[0] for r in rows}


def _quote(name: str) -> str:
    """Double-quote a validated identifier for use in SQL text."""
    return f'"{name}"'


# ── list tables ────────────────────────────────────────────────────────────────

@router.get("")
async def list_tables(db: AsyncSession = Depends(get_plenum_db)) -> list[dict]:
    rows = await db.execute(
        text(
            "SELECT t.table_name, "
            "       (SELECT reltuples::bigint FROM pg_class c "
            "        JOIN pg_namespace n ON n.oid = c.relnamespace "
            "        WHERE n.nspname = :schema AND c.relname = t.table_name) AS row_estimate "
            "FROM information_schema.tables t "
            "WHERE t.table_schema = :schema AND t.table_type = 'BASE TABLE' "
            "ORDER BY t.table_name"
        ),
        {"schema": _SCHEMA},
    )
    return [{"table": r[0], "row_estimate": max(0, r[1] or 0)} for r in rows]


# ── list columns ───────────────────────────────────────────────────────────────

@router.get("/{table}/columns")
async def list_columns(
    table: str,
    db: AsyncSession = Depends(get_plenum_db),
) -> list[dict]:
    await _require_table(table, db)
    rows = await db.execute(
        text(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "       character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table "
            "ORDER BY ordinal_position"
        ),
        {"schema": _SCHEMA, "table": table},
    )
    return [
        {
            "name": r[0],
            "type": r[1],
            "nullable": r[2] == "YES",
            "default": r[3],
            "max_length": r[4],
        }
        for r in rows
    ]


# ── list rows ──────────────────────────────────────────────────────────────────

@router.get("/{table}/rows")
async def list_rows(
    table: str,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
) -> dict:
    await _require_table(table, db)
    schema_table = f"{_quote(_SCHEMA)}.{_quote(table)}"

    count_row = await db.execute(text(f"SELECT count(*) FROM {schema_table}"))
    total: int = count_row.scalar_one()

    data_rows = await db.execute(
        text(f"SELECT * FROM {schema_table} ORDER BY 1 LIMIT :lim OFFSET :off"),
        {"lim": limit, "off": offset},
    )
    cols = list(data_rows.keys())
    rows = [dict(zip(cols, r)) for r in data_rows]
    # Serialise non-JSON-safe types
    rows = [{k: (str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v)
             for k, v in row.items()} for row in rows]

    return {"total": total, "limit": limit, "offset": offset, "columns": cols, "rows": rows}


# ── create row ─────────────────────────────────────────────────────────────────

class RowBody(BaseModel):
    data: dict[str, Any]


@router.post("/{table}/rows", status_code=201)
async def create_row(
    table: str,
    body: RowBody,
    db: AsyncSession = Depends(get_plenum_db),
) -> dict:
    await _require_table(table, db)
    valid_cols = await _valid_columns(table, db)
    data = {k: v for k, v in body.data.items() if k in valid_cols}
    if not data:
        raise HTTPException(422, "No valid columns provided")

    schema_table = f"{_quote(_SCHEMA)}.{_quote(table)}"
    col_list = ", ".join(_quote(c) for c in data)
    param_list = ", ".join(f":{c}" for c in data)
    result = await db.execute(
        text(f"INSERT INTO {schema_table} ({col_list}) VALUES ({param_list}) RETURNING *"),
        data,
    )
    await db.commit()
    cols = list(result.keys())
    row = result.fetchone()
    return {k: (str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v)
            for k, v in zip(cols, row)}


# ── update row ─────────────────────────────────────────────────────────────────

@router.patch("/{table}/rows/{row_id}")
async def update_row(
    table: str,
    row_id: str,
    body: RowBody,
    db: AsyncSession = Depends(get_plenum_db),
) -> dict:
    await _require_table(table, db)
    valid_cols = await _valid_columns(table, db)
    data = {k: v for k, v in body.data.items() if k in valid_cols and k != "id"}
    if not data:
        raise HTTPException(422, "No valid columns to update")

    schema_table = f"{_quote(_SCHEMA)}.{_quote(table)}"
    set_clause = ", ".join(f"{_quote(c)} = :{c}" for c in data)
    data["_row_id"] = row_id
    result = await db.execute(
        text(f"UPDATE {schema_table} SET {set_clause} WHERE id = :_row_id RETURNING *"),
        data,
    )
    await db.commit()
    row = result.fetchone()
    if row is None:
        raise HTTPException(404, f"Row '{row_id}' not found in table '{table}'")
    cols = list(result.keys())
    return {k: (str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v)
            for k, v in zip(cols, row)}


# ── delete row ─────────────────────────────────────────────────────────────────

@router.delete("/{table}/rows/{row_id}", status_code=200)
async def delete_row(
    table: str,
    row_id: str,
    db: AsyncSession = Depends(get_plenum_db),
) -> dict:
    await _require_table(table, db)
    schema_table = f"{_quote(_SCHEMA)}.{_quote(table)}"
    result = await db.execute(
        text(f"DELETE FROM {schema_table} WHERE id = :row_id"),
        {"row_id": row_id},
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, f"Row '{row_id}' not found in table '{table}'")
    return {"deleted": True, "id": row_id}


# ── add column (DDL) ───────────────────────────────────────────────────────────

_ALLOWED_TYPES = {
    "text", "varchar", "integer", "bigint", "smallint", "boolean",
    "numeric", "float", "double precision", "date", "timestamptz",
    "uuid", "jsonb", "json",
}


class AddColumnBody(BaseModel):
    column_name: str
    data_type: str
    nullable: bool = True
    default: str | None = None


@router.post("/{table}/columns", status_code=201)
async def add_column(
    table: str,
    body: AddColumnBody,
    confirm: bool = Query(False),
    db: AsyncSession = Depends(get_plenum_db),
) -> dict:
    if not confirm:
        raise HTTPException(
            400,
            "DDL operation requires ?confirm=true. "
            "This will permanently alter the table schema.",
        )
    await _require_table(table, db)
    if not _SAFE_IDENT.match(body.column_name):
        raise HTTPException(400, f"Invalid column name: {body.column_name!r}")
    base_type = body.data_type.lower().split("(")[0].strip()
    if base_type not in _ALLOWED_TYPES:
        raise HTTPException(400, f"Data type not allowed: {body.data_type!r}")

    schema_table = f"{_quote(_SCHEMA)}.{_quote(table)}"
    col = _quote(body.column_name)
    null_clause = "" if body.nullable else " NOT NULL"
    default_clause = f" DEFAULT {body.default}" if body.default else ""
    await db.execute(
        text(
            f"ALTER TABLE {schema_table} "
            f"ADD COLUMN IF NOT EXISTS {col} {body.data_type}{null_clause}{default_clause}"
        )
    )
    await db.commit()
    return {"table": table, "column_name": body.column_name, "data_type": body.data_type}


# ── drop column (DDL) ──────────────────────────────────────────────────────────

@router.delete("/{table}/columns/{col}", status_code=200)
async def drop_column(
    table: str,
    col: str,
    confirm: bool = Query(False),
    db: AsyncSession = Depends(get_plenum_db),
) -> dict:
    if not confirm:
        raise HTTPException(
            400,
            "DDL operation requires ?confirm=true. "
            "This will permanently drop the column and all its data.",
        )
    await _require_table(table, db)
    if not _SAFE_IDENT.match(col):
        raise HTTPException(400, f"Invalid column name: {col!r}")
    valid_cols = await _valid_columns(table, db)
    if col not in valid_cols:
        raise HTTPException(404, f"Column '{col}' not found in table '{table}'")
    if col == "id":
        raise HTTPException(400, "Cannot drop the primary key column 'id'")

    schema_table = f"{_quote(_SCHEMA)}.{_quote(table)}"
    await db.execute(text(f"ALTER TABLE {schema_table} DROP COLUMN IF EXISTS {_quote(col)}"))
    await db.commit()
    return {"dropped": True, "table": table, "column": col}


# ── Wire router into sub-app ───────────────────────────────────────────────────

table_editor_inner.include_router(router)

# When the table-editor process runs on its own (e.g. Azure) with a public URL
# that still includes the /table-editor path, mount the inner app at that
# path so /table-editor/docs and /table-editor/tables/* resolve.
# See docker-compose: TABLE_EDITOR_STANDALONE_MOUNT=1
_STANDALONE = os.environ.get("TABLE_EDITOR_STANDALONE_MOUNT", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
if _STANDALONE:
    _shell = FastAPI(title="Table Editor (gateway shell)", version="1.0.0")

    @_shell.get("/health", tags=["Health"])
    async def _te_health() -> dict[str, str]:
        return {"status": "ok", "service": "table-editor"}

    _shell.mount("/table-editor", table_editor_inner)
    app = _shell
else:
    app = table_editor_inner
