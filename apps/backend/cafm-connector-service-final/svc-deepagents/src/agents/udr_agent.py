"""
BE1 — UDR agent tools: direct database access for user lookup, schema discovery,
and generic table queries.
Two-gate SQL injection protection: regex + parameterised queries only.
"""
import re
import time
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool
from sqlalchemy import text

from .. import database
from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
_MAX_ROWS = 100

# Schema cache: avoid re-querying information_schema on every session.
# Refreshes every 5 minutes so DDL changes are picked up without a restart.
_schema_cache: dict | None = None
_schema_cache_at: float = 0.0
_SCHEMA_TTL = 300  # seconds
_TIMEOUT = 60.0
_SERVICE = "udr"


@tool
async def get_schema() -> dict:
    """Return the live database schema for the plenum_cafm schema.

    Queries information_schema to return every table and its columns (with data
    types). Always call this FIRST before any query_table or compliance call so
    you know the exact table names and column names that exist right now.

    Returns a dict:
      {
        "tables": {
          "<table_name>": ["col1 (type)", "col2 (type)", ...],
          ...
        }
      }

    Result is cached for 5 minutes so repeated calls within a session are free.
    """
    global _schema_cache, _schema_cache_at

    now = time.monotonic()
    if _schema_cache is not None and (now - _schema_cache_at) < _SCHEMA_TTL:
        log.debug("udr.get_schema.cache_hit")
        return _schema_cache

    async with database.AsyncSessionLocal() as session:
        try:
            rows = (await session.execute(
                text("""
                    SELECT
                        c.table_name,
                        c.column_name,
                        c.data_type
                    FROM information_schema.columns c
                    JOIN information_schema.tables t
                        ON t.table_name  = c.table_name
                       AND t.table_schema = c.table_schema
                    WHERE c.table_schema = 'plenum_cafm'
                      AND t.table_type   = 'BASE TABLE'
                    ORDER BY c.table_name, c.ordinal_position
                """),
            )).mappings().all()
        except Exception as exc:
            log.error("udr.get_schema.error", error=str(exc))
            return {"error": str(exc)}

    tables: dict[str, list[str]] = {}
    for row in rows:
        tbl = row["table_name"]
        col = f"{row['column_name']} ({row['data_type']})"
        tables.setdefault(tbl, []).append(col)

    result = {"tables": tables}
    _schema_cache = result
    _schema_cache_at = now
    log.info("udr.get_schema.refreshed", table_count=len(tables))
    return result


@tool
async def lookup_user(user_id: str) -> dict:
    """Look up a CAFM platform user by their UUID.

    Returns the user's name, email, roles list, department, and phone number.
    Use this whenever you need to resolve who a user is or what permissions they hold.

    Args:
        user_id: UUID string of the user to look up.
    """
    async with database.AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                text("""
                    SELECT u.id, u.full_name, u.email, u.department, u.phone,
                           array_agg(r.name) FILTER (WHERE r.name IS NOT NULL) AS roles
                    FROM plenum_cafm.users u
                    LEFT JOIN plenum_cafm.user_roles ur ON ur.user_id = u.id
                    LEFT JOIN plenum_cafm.roles r ON r.id = ur.role_id
                    WHERE u.id = :user_id
                    GROUP BY u.id, u.full_name, u.email, u.department, u.phone
                """),
                {"user_id": user_id},
            )
            row = result.mappings().one_or_none()
        except Exception as exc:
            log.error("udr.lookup_user.error", user_id=user_id, error=str(exc))
            return {"error": str(exc)}

    if row is None:
        return {"error": f"User {user_id!r} not found"}

    return {
        "user_id": str(row["id"]),
        "name": row["full_name"],
        "email": row["email"],
        "department": row["department"],
        "phone": row["phone"],
        "roles": list(row["roles"] or []),
    }


@tool
async def query_table(table_name: str, filters: dict[str, Any] | None = None) -> list[dict]:
    """Query any table in the plenum_cafm schema with optional equality filters.

    Returns up to 100 records as a list of dicts. Use this to inspect live data
    in any CAFM table. All identifiers are validated before execution.

    Always call get_schema() first so you use the correct table_name and
    column names — never guess them.

    Args:
        table_name: Exact table name in plenum_cafm as returned by get_schema().
        filters: Optional dict of {column_name: value} equality filters.
                 Column names must match what get_schema() returned for this table.
    """
    if not _SAFE_IDENT.match(table_name):
        log.warning("udr.query_table.unsafe_table", table=table_name)
        return [{"error": f"Unsafe table name: {table_name!r}"}]

    params: dict[str, Any] = {}
    where_clause = ""

    if filters:
        conditions: list[str] = []
        for col, val in filters.items():
            if not _SAFE_IDENT.match(col):
                log.warning("udr.query_table.unsafe_column", column=col)
                return [{"error": f"Unsafe column name: {col!r}"}]
            param_key = f"p_{col}"
            conditions.append(f"{col} = :{param_key}")
            params[param_key] = val
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

    sql = f"SELECT * FROM plenum_cafm.{table_name} {where_clause} LIMIT {_MAX_ROWS}"

    async with database.AsyncSessionLocal() as session:
        try:
            result = await session.execute(text(sql), params)
            rows = result.mappings().all()
            return [dict(r) for r in rows]
        except Exception as exc:
            log.error("udr.query_table.error", table=table_name, error=str(exc))
            return [{"error": str(exc)}]


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        log.error(f"udr.{op}.http_error", status=exc.response.status_code, body=exc.response.text[:300])
        return {"error": exc.response.text[:300], "status_code": exc.response.status_code}
    log.error(f"udr.{op}.error", error=str(exc)[:300])
    return {"error": str(exc)[:300]}


@tool
async def find_asset(identifier: str) -> dict:
    """Resolve an asset from ANY identifier — id (PK, e.g. 'AST-AHU-601'), asset_code,
    asset_name, barcode, serial_number, external_asset_id, qr_code — case-insensitively.

    Use this FIRST whenever the user refers to an asset by a code/name; don't guess which
    column holds it. Returns up to 10 matching rows. Each row includes `document_ids` — the
    ingested documents linked to that asset; pass the asset to get_asset_documents to read
    their content (compliance reports, manuals, certificates, etc.).
    """
    ident = (identifier or "").strip()
    if not ident:
        return {"identifier": identifier, "matches": [], "count": 0}
    cols = ["id", "asset_code", "asset_name", "barcode", "serial_number",
            "external_asset_id", "qr_code"]
    like = " OR ".join(f"CAST({c} AS TEXT) ILIKE :q" for c in cols)
    sql = f"SELECT * FROM plenum_cafm.assets WHERE {like} LIMIT 10"
    async with database.AsyncSessionLocal() as session:
        try:
            res = await session.execute(text(sql), {"q": f"%{ident}%"})
            rows = [dict(r) for r in res.mappings().all()]
            return {"identifier": ident, "matches": rows, "count": len(rows)}
        except Exception as exc:
            return _err(exc, "find_asset")


def _coerce_doc_ids(value: Any) -> list[str]:
    """Normalise an asset's document_ids (JSONB list / JSON string / None) to list[str]."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        s = str(value).strip()
        if not s or s in ("[]", "NULL"):
            return []
        try:
            parsed = json.loads(s)
            items = parsed if isinstance(parsed, list) else [parsed]
        except (ValueError, TypeError):
            items = [s]
    return [str(x).strip() for x in items if str(x).strip()]


@tool
async def get_asset_documents(identifier: str, query: str = "", max_chunks: int = 12) -> dict:
    """Read the ingested DOCUMENTS linked to an asset and return their content to summarise.

    Every asset row carries a `document_ids` array referencing files ingested into the document
    store (public.documents + public.document_chunks — compliance reports, manuals, certificates,
    inspection records, etc.). Use this whenever the user asks for an asset's details, compliance,
    history or "all information" that lives in attached documents rather than the structured
    columns of the assets table.

    Flow: resolve the asset -> its document_ids -> document metadata + the most relevant text
    chunks (filtered by `query`, falling back to the asset code, then to the opening chunks).
    Returns a bundle you should summarise for the user, citing the source file(s).

    Args:
        identifier: asset id / code / name / serial / barcode (same as find_asset).
        query: optional focus term to select the most relevant chunks (e.g. 'warranty',
            'fire', a sub-system). Defaults to the asset code.
        max_chunks: max chunks to return (capped at 40 to protect context).
    """
    ident = (identifier or "").strip()
    if not ident:
        return {"identifier": identifier, "asset": None, "documents": [], "chunks": []}
    max_chunks = max(1, min(int(max_chunks or 24), 40))

    cols = ["id", "asset_code", "asset_name", "barcode", "serial_number",
            "external_asset_id", "qr_code"]
    like = " OR ".join(f"CAST({c} AS TEXT) ILIKE :q" for c in cols)
    async with database.AsyncSessionLocal() as session:
        try:
            res = await session.execute(
                text(f"SELECT * FROM plenum_cafm.assets WHERE {like} LIMIT 5"),
                {"q": f"%{ident}%"},
            )
            assets = [dict(r) for r in res.mappings().all()]
        except Exception as exc:
            return _err(exc, "get_asset_documents.find")

        if not assets:
            return {"identifier": ident, "asset": None, "documents": [], "chunks": [],
                    "note": "No asset matched that identifier."}

        asset = assets[0]
        doc_ids = _coerce_doc_ids(asset.get("document_ids"))
        # Return the FULL asset row (every structured field) so a "complete
        # information" answer can list them all — not just a 4-field brief.
        asset_brief = {
            k: v for k, v in asset.items() if k not in ("document_ids", "embedding")
        }
        asset_brief["linked_document_count"] = len(doc_ids)
        if not doc_ids:
            return {"identifier": ident, "asset": asset_brief, "documents": [], "chunks": [],
                    "note": "This asset has no linked documents (document_ids is empty)."}

        # Document metadata (plenum_cafm ingestion store).
        documents: list[dict] = []
        try:
            res = await session.execute(
                text("SELECT id, original_filename AS file_name, blob_url AS download_url, "
                     "document_type, status, page_count AS num_pages "
                     "FROM plenum_cafm.ingestion_documents WHERE id::text = ANY(:ids)"),
                {"ids": doc_ids},
            )
            documents = [dict(r) for r in res.mappings().all()]
        except Exception as exc:
            log.warning("udr.get_asset_documents.docs_error", error=str(exc)[:200])

        focus = (query or asset.get("asset_code") or "").strip()

        async def fetch_chunks(term: str | None):
            params: dict[str, Any] = {"ids": doc_ids, "lim": max_chunks}
            where = "ingestion_id::text = ANY(:ids)"
            if term:
                where += " AND chunk_text ILIKE :term"
                params["term"] = f"%{term}%"
            sql = ("SELECT ingestion_id AS document_id, chunk_index, page_start, "
                   "heading AS section_label, chunk_text AS text_content "
                   f"FROM plenum_cafm.document_chunks WHERE {where} "
                   "ORDER BY ingestion_id, chunk_index LIMIT :lim")
            res2 = await session.execute(text(sql), params)
            return [dict(r) for r in res2.mappings().all()]

        try:
            rows = await fetch_chunks(focus) if focus else []
            if not rows:  # fall back to the document's opening chunks
                rows = await fetch_chunks(None)
        except Exception as exc:
            return _err(exc, "get_asset_documents.chunks")

        # Always expose a working download ref. "doc:<id>" is resolved by the UI
        # to GET /api/documents/<id>/download — which serves the stored original
        # when present, else the extracted text — so a link is ALWAYS available
        # even when blob_url is null (no more "Download URL is not available").
        for d in documents:
            d["download_url"] = f"doc:{d.get('id')}"
        file_by_id = {str(d.get("id")): d.get("file_name") for d in documents}
        chunks = [{
            "source_file": file_by_id.get(str(r.get("document_id"))) or r.get("document_id"),
            "download_url": f"doc:{r.get('document_id')}",
            "section": r.get("section_label"),
            "page": r.get("page_start"),
            "chunk_index": r.get("chunk_index"),
            "text": (str(r.get("text_content") or "")[:2000]),
        } for r in rows]

        return {
            "identifier": ident,
            "asset": asset_brief,
            "documents": documents,
            "chunk_count": len(chunks),
            "filter_used": focus or "(opening chunks)",
            "chunks": chunks,
            "note": (
                "Summarise the answer from these document chunks. "
                "If the chunks contain a compliance / findings table (rows of checks with a "
                "Status and a Finding/Action — e.g. warranty, Legionella/ACoP L8, SFG20 PPM), "
                "REPRODUCE it as a markdown table with columns 'Compliance Check | Status | "
                "Finding / Action'; do not drop those rows. "
                "Cite EVERY source document as a markdown link [<source_file>](<download_url>) "
                "using each chunk's download_url (always present) so the user can open/download "
                "it. Never write a placeholder like (#) or 'Download URL is not available'. "
                "Older note (no longer applies — download_url is always set): only fall back to "
                "the bare source_file name when download_url is null. "
                "These are the documents linked to this asset via document_ids."
            ),
        }


@tool
async def find_location(identifier: str) -> dict:
    """Resolve a SITE or LOCATION from any identifier — site_id (e.g. 'SITE-DXB-001'),
    site_code/site_name/city, or a location id/name/address/city — case-insensitively.

    Use this to resolve where an asset/work order is. Searches both the sites and locations
    tables. Returns matches from each (may be empty if the place truly isn't in the data).
    """
    ident = (identifier or "").strip()
    out: dict[str, Any] = {"identifier": ident, "sites": [], "locations": []}
    if not ident:
        return {**out, "count": 0}
    q = {"q": f"%{ident}%"}
    async with database.AsyncSessionLocal() as session:
        for tbl, cols, key in (
            ("sites", ["site_id", "site_code", "site_name", "city", "id"], "sites"),
            ("locations", ["id", "name", "address", "city"], "locations"),
        ):
            like = " OR ".join(f"CAST({c} AS TEXT) ILIKE :q" for c in cols)
            try:
                res = await session.execute(
                    text(f"SELECT * FROM plenum_cafm.{tbl} WHERE {like} LIMIT 10"), q
                )
                out[key] = [dict(r) for r in res.mappings().all()]
            except Exception as exc:  # one table failing shouldn't kill the other
                log.warning(f"udr.find_location.{tbl}.error", error=str(exc)[:200])
    out["count"] = len(out["sites"]) + len(out["locations"])
    return out


@tool
async def udr_agent_query(message: str) -> dict:
    """Run a natural-language UDR query via svc-udr agent endpoint.

    Use this for complex ad-hoc UDR requests where structured parameters are not
    known upfront; svc-udr will decide the sequence of list/describe/read/write.
    """
    try:
        resp = await _request(
            "POST",
            settings.udr_base_url,
            "/api/agent/query",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"message": message},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "agent_query")


@tool
async def udr_list_tables() -> dict:
    """List all UDR-exposed tables with row-count metadata."""
    try:
        resp = await _request(
            "GET",
            settings.udr_base_url,
            "/api/tables/",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_tables")


@tool
async def udr_describe_table(table: str) -> dict:
    """Describe table schema details including keys and relationships."""
    try:
        resp = await _request(
            "GET",
            settings.udr_base_url,
            f"/api/tables/{table}/schema",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "describe_table")


_MAX_READ_LIMIT = 500


@tool
async def udr_read_records(
    table: str,
    limit: int = 50,
    offset: int = 0,
    order_by: str | None = None,
    order_dir: str = "asc",
) -> dict:
    """Read records from a table with pagination and optional sorting (max 500 rows per call).

    For larger datasets use offset paging or write_file Mode 3 offload.
    """
    params: dict[str, Any] = {
        "limit": min(max(limit, 1), _MAX_READ_LIMIT),
        "offset": max(offset, 0),
        "order_dir": order_dir,
    }
    if order_by:
        params["order_by"] = order_by
    try:
        resp = await _request(
            "GET",
            settings.udr_base_url,
            f"/api/tables/{table}/records",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "read_records")


@tool
async def udr_get_record(table: str, record_id: str, id_column: str = "id") -> dict:
    """Fetch one record by primary key."""
    try:
        resp = await _request(
            "GET",
            settings.udr_base_url,
            f"/api/tables/{table}/records/{record_id}",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params={"id_column": id_column},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_record")


@tool
async def udr_search_records(
    table: str,
    search_term: str,
    search_columns: list[str],
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Search records using case-insensitive partial matches across columns."""
    try:
        resp = await _request(
            "POST",
            settings.udr_base_url,
            f"/api/tables/{table}/records/search",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "search_term": search_term,
                "search_columns": search_columns,
                "limit": limit,
                "offset": offset,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "search_records")


@tool
async def udr_create_record(table: str, data: dict[str, Any]) -> dict:
    """Create a record in a UDR table."""
    try:
        resp = await _request(
            "POST",
            settings.udr_base_url,
            f"/api/tables/{table}/records",
            service=_SERVICE,
            timeout=_TIMEOUT,
            max_attempts=1,
            json={"data": data},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "create_record")


@tool
async def udr_update_record(
    table: str,
    record_id: str,
    data: dict[str, Any],
    id_column: str = "id",
) -> dict:
    """Update fields on a record in a UDR table."""
    try:
        resp = await _request(
            "PATCH",
            settings.udr_base_url,
            f"/api/tables/{table}/records/{record_id}",
            service=_SERVICE,
            timeout=_TIMEOUT,
            max_attempts=1,
            json={"data": data, "id_column": id_column},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "update_record")


@tool
async def udr_delete_record(table: str, record_id: str, id_column: str = "id") -> dict:
    """Delete a record from a UDR table by primary key."""
    try:
        resp = await _request(
            "DELETE",
            settings.udr_base_url,
            f"/api/tables/{table}/records/{record_id}",
            service=_SERVICE,
            timeout=_TIMEOUT,
            max_attempts=1,
            params={"id_column": id_column},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "delete_record")


@tool
async def udr_execute_select(sql: str, params: dict[str, Any] | None = None) -> dict:
    """Execute a custom SELECT statement through UDR with safety checks."""
    try:
        resp = await _request(
            "POST",
            settings.udr_base_url,
            "/api/tables/query/select",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"sql": sql, "params": params or {}},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "execute_select")
