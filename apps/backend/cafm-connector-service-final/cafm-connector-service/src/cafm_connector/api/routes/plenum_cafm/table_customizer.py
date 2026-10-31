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
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db
from cafm_connector.api.routes.plenum_cafm.maintenance_vendors import (
    DELETED_STATUS,
    USE_VENDOR_DELETE,
)
from cafm_connector.api.security import _detail, require_admin

# ── Sub-application (inner — mounted at /table-editor in the main connector app) ─

table_editor_inner = FastAPI(
    title="Table Editor",
    description="Browse, edit rows, and manage columns on any plenum_cafm table.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Authorization travels as a bearer header, not a cookie, so credentialed cross-site
# requests are not needed — and "*" with allow_credentials=True is the combination browsers
# refuse and servers should never offer. Set TABLE_EDITOR_CORS_ORIGINS to a comma-separated
# list to name the UI's origins explicitly.
_CORS_ORIGINS = [
    o.strip() for o in os.environ.get(
        "TABLE_EDITOR_CORS_ORIGINS",
        "http://localhost:3001,http://127.0.0.1:3001",
    ).split(",") if o.strip()
]

table_editor_inner.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@table_editor_inner.exception_handler(DBAPIError)
async def dbapi_error_handler(_request: Request, exc: DBAPIError) -> JSONResponse:
    """Turn DB-level errors (bad UUID, type mismatch, FK violation, etc.) into 422s."""
    cause = exc.orig
    detail = str(cause).split("\n")[0] if cause else str(exc)
    return JSONResponse(status_code=422, content={"detail": detail})


# Every route here reads and writes arbitrary rows in a table named at runtime, and two of
# them add or drop columns. A per-row company filter cannot be expressed over a query whose
# table is chosen by the caller, so the boundary is the role: an administrator of the
# company, or nobody. ``get_plenum_db`` already refuses a request with no token; this refuses
# a signed-in user who is not an administrator.
router = APIRouter(prefix="/tables", tags=["Table Customizer"],
                   dependencies=[Depends(require_admin)])

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


#: Columns this editor will not write and will not return, per table.
#:
#: The editor is a deliberate raw hatch: any table, any column, straight into SQL. That
#: is fine for a rate schedule and not fine for a credential, and the columns below are
#: credentials — a generic hatch over them is a second, quieter version of the
#: password_hash hole that POST /users used to have, reachable by anyone who can reach
#: the table editor.
#:
#: Writing is refused rather than filtered, so a caller who tries is told; reading is
#: filtered rather than refused, so the rest of the row still renders.
_PROTECTED_COLUMNS: dict[str, frozenset[str]] = {
    # Set only by the auth service, which applies the password policy and hashes
    # server-side. A value written here would be a credential nobody checked.
    "users": frozenset({"password_hash"}),
    # A live session token and a live one-time code. Both are credentials in flight, and
    # both are stored hashed precisely so that reading the table gives up nothing.
    "auth_sessions": frozenset({"refresh_token_hash"}),
    "auth_otp_codes": frozenset({"code_hash", "salt"}),
}

#: Not secrets, so they are shown — but not settable here. A role change made through
#: this hatch is checked against nobody and leaves no audit row, and self-promotion to
#: superadmin would be one PATCH away. It goes through POST /api/auth/users/{id}/role,
#: which enforces who may grant what.
_WRITE_ONLY_ELSEWHERE: dict[str, frozenset[str]] = {
    "users": frozenset({"role"}),
}


def _protected(table: str) -> frozenset[str]:
    return _PROTECTED_COLUMNS.get(table, frozenset())


def _refuse_protected_writes(table: str, data: dict) -> None:
    """Raise if the payload touches a column this editor must not set."""
    blocked = sorted(set(data) & _protected(table))
    if blocked:
        raise HTTPException(
            403,
            f"{', '.join(blocked)} cannot be set here. It is a credential, and this "
            "editor cannot apply the checks that make one safe. Use the auth service: "
            "POST /api/auth/register, or /api/auth/password/forgot then /password/reset.",
        )
    audited = sorted(set(data) & _WRITE_ONLY_ELSEWHERE.get(table, frozenset()))
    if audited:
        raise HTTPException(
            403,
            f"{', '.join(audited)} cannot be set here — a change made this way is not "
            "checked against who may make it and leaves no audit row. Use "
            "POST /api/auth/users/{user_id}/role.",
        )


def _redact(table: str, row: dict) -> dict:
    """Drop protected columns from a row on its way out."""
    blocked = _protected(table)
    return {k: v for k, v in row.items() if k not in blocked}


#: A vendor's row is edited here like any other, except for its lifecycle (Hussain, 12 Oct
#: 2026). Deleting a vendor — or reviving one — goes through the Vendors page, where the delete
#: is checked, the vendor's assignments are cleared and the vendor audit trail is written; this
#: editor does none of that. So it does not delete a vendor row, does not set its status to deleted,
#: and does not edit a retired vendor at all: that row is history, kept so the work and
#: invoices that name it still resolve.
_VENDORS = "vendors"


def _vendor_status(value: Any) -> str:
    # NULL is live and case is ignored, as in operations-intelligence's live_vendor_sql.
    return "active" if value is None else str(value).lower()


async def _refuse_vendor_lifecycle_edit(db: AsyncSession, row_id: str, data: dict) -> None:
    """Raise if this PATCH would edit a retired vendor, or retire one.

    Read with the UPDATE's own predicate, so it sees exactly the row the UPDATE would write,
    and locked, so the vendor delete cannot retire it between this read and that write.
    """
    rows = await db.execute(
        text(f"SELECT to_jsonb(v)->>'status' FROM {_quote(_SCHEMA)}.{_quote(_VENDORS)} v "
             "WHERE v.id = :_row_id FOR UPDATE"),
        {"_row_id": row_id},
    )
    current = [_vendor_status(s) for s in rows.scalars().all()]
    if DELETED_STATUS in current:
        raise HTTPException(409, _detail(
            "This vendor has been deleted. Its record is kept as history and cannot be edited.",
            "vendor_deleted"))
    # Only retiring is refused (reviving is the 409 above). active <-> inactive is an ordinary
    # edit, and this editor and svc-udr are the only places it can be made (12 Oct 2026).
    if "status" in data and _vendor_status(data["status"]).strip() == DELETED_STATUS:
        raise HTTPException(403, _detail(
            "A vendor cannot be marked deleted here. " + USE_VENDOR_DELETE,
            "use_vendor_delete"))


#: Columns that name a vendor, in whichever table carries them (12 Oct 2026). A value written
#: into one of these links new work, a contact or a contract to that vendor, which the spec
#: refuses for a retired vendor on every path — the editor included.
_VENDOR_REF_COLUMNS = frozenset({"vendor_id", "assigned_vendor", "supplier_id", "contractor_id"})


async def _refuse_retired_vendor_refs(db: AsyncSession, data: dict) -> None:
    """Raise 409 if a vendor column in ``data`` names a retired vendor, 503 if unreadable.

    One status read for every vendor named. The id is compared as lower-cased text, because
    production vendor ids are varchar with legacy values and a uuid may arrive in any case, and
    the rows are read FOR SHARE so a write racing the vendor delete waits for it.
    """
    ids = sorted({str(v).strip().lower() for k, v in data.items()
                  if k in _VENDOR_REF_COLUMNS and v is not None and str(v).strip()})
    if not ids:
        return
    try:
        rows = await db.execute(
            text(f"SELECT to_jsonb(v)->>'status' FROM {_quote(_SCHEMA)}.{_quote(_VENDORS)} v "
                 "WHERE lower(v.id::text) = ANY(:_vendor_ids) FOR SHARE"),
            {"_vendor_ids": ids},
        )
        statuses = [_vendor_status(s) for s in rows.scalars().all()]
    except SQLAlchemyError as exc:
        raise HTTPException(503, _detail(
            "We could not confirm this vendor is still current, so nothing was saved. "
            "Try again in a moment.", "vendor_check_failed")) from exc
    if DELETED_STATUS in statuses:
        raise HTTPException(409, _detail(
            "This vendor has been deleted, so nothing new can be linked to it. "
            "Choose a current vendor.", "vendor_deleted"))


#: The audit trails are append-only records written by the actions they describe (12 Oct
#: 2026). The vendor audit trail is read from ops_audit_log and trusts what a row says, so an
#: entry inserted here would be a "deleted" record nobody made. Reading them stays open.
_AUDIT_TABLES = frozenset({"ops_audit_log", "audit_logs"})


def _refuse_audit_write(table: str) -> None:
    if table in _AUDIT_TABLES:
        raise HTTPException(403, _detail(
            "The audit trail cannot be edited here. Its entries are written by the actions "
            "they record.", "audit_trail_read_only"))


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
    cols = [c for c in data_rows.keys() if c not in _protected(table)]
    rows = [_redact(table, dict(zip(data_rows.keys(), r))) for r in data_rows]
    # Serialise non-JSON-safe types
    rows = [{k: (str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v)
             for k, v in row.items()} for row in rows]

    # `columns` is filtered alongside the rows: a column named in the header with no
    # values under it reads as data that failed to load rather than as data withheld.
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
    _refuse_audit_write(table)
    await _require_table(table, db)
    valid_cols = await _valid_columns(table, db)
    data = {k: v for k, v in body.data.items() if k in valid_cols}
    if not data:
        raise HTTPException(422, "No valid columns provided")
    _refuse_protected_writes(table, data)
    await _refuse_retired_vendor_refs(db, data)

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
    return _redact(table, {
        k: (str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v)
        for k, v in zip(cols, row)
    })


# ── update row ─────────────────────────────────────────────────────────────────

@router.patch("/{table}/rows/{row_id}")
async def update_row(
    table: str,
    row_id: str,
    body: RowBody,
    db: AsyncSession = Depends(get_plenum_db),
) -> dict:
    _refuse_audit_write(table)
    await _require_table(table, db)
    valid_cols = await _valid_columns(table, db)
    data = {k: v for k, v in body.data.items() if k in valid_cols and k != "id"}
    if not data:
        raise HTTPException(422, "No valid columns to update")
    _refuse_protected_writes(table, data)
    if table == _VENDORS:
        await _refuse_vendor_lifecycle_edit(db, row_id, data)
    await _refuse_retired_vendor_refs(db, data)

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
    return _redact(table, {
        k: (str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v)
        for k, v in zip(cols, row)
    })


# ── delete row ─────────────────────────────────────────────────────────────────

@router.delete("/{table}/rows/{row_id}", status_code=200)
async def delete_row(
    table: str,
    row_id: str,
    db: AsyncSession = Depends(get_plenum_db),
) -> dict:
    # Before any statement: a raw DELETE here would remove a vendor with no check, no
    # unassigning and no trail entry.
    if table == _VENDORS:
        raise HTTPException(403, _detail(USE_VENDOR_DELETE, "use_vendor_delete"))
    _refuse_audit_write(table)
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
    # Before anything else (12 Oct 2026): dropping vendors.status reads every retired vendor
    # as live again, in every company, and the vendor delete then refuses to retire anything.
    # The vendors table's shape belongs to the vendor delete and its migrations, not this editor.
    if table == _VENDORS:
        raise HTTPException(403, _detail(
            "The vendors table's columns cannot be changed here. " + USE_VENDOR_DELETE,
            "use_vendor_delete"))
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
    # Before anything else (12 Oct 2026): dropping vendors.status reads every retired vendor
    # as live again, in every company, and the vendor delete then refuses to retire anything.
    # The vendors table's shape belongs to the vendor delete and its migrations, not this editor.
    if table == _VENDORS:
        raise HTTPException(403, _detail(
            "The vendors table's columns cannot be changed here. " + USE_VENDOR_DELETE,
            "use_vendor_delete"))
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
